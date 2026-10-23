"""ComfyUI adapter; public node surface is preserved."""
import json
from pathlib import Path
from ...core.artifacts.assets import registry
from ...core.compiler import compile_scene_emd
from ...core.emd import parse, render_scene
from ...core.errors import require
from ...core.inference import GemmaTranslator
from ...core.protocols import StageRunner
from ...core.direction import StoryEnhancer
from ...core.planner import StoryTimelinePlanner
from ...core.artifacts.storage import save
from ...core.utilities import canonical
from ..common import CATEGORY, _backend, _guard, _model_fields, cache_root


class VCDirectorBuild:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": dict(input_mode=(["scenario", "saved_scene"],), emd=("STRING", {"multiline": True}), registry=("VC_ASSET_REGISTRY",),
                                  output_root=("STRING", {"default": ""}), image_facts_json=("STRING", {"default": "[]", "multiline": True}), **_model_fields()), "hidden": {"prompt": "PROMPT"}}
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("scene_emd", "revision_directory", "plan_json")
    FUNCTION = "build"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY

    def build(self, input_mode, emd, registry, output_root, image_facts_json, model_name, seed, prompt=None):
        _guard(prompt)
        require(input_mode in {"scenario", "saved_scene"}, "INPUT_MODE", "/", "Unknown language input mode")
        scenario_emd, details = None, {}
        if input_mode == "scenario":
            backend = _backend(model_name, seed=seed)
            try:
                runner = StageRunner(backend, cache_root() / "story")
                scenario = parse(emd)
                enhanced = StoryEnhancer(runner).enhance(scenario, json.loads(image_facts_json))
                document = StoryTimelinePlanner(runner).plan(scenario, enhanced)
                scene_emd = render_scene(document)
                scenario_emd = emd
                details = dict(planning_model=runner._identity, planning_trace=runner.trace, enhanced_story=enhanced)
            finally:
                backend.close()
        else:
            scene_emd = emd
        backend = None if model_name == "none" else _backend(model_name, seed=seed, stage="compiler")
        try:
            result = compile_scene_emd(scene_emd, registry["assets"], GemmaTranslator(backend, cache_root() / "translation") if backend else None)
            details["translation_model"] = backend.identity() if backend else None
            if not output_root:
                import folder_paths
                output_root = str(Path(folder_paths.get_output_directory()) / "vc_director")
            directory = save(result, output_root, scenario_emd, details)
            return scene_emd, str(directory), canonical(result.plan)
        finally:
            if backend:
                backend.close()
