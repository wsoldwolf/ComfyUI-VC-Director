"""Patch both H3 branches only when the saved audio policy requests NegPiP."""
import hashlib
import inspect
from pathlib import Path
from ...core.audio.continuity import negpip_requested
from ...core.errors import require
from ...core.utilities import canonical, digest
from ..common import CATEGORY


class VCDirectorAudioControl:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": dict(model=("MODEL",), clip=("CLIP",), compiled=("VC_COMPILED",),
            value_strength=("FLOAT", {"default": 1.0, "min": 0.0, "max": 5.0, "step": 0.25,
                "tooltip": "BGM抑制: negpipを指定したPlanで使用する負条件の強度。0は比較用です。"}),
            measure_attention_mass=("BOOLEAN", {"default": False}))}

    RETURN_TYPES = ("MODEL", "CLIP", "STRING")
    RETURN_NAMES = ("model", "clip", "generation_fingerprint")
    FUNCTION = "apply"
    CATEGORY = CATEGORY

    def apply(self, model, clip, compiled, value_strength=1.0, measure_attention_mass=False):
        base = dict(plan=compiled.plan, execution=compiled.execution)
        if not negpip_requested(compiled.document):
            return model, clip, digest(canonical(base))
        require(0 <= value_strength <= 5, "NEGPIP_STRENGTH", "/", "value_strength must be in 0..5")
        import nodes
        external = nodes.NODE_CLASS_MAPPINGS.get("ApplyMiniMaxH3NegPiP")
        require(external is not None, "NEGPIP_REQUIRED", "/",
                "H3専用comfyui-minimax-h3-negpipを導入してComfyUIを再起動してください。")
        source = Path(inspect.getfile(external))
        model, clip = external().apply(model, clip, value_strength=value_strength,
            apply_positive_weights=False, protect_text_rows=True, measure_attention_mass=measure_attention_mass)
        clip.vcd_negpip_active = True
        base["negpip"] = dict(value_strength=value_strength, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                              apply_positive_weights=False, protect_text_rows=True)
        return model, clip, digest(canonical(base))
