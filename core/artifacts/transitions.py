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


def inherited_states(previous, scene):
    owners = set(scene["actor_ids"]) | set(scene.get("prop_ids", [])) | {"environment"}
    return [state for state in previous["terminal_state"] if state["owner_id"] in owners]


def validate_scene_transition(scene, previous, beat, *, code="TRANSITION", require_state_coverage=False):
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
        terminal = {(s["owner_id"], s["key"]): s["value"] for s in inherited_states(previous, scene)}
        if require_state_coverage:
            initial = {(s["owner_id"], s["key"]) for s in scene["initial_state"]}
            missing = sorted(set(terminal) - initial)
            require(not missing, "STATE_CONTINUITY", path,
                    f"continueの開始状態が不足しています: {missing}。直前Sceneの終了状態を全て引き継ぎ、"
                    "姿勢・位置・小物・環境を開始時点でリセットしないでください。値の変化はScene内に配置してください。")
        for state in scene["initial_state"]:
            key = state["owner_id"], state["key"]
            require(key not in terminal or terminal[key] == state["value"], "STATE_CONTINUITY", path,
                    f"continueの開始状態 {key[0]}.{key[1]} が直前の終了状態と一致しません。"
                    f"必要: {terminal.get(key)!r}、実際: {state['value']!r}。"
                    "値の変化は開始時点の上書きではなく、Scene内の動作へ配置してください。")
