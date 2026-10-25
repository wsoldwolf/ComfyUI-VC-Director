"""ComfyUI adapter; public node surface is preserved."""

from ..common import CATEGORY, cache_root, model_roots
from ...core.inference.settings import resolve_inference_config
from ...core.inference import runtime_config


class VCDirectorImageToSubjectEMD:
    @classmethod
    def INPUT_TYPES(cls):
        from ...core.vision import discover_vision_model_pairs
        choices = [p.selection_id for p in discover_vision_model_pairs(model_roots())] or ["(Vision GGUF + mmproj required)"]
        return {"required": dict(image=("IMAGE",), model_name=(choices,), entity_id=("STRING", {"default": "hero"}), asset_id=("STRING", {"default": "hero_image"}), name=("STRING", {"default": "主人公"}),
                                  analysis_profile=(["general", "subject_only", "scene_only"], {"default": "subject_only"}), subject_hint=("STRING", {"default": "", "multiline": True}),
                                  additional_instruction=("STRING", {"default": "", "multiline": True}), hint_mode=(["observe_only", "assist", "lock_identity"], {"default": "lock_identity"}), hint_conflict=(["warn", "strict"],)),
                "optional": dict(inference_settings=("VC_INFERENCE_SETTINGS",), seed=("INT", dict(default=runtime_config("vision").seed, min=1, max=2147483647)))}
    RETURN_TYPES = ("STRING", "VC_ENTITY_BINDING", "IMAGE", "STRING")
    RETURN_NAMES = ("emd_fragment", "reference_bindings", "image", "observations_json")
    FUNCTION = "observe"
    CATEGORY = CATEGORY

    def observe(self, image, model_name, inference_settings=None, seed=1, **kwargs):
        from ...core.vision import image_to_entity
        from ...core.vision import resolve_vision_model_pair
        pair = resolve_vision_model_pair(model_name, model_roots())
        return image_to_entity(image, pair, cache_root=cache_root() / "vision",
                               config=resolve_inference_config("vision", inference_settings, seed=seed), **kwargs)
