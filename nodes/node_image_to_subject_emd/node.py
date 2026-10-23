"""ComfyUI adapter; public node surface is preserved."""

from ..common import CATEGORY, cache_root, model_roots


class VCDirectorImageToSubjectEMD:
    @classmethod
    def INPUT_TYPES(cls):
        from ...core.vision import discover_vision_model_pairs
        choices = [p.selection_id for p in discover_vision_model_pairs(model_roots())] or ["(Vision GGUF + mmproj required)"]
        return {"required": dict(image=("IMAGE",), model_name=(choices,), entity_id=("STRING", {"default": "hero"}), asset_id=("STRING", {"default": "hero_image"}), name=("STRING", {"default": "主人公"}),
                                  analysis_profile=(["general", "subject_only", "scene_only"], {"default": "subject_only"}), subject_hint=("STRING", {"default": "", "multiline": True}),
                                  additional_instruction=("STRING", {"default": "", "multiline": True}), hint_mode=(["observe_only", "assist", "lock_identity"], {"default": "lock_identity"}), hint_conflict=(["warn", "strict"],))}
    RETURN_TYPES = ("STRING", "VC_ENTITY_BINDING", "IMAGE", "STRING")
    RETURN_NAMES = ("emd_fragment", "reference_bindings", "image", "observations_json")
    FUNCTION = "observe"
    CATEGORY = CATEGORY

    def observe(self, image, model_name, **kwargs):
        from ...core.vision import image_to_entity
        from ...core.vision import resolve_vision_model_pair
        pair = resolve_vision_model_pair(model_name, model_roots())
        return image_to_entity(image, pair, cache_root=cache_root() / "vision", **kwargs)
