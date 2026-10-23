"""ComfyUI adapter; public node surface is preserved."""
import json
from ...core.artifacts.assets import registry
from ..common import CATEGORY


class VCDirectorAssetRegistry:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": dict(assets_json=("STRING", {"default": "[]", "multiline": True}), asset_root=("STRING", {"default": ""}))}
    RETURN_TYPES = ("VC_ASSET_REGISTRY", "STRING")
    RETURN_NAMES = ("registry", "fingerprint")
    FUNCTION = "build"
    CATEGORY = CATEGORY

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")  # Read file hashes again when queued.

    def build(self, assets_json, asset_root):
        if not asset_root:
            import folder_paths
            asset_root = folder_paths.get_input_directory()
        value = registry(json.loads(assets_json), asset_root)
        return value, value["fingerprint"]
