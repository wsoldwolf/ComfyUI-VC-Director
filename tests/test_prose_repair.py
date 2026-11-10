import copy
import unittest

from test_core import FakeBackend, fixture, temporary_directory, with_camera
from test_scene_roles import story
from vc_director.core.artifacts.actor_references import bind_scene_actor_references
from vc_director.core.errors import Diagnostic, VCError
from vc_director.core.protocols import StageRunner
from vc_director.core.protocols.prose_repair import (
    prose_targets, repair_prose_field, set_text, target_context, target_for_error)
from vc_director.core.utilities import canonical


class ProseRepairTests(unittest.TestCase):
    def scene_payload(self):
        doc, _ = story()
        scene = with_camera(doc["scenes"][0])
        payload = dict(scenario=doc)
        return scene, payload

    def test_summary_repair_changes_only_text_and_preserves_all_speech_and_timing(self):
        scene, payload = self.scene_payload()
        scene["summary"] = "@heroが端末を操作し、彼女がベンチに座る。"
        original = copy.deepcopy(scene)
        error = Diagnostic("AMBIGUOUS_ACTOR_REFERENCE", scene["id"] + "/summary", "Explicit IDs required")
        target = target_for_error(scene, error)
        backend = FakeBackend(["DETAIL\t1\t" + canonical(dict(text="@heroが端末を操作し、@heroがベンチに座る。"))])
        result = repair_prose_field(backend, scene, payload, target, error, {})
        self.assertEqual(scene, original)
        self.assertEqual({k: v for k, v in result.items() if k != "summary"},
                         {k: v for k, v in original.items() if k != "summary"})
        self.assertEqual(backend.requests[0]["allowed_actor_symbols"], ["@hero"])
        self.assertEqual(backend.requests[0]["original_text"], original["summary"])
        self.assertTrue(backend.requests[0]["fixed_event_owners"])

    def test_dialogue_is_never_a_repair_target(self):
        scene, _ = self.scene_payload()
        dialogue = next(e for sh in scene["shots"] for e in sh["events"] if e["kind"] == "dialogue")
        self.assertIsNone(target_for_error(scene, Diagnostic("UNKNOWN_ACTOR", dialogue["id"], "Do not touch speech")))
        self.assertFalse(any(dialogue["id"] in t["key"] for t in prose_targets(scene)))

    def test_patch_cannot_change_fields_or_add_timing(self):
        scene, payload = self.scene_payload()
        target = next(prose_targets(scene))
        error = Diagnostic("AMBIGUOUS_ACTOR_REFERENCE", target["path"], "Explicit IDs required")
        for extra in (dict(end_ms=1), dict(visible_actor_ids=[]), dict(shots=[])):
            backend = FakeBackend(["DETAIL\t1\t" + canonical(dict(text="Frame @hero.", **extra))])
            with self.assertRaisesRegex(VCError, "PROSE_REPAIR_SCHEMA"):
                repair_prose_field(backend, scene, payload, target, error, {})

    def test_failed_person_resolution_gets_feedback_with_same_original_field(self):
        scene, payload = self.scene_payload()
        scene["summary"] = "@hero watches the screen, then she sits down."
        target = next(prose_targets(scene))
        error = Diagnostic("AMBIGUOUS_ACTOR_REFERENCE", target["path"], "Explicit IDs required")
        backend = FakeBackend(["DETAIL\t1\t" + canonical(dict(text="@caller sits down.")),
                               "DETAIL\t1\t" + canonical(dict(text="@hero watches the screen and sits down."))])
        trace = {}
        result = repair_prose_field(backend, scene, payload, target, error, trace)
        self.assertIn("@hero", result["summary"])
        self.assertEqual(backend.requests[1]["validation_error"]["code"], "UNKNOWN_ACTOR")
        self.assertEqual(backend.requests[1]["original_text"], scene["summary"])
        self.assertEqual(len(trace["attempts"]), 2)

    def test_action_camera_and_sound_targets_preserve_structured_owners(self):
        scene, payload = self.scene_payload()
        for target in prose_targets(scene):
            if not target["key"].startswith("event:"):
                continue
            original = copy.deepcopy(scene)
            error = Diagnostic("AMBIGUOUS_ACTOR_REFERENCE", target["path"], "Explicit IDs required")
            backend = FakeBackend(["DETAIL\t1\t" + canonical(dict(text="Frame @hero holding the phone."))])
            repaired = repair_prose_field(backend, scene, payload, target, error, {})
            expected = copy.deepcopy(original)
            set_text(expected, target, "Frame @hero holding the phone.")
            self.assertEqual(repaired, expected)
            self.assertEqual(scene, original)

    def test_context_changes_with_visible_cast_and_event_owners(self):
        scene, _ = self.scene_payload()
        target = next(prose_targets(scene))
        context = target_context(scene, target)
        changed = copy.deepcopy(scene)
        changed["visible_actor_ids"] = ["caller"]
        self.assertNotEqual(target_context(changed, target), context)
        changed = copy.deepcopy(scene)
        changed["shots"][0]["events"][0]["actor_id"] = "caller"
        self.assertNotEqual(target_context(changed, target), context)

    def test_stage_repairs_summary_then_visual_directions_without_regenerating_scene(self):
        from test_planning_transport import PlanningTransportTests
        payload, base = PlanningTransportTests.silent_detail()
        payload.update(enhanced=fixture("enhanced-story"), locked_scene=None)
        caller = copy.deepcopy(payload["scenario"]["actors"][0])
        caller.update(id="caller", name="Caller")
        payload["scenario"]["actors"].append(caller)
        base.update(actor_ids=["hero", "caller"], visible_actor_ids=["hero"],
                    summary="@hero watches the screen and she sits down.", visual_directions=["An offscreen voice is heard."])
        original = copy.deepcopy(base)
        responses = ["DETAIL\t1\t" + canonical(base),
                     "DETAIL\t1\t" + canonical(dict(text="@hero watches the screen and sits down.")),
                     "DETAIL\t1\t" + canonical(dict(visual_directions=["Frame only @hero."]))]
        backend = FakeBackend(responses)
        def check(scene):
            bind_scene_actor_references(scene, payload["scenario"]["actors"])
            from vc_director.core.artifacts.validation import validate_visual_directions
            validate_visual_directions(scene)
        result = StageRunner(backend).run("detail", base["id"], payload, check)
        self.assertEqual(len(backend.requests), 3)
        self.assertEqual([r.get("task") for r in backend.requests[1:]],
                         ["repair_prose_actor_references", "repair_visual_directions"])
        self.assertEqual({k: v for k, v in result.items() if k not in {"summary", "visual_directions"}},
                         {k: v for k, v in original.items() if k not in {"summary", "visual_directions"}})

    def test_accepted_summary_survives_an_unrelated_whole_scene_retry(self):
        import json
        from pathlib import Path
        from test_planning_transport import PlanningTransportTests
        from vc_director.core.artifacts.validation import _prose
        payload, scene = PlanningTransportTests.silent_detail()
        payload.update(enhanced=fixture("enhanced-story"), locked_scene=None)
        caller = copy.deepcopy(payload["scenario"]["actors"][0])
        caller.update(id="caller", name="Caller")
        payload["scenario"]["actors"].append(caller)
        scene["actor_ids"] = ["hero", "caller"]
        scene["summary"] = "@hero holds a phone and she sits down."
        bad = copy.deepcopy(scene)
        action = next(e for s in bad["shots"] for e in s["events"] if e["kind"] == "action")
        action["text"] = "Display 「a title」."
        backend = FakeBackend(["DETAIL\t1\t" + canonical(bad),
            "DETAIL\t1\t" + canonical(dict(text="@hero holds a phone and sits down.")),
            "DETAIL\t1\t" + canonical(scene)])
        def check(value):
            bind_scene_actor_references(value, payload["scenario"]["actors"])
            for shot in value["shots"]:
                for event in shot["events"]:
                    _prose(event["text"], event["id"])
        with temporary_directory() as directory:
            result = StageRunner(backend, directory).run("detail", scene["id"], payload, check)
            self.assertEqual(result["summary"], "@hero holds a phone and sits down.")
            self.assertEqual(len(backend.requests), 3)
            log = json.loads(next(Path(directory, "diagnostics").glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(log["attempts"][0]["prose_repairs"][0]["status"], "field_accepted")
            self.assertIn("summary", log["attempts"][1]["reused_fields"])

    def test_failed_scalar_repairs_are_bounded_and_do_not_relax_the_check(self):
        from test_planning_transport import PlanningTransportTests
        payload, scene = PlanningTransportTests.silent_detail()
        payload.update(enhanced=fixture("enhanced-story"), locked_scene=None)
        caller = copy.deepcopy(payload["scenario"]["actors"][0])
        caller.update(id="caller", name="Caller")
        payload["scenario"]["actors"].append(caller)
        scene.update(actor_ids=["hero", "caller"], summary="@hero waits while she sits.")
        raw = "DETAIL\t1\t" + canonical(scene)
        patch = "DETAIL\t1\t" + canonical(dict(text="She sits."))
        backend = FakeBackend([raw, patch, patch, raw, raw])
        with self.assertRaisesRegex(VCError, "LLM_RETRIES_EXHAUSTED"):
            StageRunner(backend).run("detail", scene["id"], payload,
                lambda value: bind_scene_actor_references(value, payload["scenario"]["actors"]))
        self.assertEqual(len(backend.requests), 5)
        self.assertEqual(sum(r.get("task") == "repair_prose_actor_references" for r in backend.requests), 2)


if __name__ == "__main__":
    unittest.main()
