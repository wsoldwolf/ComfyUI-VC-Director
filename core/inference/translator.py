"""Protected prose translation through the pinned MV implementation."""
import re
from ..utilities import canonical, digest
from .gemma_backend import _LOCK, interrupt
from .cache import SuccessCache
from ..compiler.protection import ProtectedUnit
from ..compiler.whole_field import WholeFieldTranslator

class GemmaTranslator:
    def __init__(self, backend, cache_root=None):
        self.backend = backend
        self.trace = []
        self.cache = SuccessCache(cache_root) if cache_root else None

    def __call__(self, text):
        return self.translate_protected((text,), ())

    def translate_protected(self, fragments, values):
        unit = ProtectedUnit(fragments, values)
        if not re.search(r"[\u3040-\u30ff\u3400-\u9fff]", "".join(fragments)):
            return unit.text
        from ..compiler.whole_field import whole_field_prompt
        key = digest(canonical(dict(source=unit.text, model=self.backend.identity(), prompt=whole_field_prompt())))
        cached = self.cache.get(key) if self.cache else None
        if cached and isinstance(cached.get("result"), str):
            # The compiler rechecks speech/reference invariants on every cache hit.
            self.trace.append(dict(cache="hit", key=key))
            return cached["result"]
        with _LOCK:
            try:
                interrupt()
                self.backend.lifecycle.ensure_loaded(self.backend.model_path, self.backend.config)
                translator = WholeFieldTranslator(self.backend.lifecycle, runtime_config=self.backend.config, interrupt_callback=interrupt)
                result = translator.translate_unit(unit)
                from ..artifacts.validation import _prose
                ordinary = re.sub(r"<(?:Subject|Picture|Video|Audio) [0-9]+>", "", result)
                _prose(ordinary, "translation", [], cues=True)
                self.trace.extend(translator.trace)
                if self.cache:
                    self.cache.put_success(key, dict(result=result))
                return result
            except BaseException:
                self.backend.close()
                raise
