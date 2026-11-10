"""Neutral translation tests with an in-memory backend; no user data/models."""
import copy
import json
import random
import re
import tempfile
import types
import unittest
from pathlib import Path

from tools._bootstrap import load_project

load_project()

from vc_director.core.artifacts.actor_references import ActorReferences
from vc_director.core.compiler.api import compile_scene_emd
from vc_director.core.compiler.translation_lexicon import AliasGuard, guarded_text_rules
from vc_director.core.compiler.translation_slots import translation_grammar
from vc_director.core.emd.diagnostics import contextual_error
from vc_director.core.errors import VCError
from vc_director.core.inference.runtime import LlamaRuntimeConfig
from vc_director.core.inference.translator import GemmaTranslator


ACTORS = [dict(id="traveler", name="Alex"), dict(id="guide", name="Robin")]
ROLES = {"<Subject 1>": "actor", "<Subject 2>": "actor"}
NEUTRAL_SCENE = """# 形式
* schema: VCD_SCENE_EMD_V1
# 作品
* id: neutral_alias_compile
* 尺: 00:05.000
# 人物 @traveler
* 名前: Alex
* 特徴: 青い上着の成人。
# 背景 station
* 情景: A bright station.
# 展開定義 arrival
* 出演: @traveler
* 背景: station
* 原文: @traveler stands by the gate.
# Scene scene_arrival 00:00.000 --> 00:05.000
* 展開: arrival
* 出演: @traveler
* 画面内人物: @traveler
* 背景: station
* 接続: cut
* 依存: なし
* 固定: なし
* 映像方針: なし
## subject_definitions
* @traveler: 青い上着の成人。
## summary
* @traveler stands by the gate.
## retention_analysis
* @traveler: fully_preserved
## detailed_description
### Shot station_shot 00:00.000 --> 00:05.000
* CAMERA station_camera 00:00.000 --> 00:05.000 | environment | A fixed view of the gate.
## overall_soundscape
## non_diegetic_music
"""


def record(prefix, after=None):
    value = dict(prefix=prefix, slots=[dict(slot=1, position=1, after=after)]) if after is not None else prefix
    return "TRANSLATION\t1\t" + (json.dumps(value) if isinstance(value, dict) else value)


class NeutralBackend:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests, self.grammars = [], []
        self.closed = False
        self.config = LlamaRuntimeConfig(n_ctx=8192, max_tokens=256, gpu_layers=0)
        self.model_path = "in-memory-neutral-test-no-model-file"
        self.lifecycle = types.SimpleNamespace(ensure_loaded=lambda *args: None,
            count_serialized_prompt=lambda text: types.SimpleNamespace(count=100, estimated=False),
            effective_n_ctx=8192, complete_chat=self.complete_chat)

    def identity(self):
        return dict(model="neutral-translation-alias-test")

    def complete_chat(self, messages, config, **options):
        self.requests.append(copy.deepcopy(messages))
        self.grammars.append(options["grammar"])
        return next(self.responses)

    def close(self):
        self.closed = True


def generated_rules_accept(rules, text):
    """Interpret the small emitted GBNF subset, independently of the DFA."""
    accept, edges = set(), {}
    for line in rules:
        name, _, body = line.partition(" ::= ")
        edges[name] = []
        for alternative in body.split(" | "):
            if alternative == '""':
                accept.add(name)
                continue
            symbol, _, target = alternative.rpartition(" ")
            edges[name].append((re.compile(symbol) if symbol.startswith("[") else json.loads(symbol), target))
    active = [("text-0", 0)]
    visited = set()
    while active:
        name, offset = active.pop()
        if (name, offset) in visited:
            continue
        visited.add((name, offset))
        if offset == len(text) and name in accept:
            return True
        for symbol, target in edges[name]:
            if isinstance(symbol, str):
                if text.startswith(symbol, offset):
                    active.append((target, offset + len(symbol)))
            elif offset < len(text) and symbol.fullmatch(text[offset]):
                active.append((target, offset + 1))
    return False


class TranslationAliasRepairTests(unittest.TestCase):
    def test_failure_identifies_every_alias_and_id_without_rewriting(self):
        text = "Alex watches GUIDE."
        references = ActorReferences(ACTORS)
        with self.assertRaises(VCError) as caught:
            references.validate_translation(text, "translation")
        message = str(caught.exception)
        for detail in ("'Alex'", "'GUIDE'", "actor_ids=traveler", "actor_ids=guide", "characters=1..4"):
            self.assertIn(detail, message)
        self.assertEqual(text, "Alex watches GUIDE.")

    def test_protected_visible_names_and_subjects_are_not_identity_leaks(self):
        references = ActorReferences(ACTORS)
        text = '<Subject 1> reads <visible>Alex @guide</visible> on a sign.'
        self.assertEqual(references.translation_aliases(text), [])
        references.validate_translation(text, "translation")
        references.validate_translation("A travelerwood sign and a guidance panel.", "translation")

    def test_duplicate_registered_names_produce_specific_alias_feedback(self):
        references = ActorReferences([dict(id="traveler", name="Alex"), dict(id="guide", name="Alex")])
        with self.assertRaises(VCError) as caught:
            references.validate_translation("Alex stands.", "translation")
        self.assertEqual(caught.exception.diagnostic.code, "TRANSLATION_ACTOR_NAME")
        self.assertIn("actor_ids=guide,traveler", str(caught.exception))

    def test_alias_repair_keeps_slots_and_guard_is_present_from_first_attempt(self):
        backend = NeutralBackend([record("Alex stands beside ", "."), record("", " stands by the gate.")])
        result = GemmaTranslator(backend).translate_actor_protected(("", "が門の前に立つ。"),
            ("<Subject 1>",), ACTORS, owner_subject="<Subject 1>", allow_bound_pronouns=True,
            subject_roles=ROLES)
        self.assertEqual(result, "<Subject 1> stands by the gate.")
        self.assertEqual(backend.grammars[0], backend.grammars[1])
        self.assertEqual(backend.grammars[1], translation_grammar(1, tuple(sorted(ActorReferences(ACTORS).aliases))))
        retry = json.loads(backend.requests[1][1]["content"].removeprefix("/no_think\n"))
        self.assertIn("'Alex'", retry["retry_reason"])
        self.assertIn("actor_ids=traveler", retry["retry_reason"])
        self.assertEqual(retry["repair_context"]["expected_slot_ids"], [1])
        self.assertEqual(retry["field_context"]["forbidden_person_aliases"], sorted(ActorReferences(ACTORS).aliases))
        initial = json.loads(backend.requests[0][1]["content"].removeprefix("/no_think\n"))
        self.assertEqual(initial["field_context"]["forbidden_person_aliases"], sorted(ActorReferences(ACTORS).aliases))
        self.assertEqual(len(backend.requests), 2)

    def test_plain_definition_can_rephrase_word_matching_a_bare_id(self):
        actors = [dict(id="young", name="Alex")]
        backend = NeutralBackend([record("A young adult in a blue coat."), record("An adult with a youthful face wears a blue coat.")])
        result = GemmaTranslator(backend).translate_actor_protected(("青い上着を着た若い成人。",), (), actors,
            owner_subject="<Subject 1>", allow_bound_pronouns=True, subject_roles={"<Subject 1>": "actor"})
        self.assertEqual(result, "An adult with a youthful face wears a blue coat.")
        retry = json.loads(backend.requests[1][1]["content"].removeprefix("/no_think\n"))
        self.assertIn("'young'", retry["retry_reason"])
        self.assertIn("faithful synonym", retry["retry_reason"])
        self.assertEqual(retry["immutable_spans"], [])
        self.assertNotIn("<Subject", result)

    def test_authored_visible_name_is_restored_outside_generated_chunks(self):
        value = dict(prefix="", slots=[dict(slot=1, position=1, after=" reads "),
                                       dict(slot=2, position=2, after=".")])
        backend = NeutralBackend(["TRANSLATION\t1\t" + json.dumps(value)])
        result = GemmaTranslator(backend).translate_actor_protected(("", "が", "を見る。"),
            ("<Subject 1>", "<visible>Alex</visible>"), ACTORS)
        self.assertEqual(result, "<Subject 1> reads <visible>Alex</visible>.")
        self.assertEqual(backend.grammars, [translation_grammar(2, tuple(sorted(ActorReferences(ACTORS).aliases)))])

    def test_other_failures_keep_existing_grammar_and_two_attempt_limit(self):
        backend = NeutralBackend([record("", " whispers and turns."), record("", " turns.")])
        result = GemmaTranslator(backend).translate_actor_protected(("", "が振り返る。"), ("<Subject 1>",), ACTORS)
        self.assertEqual(result, "<Subject 1> turns.")
        self.assertEqual(backend.grammars, [translation_grammar(1, tuple(sorted(ActorReferences(ACTORS).aliases)))] * 2)

    def test_non_native_bad_response_still_stops_with_specific_japanese_reason(self):
        backend = NeutralBackend([record("Alex in a blue coat."), record("Alex wears a blue coat.")])
        with self.assertRaises(VCError) as caught:
            GemmaTranslator(backend).translate_actor_protected(("青い上着の成人。",), (), ACTORS)
        message = str(contextual_error(caught.exception, "# 作品\n* id: neutral_alias_test\n"))
        for text in ("人物名または裸の人物ID", "'Alex'", "actor_ids=traveler", "characters=1..4"):
            self.assertIn(text, message)
        self.assertEqual(len(backend.requests), 2)

    def test_compiler_reports_scene_owner_and_original_actor_header(self):
        backend = NeutralBackend([record("Alex in a blue coat.")] * 2)
        with self.assertRaises(VCError) as caught:
            compile_scene_emd(NEUTRAL_SCENE, [], GemmaTranslator(backend))
        error = contextual_error(caught.exception, NEUTRAL_SCENE)
        self.assertEqual(error.diagnostic.path, "traveler")
        self.assertIn("Scene scene_arrival / 対象 @traveler", str(error))
        self.assertIn("# 人物 @traveler", str(error))
        self.assertEqual(error.diagnostic.line, NEUTRAL_SCENE.splitlines().index("# 人物 @traveler") + 1)

    def test_neutral_compilation_finishes_with_subject_identity_preserved(self):
        backend = NeutralBackend([record("Alex in a blue coat."), record("An adult in a blue coat.")])
        result = compile_scene_emd(NEUTRAL_SCENE, [], GemmaTranslator(backend))
        prompt = result.plan["shots"][0]["prompt"]
        self.assertIn("<Subject 1>: An adult in a blue coat.", prompt)
        self.assertIn("<Subject 1> stands by the gate.", prompt)
        self.assertNotIn("Alex", prompt)
        self.assertEqual(len(backend.requests), 2)

    def test_valid_success_cache_remains_reusable(self):
        with tempfile.TemporaryDirectory(prefix="neutral_alias_", dir=Path(__file__).resolve().parent) as directory:
            backend = NeutralBackend([record("An adult in a blue coat.")])
            translator = GemmaTranslator(backend, directory)
            for _ in range(2):
                self.assertEqual(translator.translate_actor_protected(("青い上着の成人。",), (), ACTORS),
                                 "An adult in a blue coat.")
            self.assertEqual(len(backend.requests), 1)

    def test_guard_matches_independent_boundary_regex_instead_of_substrings(self):
        aliases = ("a", "alex", "robin", "traveler_1", "x-y", "o'neal")
        guard = AliasGuard(aliases)
        reference = re.compile(r"__mvd_keep_|" + "|".join(
            r"(?<![A-Za-z0-9_])" + re.escape(alias) + r"(?![A-Za-z0-9_])" for alias in aliases), re.I)
        texts = ["", "An adult.", "Alexandra and robins.", "x-y2", "ALEX's coat.", "__MVD_KEEP_0001__"]
        generator = random.Random(39)
        for _ in range(1000):
            texts.append(generator.choice((" ", "_", "pre", "(", "", "."))
                         + generator.choice(aliases + ("__mvd_keep_", "Alexandra", "plain"))
                         + generator.choice((" ", "_", "post", ")", "", ".")))
        for text in texts:
            self.assertEqual(guard.accepts(text), reference.search(text) is None, text)

    def test_generated_gbnf_has_same_boundary_and_escape_behavior(self):
        aliases = ("a", "alex", "traveler_1", "x-y", "o'neal")
        for json_string in (False, True):
            rules = guarded_text_rules(aliases, json_string=json_string)
            for text in ("", "An adult.", "alexandra", "ALEX", "an alex.", "x-y2", "x-y", "prealex", "_alex", "Alex's coat.", "__MVD_KEEP_0001__"):
                self.assertEqual(generated_rules_accept(rules, text), AliasGuard(aliases).accepts(text), text)
            self.assertFalse(generated_rules_accept(rules, "<Subject 1>"))
            self.assertFalse(generated_rules_accept(rules, "hello\nthere"))
        rules = guarded_text_rules(aliases, json_string=True)
        for encoded, decoded in ((r'Al\"ex', 'Al"ex'), (r'Alex\"', 'Alex"'),
                                 (r'Alex\/coat', 'Alex/coat'), (r'file\\path', 'file\\path')):
            self.assertEqual(generated_rules_accept(rules, encoded), AliasGuard(aliases).accepts(decoded), encoded)
        self.assertFalse(generated_rules_accept(rules, r'\u0061lex'))


if __name__ == "__main__":
    unittest.main()
