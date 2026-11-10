"""Neutral in-memory local sound binding repair; no project output/model reads."""
import copy
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from test_vocal_prose_repair import neutral_payload
from test_vocal_timing_repair import BudgetBackend
from vc_director.core.audio.sound_triggers import validate_sound_trigger
from vc_director.core.emd.diagnostics import contextual_error
from vc_director.core.errors import VCError
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.protocols import StageRunner
from vc_director.core.protocols.grammar import record_grammar
from vc_director.core.protocols.sound_trigger_repair import repair_sound_triggers
from vc_director.core.utilities import canonical


def sound_case():
    payload, outlines, scene = neutral_payload(nonverbal=True)
    for audio in (payload["scenario"]["audio"], payload["scenario"]["beats"][0]["audio"], scene["audio"]):
        audio.update(effects="explicit", effects_description="Footsteps on the station floor.")
    scene["sound_events"] = [dict(id="station_steps", kind="effect", start_ms=500, end_ms=800,
                                 trigger_event_id="req_arrival", text="Footsteps on the floor.")]
    return payload, outlines, scene


def bindings(*rows):
    return dict(bindings=[dict(sound_id=id, trigger_event_id=trigger, start_ms=start, end_ms=end)
                          for id, trigger, start, end in rows])


def local_events(scene):
    return {event["id"]: event for shot in scene["shots"] for event in shot["events"]}


def check_sounds(scene):
    for sound in scene["sound_events"]:
        validate_sound_trigger(sound, local_events(scene), scene["id"])


class SoundTriggerRepairTests(unittest.TestCase):
    def test_planner_repairs_only_reference_preserving_dialogue_voice_and_visuals(self):
        payload, outlines, scene = sound_case()
        original = copy.deepcopy(scene)
        backend = BudgetBackend([outlines, scene, bindings(("station_steps", "station_action", 500, 800))])
        planned = StoryTimelinePlanner(StageRunner(backend)).plan(payload["scenario"], payload["enhanced"])
        expected = copy.deepcopy(scene)
        expected["sound_events"][0]["trigger_event_id"] = "station_action"
        self.assertEqual(planned["scenes"][0], expected)
        self.assertEqual(scene, original)
        self.assertEqual(len(backend.requests), 3)
        self.assertEqual(backend.requests[-1]["task"], "repair_sound_triggers")
        grammar = record_grammar("DETAIL", payload_schema=backend.schemas[-1])
        from llama_cpp import LlamaGrammar
        LlamaGrammar.from_string(grammar, verbose=False)

    def test_partial_repair_is_available_on_final_isolated_attempt(self):
        payload, outlines, scene = sound_case()
        incomplete = 'DETAIL\t1\t{"summary":"unfinished'
        backend = BudgetBackend([outlines, incomplete, incomplete, scene,
                                 bindings(("station_steps", "station_action", 500, 800))],
                                reasons=["stop", "length", "length", "stop", "stop"])
        result = StoryTimelinePlanner(StageRunner(backend)).plan(payload["scenario"], payload["enhanced"])
        check_sounds(result["scenes"][0])
        self.assertEqual(len(backend.requests), 5)
        self.assertEqual(backend.requests[-2]["retry"], "isolated")

    def test_out_of_interval_sound_moves_without_changing_duration_or_trigger_event(self):
        payload, _, scene = sound_case()
        action = next(e for e in scene["shots"][0]["events"] if e["id"] == "station_action")
        action.update(start_ms=2000, end_ms=4000)
        scene["sound_events"][0]["trigger_event_id"] = "station_action"
        backend = BudgetBackend([bindings(("station_steps", "station_action", 2300, 2600))])
        result = repair_sound_triggers(backend, scene, payload, {})
        self.assertEqual(result["shots"], scene["shots"])
        self.assertEqual(result["audio"], scene["audio"])
        self.assertEqual(result["sound_events"][0], dict(scene["sound_events"][0], start_ms=2300, end_ms=2600))
        check_sounds(result)

    def test_existing_timing_must_stay_fixed_when_the_chosen_event_already_covers_it(self):
        payload, _, scene = sound_case()
        backend = BudgetBackend([bindings(("station_steps", "station_action", 700, 1000))])
        with self.assertRaisesRegex(VCError, "SOUND_TRIGGER_REPAIR"):
            repair_sound_triggers(backend, scene, payload, {})

    def test_all_invalid_references_are_repaired_together_and_valid_sound_is_unchanged(self):
        payload, _, scene = sound_case()
        scene["sound_events"].extend([
            dict(scene["sound_events"][0], id="station_step_second", trigger_event_id="old_scene_action", start_ms=3000, end_ms=3300),
            dict(scene["sound_events"][0], id="station_step_valid", trigger_event_id="station_action", start_ms=3500, end_ms=3800)])
        response = bindings(("station_steps", "station_action", 500, 800),
                            ("station_step_second", "station_action", 3000, 3300))
        backend = BudgetBackend([response])
        result = repair_sound_triggers(backend, scene, payload, {})
        check_sounds(result)
        self.assertEqual(result["sound_events"][-1], scene["sound_events"][-1])
        self.assertEqual(len(backend.requests), 1)
        self.assertEqual(len(backend.requests[0]["invalid_sounds"]), 2)

    def test_required_shot_actor_previous_sound_and_invented_ids_are_not_candidates(self):
        payload, _, scene = sound_case()
        values = ["req_arrival", scene["shots"][0]["id"], "traveler", "previous_action", "station_steps", "invented_event"]
        for reference in values:
            with self.subTest(reference=reference):
                backend = BudgetBackend([bindings(("station_steps", reference, 500, 800))])
                with self.assertRaisesRegex(VCError, "SOUND_TRIGGER_REPAIR"):
                    repair_sound_triggers(backend, scene, payload, {})

    def test_effects_cannot_be_silently_unbound_or_turned_into_ambience(self):
        payload, _, scene = sound_case()
        for change in ({"trigger_event_id": None}, {"kind": "ambience"}, {"text": "changed"}):
            with self.subTest(change=change):
                response = bindings(("station_steps", "station_action", 500, 800))
                response["bindings"][0].update(change)
                with self.assertRaisesRegex(VCError, "SOUND_TRIGGER_REPAIR"):
                    repair_sound_triggers(BudgetBackend([response]), scene, payload, {})

    def test_only_continuous_ambience_can_select_null_without_changing_timing(self):
        payload, _, scene = sound_case()
        scene["sound_events"][0].update(kind="ambience", text="Wind through the station.")
        backend = BudgetBackend([bindings(("station_steps", None, 500, 800))])
        result = repair_sound_triggers(backend, scene, payload, {})
        self.assertEqual(result["sound_events"][0], dict(scene["sound_events"][0], trigger_event_id=None))
        check_sounds(result)

    def test_missing_duplicate_and_extra_sound_rows_are_rejected(self):
        payload, _, scene = sound_case()
        good = bindings(("station_steps", "station_action", 500, 800))
        for response in (bindings(), dict(bindings=good["bindings"] * 2),
                         bindings(("new_sound", "station_action", 500, 800)), dict(good, summary="changed")):
            with self.subTest(response=response), self.assertRaisesRegex(VCError, "SOUND_TRIGGER_REPAIR"):
                repair_sound_triggers(BudgetBackend([response]), scene, payload, {})

    def test_duration_failure_gets_one_partial_retry_with_specific_feedback(self):
        payload, _, scene = sound_case()
        next(e for e in scene["shots"][0]["events"] if e["id"] == "station_action").update(start_ms=2000, end_ms=4000)
        backend = BudgetBackend([bindings(("station_steps", "station_action", 2300, 2700)),
                                 bindings(("station_steps", "station_action", 2300, 2600))])
        trace = {}
        result = repair_sound_triggers(backend, scene, payload, trace)
        check_sounds(result)
        self.assertEqual(len(backend.requests), 2)
        self.assertIn("長さ300ms", backend.requests[1]["validation_error"]["message"])
        self.assertEqual(trace["attempts"][0]["status"], "invalid")

    def test_two_invalid_partial_responses_do_not_loop(self):
        payload, _, scene = sound_case()
        next(e for e in scene["shots"][0]["events"] if e["id"] == "station_action").update(start_ms=2000, end_ms=4000)
        invalid = bindings(("station_steps", "station_action", 2300, 2700))
        backend = BudgetBackend([invalid, invalid])
        with self.assertRaisesRegex(VCError, "SOUND_TRIGGER_REPAIR"):
            repair_sound_triggers(backend, scene, payload, {})
        self.assertEqual(len(backend.requests), 2)

    def test_no_effect_trigger_is_not_fixed_by_adding_or_dropping_events(self):
        payload, _, scene = sound_case()
        scene["shots"][0]["events"] = []
        backend = BudgetBackend([])
        with self.assertRaisesRegex(VCError, "ローカルイベントがありません"):
            repair_sound_triggers(backend, scene, payload, {})
        self.assertEqual(backend.requests, [])
        self.assertEqual(len(scene["sound_events"]), 1)

    def test_duplicate_local_or_sound_ids_are_not_guessed(self):
        for duplicate in ("event", "sound"):
            payload, _, scene = sound_case()
            if duplicate == "event":
                scene["shots"][0]["events"].append(copy.deepcopy(scene["shots"][0]["events"][0]))
            else:
                scene["sound_events"].append(dict(scene["sound_events"][0], trigger_event_id="station_action"))
            backend = BudgetBackend([])
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(VCError, "重複"):
                repair_sound_triggers(backend, scene, payload, {})
            self.assertEqual(backend.requests, [])

    def test_correct_sounds_do_not_call_the_repair_model(self):
        payload, _, scene = sound_case()
        scene["sound_events"][0]["trigger_event_id"] = "station_action"
        backend = BudgetBackend([])
        with self.assertRaisesRegex(VCError, "修正が必要な音の参照はありません"):
            repair_sound_triggers(backend, scene, payload, {})
        self.assertEqual(backend.requests, [])

    def test_model_can_report_no_causal_event_instead_of_guessing_a_camera(self):
        payload, _, scene = sound_case()
        original = copy.deepcopy(scene)
        backend = BudgetBackend([dict(bindings=[dict(sound_id="station_steps", unresolved=True)])])
        with self.assertRaisesRegex(VCError, "原因に一致するローカルイベントを特定できません"):
            repair_sound_triggers(backend, scene, payload, {})
        self.assertEqual(scene, original)
        self.assertEqual(len(backend.requests), 1)

    def test_failed_full_validation_does_not_cache_partial_success(self):
        payload, _, scene = sound_case()
        backend = BudgetBackend([scene, bindings(("station_steps", "station_action", 500, 800)), scene, scene])

        def check(candidate):
            check_sounds(candidate)
            raise VCError("NEUTRAL_CHECK", candidate["id"], "中立テストの全体検証エラー。")

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            runner = StageRunner(backend, Path(directory) / "cache")
            with self.assertRaisesRegex(VCError, "LLM_RETRIES_EXHAUSTED"):
                runner.run("detail", scene["id"], payload, check)
            self.assertEqual(len(backend.requests), 4)
            self.assertIsNone(runner.cache.get(runner.trace[-1]["key"]))

    def test_successful_repair_is_cached_after_validation(self):
        payload, _, scene = sound_case()
        backend = BudgetBackend([scene, bindings(("station_steps", "station_action", 500, 800))])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            runner = StageRunner(backend, Path(directory) / "cache")
            result = runner.run("detail", scene["id"], payload, check_sounds)
            cached = runner.run("detail", scene["id"], payload, check_sounds)
            self.assertEqual(result, cached)
            self.assertEqual(len(backend.requests), 2)
            self.assertEqual(runner.trace[-1]["cache"], "hit")

    def test_japanese_error_keeps_missing_reference_and_available_event_ids(self):
        _, _, scene = sound_case()
        with self.assertRaises(VCError) as caught:
            check_sounds(scene)
        d = caught.exception.diagnostic
        formatted = contextual_error(VCError("LLM_RETRIES_EXHAUSTED", scene["id"], canonical([
            dict(code=d.code.lower(), path=d.path, message=d.message)])), "# 形式\n", {})
        for text in ("station_steps", "req_arrival", "station_action", "500..800ms", "Scene"):
            self.assertIn(text, str(formatted))

    def test_japanese_time_error_keeps_actual_and_required_intervals(self):
        _, _, scene = sound_case()
        sound = dict(scene["sound_events"][0], trigger_event_id="station_line", start_ms=2100, end_ms=2400)
        with self.assertRaises(VCError) as caught:
            validate_sound_trigger(sound, local_events(scene), scene["id"])
        for text in ("station_line", "2100..2400ms", "1000..2000ms", "区間外"):
            self.assertIn(text, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
