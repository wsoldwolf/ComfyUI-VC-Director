"""Immutable revisions and one atomic metadata pointer prevent mixed JSON pairs."""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..compiler import CompileResult
from ..errors import require
from .schema import validate
from ..utilities import canonical, digest, normalize, pretty_json


def verify_assets(assets, asset_root):
    for asset in assets:
        validate(asset, "asset")
        path = Path(asset["locator"])
        if not path.is_absolute():
            path = Path(asset_root) / path
        require(path.is_file(), "MISSING_ASSET", asset["id"], f"Missing material: {path}")
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(block)
        require(hasher.hexdigest() == asset["sha256"], "ASSET_HASH", asset["id"], "Material changed since registration")


def save(result: "CompileResult", output_root, scenario_emd=None, run_details=None):
    root = Path(output_root) / result.document["project_id"]
    revisions = root / "revisions"
    revisions.mkdir(parents=True, exist_ok=True)
    contents = {"scenes.emd": result.scene_emd, "plan.json": pretty_json(result.plan), "execution.json": pretty_json(result.execution)}
    if scenario_emd is not None:
        contents["scenario.emd"] = normalize(scenario_emd)
    metadata = dict(result.metadata, run_details=run_details or {}, files={name: digest(text) for name, text in contents.items()})
    contents["run_metadata.json"] = pretty_json(metadata)
    revision = digest(contents["run_metadata.json"])
    target = revisions / revision
    temporary = Path(tempfile.mkdtemp(prefix=".pending-", dir=revisions))
    try:
        for name, text in contents.items():
            with (temporary / name).open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        if target.exists():
            require(all((target / name).read_bytes() == text.encode("utf-8") for name, text in contents.items()), "REVISION_COLLISION", revision, "Existing immutable revision differs")
        else:
            temporary.rename(target)
        pointer = pretty_json(dict(schema="VCD_REVISION_POINTER_V1", active_revision=revision))
        fd, pointer_name = tempfile.mkstemp(prefix=".metadata-", dir=root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(pointer)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(pointer_name, root / "run_metadata.json")
        finally:
            if os.path.exists(pointer_name):
                os.unlink(pointer_name)
    finally:
        if temporary.exists():
            require(temporary.resolve().parent == revisions.resolve() and temporary.name.startswith(".pending-"), "TEMP_PATH", str(temporary), "Unexpected temporary directory")
            shutil.rmtree(temporary)
    return target


def load(project_root, asset_root=None, runtime_root=None):
    root = Path(project_root)
    pointer = json.loads((root / "run_metadata.json").read_text(encoding="utf-8"))
    revision = pointer.get("active_revision", "")
    import re
    require(re.fullmatch(r"[0-9a-f]{64}", revision) is not None, "REVISION", "/", "Invalid revision pointer")
    directory = root / "revisions" / revision
    raw_metadata = (directory / "run_metadata.json").read_text(encoding="utf-8")
    require(digest(raw_metadata) == revision, "METADATA_HASH", "/", "Revision metadata changed")
    metadata = json.loads(raw_metadata)
    contents = {}
    for name, expected in metadata["files"].items():
        require(name in {"plan.json", "execution.json", "scenes.emd", "scenario.emd"}, "REVISION_FILE", name, "Unknown revision file")
        raw = (directory / name).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == expected, "FILE_HASH", name, "Revision file changed or pair is mixed")
        contents[name] = raw.decode("utf-8")
    require({"plan.json", "execution.json", "scenes.emd"} <= set(contents), "REVISION_FILE", "/", "Incomplete revision")
    plan = json.loads(contents["plan.json"])
    execution = validate(json.loads(contents["execution.json"]), "execution")
    # Cross-artifact hashes describe JSON values; metadata.files verifies exact file bytes.
    # This accepts both previous compact revisions and the readable serialization.
    require(execution["plan_sha256"] == digest(canonical(plan)) and execution["scene_emd_sha256"] == digest(contents["scenes.emd"]), "PAIR_HASH", "/", "Plan, execution and EMD mismatch")
    require(metadata["execution_sha256"] == digest(canonical(execution)), "PAIR_HASH", "/", "Execution hash mismatch")
    from ..compiler import compile_scene_emd
    from ..compiler.api import LEGACY_PROMPT_POLICY

    class ReplayTranslator:
        def __init__(self):
            self.mapping = {t["source"]: t["translated"] for t in metadata["translation_trace"]}

        def __call__(self, text):
            require(text in self.mapping, "TRANSLATION_TRACE", "/", "Missing saved translation")
            return self.mapping[text]

        def translate_protected(self, fragments, values):
            source = "".join(part + (values[i] if i < len(values) else "") for i, part in enumerate(fragments))
            return self(source)

    replay = compile_scene_emd(contents["scenes.emd"], execution["assets"], ReplayTranslator(),
                               prompt_policy=metadata.get("prompt_policy_version", LEGACY_PROMPT_POLICY),
                               replay_output_sample_rate=execution["runtime_contract"]["output_sample_rate"])
    require(replay.plan == plan and replay.execution == execution, "COMPILED_MISMATCH", "/", "Saved artifacts fail deterministic recompilation")
    if asset_root is not None:
        verify_assets(execution["assets"], asset_root)
    if runtime_root is not None:
        from ..h3_contract import verify_runtime
        verify_runtime(runtime_root, independent_audio=bool(execution["runtime_contract"].get("independent_audio_contract")))
    return replay
