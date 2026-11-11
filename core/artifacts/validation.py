"""Cross-artifact invariants beyond structural JSON Schema validation."""
import re
from collections import Counter

from ..audio import dialogue, no_speech_markup, validate_audio
from ..audio.nonverbal import permits_dialogue, permits_nonverbal
from ..audio.vocal_timing import validate_vocal_timing
from ..audio.sound_triggers import validate_sound_trigger
from ..errors import require
from .schema import validate
from .transitions import validate_beat_transitions, validate_scene_transition
from ..audio.roles import scene_roles
from .props import visible_props, tag_ids
from .visible_text import outside_visible_text
from .shot_timing import validate_shot_coverage


def unique(items, path):
    ids = [item["id"] for item in items]
    duplicates = sorted(id for id, count in Counter(ids).items() if count > 1)
    require(not duplicates, "DUPLICATE_ID", path, f"IDs must be unique; repeated IDs: {duplicates}")
    return {item["id"]: item for item in items}


def _prose(text, path, actor_ids=None, cues=False, music_allowed=True):
    no_speech_markup(text, path)
    text = outside_visible_text(text)
    require(not re.search(r"</?visible\b", text, re.I), "VISIBLE_TEXT", path,
            "Use a complete, non-empty <visible>...</visible> span without nested tags or line breaks")
    if actor_ids is not None:
        symbols = re.findall(r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_-]*)", text)
        require(set(symbols) <= set(actor_ids), "UNKNOWN_ACTOR", path,
                f"Unknown or off-screen actor reference: {sorted(set(symbols) - set(actor_ids))}. "
                f"Allowed actor IDs in this field: {sorted(actor_ids)}. "
                "Use only those IDs. In visual prose, describe the visible composition without restating excluded actors even in a visibility prohibition.")
        require(not re.search(r"<(?:Subject|Picture|Video|Audio)\b", text), "NATIVE_TOKEN", path, "Native tokens are compiler-owned")
    if cues:
        # Check clauses separately so a negative in one clause cannot authorize another.
        for clause in re.split(r"[。.!?！？;；\n]", text):
            # Remove only this locally negated vocal instruction. Treating
            # "加えない" as a clause-wide exemption would also permit a separate
            # positive vocal action, e.g. "ナレーションを加えないが呟く".
            vocal_clause = re.sub(
                r"(?:ナレーション|台詞|発声|読み上げ)(?:を|は)(?:一切)?"
                r"(?:加え|入れ|付け|つけ|追加し|行わ|使わ)ない", "", clause)
            positive = re.search(r"発声|読み上げ|叫[ぶび]|歌[うい唱]|鼻歌|笑い声|囁[くき]|呟[くき]|話[すし]|喋[るり]|"
                                 r"(?:言葉|挨拶|返事|名前|感謝|謝罪|お礼|本音)を口に(?:す|し)|声を(?:出|上げ|あげ|発)|"
                                 r"台詞|ナレーション|ため息|\b(?:speech|narrat\w*|sing(?:s|ing)?|humm?ing|screams?|whispers?|says?|speaks?|vocali[sz]\w*|sighs?|groans?|laughing)\b", vocal_clause, re.I)
            negative = re.search(r"なし|禁止|しない|出さない|無言|no\b|without\b|silent\b|never\b|not\b", vocal_clause, re.I)
            require(not positive or bool(negative), "VOCAL_CUE", path,
                    f"Remove vocal description {positive[0]!r} from this prose field; describe visible actions only. "
                    "Keep the exact authored speech in explicit DIALOGUE events, and do not remove or rewrite those events."
                    if positive else "Place vocal requests only in explicit DIALOGUE events")
            music = re.search(r"(?:BGM|音楽|伴奏).*(?:流[れす]|鳴[るら]|演奏)|\b(?:music|soundtrack)\s+(?:starts?|plays?|begins?)\b", clause, re.I)
            require(music_allowed or not music or bool(negative), "MUSIC_CUE", path, "Music is forbidden in this Scene")


def validate_visual_directions(scene):
    """Validate this field independently so its accepted repair can be retained."""
    if "visual_directions" not in scene:
        return
    visible, _ = scene_roles(scene)
    for index, text in enumerate(scene["visual_directions"]):
        path = f"{scene['id']}/visual_directions/{index}"
        if "visible_actor_ids" in scene:
            require(not re.search(r"声|発話|発声|台詞|ナレーション|\b(?:voice|voices|vocal\w*|speech|spoken|speaks?|speaking|audible|narrat\w*)\b", outside_visible_text(text), re.I),
                    "VISUAL_AUDIO_ROLE", path,
                    "visual_directions must describe visibility, framing, style and acting only. "
                    "Express off-screen roles through visible_actor_ids and actual DIALOGUE, not voice-only prose. "
                    "Preserve who is outside the frame by describing the visible composition; do not add or remove authored dialogue.")
        _prose(text, path, list(visible | visible_props(scene)), cues=True, music_allowed=scene["audio"]["bgm"] != "none")


def validate_story(document):
    is_scene = document["schema"] == "VCD_SCENE_DOCUMENT_V1"
    validate(document, "scene_document" if is_scene else "scenario")
    actors = unique(document["actors"], "actors")
    props = unique(document.get("props", []), "props")
    unique(document["actors"] + document.get("props", []), "entity_ids")
    require("environment" not in props, "RESERVED_ID", "props", "environment is reserved")
    require("environment" not in actors, "RESERVED_ID", "actors", "environment is reserved for the Scene environment")
    backgrounds = unique(document["backgrounds"], "backgrounds")
    beats = unique(document["beats"], "beats")
    owners = document["beats"] + document["constraints"] + [d for b in beats.values() for d in b["dialogues"]]
    if is_scene:
        owners += document["required_events"]
    unique(owners, "source_ids")
    for owner in owners:
        require(owner["source"]["last_line"] >= owner["source"]["first_line"], "SOURCE", owner["id"], "Reversed source range")
    validate_audio(document["audio"], "audio")
    for name, config in document.get("vocal_patterns", {}).items():
        no_speech_markup(config["instruction"], "vocal_patterns/" + name)
    for value in document["direction"]:
        _prose(value, "direction")
    for actor in actors.values():
        _prose(actor["description"], actor["id"])
    for bg in backgrounds.values():
        _prose(bg["description"], bg["id"])
    for prop in props.values():
        _prose(prop["description"], prop["id"], list(actors) + list(props), cues=True)
        if prop["size"]:
            _prose(prop["size"], prop["id"] + "/size", list(actors) + list(props), cues=True)
        if prop.get("reference_hint"):
            require(prop["image_asset_id"] is not None, "PROP_REFERENCE", prop["id"], "Image selection requires a reference image")
            _prose(prop["reference_hint"], prop["id"] + "/reference_hint", [], cues=True)
    for beat in beats.values():
        path = beat["id"]
        require(set(beat["actor_ids"]) <= set(actors), "UNKNOWN_ACTOR", path, "Undefined actor")
        require(beat["background_id"] in backgrounds, "UNKNOWN_BACKGROUND", path, "Undefined background")
        available_props = set(beat.get("prop_ids", []))
        require(available_props <= set(props), "UNKNOWN_PROP", path, "Undefined prop ID")
        require(tag_ids([beat["text"], *beat.get("direction", [])]) <= set(actors) | available_props,
                "UNKNOWN_ENTITY", path, "Use defined @actor/@prop IDs and list the referenced props in this beat")
        require(len(beat["actor_ids"]) <= 4, "SUBJECT_LIMIT", path, "VC supports up to four actors per beat")
        _prose(beat["text"], path)
        for value in beat.get("direction", []):
            _prose(value, path + "/direction")
        validate_audio(beat["audio"], path)
        if "nonverbal" in beat["audio"]:
            _prose(beat["audio"]["nonverbal"]["instruction"], path + "/nonverbal/instruction", beat["actor_ids"])
        for d in beat["dialogues"]:
            require(d["actor_id"] in beat["actor_ids"] and permits_dialogue(beat["audio"]), "DIALOGUE_PERMISSION", d["id"], "Speaker must be active and speech permitted")
            require(dialogue(d["text"])[1] == d["language"], "DIALOGUE_LANGUAGE", d["id"], "Language does not match markup")
    validate_beat_transitions(document["beats"])
    if not is_scene:
        return document
    unique(document["scenes"], "scenes")
    required = unique(document["required_events"], "required_events")
    require(all(e["beat_id"] in beats for e in required.values()), "REQUIRED_EVENT", "/", "Unknown beat")
    dialogues = {d["id"]: d for b in beats.values() for d in b["dialogues"]}
    event_ids, shot_ids, used_dialogues, used_required = [], [], [], []
    previous, seen, beat_order = None, [], []
    for scene in document["scenes"]:
        path = scene["id"]
        require(scene["beat_id"] in beats, "UNKNOWN_BEAT", path, "Undefined beat")
        beat = beats[scene["beat_id"]]
        visible, _ = scene_roles(scene)
        prop_ids = set(scene.get("prop_ids", []))
        visible_prop_ids = visible_props(scene)
        require(prop_ids <= set(beat.get("prop_ids", [])), "PROP_BINDING", path, "Scene props must belong to its beat")
        require(visible_prop_ids <= prop_ids, "VISIBLE_PROP", path, "Visible props must belong to this Scene")
        visible_entities = visible | visible_prop_ids
        entities = set(scene["actor_ids"]) | visible_prop_ids
        require(visible <= set(scene["actor_ids"]), "VISIBLE_ACTOR", path,
                "visible_actor_ids must be a subset of Scene actor_ids; do not add participants")
        beat_order.append(list(beats).index(beat["id"]))
        require(set(scene["actor_ids"]) == set(beat["actor_ids"]) and scene["background_id"] == beat["background_id"], "BEAT_BINDING", path, "Scene must preserve beat cast and background")
        duration = scene["end_ms"] - scene["start_ms"]
        require(0 < duration <= 15000, "SCENE_DURATION", path, "Scene must be within 15 seconds")
        require(scene["start_ms"] == (previous["end_ms"] if previous else 0), "SCENE_COVERAGE", path, "Scenes must be contiguous from zero")
        require(set(scene["depends_on"]) <= set(seen), "DEPENDENCY", path, "Only preceding Scenes can be dependencies")
        validate_scene_transition(scene, previous, beat)
        for key in ("subject_descriptions", "retention"):
            values = [s["actor_id"] for s in scene[key]]
            require(len(values) == len(set(values)) and set(values) == set(scene["actor_ids"]), "SUBJECT_COVERAGE", path, f"{key} must contain exactly active actors")
        for key in ("initial_state", "terminal_state"):
            state_keys = [(s["owner_id"], s["key"]) for s in scene[key]]
            require(len(state_keys) == len(set(state_keys)), "STATE", path, "Duplicate state")
            require(all(s["owner_id"] in set(scene["actor_ids"]) | prop_ids | {"environment"} for s in scene[key]), "STATE", path, "Unknown state owner")
        validate_audio(scene["audio"], path, beat["audio"])
        prose = [(scene["summary"], f"{path}/summary"),
                 (backgrounds[scene["background_id"]]["description"], f"backgrounds/{scene['background_id']}/description")]
        if "visual_directions" in scene:
            validate_visual_directions(scene)
        else:
            prose.extend((text, f"direction/{index}") for index, text in enumerate(document["direction"]))
        prose.extend((subject["text"], f"{path}/subject_descriptions/{index}/text") for index, subject in enumerate(scene["subject_descriptions"]))
        for text, field_path in prose:
            _prose(text, field_path, list(visible_entities) if "visual_directions" in field_path or field_path.endswith("/summary") else list(entities), cues=True, music_allowed=scene["audio"]["bgm"] != "none")
        for key in ("bgm_description", "ambience_description", "effects_description"):
            if scene["audio"][key]:
                _prose(scene["audio"][key], f"{path}/audio/{key}", list(entities), cues=True)
        validate_shot_coverage(scene)
        local_events, spoken = {}, []
        for shot in scene["shots"]:
            shot_ids.append(shot["id"])
            for e in shot["events"]:
                event_ids.append(e["id"])
                local_events[e["id"]] = e
                require(shot["start_ms"] <= e["start_ms"] < e["end_ms"] <= shot["end_ms"], "EVENT_TIME", e["id"], "Event must fit its Shot")
                require(e["actor_id"] in entities | {"environment"}, "EVENT_ACTOR", e["id"], "Event entity is not active")
                if e["kind"] == "dialogue":
                    source = dialogues.get(e["dialogue_id"])
                    require(source is not None and source in beat["dialogues"], "DIALOGUE_SOURCE", e["id"], "Unknown dialogue in this beat")
                    require(e["id"] == e["dialogue_id"], "DIALOGUE_ID", e["id"], "DIALOGUE ID must equal the authored dialogue ID")
                    require(e["text"] == source["text"] and e["actor_id"] == source["actor_id"] and e["language"] == source["language"], "DIALOGUE_CHANGED", e["id"], "Authored words, speaker and language are immutable")
                    require(permits_dialogue(scene["audio"]), "DIALOGUE_PERMISSION", path, "Speech is forbidden")
                    dialogue(e["text"])
                    used_dialogues.append(e["dialogue_id"])
                    spoken.append(e)
                elif e["kind"] == "vocal":
                    require(permits_nonverbal(scene["audio"]), "NONVERBAL_PERMISSION", e["id"], "Nonverbal voice is forbidden")
                    require(e["actor_id"] in scene["actor_ids"], "NONVERBAL_ACTOR", e["id"], "Voice must belong to a participating actor")
                    require(e["text"] in scene["audio"]["nonverbal"]["allowed"], "NONVERBAL_KIND", e["id"], "Use only an allowed nonverbal kind code")
                    require(e["dialogue_id"] is None and e["language"] is None, "EVENT", e["id"], "Nonverbal voice has no dialogue metadata")
                else:
                    require(e["actor_id"] in visible_entities | {"environment"}, "VISUAL_EVENT_ACTOR", e["id"],
                            "ACTION/CAMERA must target a visible actor or environment; keep off-screen speech in DIALOGUE")
                    require(e["dialogue_id"] is None and e["language"] is None, "EVENT", e["id"], "Only dialogue has speech metadata")
                    _prose(e["text"], e["id"], list(visible_entities), cues=True, music_allowed=scene["audio"]["bgm"] != "none")
        require(scene["audio"]["vocal"] != "dialogue" or bool(spoken), "DIALOGUE_PERMISSION", path, "Narrow vocal to none in a Scene without dialogue")
        validate_vocal_timing(scene)
        for e in scene["sound_events"]:
            event_ids.append(e["id"])
            require(0 <= e["start_ms"] < e["end_ms"] <= duration, "SOUND_TIME", e["id"], "Sound must fit its Scene")
            permission = "effects" if e["kind"] == "effect" else "ambience"
            require(scene["audio"][permission] != "none", "AUDIO_PERMISSION", e["id"], "Sound is forbidden")
            validate_sound_trigger(e, local_events, scene["id"])
            _prose(e["text"], e["id"], list(entities), cues=True, music_allowed=scene["audio"]["bgm"] != "none")
        for event_id in scene["required_event_ids"]:
            require(event_id in required and required[event_id]["beat_id"] == scene["beat_id"], "REQUIRED_EVENT", path, "Required event belongs to another beat")
            used_required.append(event_id)
        previous = scene
        seen.append(path)
    require(beat_order == sorted(beat_order) and set(beat_order) == set(range(len(beats))), "BEAT_ORDER", "/", "Every beat must appear in authored order")
    require(document["duration_ms"] == previous["end_ms"], "DURATION", "/", "Document duration must match final Scene end")
    require(len(shot_ids) == len(set(shot_ids)) and len(event_ids) == len(set(event_ids)), "DUPLICATE_ID", "/", "Shot and event IDs must be unique")
    timeline_ids = seen + shot_ids + event_ids
    require(len(timeline_ids) == len(set(timeline_ids)), "DUPLICATE_ID", "/", "Scene, Shot and event IDs share one timeline namespace")
    require(Counter(used_dialogues) == Counter(dialogues.keys()), "DIALOGUE_COVERAGE", "/", "Every authored dialogue must appear exactly once")
    require(Counter(used_required) == Counter(required.keys()), "REQUIRED_COVERAGE", "/", "Every required event must be assigned exactly once")
    return document
