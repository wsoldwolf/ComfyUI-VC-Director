"""Scene-local audio wording; permission to expand is not a sound event."""
import re


def narrow_silent_direction(text):
    """Remove recognized positive voice-only clauses, preserving visual clauses.

    Timed DIALOGUE is the authority for speakers. Unknown author prose is
    retained, rather than broadly deleting sentences mentioning voices.
    """
    text = re.sub(r"(?:@[a-z][a-z0-9_]*|<Subject [1-4]>)は(?:電話越しの)?声のみ[。．.]?", "", text)
    text = re.sub(r"Only the voice of (?:@[a-z][a-z0-9_]*|<Subject [1-4]>) is heard\.?", "", text, flags=re.I)
    return text.strip()


def scene_soundscape(scene, bindings):
    policy = scene["audio"]
    if any(e["kind"] == "vocal" for shot in scene["shots"] for e in shot["events"]):
        from .control import directives
        return directives(policy)
    spoken = any(e["kind"] == "dialogue" for shot in scene["shots"] for e in shot["events"])
    continuous = any(policy[key + "_description"] for key in ("ambience", "effects")) or any(
        b["role"] in {"ambience", "effects", "video_audio"} for b in bindings)
    physical = continuous or bool(scene["sound_events"])
    if not spoken and not physical:
        return "Complete silence." if policy["bgm"] == "none" else "No diegetic sound or character vocalization."
    if spoken:
        text = "The only character vocalization is the exact shot-synchronized dialogue explicitly specified in this scene."
        if physical:
            text += " Only the specified physical sounds accompany it."
        else:
            text += " No other ambience, physical sound, or character vocalization is present."
    else:
        text = "Only the specified physical sounds are present. No character vocalization is present."
    if not continuous and policy["bgm"] == "none":
        text += " Silence outside the specified dialogue and sound intervals." if spoken else " Silence outside the specified sound intervals."
    return text
