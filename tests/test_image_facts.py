import copy
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_core import ROOT, FakeBackend, english_document, fixture
from vc_director.core.emd import parse
from vc_director.core.vision.image_facts import scenario_image_facts, apply_image_facts, apply_scene_image_facts
from vc_director.core.errors import VCError
from vc_director.core.inference import runtime_config
from vc_director.core.artifacts.validation import validate_story
from vc_director.core.artifacts.schema import validate
from vc_director.core.direction import StoryEnhancer
from vc_director.core.protocols import StageRunner


class ImageFactTests(unittest.TestCase):
    def setUp(self):
        self.scenario = parse((ROOT / "assets/emd/minimal.scenario.emd").read_text(encoding="utf-8"))
        self.scenario["actors"][0]["image_asset_id"] = "hero_image"
        self.scenario["backgrounds"][0]["image_asset_id"] = "station_image"
        self.registry = dict(root=str(ROOT), assets=[dict(id=id, kind="image", locator=id + ".png")
                                                    for id in ("hero_image", "station_image")])

    @patch("vc_director.core.vision.image_facts.verify_assets")
    def test_entities_select_profiles_and_output_fact_schema(self, verify):
        observer = Mock(side_effect=[("* 特徴: 赤いコート。\n", {}, None, "{}"),
                                    ("* 情景: 駅舎と時計。\n", {}, None, "{}")])
        loader = Mock(return_value="pixels")
        config = runtime_config("vision", n_ctx=8192)
        facts = scenario_image_facts(self.scenario, self.registry, "pair", Path("cache"),
                                     observer=observer, image_loader=loader, config=config)
        self.assertEqual(facts, [dict(entity_kind="actor", entity_id="hero", text="赤いコート。"),
                                 dict(entity_kind="background", entity_id="station", text="駅舎と時計。")])
        self.assertEqual([call.kwargs["analysis_profile"] for call in observer.call_args_list],
                         ["subject_only", "scene_only"])
        self.assertTrue(all(call.kwargs["hint_mode"] == "observe_only" for call in observer.call_args_list))
        self.assertEqual(verify.call_count, 2)
        self.assertTrue(all(call.kwargs["config"] is config for call in observer.call_args_list))

    @patch("vc_director.core.vision.image_facts.verify_assets")
    def test_manual_override_and_no_image_do_not_run_vision(self, verify):
        self.scenario["backgrounds"][0]["image_asset_id"] = None
        manual = [dict(entity_kind="actor", entity_id="hero", text="手入力の特徴。")]
        observer, loader = Mock(), Mock()
        self.assertEqual(scenario_image_facts(self.scenario, self.registry, None, None, manual,
                                            observer=observer, image_loader=loader), manual)
        observer.assert_not_called()
        loader.assert_not_called()

    def test_missing_image_fails_before_inference(self):
        observer = Mock()
        with self.assertRaisesRegex(VCError, "IMAGE_FACT_ASSET"):
            scenario_image_facts(self.scenario, dict(root=".", assets=[]), None, None, observer=observer)
        observer.assert_not_called()

    def test_missing_features_and_background_are_allowed_only_with_reference_images(self):
        emd = (ROOT / "assets/emd/minimal.scenario.emd").read_text(encoding="utf-8")
        missing = emd.replace("* 特徴: 赤いコートを着た旅人。\n", "").replace("* 情景: 夕暮れの駅前。\n", "")
        with self.assertRaisesRegex(VCError, "MISSING_FIELD.*特徴"):
            parse(missing)
        scenario = validate_story(parse(missing.replace("* 画像: なし", "* 画像: hero_image", 1)
                                            .replace("* 画像: なし", "* 画像: station_image", 1)))
        self.assertEqual(scenario["actors"][0]["description"], "")
        self.assertEqual(scenario["backgrounds"][0]["description"], "")
        facts = [dict(entity_kind="actor", entity_id="hero", text="Brown hair and a red coat."),
                 dict(entity_kind="background", entity_id="station", text="Stone buildings at dusk.")]
        resolved = validate_story(apply_image_facts(scenario, facts))
        self.assertEqual(resolved["actors"][0]["description"], facts[0]["text"])
        self.assertEqual(resolved["backgrounds"][0]["description"], facts[1]["text"])
        self.assertEqual(resolved["source_sha256"], scenario["source_sha256"])
        self.assertEqual(scenario["actors"][0]["description"], "")
        # Completed Scene artifacts continue to require explicit descriptions.
        document = english_document()
        document["actors"][0].update(description="", image_asset_id="hero_image")
        with self.assertRaisesRegex(VCError, "SCHEMA"):
            validate(document, "scene_document")

    def test_missing_vision_description_explains_fix_before_llm_call(self):
        scenario = copy.deepcopy(self.scenario)
        scenario["actors"][0]["description"] = ""
        backend = FakeBackend([])
        with self.assertRaisesRegex(VCError, "VISION_DESCRIPTION.*vision_model_name"):
            StoryEnhancer(StageRunner(backend)).enhance(scenario)
        self.assertEqual(backend.requests, [])

    def test_authored_features_are_retained_and_scene_subject_keeps_observations(self):
        original = copy.deepcopy(self.scenario)
        facts = [dict(entity_kind="actor", entity_id="hero", text="Long brown hair.")]
        resolved = apply_image_facts(self.scenario, facts)
        self.assertTrue(resolved["actors"][0]["description"].startswith(original["actors"][0]["description"]))
        self.assertIn("Long brown hair.", resolved["actors"][0]["description"])
        self.assertEqual(apply_image_facts(resolved, facts), resolved)
        self.assertEqual(self.scenario, original)
        scene = english_document()["scenes"][0]
        updated = apply_scene_image_facts(scene, resolved, facts)
        self.assertIn("Long brown hair.", updated["subject_descriptions"][0]["text"])
        self.assertEqual(updated["shots"], scene["shots"])
        self.assertEqual(updated["audio"], scene["audio"])
        self.assertEqual(apply_scene_image_facts(updated, resolved, facts), updated)
        with self.assertRaisesRegex(VCError, "IMAGE_FACT"):
            apply_image_facts(self.scenario, [dict(entity_kind="actor", entity_id="unknown", text="Hat.")])
