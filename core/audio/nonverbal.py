"""Closed nonverbal vocabulary: never turn model-authored prose into speech."""
from ..errors import require

VOICES = {
    "軽い息遣い": ("breath", "breathes softly"),
    "荒い息遣い": ("pant", "pants with exertion"),
    "溜め息": ("sigh", "sighs"),
    "息を呑む": ("gasp", "gasps briefly"),
    "小さな笑い声": ("chuckle", "chuckles softly"),
    "笑い声": ("laugh", "laughs without words"),
    "力む声": ("effort", "makes a brief nonverbal effort sound"),
    "短い呻き声": ("groan", "groans briefly without words"),
    "すすり泣き": ("sob", "sobs without words"),
    "悲鳴": ("scream", "gives a brief wordless scream"),
}
VOICE_TEXT = {code: text for code, text in VOICES.values()}
VOICE_LABEL = {code: label for label, (code, _) in VOICES.items()}
MODES = {"なし": "none", "指定台詞のみ": "dialogue",
         "非言語発声のみ": "nonverbal", "指定台詞と非言語発声": "dialogue_nonverbal"}


def permits_dialogue(policy):
    return policy["vocal"] in {"dialogue", "dialogue_nonverbal"}


def permits_nonverbal(policy):
    return policy["vocal"] in {"nonverbal", "dialogue_nonverbal"}


def voice_ids(value):
    values = [v.strip() for v in value.replace("、", ",").split(",")]
    result = [VOICES[v][0] if v in VOICES else v for v in values]
    require(bool(result) and all(v in VOICE_TEXT for v in result) and len(set(result)) == len(result),
            "NONVERBAL_KIND", "許可", "許可する声をカンマ区切りで指定してください: " + "、".join(VOICES))
    return result


def vocal_text(event, policy):
    config = policy["nonverbal"]
    strength = {"弱め": "quietly", "中程度": "at moderate intensity", "強め": "strongly"}[config["intensity"]]
    return VOICE_TEXT[event["text"]] + ", " + strength + ". Nonverbal sound only; no spoken words."
