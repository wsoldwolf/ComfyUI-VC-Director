import copy
import json
import unittest
from pathlib import Path

from test_core import compile_fixture, temporary_directory
from test_scene_roles import story
import test_translation_semantics as translation_tests
from vc_director.core.artifacts.actor_references import ActorReferences, bound_pronoun_owner
from vc_director.core.artifacts.storage import save, load
from vc_director.core.compiler.api import compile_document, IDENTITY_PROMPT_POLICY
from vc_director.core.emd import render_scene
from vc_director.core.errors import VCError
from vc_director.core.inference import GemmaTranslator


SOURCE = "静かに視線を落とし、独りきりでベンチに座る姿が画面に収まる。"
TRANSLATION = "The screen captures the figure of a person sitting alone on a bench, quietly casting their gaze downward."
ACTORS = [dict(id="maho", name="真帆"), dict(id="risa", name="理沙")]


class TranslationOwnershipTests(unittest.TestCase):
    def test_reported_translation_is_valid_for_structurally_bound_action_owner(self):
        with temporary_directory() as directory:
            backend, state = translation_tests.TranslationSemanticsTests.backend(directory,
                ["TRANSLATION\t1\t" + TRANSLATION])
            result = GemmaTranslator(backend).translate_actor_protected((SOURCE,), (), ACTORS,
                explicit_people=True, owner_subject="<Subject 1>", allow_bound_pronouns=True)
            self.assertEqual(result, TRANSLATION)
            self.assertEqual(len(state["requests"]), 1)
            request = json.loads(state["requests"][0][1]["content"].removeprefix("/no_think\n"))
            self.assertEqual(request["immutable_spans"], [])
            self.assertEqual(request["field_context"]["field_owner_subject"], "<Subject 1>")
            self.assertEqual(request["field_context"]["pronoun_owner"], "<Subject 1>")
            self.assertNotIn("<Subject", result)

    def test_unbound_their_is_repaired_without_inventing_a_marker(self):
        with temporary_directory() as directory:
            backend, state = translation_tests.TranslationSemanticsTests.backend(directory, [
                "TRANSLATION\t1\t" + TRANSLATION,
                "TRANSLATION\t1\tThe figure on the bench quietly lowers a gaze."])
            result = GemmaTranslator(backend).translate_actor_protected((SOURCE,), (), ACTORS,
                explicit_people=True, allow_bound_pronouns=True)
            self.assertNotIn("their", result)
            retry = json.loads(state["requests"][1][1]["content"].removeprefix("/no_think\n"))
            self.assertIn("Do not create a Subject", retry["retry_reason"])
            self.assertEqual(retry["immutable_spans"], [])

    def test_multiple_subjects_do_not_license_an_ambiguous_possessive(self):
        with temporary_directory() as directory:
            backend, state = translation_tests.TranslationSemanticsTests.backend(directory, [
                "TRANSLATION\t1\t__MVD_KEEP_0001__ watches __MVD_KEEP_0002__ lower their gaze.",
                "TRANSLATION\t1\t__MVD_KEEP_0001__ watches __MVD_KEEP_0002__ lower a gaze."])
            result = GemmaTranslator(backend).translate_actor_protected(("", "が", "を見つめる。"),
                ("<Subject 1>", "<Subject 2>"), ACTORS, explicit_people=True,
                owner_subject="<Subject 1>", allow_bound_pronouns=True)
            self.assertEqual(len(state["requests"]), 2)
            self.assertNotIn("their", result)
            request = json.loads(state["requests"][0][1]["content"].removeprefix("/no_think\n"))
            self.assertIsNone(request["field_context"]["pronoun_owner"])

    def test_owner_and_explicit_reference_must_agree(self):
        self.assertIsNone(bound_pronoun_owner("<Subject 2> turns.", "<Subject 1>"))
        self.assertEqual(bound_pronoun_owner("<Subject 1> turns.", "<Subject 1>"), "<Subject 1>")
        references = ActorReferences(ACTORS)
        for text in ("<Subject 1> watches <Subject 2> lower their gaze.", "<Subject 1> watches <Subject 2> while she sits."):
            with self.assertRaisesRegex(VCError, "AMBIGUOUS_ACTOR_REFERENCE"):
                references.validate_translation(text, "/", explicit_people=True, pronoun_owner="<Subject 1>")

    def test_unambiguous_english_anaphora_uses_the_same_bound_owner(self):
        references = ActorReferences(ACTORS)
        for text in ("<Subject 1> lowers their gaze.", "<Subject 1> sits and the camera captures them.",
                     "<Subject 1> sits while they lower a gaze.", "<Subject 1> rests her hands on a bench."):
            references.validate_translation(text, "/", explicit_people=True, pronoun_owner="<Subject 1>")
            with self.assertRaisesRegex(VCError, "AMBIGUOUS_ACTOR_REFERENCE"):
                references.validate_translation(text, "/", explicit_people=True)

    def test_compiler_passes_action_owner_and_revision_replays_exactly(self):
        doc, assets = story()
        action = next(e for sh in doc["scenes"][0]["shots"] for e in sh["events"] if e["kind"] == "action")
        action["text"] = SOURCE
        original = copy.deepcopy(doc)
        with temporary_directory() as directory:
            backend, state = translation_tests.TranslationSemanticsTests.backend(directory, ["TRANSLATION\t1\t" + TRANSLATION])
            result = compile_fixture(doc, assets, GemmaTranslator(backend))
            prompt = result.plan["shots"][0]["prompt"]
            self.assertIn("ACTION <Subject 1>: " + TRANSLATION, prompt)
            self.assertIn("<Subject 2> speaks", prompt)
            self.assertEqual(doc, original)
            trace = next(t for t in result.metadata["translation_trace"] if t["source"] == SOURCE)
            self.assertEqual(trace["owner_subject"], "<Subject 1>")
            self.assertTrue(trace["explicit_people"])
            revision = save(result, directory)
            self.assertEqual(load(revision.parents[1]).plan, result.plan)

    def test_identical_text_with_different_owners_has_separate_caches_and_replay(self):
        doc, assets = story()
        scene = doc["scenes"][0]
        scene["visible_actor_ids"] = list(scene["actor_ids"])
        action = next(e for sh in scene["shots"] for e in sh["events"] if e["kind"] == "action")
        action["text"] = "静かに視線を落とす。"
        second = dict(action, id=action["id"] + "_caller", actor_id="caller")
        scene["shots"][0]["events"].append(second)
        with temporary_directory() as directory:
            backend, state = translation_tests.TranslationSemanticsTests.backend(directory, [
                "TRANSLATION\t1\tQuietly lowers their gaze.",
                "TRANSLATION\t1\tTheir gaze quietly shifts downward."])
            result = compile_fixture(doc, assets, GemmaTranslator(backend, Path(directory) / "cache"))
            traces = [t for t in result.metadata["translation_trace"] if t["source"] == action["text"]]
            self.assertEqual([t["owner_subject"] for t in traces], ["<Subject 1>", "<Subject 2>"])
            self.assertEqual(len(state["requests"]), 2)
            revision = save(result, directory)
            self.assertEqual(load(revision.parents[1]).plan, result.plan)

    def test_v7_saved_policy_retains_its_translation_trace_and_replay(self):
        doc, assets = story()
        old = compile_document(doc, render_scene(doc), assets, prompt_policy=IDENTITY_PROMPT_POLICY)
        self.assertTrue(all("owner_subject" not in t for t in old.metadata["translation_trace"]))
        with temporary_directory() as directory:
            revision = save(old, directory)
            self.assertEqual(load(revision.parents[1]).plan, old.plan)


if __name__ == "__main__":
    unittest.main()
