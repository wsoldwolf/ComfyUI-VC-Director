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
        entries = json.loads(assets_json)
        if not asset_root:
            import folder_paths
            asset_root = folder_paths.get_input_directory()
        # Browser uploads live in ComfyUI input even when other assets use a custom root.
        # Resolve the standard annotation before persisting the compiled asset registry.
        for index, entry in enumerate(entries):
            if isinstance(entry, dict) and entry.get("kind") == "image":
                locator = entry.get("locator")
                if isinstance(locator, str) and locator.endswith(" [input]"):
                    import folder_paths
                    entries[index] = dict(entry, locator=folder_paths.get_annotated_filepath(locator))
        value = registry(entries, asset_root)
        return value, value["fingerprint"]
