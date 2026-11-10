import copy
import unittest

from test_core import compile_fixture, temporary_directory
from test_scene_roles import story
from vc_director.core.audio.roles import referenced_actors
from vc_director.core.artifacts.storage import save, load
from vc_director.core.compiler.api import compile_document, OWNERSHIP_PROMPT_POLICY
from vc_director.core.emd import render_scene


class ReferencedActorDefinitionTests(unittest.TestCase):
    def test_referenced_offscreen_speaker_retains_identity_without_visual_reference(self):
        doc, assets = story()
        doc["scenes"][0]["subject_descriptions"][1]["text"] = "A woman in her thirties."
        original = copy.deepcopy(doc)
        result = compile_fixture(doc, assets)
        prompt = result.plan["shots"][0]["prompt"]
        self.assertIn("<Subject 2>: An off-screen speaker. A woman in her thirties. Voice timbre reference: <Audio 1>.", prompt)
        self.assertIn("<Subject 2> speaks", prompt)
        self.assertNotIn("<Picture 2>", prompt)
        self.assertNotIn("<Subject 2>: fully_preserved", prompt)
        self.assertEqual(doc, original)
        bindings = result.execution["segments"][0]["reference_bindings"]
        self.assertEqual([b["asset_id"] for b in bindings], ["hero_image", "caller_voice"])
        with temporary_directory() as directory:
            revision = save(result, directory)
            self.assertEqual(load(revision.parents[1]).plan, result.plan)

    def test_unreferenced_declarations_are_not_translated_or_conditioned(self):
        doc, assets = story(False)
        doc["scenes"][0]["subject_descriptions"][1]["text"] = "未参照人物の日本語説明は翻訳へ渡さない。"
        result = compile_fixture(doc, assets)  # No Japanese translator configured.
        self.assertEqual(referenced_actors(doc["scenes"][0]), {"hero"})
        prompt = result.plan["shots"][0]["prompt"]
        self.assertNotIn("<Subject 2>", prompt)
        self.assertNotIn("<Audio", prompt)
        self.assertNotIn("<Picture 2>", prompt)

    def test_an_actor_name_inside_authored_speech_does_not_create_a_character(self):
        doc, assets = story()
        text = "「@caller、どうだった？ @someone」"
        dialogue = doc["beats"][0]["dialogues"][0]
        dialogue.update(actor_id="hero", text=text)
        event = next(e for sh in doc["scenes"][0]["shots"] for e in sh["events"] if e["kind"] == "dialogue")
        event.update(actor_id="hero", text=text)
        result = compile_fixture(doc, assets)
        prompt = result.plan["shots"][0]["prompt"]
        self.assertEqual(referenced_actors(doc["scenes"][0]), {"hero"})
        self.assertNotIn("<Subject 2>", prompt)
        self.assertIn("<d>[Japanese]@caller、どうだった？ @someone</d>", prompt)
        self.assertEqual([b["asset_id"] for b in result.execution["segments"][0]["reference_bindings"]], ["hero_image", "hero_voice"])

    def test_explicit_non_speech_reference_defines_person_without_enabling_voice(self):
        doc, assets = story(False)
        for audio in (doc["audio"], doc["beats"][0]["audio"], doc["scenes"][0]["audio"]):
            audio["effects"] = "expand"
        scene = doc["scenes"][0]
        trigger = next(e for sh in scene["shots"] for e in sh["events"] if e["kind"] == "action")
        scene["sound_events"] = [dict(id="ring", kind="effect", start_ms=0, end_ms=1000,
            trigger_event_id=trigger["id"], text="The phone belonging to @caller rings.")]
        scene["subject_descriptions"][1]["text"] = "A woman in her thirties."
        result = compile_fixture(doc, assets)
        prompt = result.plan["shots"][0]["prompt"]
        self.assertIn("<Subject 2>: An off-screen person. A woman in her thirties.", prompt)
        self.assertIn("phone belonging to <Subject 2>", prompt)
        self.assertNotIn("<Audio", prompt)
        self.assertNotIn("speaks", prompt)
        self.assertEqual([b["asset_id"] for b in result.execution["segments"][0]["reference_bindings"]], ["hero_image"])

    def test_legacy_visibility_does_not_make_cast_declarations_usage(self):
        doc, assets = story(False)
        scene = doc["scenes"][0]
        del scene["visible_actor_ids"]
        scene["subject_descriptions"][1]["text"] = "未参照の人物。"
        result = compile_fixture(doc, assets)
        self.assertNotIn("<Subject 2>", result.plan["shots"][0]["prompt"])
        self.assertEqual(result.execution["segments"][0]["local_subjects"], [dict(actor_id="hero", subject_number=1)])

    def test_v8_saved_revision_still_replays_its_anonymous_offscreen_definition(self):
        doc, assets = story()
        doc["scenes"][0]["subject_descriptions"][1]["text"] = "A woman in her thirties."
        old = compile_document(doc, render_scene(doc), assets, prompt_policy=OWNERSHIP_PROMPT_POLICY)
        self.assertNotIn("A woman in her thirties.", old.plan["shots"][0]["prompt"])
        with temporary_directory() as directory:
            revision = save(old, directory)
            self.assertEqual(load(revision.parents[1]).plan, old.plan)


if __name__ == "__main__":
    unittest.main()
