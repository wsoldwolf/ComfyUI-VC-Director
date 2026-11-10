"""Timeline construction, scene detail and numeric retiming."""
import copy
from ..utilities.progress import emit_progress

from ..errors import VCError, require
from ..artifacts.schema import validate
from ..artifacts.validation import unique, validate_story
from ..artifacts.transitions import validate_scene_transition
from ..artifacts.actor_references import bind_scene_actor_references
from ..artifacts.identifiers import resolve_required_event_ids
from ..artifacts.coverage import validate_required_coverage
from ..vision.image_facts import apply_image_facts, apply_scene_image_facts
from .camera import validate_camera_plan
from .identifiers import resolve_generated_ids
from .budget import allocate_beat_windows


class StoryTimelinePlanner:
    def __init__(self, runner):
        self.runner = runner

    def plan(self, scenario, enhanced, *, image_facts=()):
        require(scenario.get("schema") == "VCD_SCENARIO_V1", "INPUT_KIND", "/schema",
                "TimelinePlannerにはVCD_SCENARIO_EMD_V1のシナリオが必要です。シーンEMDはsaved_sceneでコンパイルしてください。")
        scenario = apply_image_facts(scenario, image_facts)
        validate_story(scenario)
        validate(enhanced, "enhanced_story")
        require(enhanced["scenario_sha256"] == scenario["source_sha256"], "ENHANCED_SOURCE", "/", "Enhancer scenario mismatch")
        # Accept old EnhancedStory caches through the same allocation boundary.
        # Do this before outlines introduce required_event_ids references.
        enhanced = copy.deepcopy(enhanced)
        remapped = resolve_required_event_ids(scenario, enhanced)
        if remapped:
            self.runner.trace.append(dict(stage="required_event_ids", source="planner", remapped=remapped))
        scenes = []
        required = [e for b in enhanced["beats"] for e in b["required_events"]]
        outlines = self._plan_outlines(scenario, enhanced)
        for scene_index, outline in enumerate(outlines):
            emit_progress("unit", stage="detail", index=scene_index, total=len(outlines), label=outline["id"])
            beat = next(b for b in scenario["beats"] if b["id"] == outline["beat_id"])
            last = not any(o["beat_id"] == beat["id"] for o in outlines[outlines.index(outline) + 1:])
            used_dialogues = {e["dialogue_id"] for previous in scenes for shot in previous["shots"]
                              for e in shot["events"] if e["kind"] == "dialogue"}
            required_dialogue_ids = [d["id"] for d in beat["dialogues"] if d["id"] not in used_dialogues] if last else []

            def check(scene):
                require(all(scene[k] == v for k, v in outline.items()), "OUTLINE_CHANGED", scene["id"], "Detail changed approved outline fields")
                remapped = resolve_generated_ids(scene, scenes, [item["id"] for item in outlines],
                    authored_dialogue_ids=[d["id"] for b in scenario["beats"] for d in b["dialogues"]])
                if remapped:
                    self.runner.trace.append(dict(stage="generated_ids", scene_id=scene["id"], remapped=remapped))
                bind_scene_actor_references(scene, scenario["actors"])
                # Validate this Scene with a narrowed, zero-based local document.
                local_scene = copy.deepcopy(scene)
                local_scene.update(start_ms=0, end_ms=scene["end_ms"] - scene["start_ms"], transition="cut", depends_on=[])
                local_beat = copy.deepcopy(beat)
                declared = [e["dialogue_id"] for s in scene["shots"] for e in s["events"] if e["kind"] == "dialogue"]
                used = [e["dialogue_id"] for s in scenes for sh in s["shots"] for e in sh["events"] if e["kind"] == "dialogue"]
                require(not set(declared) & set(used), "DIALOGUE_COVERAGE", scene["id"], "Dialogue repeated across Scenes")
                require(set(declared) <= {d["id"] for d in beat["dialogues"]}, "DIALOGUE_SOURCE", scene["id"], "Unknown dialogue")
                if last:
                    missing = [d["id"] for d in beat["dialogues"] if d["id"] not in set(declared) | set(used)]
                    require(not missing, "DIALOGUE_COVERAGE", scene["id"],
                            f"Missing authored dialogue IDs: {missing}. Emit each as a DIALOGUE event exactly once, "
                            "preserving its authored id, dialogue_id, text, actor_id and language. "
                            "Keep all required actions and camera work; do not replace speech with a summary or CAMERA.")
                local_beat["dialogues"] = [d for d in beat["dialogues"] if d["id"] in declared]
                local_doc = self._document(scenario, [local_scene], [e for e in required if e["id"] in scene["required_event_ids"]])
                local_doc["beats"] = [local_beat]
                validate_story(local_doc)
                validate_camera_plan(scene)
                all_shots = [s["id"] for sc in scenes for s in sc["shots"]]
                all_events = [e["id"] for sc in scenes for s in sc["shots"] for e in s["events"]] + [e["id"] for sc in scenes for e in sc["sound_events"]]
                require(not set(all_shots) & {s["id"] for s in scene["shots"]}, "DUPLICATE_ID", scene["id"], "Shot ID reused")
                require(not set(all_events) & ({e["id"] for s in scene["shots"] for e in s["events"]} | {e["id"] for e in scene["sound_events"]}), "DUPLICATE_ID", scene["id"], "Event ID reused")
            # Keep every earlier dialogue assignment in this beat, plus required state
            # dependencies. Unrelated beats' detailed prose is optional context.
            previous_scenes = [s for s in scenes if s["beat_id"] == outline["beat_id"] or s["id"] in outline["depends_on"] or s is scenes[-1]]
            payload = dict(scenario=scenario, enhanced=enhanced, outline=outline, previous_scenes=previous_scenes,
                           locked_scene=None, required_dialogue_ids=required_dialogue_ids)
            scene = self.runner.run("detail", outline["id"], payload, check)
            scenes.append(apply_scene_image_facts(scene, scenario, image_facts))
            emit_progress("done", stage="detail", index=scene_index, total=len(outlines))
        return validate_story(self._document(scenario, scenes, required))

    def _plan_outlines(self, scenario, enhanced):
        required = [e for b in enhanced["beats"] for e in b["required_events"]]
        outlines, windows = [], None
        total = scenario["duration_ms"]
        index = 0
        while index < len(scenario["beats"]):
            beat = scenario["beats"][index]
            emit_progress("unit", stage="outline", index=index, total=len(scenario["beats"]), label=beat["id"])
            window = windows[index] if windows else None

            def check(result):
                accepted = outlines[:]
                for outline in result["scenes"]:
                    require(outline["beat_id"] == beat["id"] and set(outline["actor_ids"]) == set(beat["actor_ids"]) and outline["background_id"] == beat["background_id"], "OUTLINE_BINDING", outline["id"], "Wrong beat, cast or background")
                    require(set(outline.get("prop_ids", [])) == set(beat.get("prop_ids", [])),
                            "PROP_BINDING", outline["id"], "Preserve the beat's available prop IDs")
                    start = accepted[-1]["end_ms"] if accepted else 0
                    limit = min(start + 15000, window["end_ms"]) if window else start + 15000
                    require(outline["start_ms"] == start and start < outline["end_ms"] <= limit,
                            "OUTLINE_TIMING", outline["id"],
                            f"Sceneの時間配置が不正です。必要な開始: {start}ms、終了の許容範囲: ({start}, {limit}]ms。"
                            f"実際: {outline['start_ms']}..{outline['end_ms']}ms"
                            f"（長さ{outline['end_ms'] - outline['start_ms']}ms、上限15000ms）。"
                            "合計尺を変えず、空白・重複のない15秒以内のSceneへ分割してください。")
                    require(outline["id"] not in {s["id"] for s in accepted} and set(outline["depends_on"]) <= {s["id"] for s in accepted}, "OUTLINE_DEPENDENCY", outline["id"], "Duplicate or forward dependency")
                    validate_scene_transition(outline, accepted[-1] if accepted else None, beat,
                                              code="OUTLINE_TRANSITION", require_state_coverage=True)
                    accepted.append(outline)
                expected = [e["id"] for e in required if e["beat_id"] == beat["id"]]
                validate_required_coverage(result["scenes"], expected, beat["id"])
                end = window["end_ms"] if window else total if index == len(scenario["beats"]) - 1 else None
                if end is not None:
                    require(accepted[-1]["end_ms"] == end, "OUTLINE_DURATION", beat["id"],
                            f"Outline for this beat must end at {end}ms; received {accepted[-1]['end_ms']}ms "
                            f"(difference {end - accepted[-1]['end_ms']}ms). "
                            "Replan this beat with enough contiguous Scenes of at most 15000ms each. "
                            "Preserve required events; do not add dialogue or new story events to fill time.")

            payload = dict(scenario=scenario, enhanced=enhanced, accepted_outlines=outlines[:])
            if windows:
                payload["beat_windows"] = windows
            result = self.runner.run("outline", beat["id"], payload, check)
            outlines.extend(result["scenes"])
            remaining_beats = len(scenario["beats"]) - index - 1
            if windows is None and total is not None and remaining_beats and total - outlines[-1]["end_ms"] < remaining_beats:
                # No detail exists yet: discard the invalid allocation, then
                # replan all beats under a validated global budget exactly once.
                windows = allocate_beat_windows(self.runner, scenario, enhanced, outlines)
                emit_progress("restart", label="時間配分を再構成中")
                outlines, index = [], 0
                continue
            emit_progress("done", stage="outline", index=index, total=len(scenario["beats"]))
            index += 1
        return outlines

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
            # Preserve complete camera plans while accepting optional legacy coverage.
            try:
                validate_camera_plan(scene)
                camera_planned = True
            except VCError:
                camera_planned = False

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
                if camera_planned:
                    camera_scene = dict(id=scene_id, shots=[])
                    for old, new in zip(scene["shots"], timing["shots"]):
                        camera_scene["shots"].append(dict(new, events=[dict(event, start_ms=t["start_ms"], end_ms=t["end_ms"])
                            for event, t in zip(old["events"], new["events"])]))
                    validate_camera_plan(camera_scene)

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
