"""Neutral, in-memory vocal-cue repair tests; no user scenarios or model files."""
import copy
import tempfile
import unittest
from pathlib import Path

from test_build_cache import SCENARIO, neutral_planning, PlanningBackend
from vc_director.core.artifacts.validation import _prose, validate_story
from vc_director.core.compiler import compile_document
from vc_director.core.emd import parse, render_scene
from vc_director.core.errors import Diagnostic, VCError
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.protocols import StageRunner
from vc_director.core.protocols.prose_repair import (
    get_text, prose_targets, repair_prose_field, target_context, target_for_error)
from vc_director.core.utilities.progress import progress_scope


GOOD = "@traveler walks toward the gate and raises a hand."
BAD = "@travelerが小さく話しながら門へ歩き、手を上げる。"


def neutral_payload(*, dialogue=True, nonverbal=False):
    emd = SCENARIO.replace("@travelerが門の前に立つ。", "@travelerが門へ歩き、手を上げる。")
    if dialogue:
        emd = emd.replace("# 作品\n", "# 作品\n* 発声: 指定台詞のみ\n")
        emd += "* 台詞 station_line: @traveler | 「おはよう。」\n"
    scenario = parse(emd)
    enhanced, outlines, scene = neutral_planning()
    enhanced["scenario_sha256"] = scenario["source_sha256"]
    requirement = enhanced["beats"][0]["required_events"][0]
    requirement.update(source=copy.deepcopy(scenario["beats"][0]["source"]), text=GOOD)
    if nonverbal:
        scenario["audio"].update(vocal="dialogue_nonverbal" if dialogue else "nonverbal",
            nonverbal=dict(allowed=["effort"], frequency="控えめ", intensity="弱め", instruction="動作に合わせる", pattern=None))
        scenario["beats"][0]["audio"] = copy.deepcopy(scenario["audio"])
    scene["audio"] = copy.deepcopy(scenario["beats"][0]["audio"])
    scene["summary"] = GOOD
    scene["subject_descriptions"][0]["text"] = "An adult in a blue coat."
    scene["shots"][0]["events"].append(dict(kind="action", id="station_action", start_ms=0, end_ms=5000,
        actor_id="traveler", text=GOOD, dialogue_id=None, language=None))
    if dialogue:
        authored = scenario["beats"][0]["dialogues"][0]
        scene["shots"][0]["events"].append(dict(kind="dialogue", id=authored["id"], start_ms=1000,
            end_ms=2000, actor_id=authored["actor_id"], text=authored["text"], dialogue_id=authored["id"], language=authored["language"]))
    if nonverbal:
        scene["shots"][0]["events"].append(dict(kind="vocal", id="station_effort", start_ms=2500,
            end_ms=2700, actor_id="traveler", text="effort", dialogue_id=None, language=None))
    payload = dict(scenario=scenario, enhanced=enhanced, outline=outlines["scenes"][0], previous_scenes=[],
                   required_dialogue_ids=["station_line"] if dialogue else [], locked_scene=None)
    return payload, outlines, scene


def vocal_error(scene, path=None):
    return Diagnostic("VOCAL_CUE", path or scene["id"] + "/summary", "Remove vocal description '話し'.")


class RecordingBackend(PlanningBackend):
    def __init__(self, responses):
        super().__init__(responses)
        self.systems = []

    def complete_record(self, system, request, record_type, *, payload_schema):
        self.systems.append(system)
        return super().complete_record(system, request, record_type, payload_schema=payload_schema)


class VocalProseRepairTests(unittest.TestCase):
    def test_planner_repairs_only_summary_preserving_authored_speech_and_timing(self):
        payload, outlines, expected = neutral_payload()
        bad = dict(copy.deepcopy(expected), summary=BAD)
        original = copy.deepcopy(bad)
        backend = RecordingBackend([outlines, bad, dict(text=GOOD)])
        planned = StoryTimelinePlanner(StageRunner(backend)).plan(payload["scenario"], payload["enhanced"])
        self.assertEqual(planned["scenes"][0], expected)
        self.assertEqual(bad, original)
        self.assertEqual(len([r for r in backend.requests if r.get("stage") == "detail"]), 1)
        request = backend.requests[2]
        self.assertEqual(request["task"], "repair_prose_vocal_cues")
        self.assertEqual(request["original_text"], BAD)
        self.assertEqual(request["fixed_audio_policy"], expected["audio"])
        dialogue = next(e for e in expected["shots"][0]["events"] if e["kind"] == "dialogue")
        self.assertEqual(request["fixed_dialogue_events"], [dict(dialogue, shot_id=expected["shots"][0]["id"])])
        self.assertIn("do not delete them", backend.systems[2])
        result = compile_document(planned, render_scene(planned))
        self.assertIn("<d>[Japanese]おはよう。</d>", result.plan["shots"][0]["prompt"])

    def test_role_noun_can_be_rephrased_without_disabling_final_validator(self):
        payload, _, scene = neutral_payload(dialogue=False)
        guide = dict(copy.deepcopy(payload["scenario"]["actors"][0]), id="guide", name="Robin")
        payload["scenario"]["actors"].append(guide)
        payload["scenario"]["beats"][0]["actor_ids"].append("guide")
        scene["actor_ids"].append("guide")
        scene["visible_actor_ids"].append("guide")
        scene["summary"] = "@travelerが話し相手の@guideを見つめる。"
        target = target_for_error(scene, vocal_error(scene))
        backend = RecordingBackend([dict(text="@traveler looks toward @guide.")])
        repaired = repair_prose_field(backend, scene, payload, target, vocal_error(scene), {})
        self.assertEqual(repaired["summary"], "@traveler looks toward @guide.")
        self.assertEqual(repaired["audio"]["vocal"], "none")
        with self.assertRaisesRegex(VCError, "VOCAL_CUE"):
            _prose(scene["summary"], "neutral", scene["actor_ids"], cues=True)

    def test_second_partial_attempt_gets_failed_text_and_specific_feedback(self):
        payload, _, scene = neutral_payload()
        scene["summary"] = BAD
        backend = RecordingBackend([dict(text="@traveler whispers and walks to the gate."), dict(text=GOOD)])
        trace, events = {}, []
        with progress_scope(lambda event, **data: events.append((event, data))):
            repaired = repair_prose_field(backend, scene, payload, target_for_error(scene, vocal_error(scene)), vocal_error(scene), trace)
        self.assertEqual(repaired["summary"], GOOD)
        self.assertEqual(trace["repair_kind"], "vocal_cue")
        self.assertEqual(len(trace["attempts"]), 2)
        self.assertEqual(backend.requests[1]["validation_error"]["code"], "VOCAL_CUE")
        self.assertEqual(backend.requests[1]["original_text"], BAD)
        self.assertIn("whispers", backend.requests[1]["previous_response"])
        self.assertTrue(any("発声表現の文章修正" in data.get("label", "") for _, data in events))

    def test_authored_nonverbal_and_dialogue_events_are_never_repair_targets(self):
        payload, _, scene = neutral_payload(nonverbal=True)
        scene["summary"] = BAD
        original = copy.deepcopy(scene)
        backend = RecordingBackend([dict(text=GOOD)])
        repaired = repair_prose_field(backend, scene, payload, target_for_error(scene, vocal_error(scene)), vocal_error(scene), {})
        self.assertEqual({k: v for k, v in repaired.items() if k != "summary"},
                         {k: v for k, v in original.items() if k != "summary"})
        for event in scene["shots"][0]["events"]:
            if event["kind"] in {"dialogue", "vocal"}:
                self.assertIsNone(target_for_error(scene, vocal_error(scene, event["id"])))

    def test_scalar_prose_targets_are_supported_but_author_audio_controls_are_not(self):
        _, _, scene = neutral_payload()
        scene["sound_events"] = [dict(id="station_effect", kind="effect", start_ms=0, end_ms=100,
            trigger_event_id="station_action", text="Footsteps.")]
        scene["audio"]["effects_description"] = "Footsteps."
        for target in prose_targets(scene):
            self.assertEqual(target_for_error(scene, vocal_error(scene, target["path"])), target)
        for path in (scene["id"] + "/audio/nonverbal/instruction", "station_line", "arrival"):
            self.assertIsNone(target_for_error(scene, vocal_error(scene, path)))

    def test_patch_cannot_change_dialogue_permissions_or_scene_structure(self):
        payload, _, scene = neutral_payload()
        scene["summary"] = BAD
        target = target_for_error(scene, vocal_error(scene))
        for extra in (dict(shots=[]), dict(audio=dict(vocal="none")), dict(end_ms=1), dict(actor_ids=[])):
            with self.subTest(extra=extra):
                backend = RecordingBackend([dict(text=GOOD, **extra)])
                with self.assertRaisesRegex(VCError, "PROSE_REPAIR_SCHEMA"):
                    repair_prose_field(backend, scene, payload, target, vocal_error(scene), {})

    def test_visible_text_is_preserved_during_vocal_cleanup(self):
        payload, _, scene = neutral_payload()
        scene["summary"] = '@travelerが話しながら<visible>案内</visible>を見る。'
        backend = RecordingBackend([dict(text="@traveler looks at the sign."),
                                    dict(text="@traveler looks at <visible>案内</visible> on the sign.")])
        repaired = repair_prose_field(backend, scene, payload, target_for_error(scene, vocal_error(scene)), vocal_error(scene), {})
        self.assertIn("<visible>案内</visible>", repaired["summary"])
        self.assertEqual(backend.requests[1]["validation_error"]["code"], "VISIBLE_TEXT_CHANGED")

    def test_successful_partial_repair_is_cached_only_after_full_validation(self):
        payload, outlines, scene = neutral_payload()
        bad = dict(copy.deepcopy(scene), summary=BAD)
        backend = RecordingBackend([outlines, bad, dict(text=GOOD)])
        with tempfile.TemporaryDirectory(prefix="neutral_vocal_cache_", dir=Path(__file__).resolve().parent) as directory:
            planner = StoryTimelinePlanner(StageRunner(backend, directory))
            first = planner.plan(payload["scenario"], payload["enhanced"])
            second = planner.plan(payload["scenario"], payload["enhanced"])
        self.assertEqual(first, second)
        self.assertEqual(len(backend.requests), 3)

    def test_failed_partial_repair_remains_bounded_and_does_not_cache_invalid_scene(self):
        payload, outlines, scene = neutral_payload()
        bad = dict(copy.deepcopy(scene), summary=BAD)
        backend = RecordingBackend([outlines, bad, dict(text=BAD), dict(text=BAD), bad, bad])
        with tempfile.TemporaryDirectory(prefix="neutral_vocal_failure_", dir=Path(__file__).resolve().parent) as directory:
            with self.assertRaisesRegex(VCError, "LLM_RETRIES_EXHAUSTED"):
                StoryTimelinePlanner(StageRunner(backend, directory)).plan(payload["scenario"], payload["enhanced"])
            self.assertEqual(sum(r.get("task") == "repair_prose_vocal_cues" for r in backend.requests), 2)
            fresh = RecordingBackend([scene])
            result = StoryTimelinePlanner(StageRunner(fresh, directory)).plan(payload["scenario"], payload["enhanced"])
            self.assertEqual(result["scenes"][0], scene)
            self.assertEqual([r["stage"] for r in fresh.requests], ["detail"])

    def test_repair_context_changes_when_timing_speech_action_or_permissions_change(self):
        _, _, scene = neutral_payload()
        target = next(prose_targets(scene))
        context = target_context(scene, target)
        for key in ("timing", "speech", "action", "audio"):
            other = copy.deepcopy(scene)
            if key == "timing":
                other["end_ms"] += 1000
            elif key == "speech":
                other["shots"][0]["events"][-1]["text"] = "「こんにちは。」"
            elif key == "action":
                other["shots"][0]["events"][1]["text"] = "@traveler sits by the gate."
            else:
                other["audio"]["vocal"] = "none"
            self.assertNotEqual(target_context(other, target), context, key)
        other = dict(copy.deepcopy(scene), summary="A newly generated summary.")
        self.assertEqual(target_context(other, target), context)

    def test_accepted_summary_survives_retry_when_fixed_context_is_unchanged(self):
        payload, _, expected = neutral_payload()
        first = dict(copy.deepcopy(expected), summary=BAD)
        first["retention"][0]["mode"] = "partially_preserved"
        retry = dict(copy.deepcopy(expected), summary=BAD)
        backend = RecordingBackend([first, dict(text=GOOD), retry])
        def check(scene):
            _prose(scene["summary"], scene["id"] + "/summary", scene["actor_ids"], cues=True)
            if scene["retention"][0]["mode"] != "fully_preserved":
                raise VCError("NEUTRAL_RETRY", scene["id"], "neutral unrelated condition")
        result = StageRunner(backend).run("detail", expected["id"], payload, check)
        self.assertEqual(result, expected)
        self.assertEqual(len(backend.requests), 3)
        self.assertNotIn("task", backend.requests[-1])

    def test_visual_direction_vocal_violation_uses_existing_field_repair(self):
        payload, outlines, scene = neutral_payload()
        bad = dict(copy.deepcopy(scene), visual_directions=["@travelerが話しながら門へ進む。"])
        backend = RecordingBackend([outlines, bad, dict(visual_directions=["Frame @traveler by the gate."])])
        result = StoryTimelinePlanner(StageRunner(backend)).plan(payload["scenario"], payload["enhanced"])
        self.assertEqual(result["scenes"][0]["visual_directions"], ["Frame @traveler by the gate."])
        self.assertEqual(backend.requests[2]["task"], "repair_visual_directions")
        self.assertEqual(len(backend.requests), 3)

    def test_compiler_still_rejects_unrepaired_vocal_summary(self):
        payload, _, scene = neutral_payload()
        document = StoryTimelinePlanner._document(payload["scenario"], [scene], payload["enhanced"]["beats"][0]["required_events"])
        validate_story(document)
        document["scenes"][0]["summary"] = BAD
        with self.assertRaisesRegex(VCError, "VOCAL_CUE"):
            compile_document(document, render_scene(document))


if __name__ == "__main__":
    unittest.main()
