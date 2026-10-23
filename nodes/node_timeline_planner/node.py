"""ComfyUI adapter; public node surface is preserved."""
from ...core.emd import parse, render_scene
from ...core.protocols import StageRunner
from ...core.planner import StoryTimelinePlanner
from ..common import CATEGORY, _backend, _model_fields, cache_root


class VCDirectorTimelinePlanner:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": dict(scenario_emd=("STRING", {"multiline": True}), enhanced_story=("VC_ENHANCED_STORY",), **_model_fields())}
    RETURN_TYPES = ("STRING", "VC_SCENE_DOCUMENT")
    RETURN_NAMES = ("scene_emd", "document")
    FUNCTION = "plan"
    CATEGORY = CATEGORY

    def plan(self, scenario_emd, enhanced_story, model_name, seed):
        backend = _backend(model_name, seed=seed)
        try:
            document = StoryTimelinePlanner(StageRunner(backend, cache_root() / "story")).plan(parse(scenario_emd), enhanced_story)
            return render_scene(document), document
        finally:
            backend.close()
