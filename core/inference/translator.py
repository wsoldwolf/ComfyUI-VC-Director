"""Protected prose translation through the pinned MV implementation."""
import logging
import re
import uuid
from pathlib import Path
from ..utilities import canonical, digest
from ..utilities.diagnostics import save_diagnostic
from ..errors import VCError
from .gemma_backend import _LOCK, interrupt
from .cache import SuccessCache
from ..compiler.protection import ProtectedUnit
from ..compiler.whole_field import WholeFieldTranslator
from ..compiler.errors import CompilerError

LOGGER = logging.getLogger("vc_director.translation")
TRANSLATION_VERSION = "vcd-prose-v2-semantic-repair"
VC_INSTRUCTIONS = """
This field contains non-vocal prose, not dialogue. It may describe visible
actions, permitted physical sounds or instrumental music; preserve their meaning.
Never add speech, narration,
singing, humming, sighs, vocalization, quoted dialogue or dialogue tags.
Translate the source faithfully and retain every protected marker.
If retry_reason reports a vocal cue, remove the invented vocal action while
preserving every source action and marker. The caller retains authored dialogue
separately; do not reconstruct it. Do not delete a source action to evade a check.
"""


def _validate_translation(result):
    from ..artifacts.validation import _prose
    ordinary = re.sub(r"<(?:Subject|Picture|Video|Audio) [0-9]+>", "", result)
    try:
        _prose(ordinary, "translation", [], cues=True)
    except VCError as exc:
        # The reused translator handles protocol, coverage and semantic repairs
        # together within its existing two-attempt limit.
        raise CompilerError(str(exc)) from exc

class GemmaTranslator:
    def __init__(self, backend, cache_root=None, *, diagnostics_root=None):
        self.backend = backend
        self.trace = []
        self.cache = SuccessCache(cache_root) if cache_root else None
        self.diagnostics_root = (Path(diagnostics_root) if diagnostics_root is not None
                                 else Path(cache_root) / "diagnostics" if cache_root else None)

    def __call__(self, text):
        return self.translate_protected((text,), ())

    def translate_protected(self, fragments, values):
        unit = ProtectedUnit(fragments, values)
        if not re.search(r"[\u3040-\u30ff\u3400-\u9fff]", "".join(fragments)):
            return unit.text
        from ..compiler.whole_field import whole_field_prompt
        system = whole_field_prompt() + VC_INSTRUCTIONS
        model = self.backend.identity()
        key = digest(canonical(dict(version=TRANSLATION_VERSION, source=unit.text, model=model, prompt=system)))
        cached = self.cache.get(key) if self.cache else None
        if cached and isinstance(cached.get("result"), str):
            try:
                _validate_translation(cached["result"])
            except CompilerError:
                pass
            else:
                self.trace.append(dict(cache="hit", key=key))
                return cached["result"]
        path = self.diagnostics_root / f"translation-{key[:12]}-{uuid.uuid4().hex}.json" if self.diagnostics_root else None
        diagnostic = dict(schema="VCD_TRANSLATION_DIAGNOSTIC_V1", version=TRANSLATION_VERSION, model=model,
                          source=unit.text, protected_values=list(unit.values), system_prompt=system, attempts=[], status="running")
        save_diagnostic(path, diagnostic, LOGGER)
        translator = None
        with _LOCK:
            try:
                from ..artifacts.validation import _prose
                _prose("".join(fragments), "translation/source", [], cues=True)
                interrupt()
                self.backend.lifecycle.ensure_loaded(self.backend.model_path, self.backend.config)
                translator = WholeFieldTranslator(self.backend.lifecycle, runtime_config=self.backend.config, interrupt_callback=interrupt,
                                                  validate_translation=_validate_translation)
                translator.system_prompt = system
                result = translator.translate_unit(unit)
                if self.cache:
                    self.cache.put_success(key, dict(result=result))
                diagnostic.update(status="success", result=result)
                return result
            except BaseException as exc:
                diagnostic.update(status="error", error=dict(type=type(exc).__name__, message=str(exc)))
                self.backend.close()
                saved = save_diagnostic(path, dict(diagnostic, attempts=translator.trace if translator else []), LOGGER)
                suffix = f"; 応答ログ: {saved}" if saved else ""
                if isinstance(exc, CompilerError):
                    raise VCError("TRANSLATION_RETRIES_EXHAUSTED", "translation", str(exc) + suffix) from exc
                if isinstance(exc, VCError):
                    d = exc.diagnostic
                    raise VCError(d.code, d.path, d.message + suffix, d.line) from exc
                raise
            finally:
                if translator is not None:
                    diagnostic["attempts"] = translator.trace
                    self.trace.extend(translator.trace)
                save_diagnostic(path, diagnostic, LOGGER)
