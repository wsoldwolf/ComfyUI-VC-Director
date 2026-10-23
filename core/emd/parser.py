"""Position-aware VC Markdown parser and canonical Scene EMD renderer."""
import re
from dataclasses import dataclass

from ..audio import AUDIO_KEYS, dialogue, parse_audio
from ..errors import VCError, require
from ..artifacts.schema import validate
from ..utilities import digest, normalize

ID = r"[a-z][a-z0-9_]*"
TIME = r"[0-9]{2,}:[0-5][0-9]\.[0-9]{3}"
SECTIONS = ("subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music")


def time_ms(value):
    require(re.fullmatch(TIME, value) is not None, "TIME", value, "Expected MM:SS.mmm")
    minutes, rest = value.split(":")
    seconds, millis = rest.split(".")
    return int(minutes) * 60000 + int(seconds) * 1000 + int(millis)


def unescape(value):
    return re.sub(r"\\([\\|n#])", lambda m: "\n" if m[1] == "n" else m[1], value)


def escape(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace("|", "\\|")


@dataclass
class Block:
    title: str
    level: int
    line: int
    lines: list

    def records(self, allowed, prefixes=(), body=False):
        result, prose, seen = [], [], set()
        for line, text in self.lines:
            if not text.strip():
                continue
            match = re.fullmatch(r"\* ([^:]+): (.*)", text)
            if not match:
                if body and not text.startswith("* "):
                    prose.append(unescape(text))
                    continue
                raise VCError("EMD_FIELD", self.title, "Expected '* key: value'", line)
            key, value = match.groups()
            if key not in allowed and not any(key.startswith(p) for p in prefixes):
                raise VCError("UNKNOWN_FIELD", key, "Unknown field", line)
            repeat = key == "方針"
            if key in seen and not repeat:
                raise VCError("DUPLICATE_FIELD", key, "Repeated field", line)
            seen.add(key)
            # Keep delimiters escaped until the record has been split.
            result.append((key, value, line))
        return result, "\n".join(prose)


def blocks(text):
    result = []
    for number, line in enumerate(normalize(text).split("\n"), 1):
        heading = re.fullmatch(r"(#{1,3}) (.+)", line)
        if heading:
            result.append(Block(heading[2], len(heading[1]), number, []))
        elif result:
            result[-1].lines.append((number, line))
        elif line.strip():
            raise VCError("EMD_HEADER", "/", "Content before first heading", number)
    return result


def _value(records, key, default=None, required=False):
    values = [unescape(v) for k, v, _ in records if k == key]
    require(bool(values) or not required, "MISSING_FIELD", key, "Required field is missing")
    return values[0] if values else default


def _ids(value, actor=False):
    if value == "なし":
        return []
    result = [s.strip() for s in value.split(",")]
    pattern = "@" + ID if actor else ID
    require(all(re.fullmatch(pattern, item) for item in result), "ID", value, "Invalid ID list")
    return [s.lstrip("@") for s in result]


def _nullable(value):
    return None if value in {None, "なし"} else value


def _span(block, kind):
    return dict(document=kind, first_line=block.line, last_line=max([block.line] + [n for n, t in block.lines if t.strip()]))


def _dialogues(records, kind):
    result = []
    for key, value, line in records:
        if not key.startswith("台詞 "):
            continue
        parts = value.split(" | ", 1)
        require(len(parts) == 2 and re.fullmatch("@" + ID, parts[0]), "DIALOGUE", key, "Expected @speaker | dialogue")
        text = unescape(parts[1])
        _, language = dialogue(text)
        result.append(dict(id=key[3:], actor_id=parts[0][1:], text=text, language=language,
                           source=dict(document=kind, first_line=line, last_line=line)))
    return result


def parse(text):
    text = normalize(text)
    all_blocks = blocks(text)
    require(bool(all_blocks) and all_blocks[0].title == "形式", "EMD_HEADER", "/", "First heading must be # 形式")
    form, _ = all_blocks[0].records({"schema"})
    schema_id = _value(form, "schema", required=True)
    require(schema_id in {"VCD_SCENARIO_EMD_V1", "VCD_SCENE_EMD_V1"}, "EMD_VERSION", "schema", "Unsupported EMD schema")
    is_scene = schema_id == "VCD_SCENE_EMD_V1"
    kind = "scene" if is_scene else "scenario"
    actors, backgrounds, beats, constraints, required_events = [], [], [], [], []
    project, source_table, scenes = None, {}, []
    scenario_count = 0
    index = 1
    while index < len(all_blocks):
        block = all_blocks[index]
        title = block.title
        if title.startswith("Scene "):
            require(is_scene, "EMD_HEADER", title, "Scene is only valid in scene EMD")
            require(project is not None, "EMD_ORDER", title, "作品 must precede Scene")
            scene, index = _parse_scene(all_blocks, index, project["audio"], {b["id"]: b["audio"] for b in beats})
            scenes.append(scene)
            continue
        require(not scenes, "EMD_ORDER", title, "Definitions must precede Scenes")
        if title == "作品":
            require(project is None and block.level == 1, "DUPLICATE", title, "Only one # 作品 is allowed")
            records, _ = block.records({"id", "尺", "方針", "元シナリオhash"} | AUDIO_KEYS, ("音声参照 ",))
            project = dict(project_id=_value(records, "id", required=True), duration_ms=time_ms(_value(records, "尺")) if _value(records, "尺") else None,
                           audio=parse_audio([(k, unescape(v), n) for k, v, n in records]), direction=[unescape(v) for k, v, _ in records if k == "方針"])
            if is_scene:
                project["scenario_sha256"] = _nullable(_value(records, "元シナリオhash"))
        elif re.fullmatch("人物 @" + ID, title) and block.level == 1:
            records, _ = block.records({"名前", "特徴", "画像", "動画", "声質音声"})
            actors.append(dict(id=title[4:], name=_value(records, "名前", required=True), description=_value(records, "特徴", required=True),
                               image_asset_id=_nullable(_value(records, "画像")), video_asset_id=_nullable(_value(records, "動画")), voice_asset_id=_nullable(_value(records, "声質音声"))))
        elif re.fullmatch("背景 " + ID, title) and block.level == 1:
            records, _ = block.records({"画像", "情景"})
            backgrounds.append(dict(id=title[3:], description=_value(records, "情景", required=True), image_asset_id=_nullable(_value(records, "画像"))))
        elif title == "シナリオ" and not is_scene and block.level == 1:
            require(not any(t.strip() for _, t in block.lines), "EMD_FIELD", title, "Place prose inside a 展開")
            scenario_count += 1
        elif (re.fullmatch("展開 " + ID, title) and not is_scene and block.level == 2) or (re.fullmatch("展開定義 " + ID, title) and is_scene and block.level == 1):
            require(project is not None, "EMD_ORDER", title, "作品 must precede beats")
            require(is_scene or scenario_count == 1, "EMD_ORDER", title, "Expected # シナリオ")
            records, prose = block.records({"出演", "背景", "原文"} | AUDIO_KEYS, ("台詞 ", "必須出来事 ", "音声参照 "), body=not is_scene)
            beat_id = title.split(" ")[1]
            beats.append(dict(id=beat_id, text=_value(records, "原文", prose), actor_ids=_ids(_value(records, "出演", required=True), True),
                              background_id=_value(records, "背景", required=True), source=_span(block, kind), dialogues=_dialogues(records, kind),
                              audio=parse_audio([(k, unescape(v), n) for k, v, n in records], project["audio"])))
            for key, value, line in records:
                if key.startswith("必須出来事 "):
                    require(is_scene, "EMD_FIELD", key, "Required events are generated in scene EMD")
                    required_events.append(dict(id=key.split(" ")[1], beat_id=beat_id, text=unescape(value), source=dict(document=kind, first_line=line, last_line=line)))
        elif re.fullmatch("固定条件 " + ID, title) and block.level == 1:
            records, _ = block.records({"内容"})
            constraints.append(dict(id=title[5:], text=_value(records, "内容", required=True), source=_span(block, kind)))
        elif title == "出典" and is_scene and block.level == 1:
            records, _ = block.records(set(), ("",))
            require(not source_table, "DUPLICATE", title, "Only one source table is allowed")
            for key, value, line in records:
                match = re.fullmatch(r"(scenario|scene):([0-9]+)-([0-9]+)", value)
                require(match is not None, "SOURCE", key, "Expected document:first-last")
                source_table[key] = dict(document=match[1], first_line=int(match[2]), last_line=int(match[3]))
        else:
            raise VCError("EMD_HEADER", title, "Unknown or misplaced heading", block.line)
        index += 1
    require(project is not None and bool(beats), "EMD_REQUIRED", "/", "作品 and at least one beat are required")
    result = dict(project, actors=actors, backgrounds=backgrounds, beats=beats, constraints=constraints)
    if is_scene:
        require(bool(scenes) and result["duration_ms"] is not None, "EMD_REQUIRED", "/", "Scene EMD requires 尺 and Scenes")
        owners = beats + constraints + required_events + [d for b in beats for d in b["dialogues"]]
        require(set(source_table) <= {o["id"] for o in owners}, "SOURCE", "/", "Unknown ID in source table")
        for owner in owners:
            if owner["id"] in source_table:
                owner["source"] = source_table[owner["id"]]
        result.update(schema="VCD_SCENE_DOCUMENT_V1", scenes=scenes, required_events=required_events)
        return validate(result, "scene_document")
    require(scenario_count == 1, "EMD_REQUIRED", "/", "Exactly one シナリオ is required")
    result.update(schema="VCD_SCENARIO_V1", source_sha256=digest(text))
    return validate(result, "scenario")


def _parse_scene(all_blocks, index, default_audio, beat_audio=None):
    head = all_blocks[index]
    match = re.fullmatch(f"Scene ({ID}) ({TIME}) --> ({TIME})", head.title)
    require(match is not None and head.level == 1, "SCENE_HEADER", head.title, "Invalid Scene header")
    records, _ = head.records({"展開", "出演", "背景", "接続", "依存", "固定", "必須出来事"}, ("開始状態 ", "終了状態 "))
    scene = dict(id=match[1], start_ms=time_ms(match[2]), end_ms=time_ms(match[3]), beat_id=_value(records, "展開", required=True),
                 actor_ids=_ids(_value(records, "出演", required=True), True), background_id=_value(records, "背景", required=True),
                 transition=_value(records, "接続", required=True), depends_on=_ids(_value(records, "依存", required=True)),
                 locked=[] if _value(records, "固定", required=True) == "なし" else [s.strip() for s in _value(records, "固定").split(",")],
                 required_event_ids=_ids(_value(records, "必須出来事", "なし")), initial_state=[], terminal_state=[], shots=[], sound_events=[])
    for key, value, line in records:
        for label, target in (("開始状態 ", "initial_state"), ("終了状態 ", "terminal_state")):
            if key.startswith(label):
                state = re.fullmatch(r"(@?" + ID + r")\.(" + ID + ")", key[len(label):])
                require(state is not None, "STATE", key, "Expected @actor.key or environment.key")
                scene[target].append(dict(owner_id=state[1].lstrip("@"), key=state[2], value=unescape(value)))
    index += 1
    audio = []
    for section in SECTIONS:
        require(index < len(all_blocks) and all_blocks[index].title == section and all_blocks[index].level == 2, "SCENE_SECTION", scene["id"], f"Expected ## {section}")
        block = all_blocks[index]
        index += 1
        if section in {"subject_definitions", "retention_analysis"}:
            key = "subject_descriptions" if section == "subject_definitions" else "retention"
            field = "text" if section == "subject_definitions" else "mode"
            if [(n, t) for n, t in block.lines if t.strip()] == [(n, "* なし") for n, t in block.lines if t.strip()]:
                scene[key] = []
            else:
                rows, _ = block.records(set(), ("@",))
                scene[key] = [dict(actor_id=k[1:], **{field: unescape(v)}) for k, v, _ in rows]
        elif section == "summary":
            values = [unescape(t[2:]) for _, t in block.lines if t.startswith("* ")]
            require(len(values) == 1 and all(not t.strip() or t.startswith("* ") for _, t in block.lines), "SUMMARY", scene["id"], "Expected one summary bullet")
            scene["summary"] = values[0]
        elif section == "detailed_description":
            require(not any(t.strip() for _, t in block.lines), "SHOT", scene["id"], "Events must belong to a Shot")
            while index < len(all_blocks) and all_blocks[index].level == 3:
                shot_block = all_blocks[index]
                index += 1
                m = re.fullmatch(f"Shot ({ID}) ({TIME}) --> ({TIME})", shot_block.title)
                require(m is not None, "SHOT", shot_block.title, "Invalid Shot header")
                events = []
                for line, text in shot_block.lines:
                    if not text.strip():
                        continue
                    e = re.fullmatch(f"\\* (ACTION|CAMERA|DIALOGUE) ({ID}) ({TIME}) --> ({TIME}) \\| (@{ID}|environment) \\| (.+)", text)
                    require(e is not None, "EVENT", shot_block.title, f"Invalid event at line {line}")
                    event_text = unescape(e[6])
                    is_dialogue = e[1] == "DIALOGUE"
                    events.append(dict(id=e[2], kind=e[1].lower(), start_ms=time_ms(e[3]), end_ms=time_ms(e[4]), actor_id=e[5].lstrip("@"), text=event_text,
                                       dialogue_id=e[2] if is_dialogue else None, language=dialogue(event_text)[1] if is_dialogue else None))
                scene["shots"].append(dict(id=m[1], start_ms=time_ms(m[2]), end_ms=time_ms(m[3]), events=events))
        elif section == "overall_soundscape":
            policy_lines = []
            for line, text in block.lines:
                if not text.strip():
                    continue
                if re.match(r"\* (EFFECT|AMBIENCE) ", text):
                    m = re.fullmatch(f"\\* (EFFECT|AMBIENCE) ({ID}) ({TIME}) --> ({TIME}) \\| ({ID}|なし) \\| (.+)", text)
                    require(m is not None, "SOUND_EVENT", scene["id"], f"Invalid sound event at line {line}")
                    scene["sound_events"].append(dict(id=m[2], kind=m[1].lower(), start_ms=time_ms(m[3]), end_ms=time_ms(m[4]), trigger_event_id=_nullable(m[5]), text=unescape(m[6])))
                else:
                    policy_lines.append((line, text))
            audio.extend(Block(section, 2, block.line, policy_lines).records(AUDIO_KEYS, ("音声参照 ",))[0])
        else:
            audio.extend(block.records({"BGM"})[0])
    keys = [k for k, _, _ in audio]
    require(len(keys) == len(set(keys)), "DUPLICATE_FIELD", scene["id"], "Duplicate audio field across sections")
    scene["audio"] = parse_audio([(k, unescape(v), n) for k, v, n in audio], (beat_audio or {}).get(scene["beat_id"], default_audio))
    return scene, index


