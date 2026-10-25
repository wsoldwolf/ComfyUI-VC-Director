"""ComfyUI adapter; public node surface is preserved."""
import json
from ...core.emd import parse, render_scene
from ...core.protocols import StageRunner
from ...core.planner import StoryTimelinePlanner
from ..common import CATEGORY, _backend, _model_fields, cache_root


class VCDirectorTimelinePlanner:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": dict(scenario_emd=("STRING", {"multiline": True}), enhanced_story=("VC_ENHANCED_STORY",), **_model_fields(allow_none=False)), "optional": {"inference_settings": ("VC_INFERENCE_SETTINGS",), "image_facts_json": ("STRING", {"default": "[]", "multiline": True})}}
    RETURN_TYPES = ("STRING", "VC_SCENE_DOCUMENT")
    RETURN_NAMES = ("scene_emd", "document")
    FUNCTION = "plan"
    CATEGORY = CATEGORY

    def plan(self, scenario_emd, enhanced_story, model_name, seed, inference_settings=None, image_facts_json="[]"):
        backend = _backend(model_name, seed=seed, inference_settings=inference_settings)
        try:
            document = StoryTimelinePlanner(StageRunner(backend, cache_root() / "story")).plan(parse(scenario_emd), enhanced_story, image_facts=json.loads(image_facts_json))
            return render_scene(document), document
        finally:
            backend.close()
