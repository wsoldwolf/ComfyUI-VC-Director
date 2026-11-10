"""ComfyUI adapter; public node surface is preserved."""
from ...core.emd import render_scene
from ...core.emd.diagnostics import parse_checked
from ...core.emd.preflight import preflight, read_image_facts
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
        source, facts = parse_checked(scenario_emd), read_image_facts(image_facts_json)
        preflight(source, scenario_emd, image_facts=facts)
        backend = _backend(model_name, seed=seed, inference_settings=inference_settings)
        try:
            document = StoryTimelinePlanner(StageRunner(backend, cache_root() / "story")).plan(source, enhanced_story, image_facts=facts)
            return render_scene(document), document
        finally:
            backend.close()
