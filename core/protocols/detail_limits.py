"""Bound generated verbosity; author constants remain byte-for-byte unchanged."""
import re


def bound_detail_strings(schema, payload, *, compact=False):
    beat = next(beat for beat in payload["scenario"]["beats"] if beat["id"] == payload["outline"]["beat_id"])
    source = [beat["text"], *beat.get("direction", []), *payload["scenario"].get("direction", [])]
    # Authored visible text is immutable even when longer than a normal prose field.
    visible = [match[1] for text in source for match in re.finditer(r"<visible>(.*?)</visible>", text, re.S)]
    limit = max([512 if compact else 1024, *[len(text) + 256 for text in visible]])
    id_limit = max(128, len(payload["outline"]["id"]) + 64)

    def visit(value):
        if isinstance(value, dict):
            if "const" in value or "enum" in value:
                return
            if value.get("type") == "string":
                maximum = id_limit if "pattern" in value else limit
                value["maxLength"] = max(value.get("minLength", 0), min(maximum, value.get("maxLength", maximum)))
                # The upstream converter prioritizes pattern over maxLength. Bound
                # our known generated-ID pattern itself, without altering other regexes.
                if value.get("pattern") == "^[a-z][a-z0-9_]*$":
                    value["pattern"] = f"^[a-z][a-z0-9_]{{0,{id_limit - 1}}}$"
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    return schema
