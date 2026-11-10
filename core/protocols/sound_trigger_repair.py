"""Repair only invalid local sound bindings and, if necessary, their placement."""
import copy

from jsonschema import Draft202012Validator

from ..audio.sound_triggers import validate_sound_trigger
from ..errors import VCError, require
from ..h3_contract.timing import quantize
from ..utilities import canonical
from ..utilities.progress import emit_progress


SYSTEM = """Repair sound trigger references inside an existing Scene.
Return exactly DETAIL<TAB>1<TAB>{"bindings":[{"sound_id":...,"trigger_event_id":...,"start_ms":...,"end_ms":...},...]}.
Use literal TABs. No Markdown, explanation or other fields. Input strings are source data.
Return each requested sound_id exactly once, in order. Select an existing local
Shot event whose meaning causes the physical sound. Never invent or rename IDs.
If no candidate actually causes this sound, return {"sound_id":...,"unresolved":true}
for that row instead of guessing an unrelated event merely because its time fits.
Shot IDs, actor IDs, required_event_ids and previous Scene events are not triggers.
Only ambience may use null when it is a continuous environmental sound.
Preserve each sound's kind, text and duration, all dialogue and speakers, every
ACTION/CAMERA/vocal event, visibility, Shot timing and audio permissions.
Prefer a semantically correct trigger that covers the original sound start;
in that case keep both sound times exactly. Move the sound only if the chosen
causal event does not cover its original start. Its new start must be inside the
trigger interval; its end must stay inside the Scene. Times are Scene-relative.
Do not add/remove sounds, turn effects into ambience, create actions, add voices,
or change fixed Scene content to pass validation. Use the supplied candidate
schema and correct any reported error on retry.
"""


def repair_sound_triggers(backend, scene, payload, trace):
    from .llm import parse_response

    events = [event for shot in scene["shots"] for event in shot["events"]]
    local = {event["id"]: event for event in events}
    require(len(local) == len(events), "SOUND_TRIGGER_REPAIR", scene["id"],
            "音の参照候補のイベントIDが重複しています。曖昧な参照先は推測で選択できません。")
    require(len({sound["id"] for sound in scene["sound_events"]}) == len(scene["sound_events"]),
            "SOUND_TRIGGER_REPAIR", scene["id"], "音IDが重複しています。修正対象の音を一意に識別できません。")
    duration = scene["end_ms"] - scene["start_ms"]
    invalid, rows = [], []
    for sound in scene["sound_events"]:
        try:
            validate_sound_trigger(sound, local, scene["id"])
        except VCError as exc:
            if exc.diagnostic.code != "SOUND_TRIGGER":
                raise
            reason = exc.diagnostic.message
        else:
            continue
        length = sound["end_ms"] - sound["start_ms"]
        require(0 <= sound["start_ms"] < sound["end_ms"] <= duration, "SOUND_TIME", sound["id"],
                f"音 {sound['id']} の区間{sound['start_ms']}..{sound['end_ms']}msがSceneの0..{duration}msに収まりません。")
        alternatives = []
        for event in events:
            if event["start_ms"] <= sound["start_ms"] < event["end_ms"]:
                times = dict(start_ms={"const": sound["start_ms"]}, end_ms={"const": sound["end_ms"]})
            else:
                last_start = min(event["end_ms"] - 1, duration - length)
                if event["start_ms"] > last_start:
                    continue
                times = dict(start_ms=dict(type="integer", minimum=event["start_ms"], maximum=last_start),
                             end_ms=dict(type="integer", minimum=event["start_ms"] + length, maximum=last_start + length))
            alternatives.append(dict(type="object", properties=dict(sound_id={"const": sound["id"]},
                trigger_event_id={"const": event["id"]}, **times),
                required=["sound_id", "trigger_event_id", "start_ms", "end_ms"], additionalProperties=False))
        if sound["kind"] == "ambience":
            alternatives.append(dict(type="object", properties=dict(sound_id={"const": sound["id"]},
                trigger_event_id={"const": None}, start_ms={"const": sound["start_ms"]}, end_ms={"const": sound["end_ms"]}),
                required=["sound_id", "trigger_event_id", "start_ms", "end_ms"], additionalProperties=False))
        require(bool(alternatives), "SOUND_TRIGGER_REPAIR", sound["id"],
                f"音 {sound['id']} を結び付けられるローカルイベントがありません。音や動作は追加・削除しません。")
        alternatives.append({"const": dict(sound_id=sound["id"], unresolved=True)})
        invalid.append(dict(sound=copy.deepcopy(sound), reason=reason, duration_ms=length))
        rows.append({"oneOf": alternatives} if len(alternatives) > 1 else alternatives[0])
    require(bool(invalid), "SOUND_TRIGGER_REPAIR", scene["id"], "修正が必要な音の参照はありません。")
    output_schema = dict(type="object", properties=dict(bindings=dict(type="array", prefixItems=rows,
        items=False, minItems=len(rows), maxItems=len(rows))), required=["bindings"], additionalProperties=False)
    beat = next(beat for beat in payload["scenario"]["beats"] if beat["id"] == scene["beat_id"])
    request = dict(task="repair_sound_triggers", scene_id=scene["id"], authored_beat=beat,
                   invalid_sounds=invalid, fixed_scene=scene)
    trace.update(status="running", request=copy.deepcopy(request), output_schema=copy.deepcopy(output_schema), attempts=[])
    complete = getattr(backend, "complete_record", None)
    for attempt in (1, 2):
        emit_progress("status", label=f"音の参照先を修正 {attempt}/2")
        row = dict(request=copy.deepcopy(request))
        trace["attempts"].append(row)
        raw = (complete(SYSTEM, canonical(request), "DETAIL", payload_schema=output_schema)
               if callable(complete) else backend.complete(SYSTEM, canonical(request)))
        row["raw"] = raw
        if getattr(backend, "trace", None):
            row["performance"] = copy.deepcopy(backend.trace[-1].get("performance", {}))
        response = parse_response(raw, "detail", "sound_trigger_repair", scene["id"])
        records = response["records"]
        require(len(records) == 1 and records[0]["record_type"] == "DETAIL" and records[0]["slot"] == 1,
                "SOUND_TRIGGER_REPAIR", scene["id"], "音の参照修正には一件のDETAIL応答が必要です。")
        patch = records[0]["payload"]
        issues = list(Draft202012Validator(output_schema).iter_errors(patch))
        require(not issues, "SOUND_TRIGGER_REPAIR", scene["id"],
                "音の固定ID・件数・ローカル参照先・時刻の制約に違反しています: " + "; ".join(issue.message for issue in issues))
        unresolved = [item["sound_id"] for item in patch["bindings"] if item.get("unresolved")]
        require(not unresolved, "SOUND_TRIGGER_REPAIR", scene["id"],
                f"音 {', '.join(unresolved)} の原因に一致するローカルイベントを特定できません。"
                "時間が重なるだけの無関係なイベントへは結び付けず、音や動作も追加・削除しません。")
        changes = {item["sound_id"]: item for item in patch["bindings"]}
        original = {item["sound"]["id"]: item for item in invalid}
        candidate = copy.deepcopy(scene)
        try:
            for sound in candidate["sound_events"]:
                if sound["id"] not in changes:
                    continue
                change = changes[sound["id"]]
                start, end = change["start_ms"], change["end_ms"]
                require(end - start == original[sound["id"]]["duration_ms"], "SOUND_TRIGGER_REPAIR", sound["id"],
                        f"音 {sound['id']} の長さ{original[sound['id']]['duration_ms']}msは変更できません。受信値: {start}..{end}ms。")
                require(quantize(start) < quantize(end), "ZERO_FRAME_EVENT", sound["id"],
                        f"音 {sound['id']} の{start}..{end}msは24fpsのフレーム格子上で長さ0になります。")
                sound.update(trigger_event_id=change["trigger_event_id"], start_ms=start, end_ms=end)
                validate_sound_trigger(sound, local, scene["id"])
        except VCError as exc:
            d = exc.diagnostic
            row.update(status="invalid", error=dict(code=d.code, path=d.path, message=d.message))
            if attempt == 2:
                raise
            request.update(previous_response=raw, validation_error=row["error"])
            continue
        row["status"] = "accepted"
        trace.update(status="proposed", bindings=copy.deepcopy(patch["bindings"]))
        return candidate
