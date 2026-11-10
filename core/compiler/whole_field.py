"""Complete-clause translation with movable, occurrence-owned protected spans.

No action semantics are repaired here. The model owns English word order;
Python validates token coverage and restores the exact immutable values.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import logging
import re

from ..artifacts import canonical_json
from ..inference import build_context_budget
from ..protocols import ProtocolError, parse_llm_records
from ..utilities.progress import emit_progress
from .errors import CompilerError
from .protection import ProtectedUnit, _H3_CAMERA_DIRECTIVES


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts/prompt_translation_whole_field_system_prompt.txt"
MARKER_PREFIX = "__MVD_KEEP_"
_MARKER_RE = re.compile(r"__MVD_KEEP_[0-9]+__")
_RAW_REFERENCE_RE = re.compile(r"<(?:Subject|Picture|Video|Audio|visible)\b|<d(?:>|\[)", re.I)
_JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_LOGGER = logging.getLogger("mv_director.compiler")


def whole_field_prompt() -> str:
    value = PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not value:
        raise CompilerError("whole-field translation prompt is empty")
    return value + "\n"


def needs_relation_translation(unit: ProtectedUnit) -> bool:
    """Keep particles around every Subject reference, including a sole owner."""
    return any(re.fullmatch(r"<Subject [1-9][0-9]*>", value) for value in unit.values)


@dataclass(frozen=True, slots=True)
class MaskedField:
    text: str
    values: tuple[str, ...]

    @property
    def markers(self) -> tuple[str, ...]:
        return tuple(f"{MARKER_PREFIX}{i:04d}__" for i in range(1, len(self.values) + 1))

    @classmethod
    def from_unit(cls, unit: ProtectedUnit) -> "MaskedField":
        if MARKER_PREFIX in unit.text:
            raise CompilerError("source collides with reserved whole-field marker prefix")
        field = cls("", unit.values)
        pieces = []
        for index, fragment in enumerate(unit.fragments):
            pieces.append(fragment)
            if index < len(field.markers):
                pieces.append(field.markers[index])
        return cls("".join(pieces), unit.values)

    def marker_counts(self, translated: str) -> list[dict[str, object]]:
        actual, expected = Counter(_MARKER_RE.findall(translated)), Counter(self.markers)
        return [{"marker": marker, "expected": expected[marker], "actual": actual[marker]}
                for marker in sorted(actual.keys() | expected.keys())]

    def payload(self, *, retry_reason: str = "", previous_response: str | None = None,
                previous_translation: str | None = None, field_context: dict | None = None,
                occurrence_slots: bool = False) -> str:
        spans = []
        for marker, value in zip(self.markers, self.values):
            dialogue = value.startswith("<d")
            visible_text = value.startswith("<visible>")
            spans.append({"marker": marker, "kind": (
                "authored_dialogue" if dialogue else "visible_text" if visible_text else "reference" if value.startswith("<")
                else "camera_literal" if value in _H3_CAMERA_DIRECTIVES else "literal_term"),
                "meaning": "literal dialogue retained by the caller" if dialogue else value})
            if occurrence_slots:
                spans[-1]["slot"] = len(spans)
        request = {"protocol": "MVD_LLM_RECORDS_V1",
            "task": "translation-whole-protected-ja-en", "slot": 1,
            "japanese_text": self.text, "immutable_spans": spans,
            "retry_reason": retry_reason}
        if occurrence_slots:
            request["output_format"] = "occurrence-slots-v1" if spans else "plain-text-v1"
        if field_context is not None:
            request["field_context"] = field_context
        if previous_response is not None:
            request["repair_context"] = {
                "previous_response": previous_response,
            }
            if occurrence_slots and spans:
                # The draft is JSON, not a masked English field. Counting
                # literal markers in it would falsely report every slot missing.
                request["repair_context"]["expected_slot_ids"] = list(range(1, len(spans) + 1))
            else:
                request["repair_context"].update(previous_translation=previous_translation,
                    marker_counts=self.marker_counts(previous_translation if previous_translation is not None else previous_response))
        return canonical_json(request)

    def restore(self, translated: str) -> str:
        if not isinstance(translated, str) or not translated.strip():
            raise CompilerError("whole-field translation is empty")
        actual, expected = Counter(_MARKER_RE.findall(translated)), Counter(self.markers)
        if actual != expected:
            errors = ", ".join(f"{marker}: expected={expected[marker]}, got={actual[marker]}"
                for marker in sorted(actual.keys() | expected.keys())
                if actual[marker] != expected[marker])
            raise CompilerError(f"whole-field marker coverage mismatch: {errors}")
        ordinary = _MARKER_RE.sub("", translated)
        if MARKER_PREFIX in ordinary or _RAW_REFERENCE_RE.search(ordinary):
            raise CompilerError("whole-field translation introduced an unprotected reference or marker")
        if _JAPANESE_RE.search(ordinary):
            raise CompilerError("whole-field translation contains untranslated Japanese")
        for value in self.values:
            if not value.startswith("<") and re.search(
                r"(?<!\w)" + re.escape(value) + r"(?!\w)", ordinary):
                raise CompilerError("whole-field translation duplicated a protected literal")
        values = dict(zip(self.markers, self.values))
        # Single substitution: authored text containing marker-like text is
        # never interpreted as a second replacement instruction.
        return _MARKER_RE.sub(lambda match: values[match.group()], translated.strip())


class WholeFieldTranslator:
    def __init__(self, lifecycle, *, runtime_config, interrupt_callback=None, validate_translation=None, response_grammar=None, field_context=None, occurrence_slots=False, grammar_factory=None):
        runtime_config.validate()
        self.lifecycle, self.runtime_config = lifecycle, runtime_config
        self.interrupt_callback = interrupt_callback
        self.validate_translation = validate_translation
        self.response_grammar = response_grammar
        self.field_context = field_context
        self.occurrence_slots = occurrence_slots
        self.grammar_factory = grammar_factory
        self.system_prompt = whole_field_prompt()
        self.batch_count = self.estimated_token_batches = 0
        self.trace = []

    def translate_unit(self, unit: ProtectedUnit) -> str:
        field = MaskedField.from_unit(unit)
        grammar = self.response_grammar
        if self.occurrence_slots:
            from .translation_slots import translation_grammar, restore_slots
            grammar = translation_grammar(len(field.markers))
        reason = ""
        previous_response = previous_translation = None
        for attempt in range(1, 3):
            if self.grammar_factory is not None:
                grammar = self.grammar_factory(unit)
            emit_progress("status", label="翻訳中" if attempt == 1 else "翻訳・再試行 2/2")
            payload = "/no_think\n" + field.payload(retry_reason=reason,
                previous_response=previous_response, previous_translation=previous_translation, field_context=self.field_context,
                occurrence_slots=self.occurrence_slots)
            count = self.lifecycle.count_serialized_prompt(self.system_prompt + "\n" + payload)
            build_context_budget(count.count, self.runtime_config.max_tokens,
                self.lifecycle.effective_n_ctx or self.runtime_config.n_ctx, estimated=count.estimated)
            # No clause splitting, source shortening, or context-free fallback.
            response = self.lifecycle.complete_chat([
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": payload}], self.runtime_config,
                interrupt_callback=self.interrupt_callback,
                **({"grammar": grammar} if grammar is not None else {}))
            self.batch_count += 1
            self.estimated_token_batches += bool(count.estimated)
            row = {"attempt": attempt, "masked_source": field.text,
                   "markers": list(field.markers), "response": response,
                   "request": payload,
                   "operation": "repair" if previous_response is not None else "translate",
                   "retry_reason": reason}
            row["input_tokens"] = count.count
            row["token_count_estimated"] = count.estimated
            row["performance"] = dict(getattr(self.lifecycle, "last_completion_metrics", {}))
            self.trace.append(row)
            normalized = re.sub(r"<think>.*?</think>", "", response, flags=re.S)
            candidate = None
            assembled = False
            try:
                parsed = parse_llm_records(normalized,
                    allowed_slots={"TRANSLATION": frozenset({1})},
                    required=frozenset({("TRANSLATION", 1)}))
                if parsed.issues or parsed.missing or len(parsed.records) != 1:
                    raise CompilerError("whole-field translation line protocol mismatch")
                candidate = parsed.records[0].text
                if self.occurrence_slots and field.markers:
                    row["slot_response"] = candidate
                    candidate = restore_slots(candidate, field.markers)
                assembled = True
                row["masked_translation"] = candidate
                restored = field.restore(candidate)
                if self.validate_translation is not None:
                    self.validate_translation(restored)
            except (CompilerError, ProtocolError) as exc:
                reason = str(exc)
                row["error"] = reason
                if assembled or not (self.occurrence_slots and field.markers):
                    row["marker_counts"] = field.marker_counts(candidate if candidate is not None else normalized)
                previous_response = normalized
                previous_translation = None if self.occurrence_slots and field.markers else candidate
                _LOGGER.warning("[MV Director - EMD Compiler (Ref2VA)] whole-field attempt %d/2 invalid: %s", attempt, reason)
                continue
            row["restored"] = restored
            return restored
        raise CompilerError(f"whole-field translation invalid after 2 attempts: {reason}")
