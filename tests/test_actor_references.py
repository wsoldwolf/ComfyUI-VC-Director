import copy
import unittest

from test_core import FakeBackend, compile_fixture, fixture, temporary_directory, with_camera
from test_scene_roles import story
import test_translation_semantics as translation_tests
from vc_director.core.artifacts.actor_references import ActorReferences, bind_scene_actor_references
from vc_director.core.artifacts.storage import load, save
from vc_director.core.compiler.api import compile_document, ROLE_PROMPT_POLICY
from vc_director.core.emd import render_scene
from vc_director.core.errors import VCError
from vc_director.core.inference import GemmaTranslator
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.protocols import StageRunner
from vc_director.core.utilities import canonical


def named_story():
    doc, assets = story()
    doc["actors"][0]["name"] = "真帆"
    doc["actors"][1]["name"] = "理沙"
    return doc, assets


class ActorReferenceTests(unittest.TestCase):
    def test_names_ids_and_existing_symbols_bind_to_the_same_person(self):
        actors = [dict(id="maho", name="真帆"), dict(id="risa", name="理沙")]
        references = ActorReferences(actors)
        self.assertEqual(references.bind("真帆が理沙を見た。Maho faces Risa. @maho <Subject 1>", "/"),
                         "@mahoが@risaを見た。@maho faces @risa. @maho <Subject 1>")
        self.assertEqual(references.bind("Maho's hand; Maharisa; risotto.", "/"),
                         "@maho's hand; Maharisa; risotto.")
        self.assertEqual(references.bind("彼方の空。", "/"), "彼方の空。")

    def test_duplicate_display_names_require_explicit_id(self):
        references = ActorReferences([dict(id="a", name="同名"), dict(id="b", name="同名")])
        with self.assertRaisesRegex(VCError, "AMBIGUOUS_ACTOR_NAME"):
            references.bind("同名が立つ。", "/")
        self.assertEqual(references.bind("@a stands beside @b.", "/"), "@a stands beside @b.")

    def test_generated_fields_bind_without_touching_authored_speech_or_owners(self):
        doc, _ = named_story()
        scene = doc["scenes"][0]
        scene.update(summary="真帆が端末を見る。", visual_directions=["Frame 真帆 only."])
        action = next(e for sh in scene["shots"] for e in sh["events"] if e["kind"] == "action")
        action["text"] = "真帆が端末を持つ。"
        dialogue = next(e for sh in scene["shots"] for e in sh["events"] if e["kind"] == "dialogue")
        dialogue["text"] = "「真帆、彼女を見た？ @hero」"
        before = copy.deepcopy(dialogue)
        bind_scene_actor_references(scene, doc["actors"])
        self.assertEqual(scene["summary"], "@heroが端末を見る。")
        self.assertEqual(action["text"], "@heroが端末を持つ。")
        self.assertEqual(scene["visual_directions"], ["Frame @hero only."])
        self.assertEqual(dialogue, before)

    def test_ambiguous_source_is_not_guessed_or_sent_to_translation(self):
        doc, assets = named_story()
        doc["scenes"][0]["summary"] = "真帆が端末を操作し、彼女がベンチに座る構図になる。"
        calls = []
        def translator(source):
            calls.append(source)
            return source
        with self.assertRaisesRegex(VCError, "AMBIGUOUS_ACTOR_REFERENCE"):
            compile_fixture(doc, assets, translator)
        self.assertFalse(any("ベンチ" in source for source in calls))

    def test_hidden_name_in_visual_prose_is_bound_and_rejected(self):
        doc, assets = named_story()
        doc["scenes"][0]["summary"] = "Caller sits on a bench."
        with self.assertRaisesRegex(VCError, "INACTIVE_PROMPT_ACTOR"):
            compile_fixture(doc, assets)

    def test_compiled_summary_directions_camera_and_action_share_subject(self):
        doc, assets = named_story()
        scene = with_camera(doc["scenes"][0])
        doc["scenes"][0] = scene
        scene.update(summary="Hero sits on a bench.", visual_directions=["Frame Hero only."])
        for shot in scene["shots"]:
            for event in shot["events"]:
                if event["kind"] != "dialogue":
                    event["text"] = "Frame Hero on a bench." if event["kind"] == "camera" else "Hero holds a phone."
        result = compile_fixture(doc, assets)
        prompt = result.plan["shots"][0]["prompt"]
        for line in ("[reference generation] <Subject 1> sits on a bench.",
                     "Frame <Subject 1> only.", "Frame <Subject 1> on a bench.", "<Subject 1> holds a phone."):
            self.assertIn(line, prompt)
        self.assertIn("<Subject 2> speaks", prompt)
        self.assertNotIn("Hero", prompt)
        with temporary_directory() as directory:
            revision = save(result, directory)
            self.assertEqual(load(revision.parents[1]).plan, result.plan)

    def test_translation_repairs_invented_name_and_preserves_marker_ownership(self):
        doc, assets = named_story()
        doc["scenes"][0]["summary"] = "真帆が端末を操作し、真帆がベンチに一人で座る構図になる。"
        with temporary_directory() as directory:
            backend, state = translation_tests.TranslationSemanticsTests.backend(directory, [
                "TRANSLATION\t1\t__MVD_KEEP_0001__ operates a phone; Caller sits on a bench beside __MVD_KEEP_0002__.",
                "TRANSLATION\t1\t__MVD_KEEP_0001__ operates a phone; the composition shows __MVD_KEEP_0002__ sitting alone on a bench."])
            translator = GemmaTranslator(backend, directory)
            result = compile_fixture(doc, assets, translator)
            prompt = result.plan["shots"][0]["prompt"]
            self.assertIn("the composition shows <Subject 1> sitting alone on a bench", prompt)
            self.assertNotIn("Caller", prompt)
            self.assertEqual(len(state["requests"]), 2)
            self.assertIn("TRANSLATION_ACTOR_NAME", state["requests"][1][1]["content"])
            self.assertEqual(result.execution["segments"][0]["local_subjects"],
                             [dict(actor_id="hero", subject_number=1), dict(actor_id="caller", subject_number=2)])

    def test_translation_pronoun_failure_uses_existing_retry_and_cache_checks(self):
        actors = [dict(id="maho", name="真帆"), dict(id="risa", name="理沙")]
        with temporary_directory() as directory:
            backend, state = translation_tests.TranslationSemanticsTests.backend(directory, [
                "TRANSLATION\t1\t__MVD_KEEP_0001__ holds a phone and she sits down.",
                "TRANSLATION\t1\t__MVD_KEEP_0001__ holds a phone and sits down."])
            translator = GemmaTranslator(backend, directory)
            for _ in range(2):
                self.assertEqual(translator.translate_actor_protected(("", "が端末を持って座る。"), ("<Subject 1>",), actors, explicit_people=True),
                                 "<Subject 1> holds a phone and sits down.")
            self.assertEqual(len(state["requests"]), 2)
            self.assertIn("AMBIGUOUS_ACTOR_REFERENCE", state["requests"][1][1]["content"])
            self.assertEqual(translator.trace[-1]["cache"], "hit")

    def test_planner_repairs_ambiguous_summary_without_changing_dialogue(self):
        doc, _ = named_story()
        scene = with_camera(doc["scenes"][0])
        scene["summary"] = "@heroが端末を操作し、@heroがベンチに一人で座る。"
        bad = copy.deepcopy(scene)
        bad["summary"] = "真帆が端末を操作し、彼女がベンチに座る。"
        scenario = {k: copy.deepcopy(v) for k, v in doc.items()
                    if k not in {"schema", "scenario_sha256", "scenes", "required_events"}}
        scenario.update(schema="VCD_SCENARIO_V1", source_sha256=doc["scenario_sha256"])
        enhanced = fixture("enhanced-story")
        enhanced["scenario_sha256"] = scenario["source_sha256"]
        outline = fixture("outline-response")["records"][0]["payload"]
        outline["scenes"][0]["actor_ids"] = scene["actor_ids"]
        backend = FakeBackend(["SCENES\t1\t" + canonical(outline), "DETAIL\t1\t" + canonical(bad),
                               "DETAIL\t1\t" + canonical(dict(text=scene["summary"]))])
        result = StoryTimelinePlanner(StageRunner(backend)).plan(scenario, enhanced)
        self.assertEqual(result["scenes"][0]["summary"], scene["summary"])
        self.assertEqual(backend.requests[2]["task"], "repair_prose_actor_references")
        self.assertEqual(backend.requests[2]["validation_error"]["code"], "AMBIGUOUS_ACTOR_REFERENCE")
        old_dialogue = [e for sh in scene["shots"] for e in sh["events"] if e["kind"] == "dialogue"]
        self.assertEqual([e for sh in result["scenes"][0]["shots"] for e in sh["events"] if e["kind"] == "dialogue"], old_dialogue)

    def test_v6_saved_revision_replays_unchanged(self):
        doc, assets = named_story()
        doc["scenes"][0]["summary"] = "Hero watches Caller; she sits on a bench."
        old = compile_document(doc, render_scene(doc), assets, prompt_policy=ROLE_PROMPT_POLICY)
        self.assertIn("Hero watches Caller; she sits", old.plan["shots"][0]["prompt"])
        with temporary_directory() as directory:
            revision = save(old, directory)
            self.assertEqual(load(revision.parents[1]).plan, old.plan)


if __name__ == "__main__":
    unittest.main()
