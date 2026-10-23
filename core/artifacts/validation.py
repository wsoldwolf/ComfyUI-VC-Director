"""Cross-artifact invariants beyond structural JSON Schema validation."""
import re
from collections import Counter

from ..audio import dialogue, no_speech_markup, validate_audio
from ..errors import require
from .schema import validate


def unique(items, path):
    ids = [item["id"] for item in items]
    require(len(ids) == len(set(ids)), "DUPLICATE_ID", path, "IDs must be unique")
    return {item["id"]: item for item in items}


def _prose(text, path, actor_ids=None, cues=False, music_allowed=True):
    no_speech_markup(text, path)
    if actor_ids is not None:
        symbols = re.findall(r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_-]*)", text)
        require(set(symbols) <= set(actor_ids), "UNKNOWN_ACTOR", path, "Unknown or off-screen actor reference")
        require(not re.search(r"<(?:Subject|Picture|Video|Audio)\b", text), "NATIVE_TOKEN", path, "Native tokens are compiler-owned")
    if cues:
        # Check clauses separately so a negative in one clause cannot authorize another.
        for clause in re.split(r"[。.!?！？;；\n]", text):
            positive = re.search(r"発声|読み上げ|叫[ぶび]|歌[うい唱]|鼻歌|笑い声|囁[くき]|呟[くき]|台詞|ナレーション|ため息|\b(?:speech|narrat\w*|sing(?:s|ing)?|humm?ing|screams?|whispers?|says?|speaks?|vocali[sz]\w*|sighs?|groans?|laughing)\b", clause, re.I)
            negative = re.search(r"なし|禁止|しない|出さない|無言|no\b|without\b|silent\b|never\b|not\b", clause, re.I)
            require(not positive or bool(negative), "VOCAL_CUE", path, "Place vocal requests only in explicit DIALOGUE events")
            music = re.search(r"(?:BGM|音楽|伴奏).*(?:流[れす]|鳴[るら]|演奏)|\b(?:music|soundtrack)\s+(?:starts?|plays?|begins?)\b", clause, re.I)
            require(music_allowed or not music or bool(negative), "MUSIC_CUE", path, "Music is forbidden in this Scene")


def validate_story(document):
    is_scene = document["schema"] == "VCD_SCENE_DOCUMENT_V1"
    validate(document, "scene_document" if is_scene else "scenario")
    actors = unique(document["actors"], "actors")
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
    for value in document["direction"]:
        _prose(value, "direction")
    for actor in actors.values():
        _prose(actor["description"], actor["id"])
    for bg in backgrounds.values():
        _prose(bg["description"], bg["id"])
    for beat in beats.values():
        path = beat["id"]
        require(set(beat["actor_ids"]) <= set(actors), "UNKNOWN_ACTOR", path, "Undefined actor")
        require(beat["background_id"] in backgrounds, "UNKNOWN_BACKGROUND", path, "Undefined background")
        require(len(beat["actor_ids"]) <= 4, "SUBJECT_LIMIT", path, "H3 supports up to four active subjects")
        _prose(beat["text"], path)
        validate_audio(beat["audio"], path)
        for d in beat["dialogues"]:
            require(d["actor_id"] in beat["actor_ids"] and beat["audio"]["vocal"] == "dialogue", "DIALOGUE_PERMISSION", d["id"], "Speaker must be active and speech permitted")
            require(dialogue(d["text"])[1] == d["language"], "DIALOGUE_LANGUAGE", d["id"], "Language does not match markup")
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
        beat_order.append(list(beats).index(beat["id"]))
        require(set(scene["actor_ids"]) == set(beat["actor_ids"]) and scene["background_id"] == beat["background_id"], "BEAT_BINDING", path, "Scene must preserve beat cast and background")
        duration = scene["end_ms"] - scene["start_ms"]
        require(0 < duration <= 15000, "SCENE_DURATION", path, "Scene must be within 15 seconds")
        require(scene["start_ms"] == (previous["end_ms"] if previous else 0), "SCENE_COVERAGE", path, "Scenes must be contiguous from zero")
        require(set(scene["depends_on"]) <= set(seen), "DEPENDENCY", path, "Only preceding Scenes can be dependencies")
        if previous is None:
            require(scene["transition"] == "cut", "TRANSITION", path, "First Scene must be cut")
        elif scene["transition"] == "continue":
            require(previous["id"] in scene["depends_on"] and set(previous["actor_ids"]) == set(scene["actor_ids"]) and previous["background_id"] == scene["background_id"], "TRANSITION", path, "Continue requires previous Scene, same cast and background")
            terminal = {(s["owner_id"], s["key"]): s["value"] for s in previous["terminal_state"]}
            for state in scene["initial_state"]:
                key = state["owner_id"], state["key"]
                require(key not in terminal or terminal[key] == state["value"], "STATE_CONTINUITY", path, "Conflicting continued state")
        for key in ("subject_descriptions", "retention"):
            values = [s["actor_id"] for s in scene[key]]
            require(len(values) == len(set(values)) and set(values) == set(scene["actor_ids"]), "SUBJECT_COVERAGE", path, f"{key} must contain exactly active actors")
        for key in ("initial_state", "terminal_state"):
            state_keys = [(s["owner_id"], s["key"]) for s in scene[key]]
            require(len(state_keys) == len(set(state_keys)), "STATE", path, "Duplicate state")
            require(all(s["owner_id"] in scene["actor_ids"] + ["environment"] for s in scene[key]), "STATE", path, "Unknown state owner")
        validate_audio(scene["audio"], path, beat["audio"])
        for text in [scene["summary"], backgrounds[scene["background_id"]]["description"]] + document["direction"] + [s["text"] for s in scene["subject_descriptions"]]:
            _prose(text, path, scene["actor_ids"], cues=True, music_allowed=scene["audio"]["bgm"] != "none")
        for key in ("bgm_description", "ambience_description", "effects_description"):
            if scene["audio"][key]:
                _prose(scene["audio"][key], path, scene["actor_ids"], cues=True)
        endpoint, local_events, spoken = 0, {}, []
        for shot in scene["shots"]:
            shot_ids.append(shot["id"])
            require(shot["start_ms"] == endpoint and shot["start_ms"] < shot["end_ms"] <= duration, "SHOT_COVERAGE", shot["id"], "Shots must cover the Scene without gaps")
            endpoint = shot["end_ms"]
            for e in shot["events"]:
                event_ids.append(e["id"])
                local_events[e["id"]] = e
                require(shot["start_ms"] <= e["start_ms"] < e["end_ms"] <= shot["end_ms"], "EVENT_TIME", e["id"], "Event must fit its Shot")
                require(e["actor_id"] in scene["actor_ids"] + ["environment"], "EVENT_ACTOR", e["id"], "Event actor is not active")
                if e["kind"] == "dialogue":
                    source = dialogues.get(e["dialogue_id"])
                    require(source is not None and source in beat["dialogues"], "DIALOGUE_SOURCE", e["id"], "Unknown dialogue in this beat")
                    require(e["id"] == e["dialogue_id"], "DIALOGUE_ID", e["id"], "DIALOGUE ID must equal the authored dialogue ID")
                    require(e["text"] == source["text"] and e["actor_id"] == source["actor_id"] and e["language"] == source["language"], "DIALOGUE_CHANGED", e["id"], "Authored words, speaker and language are immutable")
                    require(scene["audio"]["vocal"] == "dialogue", "DIALOGUE_PERMISSION", path, "Speech is forbidden")
                    dialogue(e["text"])
                    used_dialogues.append(e["dialogue_id"])
                    spoken.append(e)
                else:
                    require(e["dialogue_id"] is None and e["language"] is None, "EVENT", e["id"], "Only dialogue has speech metadata")
                    _prose(e["text"], e["id"], scene["actor_ids"], cues=True, music_allowed=scene["audio"]["bgm"] != "none")
        require(endpoint == duration, "SHOT_COVERAGE", path, "Shots do not cover entire Scene")
        require(scene["audio"]["vocal"] != "dialogue" or bool(spoken), "DIALOGUE_PERMISSION", path, "Narrow vocal to none in a Scene without dialogue")
        for e in scene["sound_events"]:
            event_ids.append(e["id"])
            require(0 <= e["start_ms"] < e["end_ms"] <= duration, "SOUND_TIME", e["id"], "Sound must fit its Scene")
            permission = "effects" if e["kind"] == "effect" else "ambience"
            require(scene["audio"][permission] != "none", "AUDIO_PERMISSION", e["id"], "Sound is forbidden")
            trigger = local_events.get(e["trigger_event_id"])
            require((e["kind"] == "ambience" and e["trigger_event_id"] is None) or trigger is not None, "SOUND_TRIGGER", e["id"], "Sound trigger must be a local event")
            if trigger:
                require(trigger["start_ms"] <= e["start_ms"] < trigger["end_ms"], "SOUND_TRIGGER", e["id"], "Sound starts outside trigger interval")
            _prose(e["text"], e["id"], scene["actor_ids"], cues=True, music_allowed=scene["audio"]["bgm"] != "none")
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
