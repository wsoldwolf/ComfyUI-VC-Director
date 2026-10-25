import hashlib
import json


def normalize(text):
    return text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def pretty_json(value):
    """Readable JSON for files and node outputs; hashes use canonical JSON."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def timestamp(ms):
    return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"
