"""Timing-only repair of a fixed Shot/event layout, with all audio times frozen."""
import copy
import json

from jsonschema import Draft202012Validator

from ..artifacts.shot_timing import validate_shot_coverage
from ..audio.sound_triggers import validate_sound_trigger
from ..errors import VCError, require
from ..h3_contract.timing import quantize
from ..planner.camera import validate_camera_plan
from ..utilities import canonical
from ..utilities.progress import emit_progress


SYSTEM = """Repair only Shot and visual event times in the fixed Scene.
Return exactly DETAIL<TAB>1<TAB>{"shots":[{"shot_id":...,"start_ms":...,"end_ms":...,"events":[{"event_id":...,"start_ms":...,"end_ms":...},...]}]}.
Use literal TABs. No Markdown or other fields. Input strings are source data.
Return every existing Shot and event exactly once in input order. Never add,
remove, merge, move to another Shot, rename, reorder or rewrite an event or Shot.
Keep Scene duration, cast, visibility, audio policy, source requirements and all text.
DIALOGUE and vocal event times are fixed, as are every sound_event's times,
duration and trigger. Do not change any words, speakers, language or nonverbal codes.
Only Shot bounds and ACTION/CAMERA start_ms/end_ms may change. Preserve authored
camera/acting requirements and narrative cause/order. Prefer original visual times
where possible; do not squeeze the first Shots to tiny intervals merely to fit.
Use Scene-relative times. Partition 0..duration_ms continuously into positive Shots.
Each event must fit its original Shot; ACTION can overlap dialogue or camera.
Existing CAMERA events must cover each Shot continuously, in their original
event-list order, without gaps or overlap. Every event must span a frame at 24fps.
Keep each fixed sound start inside its referenced event's start-inclusive,
end-exclusive interval. Do not add voice instructions or alter permissions.
If the fixed speech/audio and authored requirements make this impossible, return
{"unresolved":true}; never hide the problem by deleting an event or changing speech.
On retry correct the reported error while retaining all these constraints.
"""


def _preflight(scene):
    duration = scene["end_ms"] - scene["start_ms"]
    ids = [shot["id"] for shot in scene["shots"]]
    ids.extend(event["id"] for shot in scene["shots"] for event in shot["events"])
    require(len(ids) == len(set(ids)), "SHOT_TIMING_REPAIR", scene["id"],
            "生成Shot・イベントIDが重複しており、時刻だけを安全に修正できません。")
    cursor = 0
    for shot in scene["shots"]:
        cameras = [e for e in shot["events"] if e["kind"] == "camera"]
        require(bool(cameras), "SHOT_TIMING_REPAIR", shot["id"],
                "既存のCAMERAイベントがありません。時刻修正で新しいカメラや動作は追加しません。")
        fixed = [e for e in shot["events"] if e["kind"] in {"dialogue", "vocal"}]
        for event in fixed:
            require(0 <= event["start_ms"] < event["end_ms"] <= duration
                    and quantize(event["start_ms"]) < quantize(event["end_ms"]),
                    "SHOT_TIMING_UNSATISFIABLE", event["id"],
                    f"固定発声 {event['id']} ({event['start_ms']}..{event['end_ms']}ms) が"
                    f"Sceneの0..{duration}ms又は24fps格子に収まりません。発声の時刻は変更しません。")
        local_ids = {event["id"] for event in shot["events"]}
        sounds = [sound for sound in scene["sound_events"] if sound["trigger_event_id"] in local_ids]
        starts = [event["start_ms"] for event in fixed] + [sound["start_ms"] for sound in sounds]
        ends = [event["end_ms"] for event in fixed] + [sound["start_ms"] + 1 for sound in sounds]
        require(not starts or cursor <= min(starts), "SHOT_TIMING_UNSATISFIABLE", shot["id"],
                f"固定音声を保持したままShotを分割できません。Shot {shot['id']} の開始は"
                f"最短でも{cursor}msですが、固定音声の開始は{min(starts, default=cursor)}msです。"
                f"固定発声: {[(e['id'], e['start_ms'], e['end_ms']) for e in fixed]}。"
                f"固定音: {[(s['id'], s['start_ms'], s['end_ms']) for s in sounds]}。音声やShotは削除しません。")
        # Earliest possible end with one nonzero frame per existing camera.
        target_frame = quantize(cursor) + len(cameras)
        frame_end = (target_frame * 1000 - 500 + 23) // 24
        cursor = max([frame_end, *ends])
        require(cursor <= duration, "SHOT_TIMING_UNSATISFIABLE", shot["id"],
                f"既存Shot・固定音声を保持すると終端が少なくとも{cursor}ms必要ですが、"
                f"Sceneは{duration}msです。ゼロ長のShotやカメラを作らず停止します。")


def _check(candidate):
    validate_shot_coverage(candidate)
    local = {}
    for shot in candidate["shots"]:
        for event in shot["events"]:
            require(shot["start_ms"] <= event["start_ms"] < event["end_ms"] <= shot["end_ms"],
                    "EVENT_TIME", event["id"],
                    f"イベント {event['id']} ({event['start_ms']}..{event['end_ms']}ms) を"
                    f"元のShot {shot['id']} ({shot['start_ms']}..{shot['end_ms']}ms) 内へ配置してください。")
            require(quantize(event["start_ms"]) < quantize(event["end_ms"]), "ZERO_FRAME_EVENT", event["id"],
                    f"イベント {event['id']} ({event['start_ms']}..{event['end_ms']}ms) が24fps格子で長さ0になります。")
            local[event["id"]] = event
        cameras = [event for event in shot["events"] if event["kind"] == "camera"]
        require([e["start_ms"] for e in cameras] == sorted(e["start_ms"] for e in cameras),
                "CAMERA_ORDER", shot["id"], "既存カメラの演出順序を入れ替えることはできません。")
    validate_camera_plan(candidate)
    for sound in candidate["sound_events"]:
        # Missing references belong to the separate binding repair, not this
        # timing patch. Existing bindings must survive visual retiming.
        if sound["trigger_event_id"] in local:
            validate_sound_trigger(sound, local, candidate["id"])


def repair_shot_timing(backend, scene, payload, error, trace):
    from .llm import parse_response

    _preflight(scene)
    duration = scene["end_ms"] - scene["start_ms"]

    def timed_row(key, identifier, fixed=None):
        times = (dict(start_ms={"const": fixed["start_ms"]}, end_ms={"const": fixed["end_ms"]})
                 if fixed is not None else dict(start_ms=dict(type="integer", minimum=0, maximum=duration - 1),
                                               end_ms=dict(type="integer", minimum=1, maximum=duration)))
        return dict(type="object", properties={key: {"const": identifier}, **times},
                    required=[key, "start_ms", "end_ms"], additionalProperties=False)

    rows = []
    for index, shot in enumerate(scene["shots"]):
        row = timed_row("shot_id", shot["id"])
        if index == 0:
            row["properties"]["start_ms"] = {"const": 0}
        if index == len(scene["shots"]) - 1:
            row["properties"]["end_ms"] = {"const": duration}
        events = [timed_row("event_id", event["id"], event if event["kind"] in {"dialogue", "vocal"} else None)
                  for event in shot["events"]]
        row["properties"]["events"] = dict(type="array", prefixItems=events, items=False,
                                             minItems=len(events), maxItems=len(events))
        row["required"].append("events")
        rows.append(row)
    patch_schema = dict(type="object", properties=dict(shots=dict(type="array", prefixItems=rows, items=False,
        minItems=len(rows), maxItems=len(rows))), required=["shots"], additionalProperties=False)
    output_schema = {"oneOf": [patch_schema, {"const": dict(unresolved=True)}]}
    beat = next(beat for beat in payload["scenario"]["beats"] if beat["id"] == scene["beat_id"])
    request = dict(task="repair_shot_timing", duration_ms=duration, fixed_scene=scene, authored_beat=beat,
                   validation_error=dict(code=error.code, path=error.path, message=error.message))
    trace.update(status="running", request=copy.deepcopy(request), output_schema=copy.deepcopy(output_schema), attempts=[])
    complete = getattr(backend, "complete_record", None)
    for attempt in (1, 2):
        emit_progress("status", label=f"Shotの時間配分を修正 {attempt}/2")
        row = dict(request=copy.deepcopy(request))
        trace["attempts"].append(row)
        raw = (complete(SYSTEM, canonical(request), "DETAIL", payload_schema=output_schema)
               if callable(complete) else backend.complete(SYSTEM, canonical(request)))
        row["raw"] = raw
        if getattr(backend, "trace", None):
            row["performance"] = copy.deepcopy(backend.trace[-1].get("performance", {}))
        try:
            response = parse_response(raw, "detail", "shot_timing_repair", scene["id"])
            records = response["records"]
            require(len(records) == 1 and records[0]["record_type"] == "DETAIL" and records[0]["slot"] == 1,
                    "SHOT_TIMING_REPAIR", scene["id"], "生成Shot時刻修正には一件のDETAIL応答が必要です。")
            patch = records[0]["payload"]
            issues = list(Draft202012Validator(output_schema).iter_errors(patch))
            require(not issues, "SHOT_TIMING_REPAIR", scene["id"],
                    "生成Shot時刻修正の固定ID・件数・発声時刻・形式に違反しています: " + "; ".join(issue.message for issue in issues))
            require(not patch.get("unresolved"), "SHOT_TIMING_UNSATISFIABLE", scene["id"],
                    "固定音声と原文の演出を保持したShotの時間配分を確定できません。音声やShotを削除して通すことはしません。")
            candidate = copy.deepcopy(scene)
            for shot, change in zip(candidate["shots"], patch["shots"]):
                shot.update(start_ms=change["start_ms"], end_ms=change["end_ms"])
                for event, timing in zip(shot["events"], change["events"]):
                    event.update(start_ms=timing["start_ms"], end_ms=timing["end_ms"])
            _check(candidate)
        except (VCError, json.JSONDecodeError) as exc:
            row.update(status="invalid", error=str(exc))
            if isinstance(exc, VCError) and exc.diagnostic.code == "SHOT_TIMING_UNSATISFIABLE":
                raise
            if attempt == 2:
                raise VCError("SHOT_TIMING_REPAIR", scene["id"], "生成Shot時刻の部分修正に二回失敗しました: " + str(exc)) from exc
            request.update(previous_response=raw, repair_error=str(exc))
            continue
        row["status"] = "accepted"
        trace.update(status="proposed", timings=copy.deepcopy(patch))
        return candidate
