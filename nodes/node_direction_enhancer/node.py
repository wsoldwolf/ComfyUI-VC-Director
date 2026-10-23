"""ComfyUI adapter; public node surface is preserved."""
import json
from ...core.emd import parse
from ...core.protocols import StageRunner
from ...core.direction import StoryEnhancer
from ...core.utilities import canonical
from ..common import CATEGORY, _backend, _model_fields, cache_root


class VCDirectorEnhancer:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": dict(scenario_emd=("STRING", {"multiline": True}), image_facts_json=("STRING", {"default": "[]", "multiline": True}), **_model_fields())}
    RETURN_TYPES = ("VC_ENHANCED_STORY", "STRING")
    RETURN_NAMES = ("enhanced_story", "enhanced_json")
    FUNCTION = "enhance"
    CATEGORY = CATEGORY

    def enhance(self, scenario_emd, image_facts_json, model_name, seed):
        backend = _backend(model_name, seed=seed, stage="enhancer")
        try:
            result = StoryEnhancer(StageRunner(backend, cache_root() / "story")).enhance(parse(scenario_emd), json.loads(image_facts_json))
            return result, canonical(result)
        finally:
            backend.close()
