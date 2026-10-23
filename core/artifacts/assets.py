"""Shared file-backed registry. Media is decoded only on the active video path."""
import hashlib
from pathlib import Path

from ..errors import require
from .schema import validate
from .storage import verify_assets
from ..utilities import canonical, digest
from .validation import unique


def registry(entries, root):
    root = Path(root).resolve()
    assets = []
    for entry in entries:
        require(set(entry) <= {"id", "kind", "locator", "sha256", "sample_rate", "sample_count", "fps", "frame_count", "paired_audio_enabled"}, "ASSET_FIELD", "/", "Unknown material field")
        asset = dict(sample_rate=None, sample_count=None, fps=None, frame_count=None, paired_audio_enabled=False, **{k: v for k, v in entry.items() if k not in {"sample_rate", "sample_count", "fps", "frame_count", "paired_audio_enabled"}})
        asset.update({k: entry[k] for k in ("sample_rate", "sample_count", "fps", "frame_count", "paired_audio_enabled") if k in entry})
        path = Path(asset["locator"])
        path = path if path.is_absolute() else root / path
        require(path.is_file(), "MISSING_ASSET", asset["id"], str(path))
        if "sha256" not in asset:
            hasher = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(block)
            asset["sha256"] = hasher.hexdigest()
        validate(asset, "asset")
        require(asset["kind"] == "video" or not asset["paired_audio_enabled"], "ASSET_AUDIO", asset["id"], "Only video can carry paired audio")
        assets.append(asset)
    unique(assets, "assets")
    verify_assets(assets, root)
    return dict(assets=assets, root=str(root), fingerprint=digest(canonical(assets)))


def path_for(asset, asset_root):
    path = Path(asset["locator"])
    return str(path if path.is_absolute() else Path(asset_root) / path)


