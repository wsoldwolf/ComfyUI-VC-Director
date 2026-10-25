"""Shared ComfyUI model discovery, execution settings and group guard."""
from pathlib import Path
from ...core.errors import require
from ...core.inference import (
    GemmaBackend, ModelSelectionError, default_model_name, discover_gguf_models,
    resolve_model_selection, runtime_config,
)
from ...core.inference.settings import resolve_inference_config

CATEGORY = "VC Director"
NO_GGUF_MODELS = "(GGUF models not found)"

def model_roots():
    try:
        import folder_paths
    except ModuleNotFoundError:
        return {}  # Offline workflow generation does not import ComfyUI.
    models_dir = getattr(folder_paths, "models_dir", None)
    roots = {"default": Path(models_dir) / "LLM/GGUF"} if models_dir else {}
    getter = getattr(folder_paths, "get_folder_paths", None)
    if not callable(getter):
        return roots
    for category in ("LLM", "gguf", "llm"):
        try:
            paths = getter(category)
        except KeyError:
            paths = []
        for i, path in enumerate(paths):
            roots[f"{category}_{i}_GGUF"] = Path(path) / "GGUF"
            roots[f"{category}_{i}"] = Path(path)
    return roots


def model_path(selection):
    require(bool(selection) and selection not in {"none", NO_GGUF_MODELS}, "MODEL_REQUIRED", "model_name",
            "model_nameでGGUFモデルを選択してください。noneは英語Scene EMDのコンパイル専用です。")
    path = Path(selection)
    if path.is_file():
        return path
    try:
        return resolve_model_selection(selection, model_roots()).path
    except ModelSelectionError:
        require(False, "MODEL_SELECTION", selection,
                "GGUFが見つからないか選択が曖昧です。モデル一覧を更新してmodel_nameから選び直してください。検索先: models/LLM/GGUF、登録済みLLM・gguf・llmフォルダ。")


def cache_root():
    import folder_paths
    return Path(folder_paths.get_temp_directory()) / "vc_director"


def _backend(selection, n_ctx=None, seed=1, *, stage="planner", inference_settings=None):
    config = resolve_inference_config(stage, inference_settings, seed=seed, n_ctx=n_ctx)
    return GemmaBackend(model_path(selection), config)


def _guard(prompt):
    if not isinstance(prompt, dict):
        return
    types = {n.get("class_type") for n in prompt.values() if isinstance(n, dict)}
    require(not ({"VCDirectorBuild", "VCDirectorCompileSave"} & types and "VCDirectorLoad" in types), "WORKFLOW_CONFLICT", "/", "Mute either the language group or the video group before queuing")


def _model_fields(*, allow_none=True):
    models = discover_gguf_models(model_roots())
    choices = [model.selection_id for model in models]
    preferred = next((model.selection_id for model in models if model.relative_path == default_model_name()), None)
    if preferred is None and choices:
        preferred = choices[0]
    if allow_none:
        choices.append("none")
    elif not choices:
        choices.append(NO_GGUF_MODELS)
    return dict(model_name=(choices, {
                    "default": preferred or choices[0],
                    "tooltip": "シナリオ生成に使うGGUF。noneはsaved_sceneで英語描写をコンパイルする場合だけ使用できます。",
                }), seed=("INT", {"default": runtime_config("planner").seed, "min": 1, "max": 2147483647}))
