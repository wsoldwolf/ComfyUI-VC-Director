"""Neutral timing and continuity regression cases; no user outputs/models."""
import copy
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from test_outline_required import neutral_case, NeutralBackend
from test_translation_alias_repair import NEUTRAL_SCENE
from vc_director.core.artifacts.storage import save, load
from vc_director.core.artifacts.transitions import validate_scene_transition
from vc_director.core.compiler.api import compile_document, TYPED_PROMPT_POLICY, PROMPT_POLICY
from vc_director.core.emd import parse, render_scene
from vc_director.core.emd.diagnostics import contextual_error
from vc_director.core.errors import VCError
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.planner.outline_timing import recovery_intervals
from vc_director.core.protocols import StageRunner, system_prompt
from vc_director.core.protocols.llm import generation_schema
from vc_director.core.protocols.grammar import record_grammar


STATE = dict(owner_id="traveler", key="position", value="beside the east gate")


def timing_case():
    payload, result = neutral_case()
    scenario = payload["scenario"]
    scenario["duration_ms"] = 55000
    beat = scenario["beats"][0]
    beat["transition"] = "continue"
    setup = dict(copy.deepcopy(beat), id="setup", transition="auto")
    scenario["beats"].insert(0, setup)
    payload["enhanced"]["beats"].insert(0, dict(id="setup", intent="Wait by the gate.", required_events=[]))
    template = result["scenes"][0]
    accepted = []
    for i, (start, end) in enumerate(((0, 15000), (15000, 30000), (30000, 35000))):
        accepted.append(dict(copy.deepcopy(template), id=f"scene_setup_{i}", beat_id="setup",
            start_ms=start, end_ms=end, required_event_ids=[], transition="cut" if i == 0 else "continue",
            depends_on=[] if i == 0 else [accepted[-1]["id"]]))
    accepted[-1]["terminal_state"] = [copy.deepcopy(STATE)]
    bad = dict(copy.deepcopy(template), id="scene_arrival", start_ms=35000, end_ms=55000,
               transition="continue", depends_on=[accepted[-1]["id"]], initial_state=[copy.deepcopy(STATE)])
    first = dict(copy.deepcopy(bad), end_ms=50000, terminal_state=[copy.deepcopy(STATE)],
                 required_event_ids=bad["required_event_ids"][:4])
    second = dict(copy.deepcopy(first), id="scene_arrival_2", start_ms=50000, end_ms=55000,
                  depends_on=[first["id"]], required_event_ids=bad["required_event_ids"][4:])
    return payload, accepted, dict(scenes=[bad]), dict(scenes=[first, second])


def english_continuation():
    document = parse(NEUTRAL_SCENE.replace("青い上着の成人。", "An adult in a blue coat."))
    first = document["scenes"][0]
    first["terminal_state"] = [copy.deepcopy(STATE)]
    second = copy.deepcopy(first)
    second.update(id="scene_arrival_2", start_ms=5000, end_ms=10000, transition="continue",
                  depends_on=[first["id"]], initial_state=[copy.deepcopy(STATE)])
    second["shots"][0]["id"] = "station_shot_2"
    second["shots"][0]["events"][0]["id"] = "station_camera_2"
    document["scenes"].append(second)
    document["duration_ms"] = 10000
    return document


class OutlineContinuityTests(unittest.TestCase):
    def test_reported_twenty_second_scene_recovers_without_truncating_end(self):
        payload, accepted, bad, good = timing_case()
        original = copy.deepcopy(payload)
        backend = NeutralBackend([dict(scenes=accepted), bad, good])
        result = StoryTimelinePlanner(StageRunner(backend))._plan_outlines(payload["scenario"], payload["enhanced"])
        self.assertEqual(result, accepted + good["scenes"])
        self.assertEqual(payload, original)
        retry = backend.requests[2]
        self.assertIn("20000ms", retry["errors"][0]["message"])
        self.assertIn("15000ms", retry["errors"][0]["message"])
        schema = backend.schemas[2]
        validator = Draft202012Validator(schema)
        self.assertTrue(validator.is_valid(good))
        self.assertFalse(validator.is_valid(bad))
        self.assertEqual([[s["start_ms"], s["end_ms"]] for s in result[-2:]], [[35000, 50000], [50000, 55000]])
        self.assertEqual(sum(s["end_ms"] - s["start_ms"] for s in result[-2:]), 20000)

    def test_initial_native_schema_already_forbids_oversized_first_scene(self):
        payload, accepted, bad, good = timing_case()
        payload["accepted_outlines"] = accepted
        validator = Draft202012Validator(generation_schema("outline", payload))
        self.assertFalse(validator.is_valid(bad))
        self.assertTrue(validator.is_valid(good))

    def test_timing_recovery_survives_a_later_binding_error(self):
        payload, accepted, bad, good = timing_case()
        wrong = copy.deepcopy(good)
        wrong["scenes"][0]["background_id"] = "other_background"
        backend = NeutralBackend([dict(scenes=accepted), bad, wrong, good])
        result = StoryTimelinePlanner(StageRunner(backend))._plan_outlines(payload["scenario"], payload["enhanced"])
        self.assertEqual(result[-2:], good["scenes"])
        self.assertEqual(backend.schemas[2], backend.schemas[3])
        self.assertEqual({e["code"] for e in backend.requests[3]["errors"]}, {"outline_binding", "outline_timing"})

    def test_invalid_non_native_timing_still_stops_with_concrete_japanese_intervals(self):
        payload, accepted, bad, _ = timing_case()
        backend = NeutralBackend([dict(scenes=accepted), bad, bad, bad])
        with self.assertRaises(VCError) as caught:
            StoryTimelinePlanner(StageRunner(backend))._plan_outlines(payload["scenario"], payload["enhanced"])
        message = str(contextual_error(caught.exception, "# シナリオ\n## 展開 arrival\n中立テスト\n"))
        for text in ("35000", "50000", "55000", "15000ms", "## 展開 arrival"):
            self.assertIn(text, message)

    def test_recovery_rejects_spending_later_beats_time_and_keeps_all_requirements(self):
        payload, accepted, bad, _ = timing_case()
        payload["accepted_outlines"] = accepted
        payload["scenario"]["beats"].append(dict(copy.deepcopy(payload["scenario"]["beats"][-1]), id="departure"))
        payload["scenario"]["duration_ms"] = 65000
        self.assertEqual(recovery_intervals(payload, bad, "arrival"), [[35000, 50000], [50000, 55000]])
        over = copy.deepcopy(bad)
        over["scenes"][0]["end_ms"] = 65000
        self.assertIsNone(recovery_intervals(payload, over, "arrival"))

    def test_continue_requires_missing_and_conflicting_boundary_states_to_be_fixed(self):
        payload, accepted, _, good = timing_case()
        beat = payload["scenario"]["beats"][-1]
        missing = dict(copy.deepcopy(good["scenes"][0]), initial_state=[])
        with self.assertRaisesRegex(VCError, "STATE_CONTINUITY"):
            validate_scene_transition(missing, accepted[-1], beat, require_state_coverage=True)
        # Saved revisions retain the previous omission-tolerant validation.
        validate_scene_transition(missing, accepted[-1], beat)
        changed = copy.deepcopy(good["scenes"][0])
        changed["initial_state"][0]["value"] = "at a distant door"
        with self.assertRaisesRegex(VCError, "STATE_CONTINUITY"):
            validate_scene_transition(changed, accepted[-1], beat, require_state_coverage=True)

    def test_native_continue_schema_binds_previous_dependency_and_state(self):
        payload, accepted, _, good = timing_case()
        payload["accepted_outlines"] = accepted
        schema = generation_schema("outline", payload)
        validator = Draft202012Validator(schema)
        self.assertTrue(validator.is_valid(good))
        for key, value in (("initial_state", []), ("depends_on", [])):
            wrong = copy.deepcopy(good)
            wrong["scenes"][0][key] = value
            self.assertFalse(validator.is_valid(wrong), key)
        grammar = record_grammar("SCENES", payload_schema=schema)
        self.assertIn("initial-state", grammar)

    def test_detail_prompt_uses_previous_camera_endpoint_and_initial_state(self):
        payload, accepted, _, good = timing_case()
        previous = parse(NEUTRAL_SCENE)["scenes"][0]
        previous.update(id=accepted[-1]["id"], start_ms=30000, end_ms=35000, terminal_state=[copy.deepcopy(STATE)])
        previous["shots"][0]["events"][0]["text"] = "Track right, ending by the east gate."
        payload.update(outline=good["scenes"][0], previous_scenes=[previous], required_dialogue_ids=[], locked_scene=None)
        prompt = system_prompt("detail", payload)
        self.assertIn("ending by the east gate", prompt)
        self.assertIn("終了時の構図", prompt)
        self.assertIn(STATE["value"], prompt)

    def test_compiler_keeps_guide_context_and_adds_visual_continuity_only_to_continue(self):
        document = english_continuation()
        result = compile_document(document, render_scene(document))
        shots = result.plan["shots"]
        self.assertEqual([s["context_length"] for s in shots], [0, 22])
        self.assertEqual(shots[1]["continuation_mode"], "guide")
        self.assertNotIn("Continue seamlessly", shots[0]["prompt"])
        self.assertIn("Continue seamlessly", shots[1]["prompt"])
        self.assertNotIn("<d>", shots[1]["prompt"])
        self.assertEqual(result.metadata["prompt_policy_version"], PROMPT_POLICY)

    def test_v10_saved_policy_replays_without_new_continuity_text(self):
        document = english_continuation()
        emd = render_scene(document)
        previous = compile_document(document, emd, prompt_policy=TYPED_PROMPT_POLICY)
        current = compile_document(document, emd)
        self.assertNotIn("Continue seamlessly", previous.plan["shots"][1]["prompt"])
        self.assertIn("Continue seamlessly", current.plan["shots"][1]["prompt"])
        with tempfile.TemporaryDirectory(prefix="neutral_continuity_", dir=Path(__file__).resolve().parent) as directory:
            for result in (previous, current):
                revision = save(result, directory)
                self.assertEqual(load(revision.parents[1]).plan, result.plan)


if __name__ == "__main__":
    unittest.main()
