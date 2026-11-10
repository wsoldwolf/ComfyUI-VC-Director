"""Allocate generated timeline IDs while retaining typed event references."""
from collections import Counter, defaultdict
from ..errors import require


def resolve_generated_ids(scene, previous_scenes, reserved_ids=(), *, authored_dialogue_ids=()):
    shots = scene["shots"]
    events = [event for shot in shots for event in shot["events"]]
    entries = [(f"shots/{i}", shot) for i, shot in enumerate(shots)]
    entries += [(f"shots/{i}/events/{j}", event) for i, shot in enumerate(shots)
                for j, event in enumerate(shot["events"])]
    entries += [(f"sound_events/{i}", sound) for i, sound in enumerate(scene["sound_events"])]
    local = [item["id"] for _, item in entries]
    occupied = set(reserved_ids)
    for previous in previous_scenes:
        occupied.add(previous["id"])
        occupied.update(shot["id"] for shot in previous["shots"])
        occupied.update(event["id"] for shot in previous["shots"] for event in shot["events"])
        occupied.update(event["id"] for event in previous["sound_events"])
    occupied.add(scene["id"])
    dialogue_ids = [event["id"] for event in events if event["kind"] == "dialogue"]
    repeated_dialogue = [id for id, count in Counter(dialogue_ids).items() if count > 1]
    require(not repeated_dialogue and not set(dialogue_ids) & occupied, "DUPLICATE_ID", scene["id"],
            f"生成されたScene {scene['id']} で台詞IDが重複しています: "
            f"{sorted(set(repeated_dialogue) | (set(dialogue_ids) & occupied))}。"
            "作者の台詞IDは変更できません。原文の各台詞を一度ずつ配置してください。"
            "同一Scene内の重複: " + ", ".join(repeated_dialogue)
            + "。以前のScene・予約済みIDとの衝突: " + ", ".join(sorted(set(dialogue_ids) & occupied)))

    # SOUND.trigger_event_id refers to a local Shot event, never a Shot or
    # another sound. Bind the object before changing any IDs. A repeated camera
    # with no incoming reference is safe to rename; two possible trigger events
    # are not safe to choose between, even if their prose looks identical.
    candidates = defaultdict(list)
    for event in events:
        candidates[event["id"]].append(event)
    triggers = []
    for sound in scene["sound_events"]:
        target = sound["trigger_event_id"]
        matches = candidates.get(target, [])
        require(len(matches) <= 1, "DUPLICATE_ID", scene["id"],
                f"Ambiguous trigger_event_id {target!r} for sound {sound['id']!r}: "
                f"{len(matches)} local events use this ID. Give the events distinct IDs and explicitly "
                "reference the intended event; do not remove any action, dialogue or sound.")
        if matches:
            triggers.append((sound, matches[0]))

    # Reserve authored speech before visiting generated entries, regardless of
    # array order. Neither a Shot nor a camera can take a dialogue's identity.
    occupied.update(dialogue_ids)
    occupied.update(authored_dialogue_ids)
    unavailable = occupied | set(local)
    remapped = []
    for path, item in entries:
        old = item["id"]
        if item.get("kind") == "dialogue":
            continue
        if old not in occupied:
            occupied.add(old)
            continue
        base = f"{scene['id']}_{old}"
        new, suffix = base, 2
        while new in unavailable:
            new = f"{base}_{suffix}"
            suffix += 1
        item["id"] = new
        occupied.add(new)
        unavailable.add(new)
        remapped.append(dict(path=path, old_id=old, new_id=new))
    for sound, target in triggers:
        sound["trigger_event_id"] = target["id"]
    return remapped
