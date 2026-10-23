"""Timeline construction, scene detail and numeric retiming."""
import copy

from ..errors import require
from ..artifacts.schema import validate
from ..artifacts.validation import unique, validate_story


class StoryTimelinePlanner:
    def __init__(self, runner):
        self.runner = runner

    def plan(self, scenario, enhanced):
        validate_story(scenario)
        validate(enhanced, "enhanced_story")
        require(enhanced["scenario_sha256"] == scenario["source_sha256"], "ENHANCED_SOURCE", "/", "Enhancer scenario mismatch")
        outlines, scenes = [], []
        required = [e for b in enhanced["beats"] for e in b["required_events"]]
        for beat in scenario["beats"]:
            def check(result):
                accepted = outlines[:]
                local_required = []
                for outline in result["scenes"]:
                    require(outline["beat_id"] == beat["id"] and set(outline["actor_ids"]) == set(beat["actor_ids"]) and outline["background_id"] == beat["background_id"], "OUTLINE_BINDING", outline["id"], "Wrong beat, cast or background")
                    start = accepted[-1]["end_ms"] if accepted else 0
                    require(outline["start_ms"] == start and 0 < outline["end_ms"] - start <= 15000, "OUTLINE_TIMING", outline["id"], "Scenes must be contiguous and <=15 seconds")
                    require(outline["id"] not in {s["id"] for s in accepted} and set(outline["depends_on"]) <= {s["id"] for s in accepted}, "OUTLINE_DEPENDENCY", outline["id"], "Duplicate or forward dependency")
                    if outline["transition"] == "continue":
                        require(bool(accepted) and accepted[-1]["id"] in outline["depends_on"] and set(accepted[-1]["actor_ids"]) == set(outline["actor_ids"]) and accepted[-1]["background_id"] == outline["background_id"], "OUTLINE_TRANSITION", outline["id"], "Invalid continuation")
                    elif not accepted:
                        require(outline["transition"] == "cut", "OUTLINE_TRANSITION", outline["id"], "First Scene must be cut")
                    local_required.extend(outline["required_event_ids"])
                    accepted.append(outline)
                expected = [e["id"] for e in required if e["beat_id"] == beat["id"]]
                require(sorted(local_required) == sorted(expected), "OUTLINE_REQUIRED", beat["id"], "Every required event must be assigned once")
                if beat is scenario["beats"][-1] and scenario["duration_ms"] is not None:
                    require(accepted[-1]["end_ms"] == scenario["duration_ms"], "OUTLINE_DURATION", beat["id"], "Final outline does not match authored duration")
            result = self.runner.run("outline", beat["id"], dict(scenario=scenario, enhanced=enhanced, accepted_outlines=outlines[:]), check)
            outlines.extend(result["scenes"])
        for outline in outlines:
            beat = next(b for b in scenario["beats"] if b["id"] == outline["beat_id"])

            def check(scene):
                require(all(scene[k] == v for k, v in outline.items()), "OUTLINE_CHANGED", scene["id"], "Detail changed approved outline fields")
                # Validate this Scene with a narrowed, zero-based local document.
                local_scene = copy.deepcopy(scene)
                local_scene.update(start_ms=0, end_ms=scene["end_ms"] - scene["start_ms"], transition="cut", depends_on=[])
                local_beat = copy.deepcopy(beat)
                declared = [e["dialogue_id"] for s in scene["shots"] for e in s["events"] if e["kind"] == "dialogue"]
                used = [e["dialogue_id"] for s in scenes for sh in s["shots"] for e in sh["events"] if e["kind"] == "dialogue"]
                require(not set(declared) & set(used), "DIALOGUE_COVERAGE", scene["id"], "Dialogue repeated across Scenes")
                require(set(declared) <= {d["id"] for d in beat["dialogues"]}, "DIALOGUE_SOURCE", scene["id"], "Unknown dialogue")
                last = not any(o["beat_id"] == beat["id"] for o in outlines[outlines.index(outline) + 1:])
                if last:
                    require(set(declared) | set(used) >= {d["id"] for d in beat["dialogues"]}, "DIALOGUE_COVERAGE", scene["id"], "Missing authored dialogue")
                local_beat["dialogues"] = [d for d in beat["dialogues"] if d["id"] in declared]
                local_doc = self._document(scenario, [local_scene], [e for e in required if e["id"] in scene["required_event_ids"]])
                local_doc["beats"] = [local_beat]
                validate_story(local_doc)
                all_shots = [s["id"] for sc in scenes for s in sc["shots"]]
                all_events = [e["id"] for sc in scenes for s in sc["shots"] for e in s["events"]] + [e["id"] for sc in scenes for e in sc["sound_events"]]
                require(not set(all_shots) & {s["id"] for s in scene["shots"]}, "DUPLICATE_ID", scene["id"], "Shot ID reused")
                require(not set(all_events) & ({e["id"] for s in scene["shots"] for e in s["events"]} | {e["id"] for e in scene["sound_events"]}), "DUPLICATE_ID", scene["id"], "Event ID reused")
            # Keep every earlier dialogue assignment in this beat, plus required state
            # dependencies. Unrelated beats' detailed prose is optional context.
            previous_scenes = [s for s in scenes if s["beat_id"] == outline["beat_id"] or s["id"] in outline["depends_on"] or s is scenes[-1]]
            payload = dict(scenario=scenario, enhanced=enhanced, outline=outline, previous_scenes=previous_scenes, locked_scene=None)
            scenes.append(self.runner.run("detail", outline["id"], payload, check))
        return validate_story(self._document(scenario, scenes, required))

    @staticmethod
    def _document(scenario, scenes, required):
        document = {k: copy.deepcopy(v) for k, v in scenario.items() if k not in {"schema", "source_sha256", "duration_ms"}}
        document.update(schema="VCD_SCENE_DOCUMENT_V1", scenario_sha256=scenario["source_sha256"], duration_ms=scenes[-1]["end_ms"], scenes=scenes, required_events=required)
        return document

    def retime(self, document, changed_scene_ids):
        validate_story(document)
        result = copy.deepcopy(document)
        targets = {s["id"]: s for s in result["scenes"]}
        require(set(changed_scene_ids) <= set(targets), "RETIME_TARGET", "/", "Unknown Scene")
        for scene_id in changed_scene_ids:
            scene = targets[scene_id]
            require("timing" not in scene["locked"], "LOCKED_TIMING", scene_id, "Scene timing is fixed")

            def check(timing):
                require(timing["scene_id"] == scene_id, "RETIME_TARGET", scene_id, "Wrong target")
                duration = timing["end_ms"] - timing["start_ms"]
                require(0 < duration <= 15000, "RETIME_DURATION", scene_id, "Scene must be within 15 seconds")
                require([s["id"] for s in timing["shots"]] == [s["id"] for s in scene["shots"]], "RETIME_STRUCTURE", scene_id, "Shot structure changed")
                endpoint = 0
                for old, new in zip(scene["shots"], timing["shots"]):
                    require(new["start_ms"] == endpoint and endpoint < new["end_ms"] <= duration, "RETIME_SHOT", scene_id, "Shots must cover the Scene")
                    endpoint = new["end_ms"]
                    require([e["id"] for e in new["events"]] == [e["id"] for e in old["events"]], "RETIME_STRUCTURE", scene_id, "Event structure changed")
                    require(all(new["start_ms"] <= e["start_ms"] < e["end_ms"] <= new["end_ms"] for e in new["events"]), "RETIME_EVENT", scene_id, "Event must fit its Shot")
                require(endpoint == duration, "RETIME_SHOT", scene_id, "Shots must cover the Scene")
                require([e["id"] for e in timing["sound_events"]] == [e["id"] for e in scene["sound_events"]], "RETIME_STRUCTURE", scene_id, "Sound structure changed")
                require(all(0 <= e["start_ms"] < e["end_ms"] <= duration for e in timing["sound_events"]), "RETIME_SOUND", scene_id, "Sound must fit its Scene")

            timing = self.runner.run("retime", scene_id, dict(document=document, changed_scene_ids=list(changed_scene_ids)), check)
            scene.update(start_ms=timing["start_ms"], end_ms=timing["end_ms"])
            for old, new in zip(scene["shots"], timing["shots"]):
                old.update(start_ms=new["start_ms"], end_ms=new["end_ms"])
                for event, t in zip(old["events"], new["events"]):
                    event.update(start_ms=t["start_ms"], end_ms=t["end_ms"])
            for old, new in zip(scene["sound_events"], timing["sound_events"]):
                old.update(start_ms=new["start_ms"], end_ms=new["end_ms"])
        result["duration_ms"] = result["scenes"][-1]["end_ms"]
        return validate_story(result)
