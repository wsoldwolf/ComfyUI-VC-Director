"""Neutral in-memory dialogue/nonverbal timing and output-budget recovery."""
import copy
import tempfile
import unittest
from pathlib import Path

from test_enhance_recovery import NeutralBackend
from test_vocal_prose_repair import neutral_payload
from vc_director.core.audio.vocal_timing import validate_vocal_timing
from vc_director.core.emd.diagnostics import contextual_error
from vc_director.core.errors import Diagnostic, VCError
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.protocols import StageRunner
from vc_director.core.protocols.grammar import record_grammar
from vc_director.core.protocols.vocal_timing_repair import repair_vocal_timing
from vc_director.core.utilities import canonical


class BudgetBackend(NeutralBackend):
    def complete_record(self, *args, **kwargs):
        result = super().complete_record(*args, **kwargs)
        self.trace[-1]["performance"]["max_tokens"] = 8192
        return result


def overlap_case():
    payload, outlines, scene = neutral_payload(nonverbal=True)
    vocal = next(event for event in scene["shots"][0]["events"] if event["kind"] == "vocal")
    vocal.update(start_ms=1100, end_ms=1300)
    return payload, outlines, scene


def patch(*rows):
    return dict(timings=[dict(id=id, start_ms=start, end_ms=end) for id, start, end in rows])


def error():
    return Diagnostic("NONVERBAL_OVERLAP", "station_effort", "同じ人物の発声時刻が重複しています。")


class VocalTimingRepairTests(unittest.TestCase):
    def test_planner_changes_only_vocal_times_and_keeps_exact_authored_dialogue(self):
        payload, outlines, bad = overlap_case()
        original = copy.deepcopy(bad)
        backend = BudgetBackend([outlines, bad, patch(("station_effort", 2500, 2700))])
        runner = StageRunner(backend)
        planned = StoryTimelinePlanner(runner).plan(payload["scenario"], payload["enhanced"])
        expected = copy.deepcopy(bad)
        next(e for e in expected["shots"][0]["events"] if e["kind"] == "vocal").update(start_ms=2500, end_ms=2700)
        self.assertEqual(planned["scenes"][0], expected)
        self.assertEqual(bad, original)
        self.assertEqual(len(backend.requests), 3)
        request = backend.requests[2]
        self.assertEqual(request["task"], "repair_nonverbal_timing")
        dialogue = next(e for e in original["shots"][0]["events"] if e["kind"] == "dialogue")
        self.assertEqual(request["fixed_dialogue_events"], [dict(dialogue, shot_id=original["shots"][0]["id"])])
        self.assertEqual(request["fixed_audio_policy"], original["audio"])
        self.assertEqual(request["vocal_events"][0]["free_windows"], [[0, 1000], [2000, 5000]])
        # Exercise the actual converter without loading a model or reading model data.
        grammar = record_grammar("DETAIL", payload_schema=backend.schemas[2])
        self.assertIn('station_effort', grammar)

    def test_final_isolated_attempt_can_repair_overlap_after_two_8192_token_truncations(self):
        payload, outlines, bad = overlap_case()
        incomplete = 'DETAIL\t1\t{"summary":"unfinished'
        backend = BudgetBackend([outlines, incomplete, incomplete, bad, patch(("station_effort", 2500, 2700))],
                                reasons=["stop", "length", "length", "stop", "stop"])
        runner = StageRunner(backend)
        planned = StoryTimelinePlanner(runner).plan(payload["scenario"], payload["enhanced"])
        validate_vocal_timing(planned["scenes"][0])
        details = [request for request in backend.requests if request.get("stage") == "detail"]
        self.assertEqual(len(details), 3)
        self.assertEqual(details[-1]["retry"], "isolated")
        self.assertTrue(any("8192" in row["message"] for row in details[1]["errors"]))
        for request in details[1:]:
            compact = next(row for row in request["errors"] if row["code"] == "compact_detail")
            self.assertIn("exact authored dialogue", compact["message"])
            self.assertIn("Do not omit required events", compact["message"])
        self.assertEqual(backend.requests[-1]["task"], "repair_nonverbal_timing")

    def test_generated_full_scene_vocal_can_shorten_without_changing_dialogue(self):
        payload, _, scene = overlap_case()
        vocal = next(e for e in scene["shots"][0]["events"] if e["kind"] == "vocal")
        vocal.update(start_ms=0, end_ms=5000)
        backend = BudgetBackend([patch(("station_effort", 2200, 3000))])
        repaired = repair_vocal_timing(backend, scene, payload, error(), {})
        for old, new in zip(scene["shots"][0]["events"], repaired["shots"][0]["events"]):
            self.assertEqual(old["id"], new["id"])
            if old["kind"] != "vocal":
                self.assertEqual(old, new)
        self.assertEqual(repaired["audio"], scene["audio"])
        validate_vocal_timing(repaired)

    def test_unknown_duplicate_missing_or_extra_event_ids_are_rejected(self):
        payload, _, scene = overlap_case()
        cases = [patch(("new_voice", 2500, 2700)), patch(),
                 patch(("station_effort", 2500, 2700), ("station_effort", 3000, 3200)),
                 dict(patch(("station_effort", 2500, 2700)), summary="changed")]
        for response in cases:
            with self.subTest(response=response):
                backend = BudgetBackend([response])
                with self.assertRaisesRegex(VCError, "NONVERBAL_TIMING_PROTOCOL"):
                    repair_vocal_timing(backend, scene, payload, error(), {})
                self.assertEqual(len(backend.requests), 1)

    def test_patch_cannot_change_dialogue_or_add_event_fields(self):
        payload, _, scene = overlap_case()
        response = patch(("station_effort", 2500, 2700))
        response["timings"][0]["actor_id"] = "another_person"
        with self.assertRaisesRegex(VCError, "NONVERBAL_TIMING_PROTOCOL"):
            repair_vocal_timing(BudgetBackend([response]), scene, payload, error(), {})

    def test_semantic_overlap_has_one_partial_retry_with_specific_feedback(self):
        payload, _, scene = overlap_case()
        other = copy.deepcopy(scene["shots"][0]["events"][-1])
        other.update(id="station_effort_second", start_ms=1200, end_ms=1400)
        scene["shots"][0]["events"].append(other)
        backend = BudgetBackend([
            patch(("station_effort", 2200, 2400), ("station_effort_second", 2300, 2500)),
            patch(("station_effort", 2200, 2400), ("station_effort_second", 2400, 2600))])
        trace = {}
        repaired = repair_vocal_timing(backend, scene, payload, error(), trace)
        validate_vocal_timing(repaired)
        self.assertEqual(len(backend.requests), 2)
        self.assertEqual(backend.requests[1]["validation_error"]["code"], "NONVERBAL_OVERLAP")
        self.assertIn("station_effort_second", backend.requests[1]["validation_error"]["message"])
        self.assertEqual(trace["attempts"][0]["status"], "invalid")

    def test_two_failed_partial_attempts_do_not_loop(self):
        payload, _, scene = overlap_case()
        invalid = patch(("station_effort", 2200, 3200))  # Original duration is 200ms.
        backend = BudgetBackend([invalid, invalid])
        with self.assertRaisesRegex(VCError, "NONVERBAL_TIMING_DURATION"):
            repair_vocal_timing(backend, scene, payload, error(), {})
        self.assertEqual(len(backend.requests), 2)

    def test_vocals_cannot_move_outside_the_original_shot(self):
        payload, _, scene = overlap_case()
        scene["shots"][0].update(start_ms=2000, end_ms=4000)
        backend = BudgetBackend([patch(("station_effort", 4500, 4700))])
        with self.assertRaisesRegex(VCError, "NONVERBAL_TIMING_PROTOCOL"):
            repair_vocal_timing(backend, scene, payload, error(), {})

    def test_no_free_frame_fails_before_calling_the_model(self):
        payload, _, scene = overlap_case()
        next(e for e in scene["shots"][0]["events"] if e["kind"] == "dialogue").update(start_ms=0, end_ms=5000)
        backend = BudgetBackend([])
        with self.assertRaises(VCError) as caught:
            repair_vocal_timing(backend, scene, payload, error(), {})
        self.assertEqual(caught.exception.diagnostic.code, "NONVERBAL_TIMING_UNSATISFIABLE")
        self.assertIn("@traveler", str(caught.exception))
        self.assertIn("0..5000ms", str(caught.exception))
        self.assertEqual(backend.requests, [])

    def test_zero_frame_vocal_is_rejected_even_when_ms_times_are_positive(self):
        payload, _, scene = overlap_case()
        next(e for e in scene["shots"][0]["events"] if e["kind"] == "vocal").update(start_ms=1100, end_ms=1110)
        backend = BudgetBackend([patch(("station_effort", 0, 10)), patch(("station_effort", 20, 30))])
        repair_vocal_timing(backend, scene, payload, error(), {})
        self.assertEqual(backend.requests[1]["validation_error"]["code"], "ZERO_FRAME_EVENT")

    def test_fixed_sound_trigger_cannot_be_left_outside_moved_vocal(self):
        payload, _, scene = overlap_case()
        scene["sound_events"] = [dict(id="gate_sound", trigger_event_id="station_effort", start_ms=2550, end_ms=2600)]
        backend = BudgetBackend([patch(("station_effort", 3000, 3200)), patch(("station_effort", 2500, 2700))])
        repair_vocal_timing(backend, scene, payload, error(), {})
        self.assertEqual(backend.requests[1]["validation_error"]["code"], "SOUND_TRIGGER")

    def test_other_actor_may_overlap_and_same_actor_may_touch(self):
        _, _, scene = neutral_payload(nonverbal=True)
        scene["shots"][0]["events"].append(dict(kind="vocal", id="guide_effort", actor_id="guide",
            start_ms=1000, end_ms=2000, text="effort", dialogue_id=None, language=None))
        validate_vocal_timing(scene)
        vocal = next(e for e in scene["shots"][0]["events"] if e["id"] == "station_effort")
        vocal.update(start_ms=2000, end_ms=2200)
        validate_vocal_timing(scene)

    def test_japanese_failure_preserves_actual_actor_ids_and_intervals(self):
        _, _, scene = overlap_case()
        with self.assertRaises(VCError) as caught:
            validate_vocal_timing(scene)
        d = caught.exception.diagnostic
        final = VCError("LLM_RETRIES_EXHAUSTED", scene["id"], canonical([
            dict(code=d.code.lower(), path=d.path, message=d.message)]))
        formatted = contextual_error(final, "# 形式\n", {})
        for text in ("@traveler", "station_effort", "station_line", "1100..1300ms", "1000..2000ms"):
            self.assertIn(text, str(formatted))

    def test_only_fully_valid_repaired_scene_is_cached(self):
        payload, _, scene = overlap_case()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            backend = BudgetBackend([scene, patch(("station_effort", 2500, 2700))])
            runner = StageRunner(backend, Path(directory) / "cache")
            accepted = runner.run("detail", scene["id"], payload, validate_vocal_timing)
            again = runner.run("detail", scene["id"], payload, validate_vocal_timing)
            self.assertEqual(accepted, again)
            self.assertEqual(len(backend.requests), 2)
            self.assertEqual(runner.trace[-1]["cache"], "hit")

    def test_failed_full_validation_is_not_cached_or_repaired_without_bound(self):
        payload, _, scene = overlap_case()

        def check(candidate):
            validate_vocal_timing(candidate)
            raise VCError("NEUTRAL_CHECK", candidate["id"], "中立テストの全体検証エラー。")

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            backend = BudgetBackend([scene, patch(("station_effort", 2500, 2700)), scene, scene])
            runner = StageRunner(backend, Path(directory) / "cache")
            with self.assertRaisesRegex(VCError, "LLM_RETRIES_EXHAUSTED"):
                runner.run("detail", scene["id"], payload, check)
            self.assertEqual(len(backend.requests), 4)  # Three whole Scenes, one partial repair.
            self.assertIsNone(runner.cache.get(runner.trace[-1]["key"]))

    def test_duplicate_input_vocal_ids_fail_before_calling_the_model(self):
        payload, _, scene = overlap_case()
        scene["shots"][0]["events"].append(copy.deepcopy(scene["shots"][0]["events"][-1]))
        backend = BudgetBackend([])
        with self.assertRaisesRegex(VCError, "NONVERBAL_TIMING_PROTOCOL"):
            repair_vocal_timing(backend, scene, payload, error(), {})
        self.assertEqual(backend.requests, [])


if __name__ == "__main__":
    unittest.main()
