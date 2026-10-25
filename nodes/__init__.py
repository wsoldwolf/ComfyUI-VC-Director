"""ComfyUI-facing registrations; public node IDs remain stable."""
from .node_asset_registry import VCDirectorAssetRegistry
from .node_direction_enhancer import VCDirectorEnhancer
from .node_timeline_planner import VCDirectorTimelinePlanner
from .node_emd_compiler import VCDirectorCompileSave
from .node_story_builder import VCDirectorBuild
from .node_plan_load import VCDirectorLoad
from .node_ref2va import VCDirectorRef2VA
from .node_reference_video import VCDirectorReferenceVideo
from .node_image_to_subject_emd import VCDirectorImageToSubjectEMD
from .node_seed32 import VCDirectorSeed32
from .node_load_text_file import VCDirectorLoadTextFile
from .node_inference_settings import VCDirectorInferenceSettings

NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in (VCDirectorAssetRegistry, VCDirectorEnhancer, VCDirectorTimelinePlanner, VCDirectorCompileSave, VCDirectorBuild, VCDirectorLoad, VCDirectorRef2VA, VCDirectorReferenceVideo, VCDirectorImageToSubjectEMD, VCDirectorSeed32, VCDirectorLoadTextFile, VCDirectorInferenceSettings)}
NODE_DISPLAY_NAME_MAPPINGS = {key: "VC · " + key.removeprefix("VCDirector") for key in NODE_CLASS_MAPPINGS}
NODE_DISPLAY_NAME_MAPPINGS.update(VCDirectorSeed32="VC · 32-bit Seed", VCDirectorLoadTextFile="VC · Load Text File")
NODE_DISPLAY_NAME_MAPPINGS["VCDirectorInferenceSettings"] = "VC · Gemma Settings"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
