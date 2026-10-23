"""Reuse MV image observation and hint handling; adapt to stable VC entity IDs."""
from pathlib import Path

from ..emd import ID, escape
from ..errors import require
from ..inference import _LOCK, interrupt, runtime_config
from ..utilities import canonical, digest
from ..inference.cache import SuccessCache
from .llama_cpp_vision import LlamaCppVisionLifecycle
from .observation import VisionObservationRequest, observe_image, compose_image_to_subject, VISION_PROMPT_VERSION, build_vision_request
from .image_data import prepare_comfy_image
from .graph_binding import PictureBinding
from ..artifacts.observations import ObservationsArtifact

PROMPT = Path(__file__).resolve().parents[2] / "prompts/vision_observation_system_prompt.txt"


def image_to_entity(image, pair, entity_id, asset_id, name, *, analysis_profile="subject_only", subject_hint="", additional_instruction="",
                    hint_mode="lock_identity", hint_conflict="warn", config=None, cache_root=None, max_edge=1024, mmproj_use_gpu=True):
    import re
    require(re.fullmatch(ID, entity_id) is not None and re.fullmatch(ID, asset_id) is not None, "ENTITY_ID", entity_id, "Use stable lowercase IDs")
    require(bool(name.strip()) and "\n" not in name, "ENTITY_NAME", entity_id, "Name is required")
    config = config or runtime_config("vision")
    prepared = prepare_comfy_image(image, max_edge)
    request = VisionObservationRequest(analysis_profile=analysis_profile, subject_hint=subject_hint, additional_instruction=additional_instruction, hint_mode=hint_mode, hint_conflict=hint_conflict)
    system = PROMPT.read_text(encoding="utf-8")
    key = digest(canonical(dict(version=VISION_PROMPT_VERSION, image=prepared.image_sha256, model=pair.signature(), request=build_vision_request(request, reference_view_count=prepared.batch_size), system=system, config=config.to_dict(), mmproj_use_gpu=mmproj_use_gpu)))
    cache = SuccessCache(cache_root) if cache_root else None
    cached = cache.get(key) if cache else None
    if cached:
        observations = ObservationsArtifact.from_dict(cached["observations"])
        warnings = tuple(cached["warnings"])
    else:
        backend = LlamaCppVisionLifecycle()
        with _LOCK:
            try:
                interrupt()
                backend.ensure_loaded(pair, config, mmproj_use_gpu=mmproj_use_gpu)
                observations, warnings = observe_image(backend, prepared=prepared, request=request, model_identity=pair.signature(), system_prompt=system, runtime_config=config, interrupt_callback=interrupt)
                if cache:
                    cache.put_success(key, dict(observations=observations.to_dict(), warnings=list(warnings)))
            finally:
                backend.clear()
    result = compose_image_to_subject(observations, prepared=prepared, request=request, binding=PictureBinding("none", None), concept_type="location" if analysis_profile == "scene_only" else "person", warnings=warnings)
    # Upstream renderer is the authority for hint/conflict/visibility handling.
    # Convert the fragment body into a VC entity field; do not invent story events.
    body = " ".join(line[2:] for line in result.emd_fragment.text.splitlines() if line.startswith("* "))
    require(bool(body), "VISION_FRAGMENT", entity_id, "No visible description")
    body = body.replace("「", "『").replace("」", "』")
    if analysis_profile == "scene_only":
        emd = f"# 背景 {entity_id}\n* 画像: {asset_id}\n* 情景: {escape(body)}\n"
        entity_kind = "background"
    else:
        emd = f"# 人物 @{entity_id}\n* 名前: {escape(name)}\n* 画像: {asset_id}\n* 特徴: {escape(body)}\n"
        entity_kind = "actor"
    binding = dict(entity_kind=entity_kind, entity_id=entity_id, asset_id=asset_id, analysis_image_sha256=prepared.image_sha256, warnings=list(warnings))
    return emd, binding, image, observations.to_json()
