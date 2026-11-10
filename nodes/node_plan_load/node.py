"""ComfyUI adapter; public node surface is preserved."""
from pathlib import Path
from ...core.artifacts.assets import registry
from ...core.errors import require
from ...core.artifacts.storage import load
from ...core.utilities import canonical, digest, pretty_json
from ..common import CATEGORY, _guard


class VCDirectorLoad:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": dict(project_directory=("STRING", {"default": "minimal"}), registry=("VC_ASSET_REGISTRY",), runtime_root=("STRING", {"default": ""})), "hidden": {"prompt": "PROMPT"}}
    RETURN_TYPES = ("STRING", "VC_COMPILED", "STRING", "STRING")
    RETURN_NAMES = ("plan_json", "compiled", "generation_fingerprint", "project_name")
    OUTPUT_TOOLTIPS = ("保存されたPlan JSON", "保存されたコンパイル結果", "生成条件の識別子",
                       "EMDの作品id。Production Planのrun_nameへ接続すると、作品名のフォルダへ動画・生成物を保存します。")
    FUNCTION = "read"
    CATEGORY = CATEGORY

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def read(self, project_directory, registry, runtime_root, prompt=None):
        _guard(prompt)
        if not Path(project_directory).is_absolute() and len(Path(project_directory).parts) == 1:
            import folder_paths
            project_directory = Path(folder_paths.get_output_directory()) / "vc_director" / project_directory
        result = load(project_directory, registry["root"], runtime_root)
        require(result.execution["assets"] == registry["assets"], "REGISTRY_MISMATCH", "/", "Materials differ from the saved run")
        fingerprint = digest(canonical(dict(plan=result.plan, execution=result.execution)))
        project_name = result.document["project_id"]
        return {"result": (pretty_json(result.plan), result, fingerprint, project_name),
                "ui": {"project_name": [project_name]}}
