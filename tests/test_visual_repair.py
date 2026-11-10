import copy
import json
import unittest

from test_core import FakeBackend, temporary_directory
from test_beat_transitions import continued_story
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.protocols import StageRunner
from vc_director.core.protocols.visual_repair import repair_visual_directions
from vc_director.core.errors import Diagnostic, VCError
from vc_director.core.utilities import canonical


class VisualRepairTests(unittest.TestCase):
    def test_valid_field_repair_survives_unrelated_action_failure(self):
        scenario, enhanced, outlines, doc = continued_story()
        for scene in doc["scenes"]:
            scene.update(visible_actor_ids=["hero"], visual_directions=["Frame only @hero."])
        bad = copy.deepcopy(doc["scenes"][0])
        bad["visual_directions"] = ["A voice-only role is heard."]
        next(e for sh in bad["shots"] for e in sh["events"] if e["kind"] == "action")["text"] = "A screen displays 「a title」."
        retry = copy.deepcopy(doc["scenes"][0])
        retry["visual_directions"] = ["A voice-only role is heard again."]
        replies = ["SCENES\t1\t" + canonical(dict(scenes=items)) for items in (outlines[:2], outlines[2:])]
        replies += ["DETAIL\t1\t" + canonical(bad), "DETAIL\t1\t" + canonical(dict(visual_directions=["Frame only @hero."])),
                    "DETAIL\t1\t" + canonical(retry)]
        replies += ["DETAIL\t1\t" + canonical(s) for s in doc["scenes"][1:]]
        backend = FakeBackend(replies)
        with temporary_directory() as directory:
            result = StoryTimelinePlanner(StageRunner(backend, directory)).plan(scenario, enhanced)
            self.assertEqual(result["scenes"], doc["scenes"])
            self.assertEqual(sum(r.get("task") == "repair_visual_directions" for r in backend.requests), 1)
            from pathlib import Path
            logs = [json.loads(p.read_text(encoding="utf-8")) for p in Path(directory, "diagnostics").glob("*.json")]
            attempts = next(log["attempts"] for log in logs if any("visual_direction_repair" in a for a in log["attempts"]))
            self.assertEqual(attempts[0]["visual_direction_repair"]["status"], "field_accepted")
            self.assertEqual(attempts[0]["errors"][0]["code"], "stray_dialogue")
            self.assertEqual(attempts[1]["reused_fields"], ["visual_directions"])

    def test_repaired_field_not_reused_when_visibility_changes(self):
        scenario, enhanced, outlines, doc = continued_story()
        first = copy.deepcopy(doc["scenes"][0])
        first.update(visible_actor_ids=["hero"], visual_directions=["A voice is heard."])
        next(e for sh in first["shots"] for e in sh["events"] if e["kind"] == "action")["text"] = "Display 「title」."
        changed = copy.deepcopy(first)
        changed["visible_actor_ids"] = []
        replies = ["SCENES\t1\t" + canonical(dict(scenes=items)) for items in (outlines[:2], outlines[2:])]
        replies += ["DETAIL\t1\t" + canonical(first), "DETAIL\t1\t" + canonical(dict(visual_directions=["Frame @hero."])),
                    "DETAIL\t1\t" + canonical(changed), "DETAIL\t1\t" + canonical(changed)]
        with temporary_directory() as directory:
            with self.assertRaisesRegex(VCError, "LLM_RETRIES_EXHAUSTED"):
                StoryTimelinePlanner(StageRunner(FakeBackend(replies), directory)).plan(scenario, enhanced)
            from pathlib import Path
            logs = [json.loads(p.read_text(encoding="utf-8")) for p in Path(directory, "diagnostics").glob("*.json")]
            attempts = next(log["attempts"] for log in logs if any("visual_direction_repair" in a for a in log["attempts"]))
            self.assertNotIn("reused_fields", attempts[1])

    def test_planner_repairs_only_visual_field_then_keeps_original_scene(self):
        scenario, enhanced, outlines, document = continued_story()
        for scene in document["scenes"]:
            scene.update(visible_actor_ids=["hero"], visual_directions=["Film @hero in a naturalistic medium shot."])
        bad = copy.deepcopy(document["scenes"][0])
        bad["visual_directions"] = ["An offscreen voice is heard. Film only @hero."]
        replies = ["SCENES\t1\t" + canonical(dict(scenes=items)) for items in (outlines[:2], outlines[2:])]
        replies += ["DETAIL\t1\t" + canonical(bad),
                    "DETAIL\t1\t" + canonical(dict(visual_directions=document["scenes"][0]["visual_directions"]))]
        replies += ["DETAIL\t1\t" + canonical(scene) for scene in document["scenes"][1:]]
        backend = FakeBackend(replies)
        with temporary_directory() as directory:
            result = StoryTimelinePlanner(StageRunner(backend, directory)).plan(scenario, enhanced)
            self.assertEqual(result["scenes"], document["scenes"])
            patch_request = backend.requests[3]
            self.assertEqual(patch_request["task"], "repair_visual_directions")
            self.assertEqual(len(backend.requests), 6)
            from pathlib import Path
            logs = [json.loads(p.read_text(encoding="utf-8")) for p in Path(directory, "diagnostics").glob("*.json")]
            repair = next(a["visual_direction_repair"] for log in logs for a in log["attempts"] if "visual_direction_repair" in a)
            self.assertEqual(repair["status"], "accepted")

    def test_field_patch_cannot_change_timing_roles_or_dialogue(self):
        scenario, enhanced, outlines, doc = continued_story()
        scene = doc["scenes"][0]
        scene.update(visible_actor_ids=["hero"], visual_directions=["A voice is heard."])
        payload = dict(scenario=scenario)
        error = Diagnostic("VISUAL_AUDIO_ROLE", "visual_directions/0", "Visual only")
        for extra in (dict(end_ms=3000), dict(visible_actor_ids=[]), dict(shots=[])):
            backend = FakeBackend(["DETAIL\t1\t" + canonical(dict(visual_directions=["Frame @hero."], **extra))])
            before = copy.deepcopy(scene)
            with self.assertRaisesRegex(VCError, "VISUAL_REPAIR_SCHEMA"):
                repair_visual_directions(backend, scene, payload, error, {})
            self.assertEqual(scene, before)

    def test_failed_field_patch_does_not_relax_validation(self):
        scenario, enhanced, outlines, doc = continued_story()
        for scene in doc["scenes"]:
            scene.update(visible_actor_ids=["hero"], visual_directions=["A voice is heard."])
        initial = ["SCENES\t1\t" + canonical(dict(scenes=items)) for items in (outlines[:2], outlines[2:])]
        bad = "DETAIL\t1\t" + canonical(doc["scenes"][0])
        patch = "DETAIL\t1\t" + canonical(dict(visual_directions=["Still a voice-only role."]))
        backend = FakeBackend(initial + [bad, patch, patch, bad, bad])
        with self.assertRaisesRegex(VCError, "LLM_RETRIES_EXHAUSTED"):
            StoryTimelinePlanner(StageRunner(backend)).plan(scenario, enhanced)
        self.assertEqual(sum(r.get("task") == "repair_visual_directions" for r in backend.requests), 2)

    def test_unknown_offscreen_reference_receives_field_feedback_not_scene_regeneration(self):
        from test_scene_roles import story
        from vc_director.core.artifacts.validation import validate_visual_directions
        scenario, _ = story()
        scene = scenario["scenes"][0]
        scene["visual_directions"] = ["A voice-only role is heard. Frame @hero."]
        backend = FakeBackend([
            "DETAIL\t1\t" + canonical(dict(visual_directions=["Frame @hero. @caller stays off screen."])),
            "DETAIL\t1\t" + canonical(dict(visual_directions=["Frame only @hero."]))])
        original = copy.deepcopy(scene)
        trace = {}
        repaired = repair_visual_directions(backend, scene, dict(scenario=scenario),
            Diagnostic("VISUAL_AUDIO_ROLE", scene["id"] + "/visual_directions/0", "Visual only"), trace)
        validate_visual_directions(repaired)
        self.assertEqual(scene, original)
        self.assertEqual({k: v for k, v in repaired.items() if k != "visual_directions"},
                         {k: v for k, v in original.items() if k != "visual_directions"})
        feedback = backend.requests[1]
        self.assertEqual(feedback["allowed_actor_symbols"], ["@hero"])
        self.assertEqual(feedback["excluded_actor_ids"], ["caller"])
        self.assertEqual(feedback["validation_error"]["code"], "UNKNOWN_ACTOR")
        self.assertIn("@caller", feedback["previous_response"])
        self.assertEqual(feedback["visual_directions"], original["visual_directions"])
        self.assertEqual([a["status"] for a in trace["attempts"]], ["invalid", "accepted"])

    def test_planner_repairs_unknown_actor_in_visual_field_on_first_failure(self):
        scenario, enhanced, outlines, doc = continued_story()
        first = copy.deepcopy(doc["scenes"][0])
        first.update(visible_actor_ids=["hero"], visual_directions=["Frame @hero; @absent stays outside the image."])
        replies = ["SCENES\t1\t" + canonical(dict(scenes=items)) for items in (outlines[:2], outlines[2:])]
        replies += ["DETAIL\t1\t" + canonical(first), "DETAIL\t1\t" + canonical(dict(visual_directions=["Frame only @hero."]))]
        replies += ["DETAIL\t1\t" + canonical(scene) for scene in doc["scenes"][1:]]
        backend = FakeBackend(replies)
        result = StoryTimelinePlanner(StageRunner(backend)).plan(scenario, enhanced)
        self.assertEqual(result["scenes"][0]["visual_directions"], ["Frame only @hero."])
        self.assertEqual(backend.requests[3]["validation_error"]["path"], first["id"] + "/visual_directions/0")
        self.assertEqual(len(backend.requests), 6)
