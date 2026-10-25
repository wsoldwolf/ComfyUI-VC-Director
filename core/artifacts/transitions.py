"""Author-owned beat connection policies and Scene continuity checks."""
from ..errors import require


def validate_beat_transitions(beats):
    previous = None
    for beat in beats:
        if previous is not None and beat.get("transition", "auto") == "continue":
            require(set(previous["actor_ids"]) == set(beat["actor_ids"])
                    and previous["background_id"] == beat["background_id"],
                    "BEAT_TRANSITION", beat["id"] + "/transition",
                    "接続: continueには直前の展開と同じ背景ID・出演者集合が必要です。"
                    "背景・出演者を揃えるか、この展開を接続: cut又はautoに変更してください。")
        previous = beat


def validate_scene_transition(scene, previous, beat, *, code="TRANSITION"):
    path = scene["id"]
    if previous is None:
        require(scene["transition"] == "cut", code, path,
                "First Scene must use transition='cut' and depends_on=[]; no prior Scene exists")
    mode = beat.get("transition", "auto")
    if mode != "auto":
        expected = mode if previous is not None else "cut"
        require(scene["transition"] == expected, "BEAT_TRANSITION", path,
                f"Beat {beat['id']} requests {mode}; this Scene must use transition='{expected}'. "
                "The first Scene of the whole work is always cut; preserve the authored connection policy.")
    if previous is not None and scene["transition"] == "continue":
        require(previous["id"] in scene["depends_on"]
                and set(previous["actor_ids"]) == set(scene["actor_ids"])
                and previous["background_id"] == scene["background_id"], code, path,
                f"Continue requires depends_on to include {previous['id']!r}, "
                "with the same cast and background as that Scene.")
        terminal = {(s["owner_id"], s["key"]): s["value"] for s in previous["terminal_state"]}
        for state in scene["initial_state"]:
            key = state["owner_id"], state["key"]
            require(key not in terminal or terminal[key] == state["value"], "STATE_CONTINUITY", path,
                    f"Continued initial_state {key[0]}.{key[1]} must retain "
                    f"previous terminal value {terminal.get(key)!r}; received {state['value']!r}.")
