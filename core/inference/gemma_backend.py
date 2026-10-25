"""VC adapters around MV-Director's lazy llama.cpp and protected translation."""
import threading
from pathlib import Path

from ..utilities import canonical, digest
from .llama_cpp_backend import LlamaCppLifecycle
from .runtime import LlamaRuntimeConfig
from .budget import build_context_budget
from .profile_loader import runtime_config

_LOCK = threading.RLock()


def interrupt():
    try:
        from comfy.model_management import throw_exception_if_processing_interrupted
    except ImportError:
        return
    throw_exception_if_processing_interrupted()


class GemmaBackend:
    def __init__(self, model_path, config=None):
        self.model_path = Path(model_path).resolve(strict=True)
        self.config = config or runtime_config("planner")
        self.config.validate()
        self.lifecycle = LlamaCppLifecycle()
        self.trace = []
        self._identity = None

    def identity(self):
        if self._identity is not None:
            return self._identity
        import hashlib
        hasher = hashlib.sha256()
        with self.model_path.open("rb") as handle:
            for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                interrupt()
                hasher.update(block)
        self._identity = dict(model_sha256=hasher.hexdigest(), model_name=self.model_path.name, runtime=self.config.to_dict())
        return self._identity

    def complete_record(self, system, request, record_type, slot=1, *, payload_schema=None):
        from ..protocols.grammar import record_grammar
        return self.complete(system, request, grammar=record_grammar(record_type, slot, payload_schema=payload_schema))

    def complete(self, system, request, *, grammar=None):
        with _LOCK:
            try:
                interrupt()
                self.lifecycle.ensure_loaded(self.model_path, self.config)
                count = self.lifecycle.count_serialized_prompt(system + "\n" + request)
                # Reserve output and chat-template overhead. Mandatory source is never truncated.
                build_context_budget(count.count + 256, self.config.max_tokens,
                                     self.lifecycle.effective_n_ctx or self.config.n_ctx, estimated=count.estimated)
                response = self.lifecycle.complete_chat([dict(role="system", content=system), dict(role="user", content=request)], self.config, interrupt_callback=interrupt, grammar=grammar)
                self.trace.append(dict(request_sha256=digest(request), system_sha256=digest(system), response=response, input_tokens=count.count, estimated=count.estimated,
                                       grammar_sha256=digest(grammar) if grammar else None))
                return response
            except BaseException:
                self.lifecycle.clear()
                raise

    def close(self):
        with _LOCK:
            self.lifecycle.clear()


