import copy
import unittest
from unittest.mock import patch

from test_core import english_document, compile_fixture, temporary_directory
from vc_director.core.compiler.api import compile_document, VISUAL_PROMPT_POLICY, OWNERSHIP_PROMPT_POLICY
from vc_director.core.artifacts.storage import save, load
from vc_director.core.artifacts.validation import validate_story
from vc_director.core.emd import parse, render_scene
from vc_director.core.errors import VCError
from vc_director.core.h3_contract.runtime import conditioning_inputs
from vc_director.core.utilities import canonical, digest
from vc_director.core.vision.image_facts import apply_scene_image_facts


def asset(id, kind):
    return dict(id=id, kind=kind, locator=id + ".dat", sha256="a" * 64,
                sample_rate=32000 if kind == "audio" else None, sample_count=32000 if kind == "audio" else None,
                fps=24 if kind == "video" else None, frame_count=24 if kind == "video" else None, paired_audio_enabled=kind == "video")


def story(speaking=True):
    doc = english_document()
    friend = copy.deepcopy(doc["actors"][0])
    friend.update(id="caller", name="Caller", description="A friend available over a telephone.",
                  image_asset_id="caller_image", voice_asset_id="caller_voice")
    doc["actors"].append(friend)
    doc["actors"][0].update(image_asset_id="hero_image", voice_asset_id="hero_voice")
    doc["beats"][0]["actor_ids"].append("caller")
    scene = doc["scenes"][0]
    scene.update(actor_ids=["hero", "caller"], visible_actor_ids=["hero"],
                 visual_directions=["Frame only @hero with a static medium shot."])
    scene["subject_descriptions"].append(dict(actor_id="caller", text="An invisible friend with a pink dress."))
    scene["retention"].append(dict(actor_id="caller", mode="fully_preserved"))
    if speaking:
        doc["beats"][0]["dialogues"][0]["actor_id"] = "caller"
        next(e for sh in scene["shots"] for e in sh["events"] if e["kind"] == "dialogue")["actor_id"] = "caller"
    else:
        doc["beats"][0]["dialogues"] = []
        scene["shots"][0]["events"] = [e for e in scene["shots"][0]["events"] if e["kind"] != "dialogue"]
        for policy in (doc["audio"], doc["beats"][0]["audio"], scene["audio"]):
            policy["vocal"] = "none"
    assets = [asset("hero_image", "image"), asset("caller_image", "image"),
              asset("hero_voice", "audio"), asset("caller_voice", "audio")]
    return doc, assets


class SceneRoleTests(unittest.TestCase):
    def test_invisible_non_speaker_has_no_definition_or_reference(self):
        doc, assets = story(False)
        result = compile_fixture(doc, assets)
        prompt = result.plan["shots"][0]["prompt"]
        self.assertIn("A traveler wearing a red coat.", prompt)
        self.assertNotIn("pink dress", prompt)
        self.assertNotIn("<Subject 2>", prompt)
        self.assertNotIn("<Audio", prompt)
        self.assertEqual([b["asset_id"] for b in result.execution["segments"][0]["reference_bindings"]], ["hero_image"])
        self.assertEqual(result.execution["segments"][0]["local_subjects"], [dict(actor_id="hero", subject_number=1)])
        self.assertEqual(parse(result.scene_emd), doc)
        with temporary_directory() as directory:
            revision = save(result, directory)
            self.assertEqual(load(revision.parents[1]).execution, result.execution)

    def test_v8_offscreen_speaker_gets_voice_but_no_identity_text(self):
        doc, assets = story()
        result = compile_document(doc, render_scene(doc), assets, prompt_policy=OWNERSHIP_PROMPT_POLICY)
        prompt = result.plan["shots"][0]["prompt"]
        self.assertIn("<Subject 2>: An off-screen speaker. Voice timbre reference: <Audio 1>.", prompt)
        self.assertIn("<Subject 2> speaks <d>[Japanese]", prompt)
        self.assertNotIn("pink dress", prompt)
        self.assertNotIn("<Picture 2>", prompt)
        self.assertNotIn("<Subject 2>: fully_preserved", prompt)
        self.assertEqual([b["asset_id"] for b in result.execution["segments"][0]["reference_bindings"]], ["hero_image", "caller_voice"])

    def test_numbering_is_rebuilt_when_no_visible_subject_exists(self):
        doc, assets = story()
        scene = doc["scenes"][0]
        scene.update(visible_actor_ids=[], summary="An empty park.", visual_directions=[])
        for e in scene["shots"][0]["events"]:
            if e["kind"] != "dialogue": e.update(actor_id="environment", text="The park remains still.")
        result = compile_fixture(doc, assets)
        self.assertIn("<Subject 1> speaks", result.plan["shots"][0]["prompt"])
        self.assertEqual(result.execution["segments"][0]["local_subjects"], [dict(actor_id="caller", subject_number=1)])

    def test_hidden_visual_actions_are_rejected(self):
        doc, _ = story()
        scene = doc["scenes"][0]
        scene["shots"][0]["events"][0]["actor_id"] = "caller"
        with self.assertRaisesRegex(VCError, "VISUAL_EVENT_ACTOR"):
            validate_story(doc)
        scene["shots"][0]["events"][0]["actor_id"] = "hero"
        scene["visible_actor_ids"] = ["unknown"]
        with self.assertRaisesRegex(VCError, "VISIBLE_ACTOR"):
            validate_story(doc)

    def test_generated_voice_role_does_not_leak_through_visual_directions(self):
        for text in ("Risa is voice-only.", "An audible narrator is outside the image.", "理沙は声のみ。"):
            doc, _ = story(False)
            doc["scenes"][0]["visual_directions"] = [text]
            with self.assertRaisesRegex(VCError, "VISUAL_AUDIO_ROLE"):
                validate_story(doc)

    def test_muted_video_audio_does_not_reenter_native_conditioning(self):
        doc, assets = story(False)
        doc["actors"][0]["video_asset_id"] = "motion"
        assets.append(asset("motion", "video"))
        result = compile_fixture(doc, assets)
        self.assertNotIn("video_audio", [b["role"] for b in result.execution["segments"][0]["reference_bindings"]])
        registry = dict(root=".", assets=assets, fingerprint=digest(canonical(assets)))
        with patch("vc_director.core.h3_contract.runtime.verify_runtime"), patch("vc_director.core.h3_contract.runtime.verify_assets"):
            active = conditioning_inputs(result, registry, 1, result.plan["shots"][0]["prompt"], result.plan["shots"][0]["length"], "unused")
        self.assertFalse(next(a for a in active if a["id"] == "motion")["paired_audio_enabled"])
        self.assertTrue(assets[-1]["paired_audio_enabled"])

    def test_v5_replay_and_visual_fact_merge_keep_their_boundaries(self):
        doc, assets = story(False)
        old = compile_document(doc, render_scene(doc), assets, prompt_policy=VISUAL_PROMPT_POLICY)
        self.assertIn("pink dress", old.plan["shots"][0]["prompt"])
        with temporary_directory() as directory:
            revision = save(old, directory)
            self.assertEqual(load(revision.parents[1]).plan, old.plan)
        doc["actors"][0]["description"] = "Only heard through a radio."
        merged = apply_scene_image_facts(doc["scenes"][0], doc, [dict(entity_kind="actor", entity_id="hero", text="Brown hair.")])
        self.assertIn("Brown hair.", merged["subject_descriptions"][0]["text"])
        self.assertNotIn("radio", merged["subject_descriptions"][0]["text"])

    def test_generated_visibility_is_required_subset_and_empty_is_allowed(self):
        from jsonschema import Draft202012Validator
        from test_planning_transport import PlanningTransportTests
        from vc_director.core.protocols.llm import generation_schema
        payload, scene = PlanningTransportTests.silent_detail()
        validator = Draft202012Validator(generation_schema("detail", payload))
        self.assertTrue(validator.is_valid(scene))
        for value in (["unknown"], ["hero", "hero"]):
            invalid = copy.deepcopy(scene)
            invalid["visible_actor_ids"] = value
            self.assertFalse(validator.is_valid(invalid))
        no_visible = copy.deepcopy(scene)
        no_visible["visible_actor_ids"] = []
        self.assertTrue(validator.is_valid(no_visible))
        del no_visible["visible_actor_ids"]
        self.assertFalse(validator.is_valid(no_visible))
