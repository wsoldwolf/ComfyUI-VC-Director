"""ComfyUI adapter; public node surface is preserved."""
from ...core.emd.diagnostics import parse_checked
from ...core.emd.preflight import preflight, read_image_facts
from ...core.protocols import StageRunner
from ...core.direction import StoryEnhancer
from ...core.utilities import canonical
from ..common import CATEGORY, _backend, _model_fields, cache_root


class VCDirectorEnhancer:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": dict(scenario_emd=("STRING", {"multiline": True}), image_facts_json=("STRING", {"default": "[]", "multiline": True}), **_model_fields(allow_none=False)), "optional": {"inference_settings": ("VC_INFERENCE_SETTINGS",)}}
    RETURN_TYPES = ("VC_ENHANCED_STORY", "STRING")
    RETURN_NAMES = ("enhanced_story", "enhanced_json")
    FUNCTION = "enhance"
    CATEGORY = CATEGORY

    def enhance(self, scenario_emd, image_facts_json, model_name, seed, inference_settings=None):
        document, facts = parse_checked(scenario_emd), read_image_facts(image_facts_json)
        preflight(document, scenario_emd, image_facts=facts)
        backend = _backend(model_name, seed=seed, stage="enhancer", inference_settings=inference_settings)
        try:
            result = StoryEnhancer(StageRunner(backend, cache_root() / "story")).enhance(document, facts)
            return result, canonical(result)
        finally:
            backend.close()
