import copy
import unittest
from unittest.mock import patch

from test_core import english_document, compile_fixture, temporary_directory
from vc_director.core.artifacts.storage import save, load
from vc_director.core.artifacts.validation import validate_story
from vc_director.core.emd import parse, render_scene
from vc_director.core.errors import VCError
from vc_director.core.protocols.llm import generation_schema


class VisualDirectionTests(unittest.TestCase):
    def test_scene_visual_direction_replaces_raw_author_direction_without_regex(self):
        for source in ("Only the announcer can be heard through a distant loudspeaker.",
                       "The offscreen friend speaks through a radio.",
                       "A narrator whispers over the image."):
            doc = english_document()
            doc["direction"] = [source]
            doc["scenes"][0]["visual_directions"] = ["Film only @hero, in a naturalistic medium shot."]
            with patch("vc_director.core.compiler.api.narrow_silent_direction", side_effect=AssertionError("regex used")):
                result = compile_fixture(doc)
            prompt = result.plan["shots"][0]["prompt"]
            self.assertNotIn(source, prompt)
            self.assertIn("Film only <Subject 1>, in a naturalistic medium shot.", prompt)
            self.assertEqual(result.document["direction"], [source])
            self.assertIn("<d>[Japanese]", prompt)
            self.assertEqual(parse(render_scene(doc)), doc)
            with temporary_directory() as directory:
                revision = save(result, directory)
                self.assertEqual(load(revision.parents[1]).plan, result.plan)

    def test_visual_field_vocal_request_rejected_not_silently_deleted(self):
        doc = english_document()
        doc["scenes"][0]["visual_directions"] = ["@hero whispers quietly."]
        with self.assertRaises(VCError) as caught:
            validate_story(doc)
        self.assertEqual(caught.exception.diagnostic.code, "VOCAL_CUE")
        self.assertIn("visual_directions", caught.exception.diagnostic.path)

    def test_generated_visual_field_required_and_empty_roundtrips(self):
        from test_planning_transport import PlanningTransportTests
        from jsonschema import Draft202012Validator
        payload, scene = PlanningTransportTests.silent_detail()
        validator = Draft202012Validator(generation_schema("detail", payload))
        self.assertTrue(validator.is_valid(scene))
        del scene["visual_directions"]
        self.assertFalse(validator.is_valid(scene))
        doc = english_document()
        doc["scenes"][0]["visual_directions"] = []
        self.assertEqual(parse(render_scene(doc)), doc)

    def test_planner_preserves_generated_visual_directions_to_compiler(self):
        from test_beat_transitions import continued_story
        from test_core import FakeBackend
        from vc_director.core.protocols import StageRunner
        from vc_director.core.planner import StoryTimelinePlanner
        from vc_director.core.utilities import canonical
        scenario, enhanced, outlines, doc = continued_story()
        scenario["direction"] = ["The friend is heard offscreen through a radio."]
        for scene in doc["scenes"]:
            scene["visual_directions"] = ["Keep the unseen friend outside the frame."]
        responses = ["SCENES\t1\t" + canonical(dict(scenes=items)) for items in (outlines[:2], outlines[2:])]
        responses += ["DETAIL\t1\t" + canonical(scene) for scene in doc["scenes"]]
        result = StoryTimelinePlanner(StageRunner(FakeBackend(responses))).plan(scenario, enhanced)
        compiled = compile_fixture(result)
        for shot in compiled.plan["shots"]:
            self.assertIn("Keep the unseen friend outside the frame.", shot["prompt"])
            self.assertNotIn("heard offscreen", shot["prompt"])
