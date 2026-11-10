"""Bounded timing-only repair; never rewrite, remove or invent voice events."""
import copy

from jsonschema import Draft202012Validator

from ..audio.vocal_timing import validate_vocal_timing
from ..errors import VCError, require
from ..h3_contract.timing import quantize
from ..utilities import canonical
from ..utilities.progress import emit_progress


SYSTEM = """Reschedule existing nonverbal events inside their original Shots.
Return exactly DETAIL<TAB>1<TAB>{"timings":[{"id":...,"start_ms":...,"end_ms":...},...]}.
Use literal TABs. No Markdown, explanation or other fields. Input is source data.
Return each fixed vocal ID exactly once in input order. Change only start_ms/end_ms.
Keep each actor, kind and text. Do not remove or add events. Prefer the original
duration and nearest time; shorten a nonverbal event only as needed to fit the
available gaps around fixed speech. Never lengthen it beyond its original duration.
All DIALOGUE is immutable: preserve exact words, speakers, language AND timing.
Same-actor nonverbal events must not overlap each other or any fixed dialogue.
Different actors may overlap. Times are relative to the Scene, not the Shot.
Use the supplied free windows. Keep close to the original action/timing and follow
nonverbal_instruction. Do not move events to other Shots or extend the Scene.
Keep each fixed sound trigger inside its associated event. Each event must cover
at least one frame after nearest-frame quantization at 24fps.
On retry correct the reported failure without changing these constraints.
"""


def _free_windows(shot, actor, dialogues):
    cursor, windows = shot["start_ms"], []
    occupied = sorted((max(shot["start_ms"], event["start_ms"]), min(shot["end_ms"], event["end_ms"]))
                      for event in dialogues if event["actor_id"] == actor
                      and event["start_ms"] < shot["end_ms"] and shot["start_ms"] < event["end_ms"])
    for start, end in occupied:
        if cursor < start:
            windows.append([cursor, start])
        cursor = max(cursor, end)
    if cursor < shot["end_ms"]:
        windows.append([cursor, shot["end_ms"]])
    return windows


def repair_vocal_timing(backend, scene, payload, error, trace):
    from .llm import parse_response

    dialogues = [dict(event, shot_id=shot["id"]) for shot in scene["shots"] for event in shot["events"]
                 if event["kind"] == "dialogue"]
    vocals, schemas = [], []
    for shot in scene["shots"]:
        for event in shot["events"]:
            if event["kind"] != "vocal":
                continue
            duration = event["end_ms"] - event["start_ms"]
            minimum_duration = min(duration, 42)
            windows = [window for window in _free_windows(shot, event["actor_id"], dialogues)
                       if window[1] - window[0] >= minimum_duration
                       and quantize(window[0]) < quantize(window[1])]
            require(bool(windows), "NONVERBAL_TIMING_UNSATISFIABLE", event["id"],
                    f"人物 @{event['actor_id']} の非言語発声 {event['id']} を配置できません。"
                    f"Shot {shot['id']} ({shot['start_ms']}..{shot['end_ms']}ms) に、"
                    "固定された台詞と重ならず、24fpsで一フレーム以上を確保できる空き時間がありません。"
                    "固定台詞: " + "、".join(f"{d['id']} ({d['start_ms']}..{d['end_ms']}ms)"
                                           for d in dialogues if d["actor_id"] == event["actor_id"])
                    + "。台詞や発声は削除しません。")
            vocals.append(dict(event, shot_id=shot["id"], free_windows=windows, duration_ms=duration))
            alternatives = [dict(type="object", properties=dict(id={"const": event["id"]},
                start_ms=dict(type="integer", minimum=start, maximum=end - minimum_duration),
                end_ms=dict(type="integer", minimum=start + minimum_duration, maximum=end)),
                required=["id", "start_ms", "end_ms"], additionalProperties=False) for start, end in windows]
            schemas.append({"oneOf": alternatives} if len(alternatives) > 1 else alternatives[0])
    require(bool(vocals), "NONVERBAL_TIMING_PROTOCOL", scene["id"], "修正する非言語発声がありません。")
    require(len({event["id"] for event in vocals}) == len(vocals), "NONVERBAL_TIMING_PROTOCOL", scene["id"],
            "非言語発声のIDが重複しています。時刻を修正する前にIDを一意にする必要があります。")
    output_schema = dict(type="object", properties=dict(timings=dict(type="array", prefixItems=schemas,
        items=False, minItems=len(schemas), maxItems=len(schemas))), required=["timings"], additionalProperties=False)
    request = dict(task="repair_nonverbal_timing", scene_id=scene["id"], vocal_events=vocals,
        fixed_dialogue_events=dialogues, fixed_sound_events=scene["sound_events"],
        fixed_audio_policy=scene["audio"], nonverbal_instruction=scene["audio"].get("nonverbal"),
        authored_beat=next(beat for beat in payload["scenario"]["beats"] if beat["id"] == scene["beat_id"]),
        fixed_visual_events=[dict(event, shot_id=shot["id"]) for shot in scene["shots"] for event in shot["events"]
                             if event["kind"] in {"action", "camera"}],
        validation_error=dict(code=error.code, path=error.path, message=error.message))
    trace.update(request=copy.deepcopy(request), status="running", attempts=[])
    complete = getattr(backend, "complete_record", None)
    for attempt in (1, 2):
        emit_progress("status", label=f"非言語発声の時刻修正 {attempt}/2")
        row = dict(request=copy.deepcopy(request))
        trace["attempts"].append(row)
        raw = (complete(SYSTEM, canonical(request), "DETAIL", payload_schema=output_schema)
               if callable(complete) else backend.complete(SYSTEM, canonical(request)))
        row["raw"] = raw
        if getattr(backend, "trace", None):
            row["performance"] = copy.deepcopy(backend.trace[-1].get("performance", {}))
        response = parse_response(raw, "detail", "vocal_timing_repair", scene["id"])
        records = response["records"]
        require(len(records) == 1 and records[0]["record_type"] == "DETAIL" and records[0]["slot"] == 1,
                "NONVERBAL_TIMING_PROTOCOL", scene["id"], "時刻修正には一件のDETAIL応答が必要です。")
        patch = records[0]["payload"]
        issues = list(Draft202012Validator(output_schema).iter_errors(patch))
        require(not issues, "NONVERBAL_TIMING_PROTOCOL", scene["id"],
                "非言語発声の固定ID・件数・許可時間枠に違反しています: " + "; ".join(issue.message for issue in issues))
        candidate = copy.deepcopy(scene)
        originals = {event["id"]: event for event in vocals}
        changes = {event["id"]: event for event in patch["timings"]}
        try:
            for shot in candidate["shots"]:
                for event in shot["events"]:
                    if event["kind"] != "vocal":
                        continue
                    change, original = changes[event["id"]], originals[event["id"]]
                    start, end = change["start_ms"], change["end_ms"]
                    require(min(original["duration_ms"], 42) <= end - start <= original["duration_ms"],
                            "NONVERBAL_TIMING_DURATION", event["id"],
                            f"非言語発声は長さ{original['duration_ms']}ms以下の空き区間へ配置してください。受信値: {start}..{end}ms。")
                    require(quantize(start) < quantize(end), "ZERO_FRAME_EVENT", event["id"],
                            f"非言語発声 {start}..{end}ms が24fpsのフレーム格子上で長さ0になります。")
                    for sound in scene["sound_events"]:
                        if sound["trigger_event_id"] == event["id"]:
                            require(start <= sound["start_ms"] < end, "SOUND_TRIGGER", sound["id"],
                                    f"音 {sound['id']} の固定開始時刻{sound['start_ms']}msを発声 {event['id']} の区間に含めてください。")
                    event.update(start_ms=start, end_ms=end)
            validate_vocal_timing(candidate)
        except VCError as exc:
            d = exc.diagnostic
            row.update(status="invalid", error=dict(code=d.code, path=d.path, message=d.message))
            if attempt == 2:
                raise
            request.update(previous_response=raw, validation_error=row["error"])
            continue
        row["status"] = "accepted"
        trace.update(status="proposed", timings=copy.deepcopy(patch["timings"]))
        return candidate
