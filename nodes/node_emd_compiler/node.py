"""ComfyUI adapter; public node surface is preserved."""
from pathlib import Path
from ...core.compiler import compile_scene_emd
from ...core.emd import parse
from ...core.inference import GemmaTranslator
from ...core.artifacts.storage import save
from ...core.utilities import pretty_json
from ..common import CATEGORY, _backend, _guard, _model_fields, cache_root


class VCDirectorCompileSave:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": dict(scene_emd=("STRING", {"multiline": True}), registry=("VC_ASSET_REGISTRY",), output_root=("STRING", {"default": ""}), **_model_fields()), "optional": {"inference_settings": ("VC_INFERENCE_SETTINGS",)}, "hidden": {"prompt": "PROMPT"}}
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("revision_directory", "plan_json")
    FUNCTION = "compile_save"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY

    def compile_save(self, scene_emd, registry, output_root, model_name, seed, prompt=None, inference_settings=None):
        _guard(prompt)
        if not output_root:
            import folder_paths
            output_root = str(Path(folder_paths.get_output_directory()) / "vc_director")
        backend = None if model_name == "none" else _backend(model_name, seed=seed, stage="compiler", inference_settings=inference_settings)
        try:
            project_id = parse(scene_emd)["project_id"]
            result = compile_scene_emd(scene_emd, registry["assets"], GemmaTranslator(backend, cache_root() / "translation",
                                       diagnostics_root=Path(output_root) / project_id / "diagnostics") if backend else None)
            directory = save(result, output_root, run_details=dict(model=backend.identity() if backend else None))
            return str(directory), pretty_json(result.plan)
        finally:
            if backend:
                backend.close()
