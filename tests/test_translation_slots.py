import copy
import json
import unittest
from pathlib import Path

from test_core import compile_fixture, temporary_directory
from test_prop_tags import prop_document
from test_scene_roles import story
import test_translation_semantics as translation_tests
from vc_director.core.artifacts.actor_references import bound_pronoun_owner, ActorReferences
from vc_director.core.artifacts.storage import save, load
from vc_director.core.compiler import compile_document
from vc_director.core.compiler.api import REFERENCE_PROMPT_POLICY
from vc_director.core.compiler.errors import CompilerError
from vc_director.core.compiler.translation_slots import restore_slots, translation_grammar
from vc_director.core.emd import render_scene
from vc_director.core.emd.diagnostics import contextual_error
from vc_director.core.errors import VCError
from vc_director.core.inference import GemmaTranslator

ACTORS = [dict(id="maho", name="真帆"), dict(id="risa", name="梨沙")]
ROLES = {"<Subject 1>": "actor", "<Subject 2>": "actor", "<Subject 3>": "prop", "<Subject 4>": "prop"}
MARKERS = ("__MVD_KEEP_0001__", "__MVD_KEEP_0002__")


def record(value):
    return "TRANSLATION\t1\t" + json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class TypedSlotTests(unittest.TestCase):
    def test_person_with_multiple_props_uses_only_the_person_as_antecedent(self):
        source = "<Subject 1>が<Subject 3>と<Subject 4>を持つ。"
        self.assertEqual(bound_pronoun_owner(source, "<Subject 1>", subject_roles=ROLES), "<Subject 1>")
        references = ActorReferences(ACTORS)
        references.validate_translation("<Subject 1> holds <Subject 3> and <Subject 4> in their hands.", "/",
                                        explicit_people=True, pronoun_owner="<Subject 1>", subject_roles=ROLES)
        self.assertIsNone(bound_pronoun_owner("<Subject 3> lies still.", "<Subject 3>", subject_roles=ROLES))
        self.assertIsNone(bound_pronoun_owner("<Subject 1> watches <Subject 2> hold <Subject 3>.", "<Subject 1>", subject_roles=ROLES))
        for output in ("<Subject 1> watches <Subject 2> lower their gaze.", "<Subject 3> rests in their hands."):
            with self.assertRaisesRegex(VCError, "AMBIGUOUS_ACTOR_REFERENCE"):
                references.validate_translation(output, "/", explicit_people=True, pronoun_owner="<Subject 3>", subject_roles=ROLES)
        with self.assertRaisesRegex(VCError, "TRANSLATION_ROLES"):
            bound_pronoun_owner("<Subject 5> sits.", subject_roles=ROLES)

    def test_slot_assembly_preserves_arbitrary_order_and_occurrences(self):
        value = dict(prefix="", slots=[dict(slot=1, position=2, after="."), dict(slot=2, position=1, after=" walks toward ")])
        self.assertEqual(restore_slots(json.dumps(value), MARKERS), MARKERS[1] + " walks toward " + MARKERS[0] + ".")
        self.assertLess(len(translation_grammar(50)), 10000)  # No factorial/subset grammar growth.

    def test_extra_duplicate_missing_slots_and_text_marker_injection_fail_closed(self):
        good = dict(prefix="", slots=[dict(slot=1, position=1, after=" holds "), dict(slot=2, position=2, after=".")])
        cases = []
        value = copy.deepcopy(good); value["slots"].append(dict(value["slots"][0])); cases.append(value)
        value = copy.deepcopy(good); value["slots"].pop(); cases.append(value)
        value = copy.deepcopy(good); value["slots"][1]["slot"] = 1; cases.append(value)
        value = copy.deepcopy(good); value["slots"][1]["position"] = 1; cases.append(value)
        for injection in (MARKERS[0], "__MVD_KEEP_9999__", "__mvd_keep_0001__", "<Subject 1>", "\nextra"):
            value = copy.deepcopy(good); value["prefix"] = injection; cases.append(value)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(CompilerError):
                restore_slots(json.dumps(value), MARKERS)
        with self.assertRaisesRegex(CompilerError, "duplicate key"):
            restore_slots('{"prefix":"","prefix":"","slots":[]}', MARKERS)
        escaped = json.dumps(good).replace('"prefix": ""', '"prefix": "\\u005f\\u005fMVD_KEEP_0001__"')
        with self.assertRaisesRegex(CompilerError, "text contains"):
            restore_slots(escaped, MARKERS)

    def test_native_transport_repairs_bad_order_without_duplicating_a_marker(self):
        bad = dict(prefix="", slots=[dict(slot=1, position=1, after=" holds "), dict(slot=2, position=1, after=".")])
        good = dict(prefix="", slots=[dict(slot=1, position=1, after=" holds "), dict(slot=2, position=2, after=" in their hands.")])
        roles = {"<Subject 1>": "actor", "<Subject 2>": "prop"}
        with temporary_directory() as directory:
            backend, state = translation_tests.TranslationSemanticsTests.backend(directory, [record(bad), record(good)])
            result = GemmaTranslator(backend).translate_actor_protected(("", "が", "を持つ。"),
                ("<Subject 1>", "<Subject 2>"), ACTORS, explicit_people=True,
                owner_subject="<Subject 1>", allow_bound_pronouns=True, subject_roles=roles)
            self.assertEqual(result, "<Subject 1> holds <Subject 2> in their hands.")
            retry = json.loads(state["requests"][1][1]["content"].removeprefix("/no_think\n"))
            self.assertIn("occurrence-slot order mismatch", retry["retry_reason"])
            self.assertNotIn("marker_counts", retry["repair_context"])
            self.assertEqual(retry["repair_context"]["expected_slot_ids"], [1, 2])
            self.assertEqual(retry["field_context"]["subject_roles"], roles)
            self.assertEqual(state["grammars"], [translation_grammar(2)] * 2)

    def test_role_assignment_separates_success_caches(self):
        source = ("", "が", "を持つ。")
        values = ("<Subject 1>", "<Subject 2>")
        with temporary_directory() as directory:
            backend, state = translation_tests.TranslationSemanticsTests.backend(directory, [
                "TRANSLATION\t1\t__MVD_KEEP_0001__ holds __MVD_KEEP_0002__ in their hands.",
                "TRANSLATION\t1\t__MVD_KEEP_0001__ holds __MVD_KEEP_0002__."])
            translator = GemmaTranslator(backend, Path(directory) / "cache")
            for roles in ({"<Subject 1>": "actor", "<Subject 2>": "prop"},
                          {"<Subject 1>": "actor", "<Subject 2>": "actor"}):
                translator.translate_actor_protected(source, values, ACTORS, explicit_people=True,
                    owner_subject="<Subject 1>", allow_bound_pronouns=True, subject_roles=roles)
            self.assertEqual(len(state["requests"]), 2)

    def test_compiler_role_mapping_includes_props_and_replays_old_and_new_revisions(self):
        document = prop_document()
        # The fixture's object references need no file assets for this test.
        for prop in document["props"]:
            prop.update(image_asset_id=None, reference_hint=None)
        result = compile_fixture(document)
        roles = result.metadata["translation_trace"][0]["subject_roles"]
        self.assertEqual(set(roles.values()), {"actor", "prop"})
        self.assertEqual(result.metadata["prompt_policy_version"], "vcd-audio-v10")
        old = compile_document(document, render_scene(document), prompt_policy=REFERENCE_PROMPT_POLICY)
        self.assertTrue(all("subject_roles" not in row for row in old.metadata["translation_trace"]))
        for compiled in (result, old):
            with temporary_directory() as directory:
                revision = save(compiled, directory)
                self.assertEqual(load(revision.parents[1]).plan, compiled.plan)

    def test_compiler_with_two_actors_and_props_translates_personal_anaphora_and_replays(self):
        document, assets = story()
        document["props"] = prop_document()["props"]
        for prop in document["props"]:
            prop.update(image_asset_id=None, reference_hint=None)
        prop_ids = [prop["id"] for prop in document["props"]]
        document["beats"][0]["prop_ids"] = prop_ids[:]
        scene = document["scenes"][0]
        scene.update(prop_ids=prop_ids[:], visible_prop_ids=prop_ids[:])
        action = next(event for shot in scene["shots"] for event in shot["events"] if event["kind"] == "action")
        action["text"] = "@heroが@notebookと@pencilを両手に持つ。"
        with temporary_directory() as directory:
            backend, state = translation_tests.TranslationSemanticsTests.backend(directory, [
                "TRANSLATION\t1\t__MVD_KEEP_0001__ holds __MVD_KEEP_0002__ and __MVD_KEEP_0003__ in their hands."])
            result = compile_fixture(document, assets, GemmaTranslator(backend))
            self.assertIn("<Subject 1> holds <Subject 3> and <Subject 4> in their hands.", result.plan["shots"][0]["prompt"])
            self.assertEqual(len(state["requests"]), 1)
            context = json.loads(state["requests"][0][1]["content"].removeprefix("/no_think\n"))["field_context"]
            self.assertEqual(context["pronoun_owner"], "<Subject 1>")
            self.assertEqual(context["subject_roles"]["<Subject 3>"], "prop")
            revision = save(result, directory)
            self.assertEqual(load(revision.parents[1]).plan, result.plan)

    def test_japanese_wrapping_preserves_specific_reason_counts_and_diagnostic_path(self):
        cases = [("whole-field marker coverage mismatch: __MVD_KEEP_0002__: expected=1, got=2", "必要数1、出力数2"),
                 ("AMBIGUOUS_ACTOR_REFERENCE translation: Unbound person pronoun 'their'.", "代名詞「their」"),
                 ("occurrence-slot order mismatch: positions must be a permutation", "読み順が重複"),
                 ("occurrence-slot text contains a protected marker", "通常の英訳テキスト"),
                 ("unexpected diagnostic information", "unexpected diagnostic information")]
        for reason, expected in cases:
            original = "whole-field translation invalid after 2 attempts: " + reason
            error = VCError("TRANSLATION_RETRIES_EXHAUSTED", "translation", original + "; 応答ログ: trace.json")
            formatted = contextual_error(error, "# 形式\n")
            self.assertIn(expected, str(formatted))
            self.assertIn(reason, str(formatted))
            self.assertIn("trace.json", str(formatted))


if __name__ == "__main__":
    unittest.main()
