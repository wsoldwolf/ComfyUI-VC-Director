import io
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_core import ROOT, FakeBackend, english_document, fixture, temporary_directory, with_camera
from vc_director.core.artifacts.storage import load
from vc_director.core.direction import StoryEnhancer
from vc_director.core.emd import parse
from vc_director.core.errors import VCError
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.protocols import StageRunner
from vc_director.core.utilities import canonical
from vc_director.nodes import VCDirectorBuild
from test_inference_settings import settings
from vc_director.tools.cli import main


class BuildInputTests(unittest.TestCase):
    def test_mismatched_mode_stops_before_backend_creation(self):
        cases = (("scenario", "minimal.scene.emd", "saved_scene"),
                 ("saved_scene", "minimal.scenario.emd", "scenario"))
        for mode, filename, suggestion in cases:
            with self.subTest(mode=mode):
                emd = (ROOT / "assets/emd" / filename).read_text(encoding="utf-8")
                with patch("vc_director.nodes.node_story_builder.node._backend") as backend:
                    with self.assertRaises(VCError) as caught:
                        VCDirectorBuild().build(mode, emd, {"assets": []}, "", "[]", "gemma.gguf", 1)
                    self.assertEqual(caught.exception.diagnostic.code, "INPUT_KIND")
                    self.assertIn(suggestion, caught.exception.diagnostic.message)
                    backend.assert_not_called()

    def test_planning_cores_reject_scene_document_before_request(self):
        backend = FakeBackend([])
        runner = StageRunner(backend)
        with self.assertRaisesRegex(VCError, "INPUT_KIND"):
            StoryEnhancer(runner).enhance(english_document())
        with self.assertRaisesRegex(VCError, "INPUT_KIND"):
            StoryTimelinePlanner(runner).plan(english_document(), fixture("enhanced-story"))
        self.assertEqual(backend.requests, [])
        self.assertIsNone(runner._identity)

    def test_cli_plan_explains_scene_input_without_creating_backend(self):
        error = io.StringIO()
        with patch("vc_director.tools.cli.GemmaBackend") as backend, patch("sys.stderr", error):
            code = main(["plan", str(ROOT / "assets/emd/minimal.scene.emd"), "--model", "missing.gguf"])
            self.assertEqual(code, 2)
            self.assertIn("INPUT_KIND", error.getvalue())
            self.assertIn("compile", error.getvalue())
            backend.assert_not_called()

    def test_scenario_emd_through_all_build_stages_to_saved_revision(self):
        self._build_with_vision()

    def test_missing_actor_features_are_filled_by_vision_and_saved_in_emd(self):
        self._build_with_vision(omit_features=True)

    def _build_with_vision(self, omit_features=False):
        # Exercise the public Build entry with real EMD parsing and request schemas.
        # Only model inference and prose translation are replaced with fixed responses.
        emd = (ROOT / "assets/emd/minimal.scenario.emd").read_text(encoding="utf-8")
        assets = []
        if omit_features:
            emd = emd.replace("* 特徴: 赤いコートを着た旅人。\n", "").replace("* 画像: なし", "* 画像: hero_image", 1)
            assets = [dict(id="hero_image", kind="image", locator="hero.png", sha256="a" * 64,
                           sample_rate=None, sample_count=None, fps=None, frame_count=None, paired_audio_enabled=False)]
        scenario = parse(emd)
        enhanced = fixture("enhanced-story")
        enhanced.update(project_id=scenario["project_id"], scenario_sha256=scenario["source_sha256"])
        enhanced["beats"][0]["required_events"][0]["source"] = scenario["beats"][0]["source"]
        outline = fixture("outline-response")["records"][0]["payload"]
        detail = english_document()["scenes"][0]
        detail["shots"][0]["events"][1]["text"] = scenario["beats"][0]["dialogues"][0]["text"]
        detail = with_camera(detail)
        planning = FakeBackend(["ENHANCED\t1\t" + canonical(enhanced),
                                "SCENES\t1\t" + canonical(outline),
                                "DETAIL\t1\t" + canonical(detail)])
        planning.close = Mock()
        translation = FakeBackend([])
        translation.close = Mock()
        translations = {"夕暮れの駅前。": "A station square at dusk.", "静かな短編映画。": "Quiet cinema."}
        facts = [dict(entity_kind="actor", entity_id="hero", text="A red coat and long brown hair.")]
        appearance = "\n".join(filter(None, [scenario["actors"][0]["description"], facts[0]["text"]]))
        local_description = appearance + "\n" + detail["subject_descriptions"][0]["text"]
        translations[local_description] = "A traveler with a red coat and long brown hair."
        inference = settings(planning_n_ctx=32768, compiler_n_ctx=12288, vision_n_ctx=8192,
                             vision_max_tokens=512, vision_gpu_layers=20)
        with temporary_directory() as temp:
            with patch("vc_director.nodes.node_story_builder.node._backend", side_effect=[planning, translation]) as backend, \
                 patch("vc_director.nodes.node_story_builder.node.cache_root", return_value=Path(temp) / "cache"), \
                 patch("vc_director.core.vision.resolve_vision_model_pair", return_value="vision-pair"), \
                 patch("vc_director.core.vision.image_facts.scenario_image_facts", return_value=facts) as vision, \
                 patch("vc_director.nodes.node_story_builder.node.GemmaTranslator", return_value=lambda text: translations.get(text, text)):
                scene_emd, directory, plan_json = VCDirectorBuild().build(
                    "scenario", emd, {"assets": assets}, temp, "[]", "gemma.gguf", 1, vision_model_name="vision.gguf", inference_settings=inference)
            vision.assert_called_once()
            self.assertEqual(vision.call_args.kwargs["config"].n_ctx, 8192)
            self.assertEqual(vision.call_args.kwargs["config"].gpu_layers, 20)
            self.assertEqual([call.kwargs["inference_settings"] for call in backend.call_args_list], [inference, inference])
            self.assertEqual(backend.call_args_list[1].kwargs["stage"], "compiler")
            self.assertEqual(planning.requests[0]["payload"]["image_facts"], facts)
            self.assertEqual(parse(scene_emd)["schema"], "VCD_SCENE_DOCUMENT_V1")
            self.assertEqual(parse(scene_emd)["actors"][0]["description"], appearance)
            self.assertIn(facts[0]["text"], parse(scene_emd)["scenes"][0]["subject_descriptions"][0]["text"])
            self.assertTrue(all(request["payload"]["scenario"]["actors"][0]["description"] == appearance for request in planning.requests))
            self.assertEqual([request["stage"] for request in planning.requests], ["enhance", "outline", "detail"])
            self.assertEqual(planning.requests[0]["payload"]["scenario"]["schema"], "VCD_SCENARIO_V1")
            self.assertEqual(Path(directory, "scenario.emd").read_text(encoding="utf-8"), emd)
            restored = load(Path(directory).parents[1])
            self.assertEqual(json.loads(plan_json), restored.plan)
            self.assertIn("<d>[Japanese]ただいま。</d>", restored.plan["shots"][0]["prompt"])
            self.assertIn("long brown hair", restored.plan["shots"][0]["prompt"])
            planning.close.assert_called_once()
            translation.close.assert_called_once()

    def test_saved_scene_does_not_resolve_vision_model(self):
        emd = (ROOT / "assets/emd/minimal.scene.emd").read_text(encoding="utf-8")
        with temporary_directory() as temp, patch("vc_director.core.vision.resolve_vision_model_pair") as resolve:
            VCDirectorBuild().build("saved_scene", emd, {"assets": []}, temp, "[]", "none", 1,
                                    vision_model_name="unused-model")
        resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
