"""ComfyUI adapter; public node surface is preserved."""
from ...core.vision.reference_video import decode_reference_video
from ..common import CATEGORY


class VCDirectorReferenceVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": dict(path=("STRING",), paired_audio=("BOOLEAN", {"default": False}))}
    RETURN_TYPES = ("IMAGE", "AUDIO")
    FUNCTION = "decode"
    CATEGORY = CATEGORY

    def decode(self, path, paired_audio):
        return decode_reference_video(path, paired_audio)
