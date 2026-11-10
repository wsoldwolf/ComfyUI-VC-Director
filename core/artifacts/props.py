"""Props use stable @IDs but never acquire a person's speaking role."""
import re
from .visible_text import outside_visible_text

SYMBOL = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_-]*)")


def visible_props(scene):
    return set(scene.get("visible_prop_ids", scene.get("prop_ids", [])))


def tag_ids(texts):
    return {match[1] for text in texts for match in SYMBOL.finditer(outside_visible_text(text))}
