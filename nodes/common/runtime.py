"""Shared ComfyUI model discovery, execution settings and group guard."""
from pathlib import Path
from ...core.errors import require
from ...core.inference import GemmaBackend, default_model_name, runtime_config

CATEGORY = "VC Director"

def model_roots():
    import folder_paths
    roots = {"default": Path(folder_paths.models_dir) / "LLM/GGUF"}
    for category in ("LLM", "gguf", "llm"):
        try:
            paths = folder_paths.get_folder_paths(category)
        except KeyError:
            paths = []
        for i, path in enumerate(paths):
            roots[f"{category}_{i}"] = Path(path)
    return roots


def model_path(selection):
    path = Path(selection)
    if path.is_file():
        return path
    candidates = list(dict.fromkeys((root / selection).resolve() for root in model_roots().values() if (root / selection).is_file()))
    require(len(candidates) == 1, "MODEL_SELECTION", selection, "Model missing or ambiguous; specify a GGUF path")
    return candidates[0]


def cache_root():
    import folder_paths
    return Path(folder_paths.get_temp_directory()) / "vc_director"


def _backend(selection, n_ctx=None, seed=1, *, stage="planner"):
    return GemmaBackend(model_path(selection), runtime_config(stage, n_ctx=n_ctx, seed=seed))


def _guard(prompt):
    if not isinstance(prompt, dict):
        return
    types = {n.get("class_type") for n in prompt.values() if isinstance(n, dict)}
    require(not ({"VCDirectorBuild", "VCDirectorCompileSave"} & types and "VCDirectorLoad" in types), "WORKFLOW_CONFLICT", "/", "Mute either the language group or the video group before queuing")


def _model_fields():
    return dict(model_name=("STRING", {"default": default_model_name()}), seed=("INT", {"default": runtime_config("planner").seed, "min": 1, "max": 2147483647}))
