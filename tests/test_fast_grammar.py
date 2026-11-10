import ctypes
import itertools
import unittest
import numpy as np

from tools._bootstrap import load_project
load_project()
from vc_director.core.inference.fast_grammar import GrammarPrefilter, install_fast_grammar, completion_profile


class Token(ctypes.Structure):
    _fields_ = [("id", ctypes.c_int32), ("logit", ctypes.c_float), ("p", ctypes.c_float)]


class Candidates(ctypes.Structure):
    _fields_ = [("data", ctypes.POINTER(Token)), ("size", ctypes.c_size_t), ("selected", ctypes.c_int64), ("sorted", ctypes.c_bool)]


class Native:
    """Pure CPU sampler operations; retain actual pointer ownership semantics."""
    llama_token_data = Token
    llama_token_data_array = Candidates

    def __init__(self):
        self.freed = []

    def llama_sampler_init_top_k(self, count):
        return count

    def llama_sampler_free(self, sampler):
        self.freed.append(sampler)

    def llama_sampler_apply(self, sampler, pointer):
        array = ctypes.cast(pointer, ctypes.POINTER(Candidates)).contents
        data = np.ctypeslib.as_array(array.data, shape=(array.size,))
        if isinstance(sampler, int):
            order = np.argsort(-data["logit"], kind="stable")
            data[:] = data[order].copy()
            array.size = min(array.size, sampler)
            array.sorted = True
        else:
            sampler(data)


def candidates(logits):
    values = (Token * len(logits))()
    for index, logit in enumerate(logits):
        values[index].id, values[index].logit = index, logit
    return values, Candidates(data=values, size=len(values), selected=-1, sorted=False)


class FastGrammarTests(unittest.TestCase):
    def run_probe(self, logits, allowed, keep=40):
        native, stats = Native(), {}
        def grammar(data):
            data["logit"][~np.isin(data["id"], allowed)] = -np.inf
        buffer, array = candidates(logits)
        original, expected = candidates(logits)
        before = bytes(buffer)
        probe = GrammarPrefilter(native, grammar, keep, stats)
        probe.apply(ctypes.pointer(array))
        narrowed = array.size < len(logits)
        if not narrowed:
            self.assertEqual(bytes(buffer), before)
            self.assertEqual(ctypes.addressof(array.data.contents), ctypes.addressof(buffer))
        # In either path the original native grammar and top-k still execute.
        for value in (array, expected):
            native.llama_sampler_apply(grammar, ctypes.pointer(value))
            native.llama_sampler_apply(keep, ctypes.pointer(value))
        actual_ids = [(array.data[i].id, array.data[i].logit) for i in range(array.size)]
        expected_ids = [(expected.data[i].id, expected.data[i].logit) for i in range(expected.size)]
        self.assertEqual(actual_ids, expected_ids)
        probe.close(); probe.close()
        self.assertEqual(native.freed, [max(128, keep * 4)])
        return narrowed, stats

    def test_shortlist_preserves_valid_top_k_and_native_selection_inputs(self):
        narrowed, stats = self.run_probe(np.arange(512, dtype=np.float32), np.arange(200, 512))
        self.assertTrue(narrowed)
        self.assertEqual(stats["grammar_shortlist_hits"], 1)
        self.assertGreater(stats["grammar_candidates_avoided"], 0)

    def test_low_rank_valid_tokens_and_small_valid_vocab_fall_back_without_data_loss(self):
        for allowed in (np.arange(40), np.array([500, 501, 502])):
            narrowed, stats = self.run_probe(np.arange(512, dtype=np.float32), allowed)
            self.assertFalse(narrowed)
            self.assertNotIn("grammar_shortlist_hits", stats)

    def test_ties_and_nonfinite_cutoff_use_stock_sampler_for_seed_equivalence(self):
        for logits in (np.zeros(512, dtype=np.float32), np.r_[np.full(500, -np.inf), np.arange(12)]):
            narrowed, _ = self.run_probe(logits, np.arange(512))
            self.assertFalse(narrowed)

    def test_greedy_shortlist_retains_the_same_unique_maximum(self):
        narrowed, _ = self.run_probe(np.arange(512, dtype=np.float32), np.arange(200, 512), keep=1)
        self.assertTrue(narrowed)

    def test_probe_exception_leaves_native_grammar_and_input_intact(self):
        native, stats = Native(), {}
        def fail(data):
            raise RuntimeError("simulated candidate inspection failure")
        buffer, array = candidates(np.arange(512, dtype=np.float32))
        before = bytes(buffer)
        probe = GrammarPrefilter(native, fail, 40, stats)
        probe.apply(ctypes.pointer(array))
        self.assertEqual(bytes(buffer), before)
        self.assertEqual(array.size, 512)
        self.assertEqual(stats["grammar_probe_errors"], 1)
        probe.close()

    def test_nucleus_certificate_preserves_survivors_for_every_bounded_tail(self):
        def survivors(logits, k, top_p, min_p):
            order = np.argsort(-logits, kind="stable")[:k]
            values = logits[order]
            weights = np.exp(values.astype(np.float64) - float(values[0]))
            probabilities = (weights / weights.sum()).astype(np.float32)
            if top_p < 1:
                end = int(np.searchsorted(np.cumsum(probabilities), top_p, side="left")) + 1
                order, probabilities = order[:end], probabilities[:end]
            return order[probabilities >= probabilities[0] * min_p].tolist()
        certified = 0
        for n, cutoff, p, minimum in itertools.product((1, 2, 3), (-20., -8., -3.), (.4, .9, .95, 1.), (0., .05, .2)):
            known = np.linspace(0, -2, n, dtype=np.float32)
            probe = GrammarPrefilter(Native(), None, 4, {}, top_p=p, min_p=minimum)
            try:
                if not probe._certified_nucleus(known, cutoff):
                    continue
                certified += 1
                expected = survivors(known, 4, p, minimum)
                for count in range(5 - n):
                    for tail in itertools.product((cutoff, cutoff - 1, cutoff - 5), repeat=count):
                        actual = survivors(np.r_[known, np.array(tail, dtype=np.float32)], 4, p, minimum)
                        self.assertEqual(actual, expected, (known, cutoff, p, minimum, tail))
            finally:
                probe.close()
        self.assertGreater(certified, 20)

    def test_uncertain_nucleus_ties_and_typical_sampling_use_full_grammar(self):
        for options, known, cutoff in [
            (dict(top_p=.9), [0., -1.], -2.),
            (dict(top_p=.4), [0., 0.], -20.),
            (dict(top_p=.9, typical_p=.8), [0., -1.], -20.),
        ]:
            probe = GrammarPrefilter(Native(), None, 40, {}, **options)
            try:
                self.assertFalse(probe._certified_nucleus(np.array(known, dtype=np.float32), cutoff))
            finally:
                probe.close()

    def test_unsupported_backend_is_untouched(self):
        class Model:
            def _init_sampler(self): return "native"
        model = Model()
        self.assertFalse(install_fast_grammar(model, object()))
        self.assertEqual(model._init_sampler(), "native")

    def test_request_profile_restores_inherited_methods_even_on_interruption(self):
        class Model:
            def eval(self, values): return values
            def sample(self): return 7
        model, metrics = Model(), {}
        with self.assertRaises(InterruptedError):
            with completion_profile(model, metrics):
                self.assertEqual(model.eval([1]), [1])
                self.assertEqual(model.sample(), 7)
                model._vcd_sampling_stats["grammar_shortlist_hits"] = 2
                raise InterruptedError()
        self.assertNotIn("eval", model.__dict__)
        self.assertNotIn("sample", model.__dict__)
        self.assertGreaterEqual(metrics["evaluation_seconds"], 0)
        self.assertGreaterEqual(metrics["sampling_seconds"], 0)
        self.assertEqual(metrics["grammar_shortlist_hits"], 2)
        class Static:
            __slots__ = ()
        with completion_profile(Static(), {}):
            pass


if __name__ == "__main__":
    unittest.main()
