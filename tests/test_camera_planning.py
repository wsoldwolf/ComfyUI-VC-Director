import copy
import importlib.util
import unittest

from test_core import (FakeBackend, compile_fixture, english_document, fixture,
                       temporary_directory, with_camera)
import test_planning_transport as transport
from jsonschema import Draft202012Validator
from vc_director.core.artifacts.schema import validate
from vc_director.core.emd import parse, render_scene
from vc_director.core.errors import VCError
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.planner.camera import validate_camera_plan
from vc_director.core.protocols import StageRunner
from vc_director.core.protocols.grammar import record_grammar
from vc_director.core.protocols.llm import generation_schema
from vc_director.core.utilities import canonical


class CameraPlanningTests(unittest.TestCase):
    def scene(self):
        return with_camera(english_document()["scenes"][0])

    def test_static_follow_pan_and_multiple_shots_cover_without_reordering(self):
        scene = self.scene()
        first = scene["shots"][0]
        first["end_ms"] = 4000
        first["events"] = first["events"][:1]
        first["events"][0].update(end_ms=4000, text="Static medium shot; hold position and framing.")
        second = copy.deepcopy(first)
        second.update(id="shot_follow", start_ms=4000, end_ms=10000)
        follow = second["events"][0]
        follow.update(id="cam_follow", start_ms=4000, end_ms=7000,
                      text="Track @hero slowly to the right from medium to medium-wide framing.")
        pan = dict(follow, id="cam_pan", start_ms=7000, end_ms=10000, actor_id="environment",
                   text="Pan slowly right from the traveler to center the house in a wide view.")
        second["events"] = [pan, follow]
        scene["shots"].append(second)
        before = copy.deepcopy(scene)
        self.assertEqual(validate_camera_plan(scene), before)
        self.assertEqual(scene, before)
        scene.update(start_ms=10000, end_ms=20000, transition="continue", depends_on=["earlier"])
        validate_camera_plan(scene)  # Events remain Scene-relative, independent of absolute time.
        scene["actor_ids"] = []
        for shot in scene["shots"]:
            for event in shot["events"]:
                event["actor_id"] = "environment"
        validate_camera_plan(scene)

    def test_missing_gap_overlap_zero_and_outside_diagnostics(self):
        for intervals, code, span in (([], "CAMERA_MISSING", "0 to 10000"),
                ([(1, 10000)], "CAMERA_COVERAGE", "0..1"),
                ([(0, 4000), (5000, 10000)], "CAMERA_COVERAGE", "4000..5000"),
                ([(0, 9999)], "CAMERA_COVERAGE", "9999..10000"),
                ([(0, 6000), (5000, 10000)], "CAMERA_OVERLAP", "5000"),
                ([(0, 0)], "CAMERA_TIME", "0..0"),
                ([(0, 10001)], "CAMERA_TIME", "0..10001")):
            with self.subTest(intervals=intervals):
                scene = self.scene()
                camera = scene["shots"][0]["events"][0]
                scene["shots"][0]["events"] = [dict(camera, id=f"cam_{i}", start_ms=a, end_ms=b)
                                               for i, (a, b) in enumerate(intervals)]
                with self.assertRaises(VCError) as caught:
                    validate_camera_plan(scene)
                self.assertEqual(caught.exception.diagnostic.code, code)
                self.assertIn(span, caught.exception.diagnostic.message)

    def test_generation_requires_first_camera_but_published_schema_keeps_legacy(self):
        payload, scene = transport.PlanningTransportTests.silent_detail()
        narrowed = generation_schema("detail", payload)
        Draft202012Validator.check_schema(narrowed)
        validator = Draft202012Validator(narrowed)
        self.assertTrue(validator.is_valid(scene))
        legacy = copy.deepcopy(scene)
        legacy["shots"][0]["events"].pop(0)
        validate(legacy, "scene")
        self.assertFalse(validator.is_valid(legacy))
        scene["shots"][0]["events"].reverse()
        self.assertFalse(validator.is_valid(scene))
        self.assertNotIn("prefixItems", canonical(fixture("scene-document-dialogue")))

    def test_missing_camera_is_repaired_in_planner_without_changing_dialogue(self):
        scenario, enhanced = fixture("scenario"), fixture("enhanced-story")
        outline = fixture("outline-response")["records"][0]["payload"]
        legacy = fixture("detail-response")["records"][0]["payload"]
        good = with_camera(legacy)
        backend = FakeBackend(["SCENES\t1\t" + canonical(outline),
                               "DETAIL\t1\t" + canonical(legacy), "DETAIL\t1\t" + canonical(good)])
        document = StoryTimelinePlanner(StageRunner(backend)).plan(scenario, enhanced)
        self.assertEqual(document["scenes"], [good])
        self.assertEqual(backend.requests[-1]["retry"], "repair")
        self.assertEqual(backend.requests[-1]["errors"][0]["code"], "camera_missing")
        self.assertEqual(good["shots"][0]["events"][1:], legacy["shots"][0]["events"])

    def test_invalid_cached_camera_is_rechecked_and_three_failures_are_bounded(self):
        scenario, enhanced = fixture("scenario"), fixture("enhanced-story")
        legacy = fixture("detail-response")["records"][0]["payload"]
        outline = fixture("outline-response")["records"][0]["payload"]["scenes"][0]
        payload = dict(scenario=scenario, enhanced=enhanced, outline=outline, previous_scenes=[], locked_scene=None)
        raw = "DETAIL\t1\t" + canonical(legacy)
        with temporary_directory() as directory:
            StageRunner(FakeBackend([raw]), directory).run("detail", legacy["id"], payload, lambda x: None)
            backend = FakeBackend([raw] * 3)
            runner = StageRunner(backend, directory)
            with self.assertRaises(VCError) as caught:
                runner.run("detail", legacy["id"], payload, validate_camera_plan)
            self.assertEqual(caught.exception.diagnostic.code, "LLM_RETRIES_EXHAUSTED")
            self.assertEqual([r["retry"] for r in backend.requests], ["initial", "repair", "isolated"])
            self.assertIn("camera_missing", str(caught.exception))

    def test_retime_preserves_complete_camera_coverage_and_legacy_compatibility(self):
        doc = english_document()
        doc["scenes"][0] = self.scene()
        scene = doc["scenes"][0]
        timing = dict(scene_id=scene["id"], start_ms=0, end_ms=12000, sound_events=[], shots=[
            dict(id=shot["id"], start_ms=0, end_ms=12000,
                 events=[{k: event[k] for k in ("id", "start_ms", "end_ms")} for event in shot["events"]])
            for shot in scene["shots"]])
        fixed = copy.deepcopy(timing)
        fixed["shots"][0]["events"][0]["end_ms"] = 12000
        backend = FakeBackend(["TIMING\t1\t" + canonical(timing), "TIMING\t1\t" + canonical(fixed)])
        result = StoryTimelinePlanner(StageRunner(backend)).retime(doc, [scene["id"]])
        validate_camera_plan(result["scenes"][0])
        self.assertEqual(backend.requests[1]["errors"][0]["code"], "camera_coverage")
        self.assertEqual([e["text"] for e in result["scenes"][0]["shots"][0]["events"]],
                         [e["text"] for e in scene["shots"][0]["events"]])
        legacy = english_document()
        timing["shots"][0]["events"].pop(0)
        legacy_result = StoryTimelinePlanner(StageRunner(FakeBackend(["TIMING\t1\t" + canonical(timing)]))).retime(
            legacy, [scene["id"]])
        self.assertFalse(any(e["kind"] == "camera" for e in legacy_result["scenes"][0]["shots"][0]["events"]))

    @unittest.skipUnless(importlib.util.find_spec("llama_cpp"), "native grammar converter unavailable")
    def test_native_grammar_preserves_prefix_camera_and_open_event_tail(self):
        payload, scene = transport.PlanningTransportTests.silent_detail()
        grammar = record_grammar("DETAIL", payload_schema=generation_schema("detail", payload))
        array = next(line for line in grammar.splitlines() if line.startswith("shot-events ::="))
        self.assertIn("shot-events-prefix-0", array)
        self.assertIn('(\",\" space shot-events-tail)*', array)
        self.assertIn('shot-events-prefix-0-kind ::= "\\\"camera\\\""', grammar)
        from llama_cpp import LlamaGrammar
        LlamaGrammar.from_string(grammar, verbose=False)

    def test_camera_roundtrip_native_subject_and_frame_mapping_preserve_audio_and_dialogue(self):
        doc = english_document()
        old = compile_fixture(doc)
        doc["scenes"][0] = self.scene()
        camera = doc["scenes"][0]["shots"][0]["events"][0]
        camera.update(actor_id="hero", text="Track @hero slowly right, from a medium shot to a wide view.")
        emd = render_scene(doc)
        self.assertEqual(parse(emd), doc)
        result = compile_fixture(doc)
        prompt = result.plan["shots"][0]["prompt"]
        line = next(line for line in prompt.splitlines() if " CAMERA " in line)
        self.assertIn("CAMERA <Subject 1>: Track <Subject 1> slowly right", line)
        self.assertIn("[00:00.000 --> 00:10.000]", line)
        self.assertEqual(prompt.replace(line + "\n", ""), old.plan["shots"][0]["prompt"])
        timing = next(t for t in result.execution["segments"][0]["event_timing"] if t["event_id"] == camera["id"])
        self.assertEqual((timing["raw_start_frame"], timing["raw_end_frame"]), (0, 240))

    def test_continuation_camera_receives_guide_context_offset(self):
        doc = english_document()
        doc["beats"][0]["dialogues"] = []
        for policy in (doc["audio"], doc["beats"][0]["audio"], doc["scenes"][0]["audio"]):
            policy["vocal"] = "none"
        first = doc["scenes"][0]
        first["shots"][0]["events"].pop()
        first = doc["scenes"][0] = with_camera(first)
        second = copy.deepcopy(first)
        second.update(id="second", start_ms=10000, end_ms=20000, transition="continue",
                      depends_on=[first["id"]], required_event_ids=[], initial_state=copy.deepcopy(first["terminal_state"]))
        second["shots"][0]["id"] = "second_shot"
        for event in second["shots"][0]["events"]:
            event["id"] = "second_" + event["id"]
        doc["scenes"].append(second)
        doc["duration_ms"] = 20000
        result = compile_fixture(doc)
        camera_timing = result.execution["segments"][1]["event_timing"][0]
        self.assertEqual((camera_timing["raw_start_frame"], camera_timing["raw_end_frame"]), (22, 262))
        self.assertIn("[00:00.917 --> 00:10.917] CAMERA Environment:", result.plan["shots"][1]["prompt"])


if __name__ == "__main__":
    unittest.main()
