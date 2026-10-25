"""Audio permission and immutable dialogue handling; never translates speech."""
import copy
import re

from ..errors import VCError, require

DEFAULT_AUDIO = dict(vocal="none", bgm="none", bgm_description=None,
                     ambience="none", ambience_description=None,
                     effects="none", effects_description=None, reference_audio=[])
AUDIO_KEYS = {"発声", "BGM", "環境音", "効果音", "音響", "音声参照", "音声接続", "BGM抑制"}


def dialogue(text):
    if text.startswith("「") and text.endswith("」"):
        body = text[1:-1]
        require(bool(body.strip()) and not re.search(r"[「」]|</?d[>\[]", body), "DIALOGUE", "text", "Empty or nested dialogue")
        return "<d>[Japanese]" + body + "</d>", "Japanese"
    match = re.fullmatch(r"<d>(?:\[([^\]\r\n]+)\])?(.+)</d>", text, flags=re.S)
    require(match is not None, "DIALOGUE", "text", "Use 「...」 or a complete <d>...</d> span")
    require(bool(match[2].strip()) and not re.search(r"</?d\b|[「」]", match[2]), "DIALOGUE", "text", "Empty or nested dialogue")
    return text, match[1]


def no_speech_markup(text, path):
    require(not re.search(r"(?<!\\)[「」]|</?d(?:>|\[)", text), "STRAY_DIALOGUE", path,
            "Remove quoted dialogue and <d> tags from this prose field. Keep the exact authored speech only in DIALOGUE events; do not remove the DIALOGUE events.")


def parse_audio(records, parent=None):
    result = copy.deepcopy(parent or DEFAULT_AUDIO)
    for key, value, line in records:
        if key == "音響":
            require(value == "無音", "AUDIO_POLICY", str(line), "音響 accepts 無音")
            controls = {k: result[k] for k in ("continuity", "bgm_suppression") if k in result}
            result = dict(copy.deepcopy(DEFAULT_AUDIO), **controls)
        elif key in {"音声接続", "BGM抑制"}:
            field, allowed = (("continuity", {"auto", "cut", "continue"}) if key == "音声接続"
                              else ("bgm_suppression", {"prompt", "negpip"}))
            require(value in allowed, "AUDIO_POLICY", str(line), key + " accepts " + " / ".join(sorted(allowed)))
            result[field] = value
        elif key == "発声":
            require(value in {"なし", "指定台詞のみ"}, "AUDIO_POLICY", str(line), "Unknown vocal permission")
            result["vocal"] = "none" if value == "なし" else "dialogue"
        elif key in {"BGM", "環境音", "効果音"}:
            target = {"BGM": "bgm", "環境音": "ambience", "効果音": "effects"}[key]
            if value == "なし":
                mode, desc = "none", None
            elif target == "bgm":
                require(value.startswith("生成:") and bool(value[3:].strip()), "AUDIO_POLICY", str(line), "BGM must be 生成:description or なし")
                mode, desc = "generate", value[3:].strip()
            elif value == "シーンに合わせて補完":
                mode, desc = "expand", None
            else:
                mode, desc = "explicit", value
            result[target], result[target + "_description"] = mode, desc
        elif key == "音声参照":
            require(value == "なし", "AUDIO_REFERENCE", str(line), "Use 音声参照 music/ambience/effects")
            result["reference_audio"] = []
        elif key.startswith("音声参照 "):
            purpose = key.split(" ", 1)[1]
            require(purpose in {"music", "ambience", "effects"}, "AUDIO_REFERENCE", str(line), "Unknown reference purpose")
            # A purpose override replaces its inherited list.
            result["reference_audio"] = [r for r in result["reference_audio"] if r["purpose"] != purpose]
            result["reference_audio"].extend({"asset_id": a.strip(), "purpose": purpose} for a in value.split(","))
    if any(k == "音響" for k, _, _ in records):
        require(all(result[k] == v for k, v in DEFAULT_AUDIO.items()), "AUDIO_CONFLICT", "音響", "無音 cannot coexist with enabled audio")
    validate_audio(result, "audio")
    return result


def validate_audio(policy, path, parent=None):
    require(policy.get("bgm_suppression", "prompt") != "negpip" or policy["bgm"] == "none",
            "AUDIO_SUPPRESSION", path, "BGM抑制: negpipはBGM: なしの区間で使用してください。BGMを生成する区間はBGM抑制: promptに変更してください。")
    for key in ("bgm", "ambience", "effects"):
        description = policy[key + "_description"]
        require(description is None if policy[key] in {"none", "expand"} else isinstance(description, str) and bool(description.strip()), "AUDIO_DESCRIPTION", path, f"Description does not match {key} mode")
    seen = set()
    for ref in policy["reference_audio"]:
        key = (ref["asset_id"], ref["purpose"])
        require(key not in seen, "DUPLICATE", path, "Duplicate audio reference")
        seen.add(key)
        field = "bgm" if ref["purpose"] == "music" else ref["purpose"]
        require(policy[field] != "none", "AUDIO_REFERENCE", path, f"Reference for disabled {field}")
    if parent:
        for key, default in (("continuity", "auto"), ("bgm_suppression", "prompt")):
            require(policy.get(key, default) == parent.get(key, default), "AUDIO_CONTROL", f"{path}/audio/{key}",
                    f"Keep the authored {key}={parent.get(key, default)!r}; do not change audio control policy.")
        for key in ("vocal", "bgm", "ambience", "effects"):
            require(parent[key] != "none" or policy[key] == "none", "AUDIO_PERMISSION", f"{path}/audio/{key}",
                    f"Parent forbids {key}. Set audio.{key}='none'"
                    + (f", audio.{key}_description=null and remove related sound_events/references." if key != "vocal" else "; remove unauthorized dialogue events."))
        require(all(r in parent["reference_audio"] for r in policy["reference_audio"]), "AUDIO_PERMISSION", path, "Scene adds an unauthorized reference")


def directives(policy, *, explicit=False, compact=False):
    if compact:
        physical = any(policy[k] != "none" for k in ("ambience", "effects"))
        if policy["vocal"] == "none":
            return ("No character vocalization is present. No other ambience or physical sound is present." if physical
                    else "Complete silence.")
        return ("The only character vocalization is the exact shot-synchronized dialogue explicitly specified in this scene. "
                "No other ambience, physical sound, or character vocalization is present.")
    lines = []
    if all(policy[k] == "none" for k in ("vocal", "bgm", "ambience", "effects")):
        lines.append("Complete silence. No speech, narration, singing, humming, character vocalization, music, ambience or sound effects.")
        if explicit:
            lines.append("The audio track is silent for the entire scene, from beginning to end.")
    elif policy["vocal"] == "none":
        lines.append("No speech, narration, singing, humming or character vocalization. Generate only explicitly permitted physical sounds.")
    else:
        lines.append("Only the assigned speaker speaks the exact <d> words during the specified dialogue intervals. No extra words, paraphrasing, repetition, narration, singing, humming or other vocalization. Never read descriptions or reference audio aloud. No vocalization outside dialogue intervals.")
        if explicit and all(policy[k] == "none" for k in ("bgm", "ambience", "effects")):
            lines.append("Dry, isolated spoken dialogue only. Silence before and after each utterance. No backing track.")
    if policy["bgm"] == "none":
        lines.append("No background music or diegetic music.")
    else:
        lines.append("Instrumental music only. No lyrics or singing.")
    for key, label in (("ambience", "ambience"), ("effects", "sound effects")):
        if policy[key] == "none":
            lines.append(f"No {label}.")
    lines.append("Audio references condition timbre and sound character only; do not copy or recite their content.")
    return " ".join(lines)


def audio_records(policy):
    records = [("発声", "なし" if policy["vocal"] == "none" else "指定台詞のみ")]
    for label, field in (("音声接続", "continuity"), ("BGM抑制", "bgm_suppression")):
        if field in policy:
            records.append((label, policy[field]))
    for key, label in (("bgm", "BGM"), ("ambience", "環境音"), ("effects", "効果音")):
        mode = policy[key]
        value = "なし" if mode == "none" else "シーンに合わせて補完" if mode == "expand" else ("生成:" if key == "bgm" else "") + policy[key + "_description"]
        records.append((label, value))
    if not policy["reference_audio"]:
        records.append(("音声参照", "なし"))
    for purpose in ("music", "ambience", "effects"):
        ids = [r["asset_id"] for r in policy["reference_audio"] if r["purpose"] == purpose]
        if ids:
            records.append(("音声参照 " + purpose, ", ".join(ids)))
    return records
