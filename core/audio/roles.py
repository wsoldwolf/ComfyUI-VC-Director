"""Scene participation is distinct from visibility and actual speaking."""
import re
from ..artifacts.visible_text import outside_visible_text


def scene_roles(scene):
    # Old Scene documents did not distinguish visibility. Do not guess it from
    # telephone/voice prose; keep their explicit cast as the compatibility default.
    visible = set(scene.get("visible_actor_ids", scene["actor_ids"]))
    speakers = {event["actor_id"] for shot in scene["shots"] for event in shot["events"]
                if event["kind"] in {"dialogue", "vocal"}}
    return visible, speakers


def referenced_actors(scene):
    """Actual Scene uses, excluding declarations and literal words inside speech."""
    symbols = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_-]*)")
    used = set(scene.get("visible_actor_ids", []))
    texts = [scene["summary"], *scene.get("visual_directions", [])]
    for shot in scene["shots"]:
        for event in shot["events"]:
            if event["actor_id"] != "environment":
                used.add(event["actor_id"])
            if event["kind"] != "dialogue":
                texts.append(event["text"])
    texts.extend(event["text"] for event in scene["sound_events"])
    texts.extend(scene["audio"][key] for key in ("bgm_description", "ambience_description", "effects_description")
                 if scene["audio"][key])
    for text in texts:
        used.update(symbols.findall(outside_visible_text(text)))
    # actor_ids, subject_descriptions and retention declare participants; they do
    # not themselves justify adding a character or voice to the final prompt.
    return used
