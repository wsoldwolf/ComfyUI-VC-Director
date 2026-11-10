"""Shared timing invariant for authored speech and permitted nonverbal events."""
from ..errors import require


def validate_vocal_timing(scene):
    events = [event for shot in scene["shots"] for event in shot["events"]
              if event["kind"] in {"dialogue", "vocal"}]
    for event in events:
        if event["kind"] != "vocal":
            continue
        conflicts = [other for other in events if other is not event
                     and other["actor_id"] == event["actor_id"]
                     and event["start_ms"] < other["end_ms"]
                     and other["start_ms"] < event["end_ms"]]
        require(not conflicts, "NONVERBAL_OVERLAP", event["id"],
                f"同じ人物 @{event['actor_id']} の非言語発声 {event['id']} "
                f"({event['start_ms']}..{event['end_ms']}ms) が、"
                + "、".join(f"{other['id']} ({other['start_ms']}..{other['end_ms']}ms)" for other in conflicts)
                + " と重複しています。台詞の本文・話者・時刻を保持し、非言語発声だけを元のShot内の空き時間へ移してください。")
