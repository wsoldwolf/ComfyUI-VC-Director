"""Distribution defaults kept in profiles/, independently of node wrappers."""
import json
from pathlib import Path

from ..errors import require
from .runtime import LlamaRuntimeConfig

PROFILE_PATH = Path(__file__).resolve().parents[2] / "profiles/runtime/gemma4.json"


def load_runtime_profile():
    value = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    require(set(value) == {"schema", "model_name", "shared", "stages"} and value["schema"] == "VCD_RUNTIME_PROFILE_V1", "RUNTIME_PROFILE", str(PROFILE_PATH), "Invalid runtime profile")
    require(isinstance(value["model_name"], str) and bool(value["model_name"].strip()), "RUNTIME_PROFILE", "model_name", "Model name is required")
    require(set(value["stages"]) == {"enhancer", "planner", "compiler", "vision"}, "RUNTIME_PROFILE", "stages", "Expected four stage settings")
    return value


def default_model_name():
    return load_runtime_profile()["model_name"]


def runtime_config(stage, *, seed=None, n_ctx=None):
    profile = load_runtime_profile()
    require(stage in profile["stages"], "RUNTIME_PROFILE", stage, "Unknown inference stage")
    settings = {**profile["shared"], **profile["stages"][stage]}
    if seed is not None:
        settings["seed"] = seed
    if n_ctx is not None:
        settings["n_ctx"] = n_ctx
    result = LlamaRuntimeConfig(**settings)
    result.validate()
    return result
