import hashlib
import json
from pathlib import Path

from ..errors import require
from ..utilities import canonical, digest

CONTRACT = json.loads((Path(__file__).parent / "contracts/context-loop-timing.json").read_text(encoding="utf-8"))


def quantize(ms):
    return (ms * 24 + 500) // 1000


def lengths(duration_ms, continued):
    context = 22 if continued else 0
    needed = (duration_ms * 24 + 999) // 1000 + context
    raw = max(5, ((needed - 5 + 16) // 17) * 17 + 5)
    require(raw <= CONTRACT["max_raw_length"], "RAW_LENGTH", "/", "H3 raw length exceeds contract")
    return raw, context


def event_timing(event, context, output_start):
    start, end = quantize(event["start_ms"]), quantize(event["end_ms"])
    require(start < end, "ZERO_FRAME_EVENT", event["id"], "Event collapses on the H3 frame grid")
    return dict(event_id=event["id"], planned_start_ms=event["start_ms"], planned_end_ms=event["end_ms"],
                raw_start_frame=context + start, raw_end_frame=context + end,
                output_start_frame=output_start + start, output_end_frame=output_start + end)


def verify_runtime(root, *, independent_audio=False):
    root = Path(root)
    require(digest(canonical(CONTRACT["source_files"])) == CONTRACT["source_fingerprint"], "RUNTIME_CONTRACT", "/", "Bundled source fingerprint is inconsistent")
    for name, expected in CONTRACT["source_files"].items():
        path = root / name
        require(path.is_file(), "RUNTIME_CONTRACT", name, "Missing runtime source")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        require(actual == expected, "RUNTIME_CONTRACT", name, "Runtime changed; compatibility verification is required")
    if independent_audio:
        extension = CONTRACT["independent_audio"]
        require(digest(canonical(extension["source_files"])) == extension["source_fingerprint"],
                "RUNTIME_CONTRACT", "/", "Independent audio contract fingerprint is inconsistent")
        for name, expected in extension["source_files"].items():
            path = root / name
            require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                    "RUNTIME_CONTRACT", name, "Independent audio Guide runtime changed; verify timing and trim before use")
    return CONTRACT
