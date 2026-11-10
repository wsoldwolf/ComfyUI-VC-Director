"""Protected prose translation through the pinned MV implementation."""
import logging
import re
import uuid
from time import perf_counter
from pathlib import Path
from ..utilities import canonical, digest
from ..utilities.progress import emit_progress
from ..utilities.diagnostics import save_diagnostic
from ..errors import VCError
from .gemma_backend import _LOCK, interrupt
from .cache_policy import PipelineCache as SuccessCache
from ..compiler.protection import ProtectedUnit
from ..compiler.whole_field import WholeFieldTranslator
from ..compiler.translation_slots import slot_prompt, translation_grammar
from ..compiler.errors import CompilerError
from ..artifacts.actor_references import ActorReferences, bound_pronoun_owner, ENGLISH_PERSON_PRONOUNS

LOGGER = logging.getLogger("vc_director.translation")
TRANSLATION_VERSION = "vcd-prose-v7-proactive-reference-grammar"
TRANSLATION_GRAMMAR = translation_grammar()
VC_INSTRUCTIONS = """
This field contains non-vocal prose, not dialogue. It may describe visible
actions, permitted physical sounds or instrumental music; preserve their meaning.
Never add speech, narration,
singing, humming, sighs, vocalization, quoted dialogue or dialogue tags.
Translate the source faithfully and retain every protected occurrence through its slot.
Do not write marker spellings in English chunks.
Source line breaks separate parts of the same field. Translate all parts into
one physical output line, separated by spaces. Never omit a part to avoid a newline.
If retry_reason reports a vocal cue, remove the invented vocal action while
preserving every source action and marker. The caller retains authored dialogue
separately; do not reconstruct it. Do not delete a source action to evade a check.
"""
ACTOR_INSTRUCTIONS = """
Person identities have already been bound to protected Subject occurrences.
Never invent, translate, transliterate or restore a person's name or bare actor ID.
Keep each occurrence's actor, target and ownership exactly as the source states.
Use its source slot; never print a raw reference or marker inside English chunks.
"""
EXPLICIT_PEOPLE_INSTRUCTIONS = """
This is a multi-participant visual field. Do not introduce person pronouns such
as she, her, he, his, they or their. Retain explicit protected actor occurrences.
Use coordinated verbs or restructure the sentence to preserve each source slot once
without adding a new person mention. Do not guess an antecedent or replace a
visible actor with an off-screen speaker. Do not invent additional slots or markers.
"""
BOUND_OWNER_INSTRUCTIONS = """
The caller supplies field_owner_subject and pronoun_owner as context.
field_owner_subject is the ACTION/CAMERA or definition owner already emitted by
the caller; it is not a new source occurrence and is NOT an immutable span.
If pronoun_owner is present, English personal, possessive and reflexive pronouns
may refer only to that one person. They do not authorize an additional actor.
Keep the source occurrences explicit using all existing slots exactly once;
use pronouns only for clear anaphora, never to remove a source occurrence.
If pronoun_owner is absent, avoid person pronouns by coordinating
verbs or using an unambiguous noun phrase without changing the action.
Never emit field_owner_subject directly, create a slot for it, or duplicate
an existing occurrence. The immutable_spans list is exhaustive, even when empty.
A marker-free source must have a marker-free translation. Do not invent a
person, slot or reference to satisfy a pronoun-related retry_reason.
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
        return self._translate_protected(fragments, values)

    def translate_actor_protected(self, fragments, values, actors, *, explicit_people=False,
                                  owner_subject=None, allow_bound_pronouns=False, subject_roles=None):
        return self._translate_protected(fragments, values, actor_references=ActorReferences(actors),
            explicit_people=explicit_people, owner_subject=owner_subject, allow_bound_pronouns=allow_bound_pronouns,
            subject_roles=subject_roles)

    def _translate_protected(self, fragments, values, *, actor_references=None, explicit_people=False,
                             owner_subject=None, allow_bound_pronouns=False, subject_roles=None):
        unit = ProtectedUnit(fragments, values)
        pronoun_owner = bound_pronoun_owner(unit.text, owner_subject, subject_roles=subject_roles) if allow_bound_pronouns else None
        aliases = tuple(sorted(actor_references.aliases)) if actor_references is not None else ()
        pronouns = (ENGLISH_PERSON_PRONOUNS if actor_references is not None and explicit_people
                    and pronoun_owner is None else ())
        forbidden = tuple(sorted(set(aliases) | set(pronouns)))
        field_context = (dict(field_owner_subject=owner_subject, pronoun_owner=pronoun_owner)
                         | (dict(subject_roles=subject_roles) if subject_roles is not None else {})
                         if allow_bound_pronouns else {})
        if actor_references is not None:
            field_context.update(forbidden_person_aliases=list(aliases), forbidden_person_pronouns=list(pronouns))

        def validate_result(result):
            _validate_translation(result)
            if actor_references is not None:
                try:
                    actor_references.validate_translation(result, "translation", explicit_people=explicit_people, pronoun_owner=pronoun_owner,
                                                         subject_roles=subject_roles)
                except VCError as exc:
                    raise CompilerError(str(exc)) from exc

        def grammar_for_attempt(source_unit):
            # These constraints are known before generation. Do not relearn the
            # same alias/pronoun failure on the first attempt of every field.
            return translation_grammar(len(source_unit.values), forbidden_aliases=forbidden)

        if not re.search(r"[\u3040-\u30ff\u3400-\u9fff]", "".join(fragments)):
            if actor_references is not None:
                validate_result(unit.text)
            return unit.text
        system = slot_prompt() + VC_INSTRUCTIONS
        if actor_references is not None:
            system += ACTOR_INSTRUCTIONS
            if allow_bound_pronouns:
                system += BOUND_OWNER_INSTRUCTIONS
            elif explicit_people:
                system += EXPLICIT_PEOPLE_INSTRUCTIONS
        model = self.backend.identity()
        key = digest(canonical(dict(version=TRANSLATION_VERSION, source=unit.text, model=model, prompt=system,
                                    actor_aliases={name: sorted(ids) for name, ids in actor_references.aliases.items()} if actor_references else None,
                                    owner_subject=owner_subject, pronoun_owner=pronoun_owner, subject_roles=subject_roles,
                                    explicit_people=explicit_people, allow_bound_pronouns=allow_bound_pronouns,
                                    generation_constraints=field_context)))
        cached = self.cache.get(key) if self.cache else None
        if cached and isinstance(cached.get("result"), str):
            try:
                validate_result(cached["result"])
            except CompilerError:
                pass
            else:
                self.trace.append(dict(cache="hit", key=key))
                emit_progress("status", label="翻訳キャッシュを使用")
                return cached["result"]
        path = self.diagnostics_root / f"translation-{key[:12]}-{uuid.uuid4().hex}.json" if self.diagnostics_root else None
        diagnostic = dict(schema="VCD_TRANSLATION_DIAGNOSTIC_V1", version=TRANSLATION_VERSION, model=model,
                          source=unit.text, protected_values=list(unit.values), system_prompt=system,
                          owner_subject=owner_subject, pronoun_owner=pronoun_owner, subject_roles=subject_roles, attempts=[], status="running")
        save_diagnostic(path, diagnostic, LOGGER)
        translator = None
        with _LOCK:
            try:
                from ..artifacts.validation import _prose
                _prose("".join(fragments), "translation/source", [], cues=True)
                interrupt()
                load_started = perf_counter()
                self.backend.lifecycle.ensure_loaded(self.backend.model_path, self.backend.config)
                diagnostic["model_load_seconds"] = perf_counter() - load_started
                translator = WholeFieldTranslator(self.backend.lifecycle, runtime_config=self.backend.config, interrupt_callback=interrupt,
                    validate_translation=validate_result, response_grammar=TRANSLATION_GRAMMAR,
                    field_context=field_context or None, occurrence_slots=True,
                    grammar_factory=grammar_for_attempt)
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
