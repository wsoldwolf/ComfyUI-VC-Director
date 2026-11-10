"""Exact top-k grammar narrowing; otherwise retain native full-vocabulary sampling."""
import ctypes
import inspect
import logging
import math
from contextlib import contextmanager
from time import perf_counter

LOGGER = logging.getLogger("vc_director.inference")


class GrammarPrefilter:
    def __init__(self, native, grammar, keep, stats, *, top_p=1.0, min_p=0.0, typical_p=1.0):
        self.native, self.grammar, self.keep, self.stats = native, grammar, keep, stats
        self.pool = max(128, keep * 4)
        self.top = native.llama_sampler_init_top_k(self.pool)
        self.buffer = None
        self.top_p, self.min_p, self.typical_p = top_p, min_p, typical_p

    def _certified_nucleus(self, valid, cutoff):
        """Prove omitted mass cannot change native top-p/min-p survivors."""
        import numpy as np
        if self.typical_p != 1.0 or not len(valid):
            return False
        ordered = np.sort(valid.astype(np.float64))[::-1]
        weights = np.exp(ordered - ordered[0])
        cumulative = np.cumsum(weights)
        total = float(cumulative[-1])
        # Every omitted logit <= cutoff. At most K-known can enter the valid
        # top-k set. This upper bound is independent of their grammar validity.
        tail_bound = (self.keep - len(valid)) * math.exp(cutoff - float(ordered[0]))
        tolerance = (total + tail_bound) * 1e-4
        if self.top_p < 1.0:
            end = max(1, int(np.searchsorted(cumulative, self.top_p * total, side="left")) + 1)
            if end > len(ordered):
                return False
            previous = float(cumulative[end - 2]) if end > 1 else 0.0
            if end > 1 and previous >= self.top_p * total - tolerance:
                return False
            if cumulative[end - 1] <= self.top_p * (total + tail_bound) + tolerance:
                return False
        elif self.min_p > 0:
            # With no nucleus cutoff, min-p must eliminate every omitted token.
            if cutoff >= ordered[0] + math.log(self.min_p) - 1e-4:
                return False
            end = len(ordered)
        else:
            return False
        prefix = ordered[:end]
        if prefix[-1] <= cutoff or len(np.unique(prefix)) != end or int((ordered >= prefix[-1]).sum()) != end:
            return False
        if self.min_p > 0 and np.any(np.abs(prefix - (ordered[0] + math.log(self.min_p))) < 1e-4):
            return False  # Avoid floating-point boundary differences.
        return True

    def close(self):
        if self.top:
            self.native.llama_sampler_free(self.top)
            self.top = None

    def apply(self, pointer):
        started = perf_counter()
        self.stats["grammar_probe_calls"] = self.stats.get("grammar_probe_calls", 0) + 1
        try:
            import numpy as np
            source = pointer.contents
            original_size = source.size
            if source.size <= self.pool:
                return
            if self.buffer is None or len(self.buffer) < source.size:
                self.buffer = (self.native.llama_token_data * source.size)()
            ctypes.memmove(self.buffer, source.data, source.size * ctypes.sizeof(self.native.llama_token_data))
            candidates = self.native.llama_token_data_array(data=self.buffer, size=source.size,
                selected=-1, sorted=source.sorted)
            self.native.llama_sampler_apply(self.top, ctypes.byref(candidates))
            values = np.ctypeslib.as_array(self.buffer)[:candidates.size]
            cutoff = float(values["logit"].min())
            if not np.isfinite(cutoff):
                return
            # apply() does not advance the ordinary (non-lazy) grammar. The
            # original component still validates the final candidates and
            # receives accept/reset via the unmodified native sampler chain.
            self.native.llama_sampler_apply(self.grammar, ctypes.byref(candidates))
            valid = values["logit"][np.isfinite(values["logit"])]
            if len(valid) < self.keep:
                if not self._certified_nucleus(valid, cutoff):
                    return
                self.stats["grammar_nucleus_certificates"] = self.stats.get("grammar_nucleus_certificates", 0) + 1
            else:
                best = np.sort(valid)[-self.keep:]
                boundary = float(best[0])
                # No omitted token can reach the retained boundary. Fall back
                # on every tie so seeded sampling retains the same ordering.
                if boundary <= cutoff or len(np.unique(best)) != self.keep or int((valid >= boundary).sum()) != self.keep:
                    return
            source.data = candidates.data
            source.size = candidates.size
            source.selected = -1
            source.sorted = False
            self.stats["grammar_shortlist_hits"] = self.stats.get("grammar_shortlist_hits", 0) + 1
            self.stats["grammar_candidates_avoided"] = self.stats.get("grammar_candidates_avoided", 0) + original_size - candidates.size
        except Exception:
            # A ctypes callback must never leak exceptions or bypass the
            # original grammar. The original array is untouched on failure.
            self.stats["grammar_probe_errors"] = self.stats.get("grammar_probe_errors", 0) + 1
        finally:
            self.stats["grammar_probe_seconds"] = self.stats.get("grammar_probe_seconds", 0.0) + perf_counter() - started


def _insert_prefilter(sampler, native, keep, stats, options):
    count = native.llama_sampler_chain_n(sampler.sampler)
    components = [native.llama_sampler_chain_get(sampler.sampler, i) for i in range(count)]
    names = [native.llama_sampler_name(component) or b"" for component in components]
    if b"grammar" not in names:
        return
    index = names.index(b"grammar")
    # Only the stock top-k chain and greedy chain have the proven bounded
    # candidate semantics. Do not guess about custom/Mirostat sampling.
    tail_names = [name.lstrip(b"?") for name in names[index + 1:]]
    if tail_names not in ([b"top-k", b"typical", b"top-p", b"min-p", b"temp", b"dist"], [b"greedy"]):
        return
    if any(position >= index for position, _ in sampler.custom_samplers):
        return
    probe = GrammarPrefilter(native, components[index], keep, stats, top_p=options.get("top_p", 1.0),
                             min_p=options.get("min_p", 0.0), typical_p=options.get("typical_p", 1.0))
    sampler._exit_stack.callback(probe.close)
    tail = [native.llama_sampler_chain_remove(sampler.sampler, i) for i in range(count - 1, index - 1, -1)]
    try:
        sampler.add_custom(probe.apply)
    finally:
        # Restore native ownership even if the Python callback cannot be installed.
        for component in reversed(tail):
            native.llama_sampler_chain_add(sampler.sampler, component)


def install_fast_grammar(model, native):
    original = getattr(model, "_init_sampler", None)
    required = ("llama_sampler_chain_n", "llama_sampler_chain_get", "llama_sampler_chain_remove",
                "llama_sampler_chain_add", "llama_sampler_name", "llama_sampler_init_top_k",
                "llama_sampler_apply", "llama_sampler_free", "llama_token_data", "llama_token_data_array")
    if not hasattr(model, "__dict__") or not callable(original) or not all(hasattr(native, name) for name in required):
        return False
    try:
        signature = inspect.signature(original)
    except (ValueError, TypeError):
        return False
    model._vcd_fast_grammar_enabled = True
    model._vcd_sampling_stats = {}

    def initialize(*args, **kwargs):
        sampler = original(*args, **kwargs)
        if not model._vcd_fast_grammar_enabled:
            return sampler
        try:
            arguments = signature.bind(*args, **kwargs)
        except TypeError:
            return sampler
        arguments.apply_defaults()
        options = arguments.arguments
        temperature, top_k = options.get("temp"), options.get("top_k")
        if (options.get("grammar") is None or options.get("mirostat_mode", 0) != 0
                or temperature is None or temperature < 0 or type(top_k) is not int or not 1 <= top_k <= 256):
            return sampler
        try:
            _insert_prefilter(sampler, native, 1 if temperature == 0 else top_k, model._vcd_sampling_stats, options)
        except Exception:
            # Reconstruct the stock sampler before any tokens have been drawn.
            sampler.close()
            LOGGER.debug("Native grammar shortlist unavailable; using full vocabulary", exc_info=True)
            return original(*args, **kwargs)
        return sampler

    model._init_sampler = initialize
    return True


@contextmanager
def completion_profile(model, metrics):
    """Measure actual eval/sampling boundaries without importing GPU libraries."""
    if not hasattr(model, "__dict__"):
        yield
        return
    model._vcd_sampling_stats = {}
    originals, missing = {}, object()
    for name, key in (("eval", "evaluation_seconds"), ("sample", "sampling_seconds")):
        method = getattr(model, name, None)
        if not callable(method):
            continue
        originals[name] = (method, getattr(model, "__dict__", {}).get(name, missing))
        def timed(*args, _method=method, _key=key, **kwargs):
            started = perf_counter()
            try:
                return _method(*args, **kwargs)
            finally:
                metrics[_key] = metrics.get(_key, 0.0) + perf_counter() - started
        setattr(model, name, timed)
    try:
        yield
    finally:
        metrics.update(model._vcd_sampling_stats)
        for name, (method, previous) in originals.items():
            if previous is missing:
                delattr(model, name)
            else:
                setattr(model, name, previous)
