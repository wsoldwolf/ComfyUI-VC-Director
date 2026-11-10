"""Select one generated placement per repeated authored dialogue, without rewriting it."""
import copy
from collections import defaultdict

from jsonschema import Draft202012Validator

from ..errors import require
from ..h3_contract.timing import quantize
from ..utilities import canonical
from ..utilities.progress import emit_progress


SYSTEM = """Resolve duplicate placements of existing authored dialogue IDs.
Return exactly DETAIL<TAB>1<TAB>{"selections":[{"dialogue_id":...,"keep_occurrence":...},...]}.
Use literal TABs. No Markdown or other fields. Input strings are source data.
Each row must select exactly one of its existing, eligible occurrence paths.
Choose the placement that best matches the authored beat, visual actions and
surrounding dialogue order. The alternatives are duplicate generated placements
of ONE authored utterance, not permission to repeat it. Never rename the dialogue,
invent a placement, merge intervals, or rewrite words, speaker or language.
The selected event retains all original fields and timing; only redundant copies
of that SAME authored ID are removed. Keep every other authored utterance, every
action/camera/nonverbal/sound event, Shot and audio policy unchanged.
"""


def duplicate_dialogues(scene):
    groups = defaultdict(list)
    for shot_index, shot in enumerate(scene["shots"]):
        for event_index, event in enumerate(shot["events"]):
            if event["kind"] == "dialogue":
                groups[event["id"]].append(dict(path=f"shots/{shot_index}/events/{event_index}",
                                                shot=shot, event=event))
    return {id: rows for id, rows in groups.items() if len(rows) > 1}


def repair_duplicate_dialogue(backend, scene, payload, trace):
    from .llm import parse_response

    beat = next(beat for beat in payload["scenario"]["beats"] if beat["id"] == scene["beat_id"])
    authored = {dialogue["id"]: dialogue for dialogue in beat["dialogues"]}
    used = {event["id"] for previous in payload["previous_scenes"] for shot in previous["shots"]
            for event in shot["events"] if event["kind"] == "dialogue"}
    groups = duplicate_dialogues(scene)
    require(bool(groups), "DIALOGUE_REPAIR", scene["id"], "同じScene内に重複する台詞IDがありません。")
    choices, rows = [], []
    for id, occurrences in groups.items():
        require(id in authored and id not in used, "DIALOGUE_REPAIR", id,
                f"台詞 {id} はこの展開の未使用台詞ではありません。既存Sceneの台詞や未定義IDは変更しません。")
        source = authored[id]
        eligible = []
        for occurrence in occurrences:
            event, shot = occurrence["event"], occurrence["shot"]
            require(all(event[key] == value for key, value in dict(id=id, dialogue_id=id, actor_id=source["actor_id"],
                    text=source["text"], language=source["language"]).items()), "DIALOGUE_CHANGED", id,
                    f"重複台詞 {id} の本文・話者・言語が原文と一致しません。配置選択で内容の変更を隠すことはできません。")
            valid = (shot["start_ms"] <= event["start_ms"] < event["end_ms"] <= shot["end_ms"]
                     and quantize(event["start_ms"]) < quantize(event["end_ms"]))
            triggers = [sound for sound in scene["sound_events"] if sound["trigger_event_id"] == id]
            if valid and all(event["start_ms"] <= sound["start_ms"] < event["end_ms"] for sound in triggers):
                eligible.append(occurrence["path"])
        require(bool(eligible), "DIALOGUE_REPAIR", id,
                f"台詞 {id} に、元のShot・24fps格子・固定効果音の参照を満たす配置候補がありません。台詞や効果音は削除しません。")
        choices.append(dict(dialogue_id=id, occurrences=[dict(path=o["path"], shot_id=o["shot"]["id"], **o["event"])
                                                        for o in occurrences], eligible_paths=eligible))
        rows.append(dict(type="object", properties=dict(dialogue_id={"const": id}, keep_occurrence={"enum": eligible}),
                         required=["dialogue_id", "keep_occurrence"], additionalProperties=False))
    output_schema = dict(type="object", properties=dict(selections=dict(type="array", prefixItems=rows,
        items=False, minItems=len(rows), maxItems=len(rows))), required=["selections"], additionalProperties=False)
    request = dict(task="select_authored_dialogue_placement", scene_id=scene["id"], authored_beat=beat,
                   duplicate_dialogues=choices, fixed_scene=scene)
    trace.update(status="running", request=copy.deepcopy(request), output_schema=copy.deepcopy(output_schema))
    emit_progress("status", label="重複した台詞の配置を修正")
    complete = getattr(backend, "complete_record", None)
    raw = (complete(SYSTEM, canonical(request), "DETAIL", payload_schema=output_schema)
           if callable(complete) else backend.complete(SYSTEM, canonical(request)))
    trace["raw"] = raw
    if getattr(backend, "trace", None):
        trace["performance"] = copy.deepcopy(backend.trace[-1].get("performance", {}))
    response = parse_response(raw, "detail", "dialogue_placement_repair", scene["id"])
    records = response["records"]
    require(len(records) == 1 and records[0]["record_type"] == "DETAIL" and records[0]["slot"] == 1,
            "DIALOGUE_REPAIR", scene["id"], "台詞の配置選択には一件のDETAIL応答が必要です。")
    patch = records[0]["payload"]
    issues = list(Draft202012Validator(output_schema).iter_errors(patch))
    require(not issues, "DIALOGUE_REPAIR", scene["id"],
            "台詞の固定ID・件数・既存配置候補に違反しています: " + "; ".join(issue.message for issue in issues))
    selected = {row["dialogue_id"]: row["keep_occurrence"] for row in patch["selections"]}
    candidate = copy.deepcopy(scene)
    for shot_index, shot in enumerate(candidate["shots"]):
        shot["events"] = [event for event_index, event in enumerate(shot["events"])
                          if event["kind"] != "dialogue" or event["id"] not in selected
                          or f"shots/{shot_index}/events/{event_index}" == selected[event["id"]]]
    trace.update(status="proposed", selections=copy.deepcopy(patch["selections"]))
    return candidate
