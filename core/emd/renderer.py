"""Canonical Scene EMD rendering."""
from ..artifacts.schema import validate
from ..audio import audio_records
from ..utilities import timestamp
from .parser import escape


def render_scene(document):
    validate(document, "scene_document")
    lines = ["# 形式", "* schema: VCD_SCENE_EMD_V1", "", "# 作品"]

    def fields(records):
        lines.extend(f"* {key}: {escape(value)}" for key, value in records)

    fields([("id", document["project_id"]), ("尺", timestamp(document["duration_ms"])), ("元シナリオhash", document["scenario_sha256"] or "なし")])
    fields([("方針", value) for value in document["direction"]] + audio_records(document["audio"]))
    for actor in document["actors"]:
        lines.extend(["", "# 人物 @" + actor["id"]])
        fields([("名前", actor["name"]), ("特徴", actor["description"])] + [(key, actor[field] or "なし") for key, field in (("画像", "image_asset_id"), ("動画", "video_asset_id"), ("声質音声", "voice_asset_id"))])
    for bg in document["backgrounds"]:
        lines.extend(["", "# 背景 " + bg["id"]])
        fields([("画像", bg["image_asset_id"] or "なし"), ("情景", bg["description"])])
    for beat in document["beats"]:
        lines.extend(["", "# 展開定義 " + beat["id"]])
        fields([("出演", ", ".join("@" + a for a in beat["actor_ids"]) or "なし"), ("背景", beat["background_id"]), ("原文", beat["text"])])
        # Delimiters are syntax, not part of the escaped authored speech.
        for d in beat["dialogues"]:
            lines.append(f"* 台詞 {d['id']}: @{d['actor_id']} | {escape(d['text'])}")
        fields(audio_records(beat["audio"]))
        fields([("必須出来事 " + e["id"], e["text"]) for e in document["required_events"] if e["beat_id"] == beat["id"]])
    for constraint in document["constraints"]:
        lines.extend(["", "# 固定条件 " + constraint["id"]])
        fields([("内容", constraint["text"])])
    lines.extend(["", "# 出典"])
    owners = document["beats"] + document["constraints"] + document["required_events"] + [d for b in document["beats"] for d in b["dialogues"]]
    fields([(o["id"], f"{o['source']['document']}:{o['source']['first_line']}-{o['source']['last_line']}") for o in owners])
    for s in document["scenes"]:
        lines.extend(["", f"# Scene {s['id']} {timestamp(s['start_ms'])} --> {timestamp(s['end_ms'])}"])
        fields([("展開", s["beat_id"]), ("出演", ", ".join("@" + a for a in s["actor_ids"]) or "なし"), ("背景", s["background_id"]), ("接続", s["transition"]),
                ("依存", ", ".join(s["depends_on"]) or "なし"), ("固定", ", ".join(s["locked"]) or "なし"), ("必須出来事", ", ".join(s["required_event_ids"]) or "なし")])
        for label, key in (("開始状態", "initial_state"), ("終了状態", "terminal_state")):
            fields([(f"{label} {'' if state['owner_id'] == 'environment' else '@'}{state['owner_id']}.{state['key']}", state["value"]) for state in s[key]])
        for section, key, field in (("subject_definitions", "subject_descriptions", "text"), ("summary", None, None), ("retention_analysis", "retention", "mode")):
            lines.extend(["", "## " + section])
            if key is None:
                lines.append("* " + escape(s["summary"]))
            elif s[key]:
                fields([("@" + item["actor_id"], item[field]) for item in s[key]])
            else:
                lines.append("* なし")
        lines.extend(["", "## detailed_description"])
        for shot in s["shots"]:
            lines.append(f"### Shot {shot['id']} {timestamp(shot['start_ms'])} --> {timestamp(shot['end_ms'])}")
            for e in shot["events"]:
                lines.append(f"* {e['kind'].upper()} {e['id']} {timestamp(e['start_ms'])} --> {timestamp(e['end_ms'])} | {'' if e['actor_id'] == 'environment' else '@'}{e['actor_id']} | {escape(e['text'])}")
        lines.extend(["", "## overall_soundscape"])
        fields([(k, v) for k, v in audio_records(s["audio"]) if k != "BGM"])
        for e in s["sound_events"]:
            lines.append(f"* {e['kind'].upper()} {e['id']} {timestamp(e['start_ms'])} --> {timestamp(e['end_ms'])} | {e['trigger_event_id'] or 'なし'} | {escape(e['text'])}")
        lines.extend(["", "## non_diegetic_music"])
        fields([(k, v) for k, v in audio_records(s["audio"]) if k == "BGM"])
    return "\n".join(lines) + "\n"
