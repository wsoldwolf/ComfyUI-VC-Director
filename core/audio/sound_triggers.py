"""Scene-local sound bindings and concrete Japanese validation failures."""
from ..errors import require


def validate_sound_trigger(sound, local_events, scene_id):
    reference = sound["trigger_event_id"]
    if sound["kind"] == "ambience" and reference is None:
        return
    event = local_events.get(reference)
    label = "効果音" if sound["kind"] == "effect" else "環境音"
    candidates = list(local_events.values())
    available = "、".join(f"{item['id']} ({item['start_ms']}..{item['end_ms']}ms)" for item in candidates[:16])
    if len(candidates) > 16:
        available += f"、ほか{len(candidates) - 16}件"
    require(event is not None, "SOUND_TRIGGER", sound["id"],
            f"音の参照先が不正です。Scene {scene_id} の{label} {sound['id']} "
            f"({sound['start_ms']}..{sound['end_ms']}ms) のtrigger_event_id={reference!r} は、"
            "このSceneのShot内のイベントIDではありません。"
            f"参照可能なイベント: {available or 'なし'}。Shot・人物・出典の必須イベント・別SceneのIDは参照できません。")
    require(event["start_ms"] <= sound["start_ms"] < event["end_ms"], "SOUND_TRIGGER", sound["id"],
            f"音の開始時刻が参照先の区間外です。Scene {scene_id} の{label} {sound['id']} "
            f"({sound['start_ms']}..{sound['end_ms']}ms) は、イベント {event['id']} "
            f"({event['start_ms']}..{event['end_ms']}ms) の開始以上・終了未満で開始する必要があります。")
