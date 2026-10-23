"""Use the published JSON Schema as the single structural authority."""
import json
from functools import lru_cache
from pathlib import Path

from ..errors import VCError


@lru_cache(maxsize=1)
def schema():
    return json.loads((Path(__file__).parent / "schemas/vc-artifacts.schema.json").read_text(encoding="utf-8"))


def validate(value, definition):
    from jsonschema import Draft202012Validator

    root = {"$schema": schema()["$schema"], "$defs": schema()["$defs"], "$ref": f"#/$defs/{definition}"}
    errors = sorted(Draft202012Validator(root).iter_errors(value), key=lambda e: str(list(e.absolute_path)))
    if errors:
        error = errors[0]
        path = "/" + "/".join(str(part) for part in error.absolute_path)
        raise VCError("SCHEMA", path, error.message)
    return value
