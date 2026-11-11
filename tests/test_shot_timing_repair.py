"""Neutral in-memory regression for overlapping Shots on the last retry."""
import copy
import tempfile
import unittest
from pathlib import Path

from test_vocal_prose_repair import neutral_payload
from test_vocal_timing_repair import BudgetBackend
from vc_director.core.artifacts.shot_timing import validate_shot_coverage
from vc_director.core.emd.diagnostics import contextual_error
from vc_director.core.errors import Diagnostic, VCError
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.protocols import StageRunner
from vc_director.core.protocols.grammar import record_grammar
from vc_director.core.protocols.shot_timing_repair import repair_shot_timing, _check
from vc_director.core.utilities import canonical


def shot_case(*, sound=False):
    payload, outlines, scene = neutral_payload(nonverbal=True)
    payload["scenario"]["duration_ms"] = 15000
    payload["outline"]["end_ms"] = outlines["scenes"][0]["end_ms"] = scene["end_ms"] = 15000
    first = scene["shots"][0]
    first["end_ms"] = 15000
    for event in first["events"]:
        if event["kind"] in {"camera", "action"}:
            event["end_ms"] = 15000
    camera = next(e for e in first["events"] if e["kind"] == "camera")
    action = next(e for e in first["events"] if e["kind"] == "action")
    scene["shots"].append(dict(id="station_second", start_ms=1500, end_ms=15000, events=[
        dict(camera, id="station_camera_second", start_ms=1500, end_ms=15000),
        dict(action, id="station_action_second", start_ms=1500, end_ms=15000)]))
    if sound:
        for audio in (payload["scenario"]["audio"], payload["scenario"]["beats"][0]["audio"], scene["audio"]):
            audio.update(effects="explicit", effects_description="Footsteps on the station floor.")
        scene["sound_events"] = [dict(id="station_steps", kind="effect", start_ms=3000, end_ms=3300,
                                      trigger_event_id="station_action", text="Footsteps on the floor.")]
    good = copy.deepcopy(scene)
    for index, shot in enumerate(good["shots"]):
        start, end = (0, 5000) if index == 0 else (5000, 15000)
        shot.update(start_ms=start, end_ms=end)
        for event in shot["events"]:
            if event["kind"] in {"camera", "action"}:
                event.update(start_ms=start, end_ms=end)
    return payload, outlines, scene, good


def timings(scene):
    return dict(shots=[dict(shot_id=shot["id"], start_ms=shot["start_ms"], end_ms=shot["end_ms"],
        events=[dict(event_id=e["id"], start_ms=e["start_ms"], end_ms=e["end_ms"]) for e in shot["events"]])
        for shot in scene["shots"]])


def error(scene):
    return Diagnostic("SHOT_COVERAGE", scene["shots"][-1]["id"], "生成Shotが重複しています。")


class ShotTimingRepairTests(unittest.TestCase):
    def test_reported_fifteen_second_overlap_is_repaired_without_dropping_shots_or_audio(self):
        payload, outlines, bad, good = shot_case(sound=True)
        original = copy.deepcopy((payload, bad))
        backend = BudgetBackend([outlines, bad, timings(good)])
        result = StoryTimelinePlanner(StageRunner(backend)).plan(payload["scenario"], payload["enhanced"])
        self.assertEqual(result["scenes"][0], good)
        self.assertEqual((payload, bad), original)
        self.assertEqual(len(backend.requests), 3)
        self.assertEqual(backend.requests[-1]["task"], "repair_shot_timing")
        self.assertEqual(good["sound_events"], bad["sound_events"])
        self.assertEqual(good["audio"], bad["audio"])
        for before, after in zip(bad["shots"], good["shots"]):
            self.assertEqual([e for e in before["events"] if e["kind"] in {"dialogue", "vocal"}],
                             [e for e in after["events"] if e["kind"] in {"dialogue", "vocal"}])
        from llama_cpp import LlamaGrammar
        LlamaGrammar.from_string(record_grammar("DETAIL", payload_schema=backend.schemas[-1]), verbose=False)

    def test_final_isolated_generation_can_recover_without_another_whole_scene_retry(self):
        payload, outlines, bad, good = shot_case()
        raw = 'DETAIL\t1\t{"summary":"unfinished'
        backend = BudgetBackend([outlines, raw, raw, bad, timings(good)],
                                reasons=["stop", "length", "length", "stop", "stop"])
        result = StoryTimelinePlanner(StageRunner(backend)).plan(payload["scenario"], payload["enhanced"])
        self.assertEqual(result["scenes"][0], good)
        self.assertEqual(backend.requests[-2]["retry"], "isolated")
        self.assertEqual(len(backend.requests), 5)

    def test_gap_at_scene_end_is_fixed_preserving_single_shot_and_original_action(self):
        payload, _, scene = neutral_payload(nonverbal=True)
        scene["shots"][0]["end_ms"] = 4000
        for e in scene["shots"][0]["events"]:
            if e["kind"] in {"camera", "action"}:
                e["end_ms"] = 4000
        good = copy.deepcopy(scene)
        good["shots"][0]["end_ms"] = 5000
        next(e for e in good["shots"][0]["events"] if e["kind"] == "camera")["end_ms"] = 5000
        result = repair_shot_timing(BudgetBackend([timings(good)]), scene, payload, error(scene), {})
        self.assertEqual(result, good)

    def test_wrong_ids_reordered_or_deleted_events_and_extra_fields_are_rejected(self):
        payload, _, bad, good = shot_case()
        unknown, dropped, reordered, speech, extra = [copy.deepcopy(timings(good)) for _ in range(5)]
        unknown["shots"][0]["shot_id"] = "new_shot"
        dropped["shots"][0]["events"].pop()
        reordered["shots"].reverse()
        speech["shots"][0]["events"][0]["text"] = "changed"
        extra["sound_events"] = []
        for invalid in (unknown, dropped, reordered, speech, extra):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(VCError, "SHOT_TIMING_REPAIR"):
                repair_shot_timing(BudgetBackend([invalid, invalid]), bad, payload, error(bad), {})

    def test_dialogue_and_nonverbal_times_cannot_be_changed_to_make_shots_fit(self):
        payload, _, bad, good = shot_case()
        for kind in ("dialogue", "vocal"):
            response = timings(good)
            index = next(i for i,e in enumerate(bad["shots"][0]["events"]) if e["kind"] == kind)
            response["shots"][0]["events"][index]["start_ms"] += 100
            with self.subTest(kind=kind), self.assertRaisesRegex(VCError, "SHOT_TIMING_REPAIR"):
                repair_shot_timing(BudgetBackend([response, response]), bad, payload, error(bad), {})

    def test_invalid_partition_has_one_partial_retry_with_specific_feedback(self):
        payload, _, bad, good = shot_case()
        backend = BudgetBackend([timings(bad), timings(good)])
        result = repair_shot_timing(backend, bad, payload, error(bad), {})
        self.assertEqual(result, good)
        self.assertIn("15000", backend.requests[1]["repair_error"])
        self.assertIn("1500", backend.requests[1]["repair_error"])
        self.assertEqual(len(backend.requests), 2)

    def test_zero_frame_visual_event_is_rejected(self):
        payload, _, bad, good = shot_case()
        invalid = timings(good)
        action = next(i for i,e in enumerate(good["shots"][0]["events"]) if e["kind"] == "action")
        invalid["shots"][0]["events"][action].update(start_ms=1, end_ms=2)
        backend = BudgetBackend([invalid, timings(good)])
        repair_shot_timing(backend, bad, payload, error(bad), {})
        self.assertIn("ZERO_FRAME_EVENT", backend.requests[1]["repair_error"])

    def test_fixed_sound_trigger_must_survive_action_retiming(self):
        payload, _, bad, good = shot_case(sound=True)
        invalid = timings(good)
        action = next(i for i,e in enumerate(good["shots"][0]["events"]) if e["kind"] == "action")
        invalid["shots"][0]["events"][action]["start_ms"] = 4000
        backend = BudgetBackend([invalid, timings(good)])
        result = repair_shot_timing(backend, bad, payload, error(bad), {})
        self.assertEqual(result["sound_events"], bad["sound_events"])
        self.assertIn("SOUND_TRIGGER", backend.requests[1]["repair_error"])

    def test_camera_order_cannot_be_swapped_even_if_sorted_coverage_would_pass(self):
        payload, _, bad, good = shot_case()
        old_camera = copy.deepcopy(bad["shots"][0]["events"][0])
        bad["shots"][0]["events"].append(dict(old_camera, id="station_camera_close"))
        good["shots"][0]["events"][0]["end_ms"] = 2500
        good["shots"][0]["events"].append(dict(old_camera, id="station_camera_close", start_ms=2500, end_ms=5000))
        invalid = timings(good)
        invalid["shots"][0]["events"][0].update(start_ms=2500, end_ms=5000)
        invalid["shots"][0]["events"][-1].update(start_ms=0, end_ms=2500)
        backend = BudgetBackend([invalid, timings(good)])
        repair_shot_timing(backend, bad, payload, error(bad), {})
        self.assertIn("CAMERA_ORDER", backend.requests[1]["repair_error"])

    def test_conflicting_fixed_speech_stops_before_calling_a_model(self):
        payload, _, bad, _ = shot_case()
        line = next(e for e in bad["shots"][0]["events"] if e["kind"] == "dialogue")
        bad["shots"][1]["events"].append(dict(line, id="station_second_line", dialogue_id="station_second_line"))
        backend = BudgetBackend([])
        with self.assertRaisesRegex(VCError, "SHOT_TIMING_UNSATISFIABLE") as caught:
            repair_shot_timing(backend, bad, payload, error(bad), {})
        self.assertIn("station_second_line", str(caught.exception))
        self.assertEqual(backend.requests, [])

    def test_insufficient_frame_budget_does_not_create_zero_length_shots(self):
        payload, _, bad, _ = shot_case()
        bad["end_ms"] = 20
        for shot in bad["shots"]:
            shot["events"] = [event for event in shot["events"] if event["kind"] == "camera"]
        with self.assertRaisesRegex(VCError, "SHOT_TIMING_UNSATISFIABLE"):
            repair_shot_timing(BudgetBackend([]), bad, payload, error(bad), {})

    def test_missing_camera_is_not_hidden_by_adding_or_dropping_events(self):
        payload, _, bad, _ = shot_case()
        bad["shots"][1]["events"] = [e for e in bad["shots"][1]["events"] if e["kind"] != "camera"]
        with self.assertRaisesRegex(VCError, "既存のCAMERA"):
            repair_shot_timing(BudgetBackend([]), bad, payload, error(bad), {})

    def test_missing_sound_binding_is_left_for_the_separate_reference_repair(self):
        payload, outlines, bad, good = shot_case(sound=True)
        bad["sound_events"][0]["trigger_event_id"] = good["sound_events"][0]["trigger_event_id"] = "unknown_action"
        binding = dict(bindings=[dict(sound_id="station_steps", trigger_event_id="station_action", start_ms=3000, end_ms=3300)])
        backend = BudgetBackend([outlines, bad, timings(good), binding])
        result = StoryTimelinePlanner(StageRunner(backend)).plan(payload["scenario"], payload["enhanced"])
        good["sound_events"][0]["trigger_event_id"] = "station_action"
        self.assertEqual(result["scenes"][0], good)
        self.assertEqual([r.get("task") for r in backend.requests[-2:]], ["repair_shot_timing", "repair_sound_triggers"])

    def test_unresolved_does_not_change_the_input_or_remove_any_event(self):
        payload, _, bad, _ = shot_case()
        before = copy.deepcopy(bad)
        backend = BudgetBackend([dict(unresolved=True)])
        with self.assertRaisesRegex(VCError, "SHOT_TIMING_UNSATISFIABLE"):
            repair_shot_timing(backend, bad, payload, error(bad), {})
        self.assertEqual(before, bad)
        self.assertEqual(len(backend.requests), 1)

    def test_only_fully_valid_repaired_timing_is_cached(self):
        payload, _, bad, good = shot_case()
        backend = BudgetBackend([bad, timings(good)])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            runner = StageRunner(backend, directory)
            result = runner.run("detail", bad["id"], payload, _check)
            again = runner.run("detail", bad["id"], payload, _check)
            self.assertEqual(result, good)
            self.assertEqual(result, again)
            self.assertEqual(len(backend.requests), 2)
            self.assertEqual(runner.trace[-1]["cache"], "hit")

    def test_japanese_error_keeps_actual_boundaries_after_retries_exhausted(self):
        _, _, bad, _ = shot_case()
        with self.assertRaises(VCError) as caught:
            validate_shot_coverage(bad)
        d = caught.exception.diagnostic
        formatted = contextual_error(VCError("LLM_RETRIES_EXHAUSTED", bad["id"], canonical([
            dict(code=d.code.lower(), path=d.path, message=d.message)])), "# 形式\n", {})
        for text in ("15000", "1500", "station_second", "時間が残っていません"):
            self.assertIn(text, str(formatted))
        self.assertNotIn("(15000, 15000]", str(formatted))

    def test_absolute_scene_offset_does_not_leak_into_relative_event_times(self):
        payload, _, bad, good = shot_case()
        bad.update(start_ms=50000, end_ms=65000)
        good.update(start_ms=50000, end_ms=65000)
        result = repair_shot_timing(BudgetBackend([timings(good)]), bad, payload, error(bad), {})
        self.assertEqual(result, good)
        self.assertEqual(result["shots"][0]["start_ms"], 0)

    def test_other_validation_failure_does_not_cache_a_temporal_patch(self):
        payload, _, bad, good = shot_case()
        backend = BudgetBackend([bad, timings(good), bad, bad])

        def check(candidate):
            _check(candidate)
            raise VCError("NEUTRAL_CHECK", candidate["id"], "中立テストの全体検証エラー。")

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            runner = StageRunner(backend, directory)
            with self.assertRaisesRegex(VCError, "LLM_RETRIES_EXHAUSTED"):
                runner.run("detail", bad["id"], payload, check)
            self.assertEqual(len(backend.requests), 4)
            self.assertIsNone(runner.cache.get(runner.trace[-1]["key"]))


if __name__ == "__main__":
    unittest.main()
