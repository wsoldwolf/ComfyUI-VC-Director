"""Allocate generated requirement IDs before any Scene references exist."""


def resolve_required_event_ids(scenario, enhanced):
    """Preserve authored IDs and every requirement, renaming generated collisions.

    Call only before outline construction. There are no references to requirements
    inside EnhancedStory, so each generated record can be allocated independently,
    even when two records initially have the same ID.
    """
    authored = scenario["actors"] + scenario["backgrounds"] + scenario["beats"] + scenario["constraints"]
    authored += scenario.get("props", [])
    authored += [dialogue for beat in scenario["beats"] for dialogue in beat["dialogues"]]
    occupied = {item["id"] for item in authored}
    unavailable = occupied | {event["id"] for beat in enhanced["beats"] for event in beat["required_events"]}
    remapped = []
    for beat in enhanced["beats"]:
        for index, event in enumerate(beat["required_events"], 1):
            old = event["id"]
            if old in occupied:
                base = f"req_{beat['id']}_{index}"
                new, suffix = base, 2
                while new in unavailable:
                    new = f"{base}_{suffix}"
                    suffix += 1
                event["id"] = new
                unavailable.add(new)
                remapped.append(dict(beat_id=beat["id"], index=index, old_id=old, new_id=new))
            occupied.add(event["id"])
    return remapped
