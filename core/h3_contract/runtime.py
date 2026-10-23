"""Native Ref2VA graph adapter, without any alias-scanning intermediary."""
from ..errors import require
from ..artifacts.storage import verify_assets
from . import verify_runtime
from ..utilities import canonical, digest


def conditioning_inputs(result, shared_registry, scene_index, prompt, length, runtime_root):
    verify_runtime(runtime_root)
    execution = result.execution
    require(canonical(shared_registry["assets"]) == canonical(execution["assets"]), "REGISTRY_MISMATCH", "/", "Current registry differs from compiled materials")
    require(shared_registry["fingerprint"] == digest(canonical(execution["assets"])), "REGISTRY_HASH", "/", "Invalid shared registry fingerprint")
    verify_assets(execution["assets"], shared_registry["root"])
    require(1 <= scene_index <= len(execution["segments"]), "SCENE_INDEX", "/", "Scene outside Plan")
    segment = execution["segments"][scene_index - 1]
    shot = result.plan["shots"][scene_index - 1]
    require(prompt == shot["prompt"] and length == shot["length"], "CURRENT_SCENE", segment["id"], "Runtime prompt/length differs from compiled Plan")
    assets = {a["id"]: a for a in execution["assets"]}
    active = []
    for binding in segment["reference_bindings"]:
        if binding["role"] != "video_audio" and binding["asset_id"] not in [a["id"] for a in active]:
            active.append(assets[binding["asset_id"]])
    return active
