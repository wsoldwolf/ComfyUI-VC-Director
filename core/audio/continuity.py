"""Generated-audio Guide carry, independent of visual cuts when authored."""
from ..errors import require


def context_frames(policy, previous, visual_context, bindings):
    if previous is None or policy.get("continuity", "auto") == "cut":
        return 0
    if policy.get("continuity", "auto") == "continue":
        for key in ("vocal", "bgm", "ambience", "effects"):
            require(previous["audio"][key] == "none" or policy[key] != "none", "AUDIO_CONTINUITY_PERMISSION", key,
                    "直前で許可した音をこのSceneで禁止しています。音声接続: cutで音を切り替えてください。")
        return 22
    # Keep the exact legacy behavior for omitted/auto settings.
    comparable = lambda value: {key: item for key, item in value.items() if key != "continuity"}
    same = comparable(previous["audio"]) == comparable(policy) and previous["bindings"] == bindings
    return 22 if visual_context and same and policy["vocal"] == previous["audio"]["vocal"] == "none" else 0


def negpip_requested(document):
    return any(scene["audio"].get("bgm_suppression", "prompt") == "negpip" for scene in document["scenes"])


NEGPIP_MUSIC = "(background music, instrumental soundtrack:-1.0@a0-)"
