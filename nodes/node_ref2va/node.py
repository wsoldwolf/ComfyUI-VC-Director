"""ComfyUI adapter; public node surface is preserved."""
from ...core.artifacts.assets import registry, path_for
from ...core.h3_contract.runtime import conditioning_inputs
from ...core.audio.continuity import negpip_requested
from ...core.errors import require
from ..common import CATEGORY


class VCDirectorRef2VA:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": dict(clip=("CLIP",), vae=("VAE",), audio_vae=("VAE",), prompt=("STRING", {"multiline": True}),
                                  width=("INT", {"default": 896, "min": 32, "step": 32}), height=("INT", {"default": 672, "min": 32, "step": 32}), length=("INT", {"default": 243, "min": 5}),
                                  compiled=("VC_COMPILED",), registry=("VC_ASSET_REGISTRY",), clip_index=("INT", {"default": 1, "min": 1}), runtime_root=("STRING", {"default": ""})),
                "optional": dict(ref_image_size=(["max", "match"], {"default": "max",
                    "tooltip": "参照画像の解像度。max: 標準Ref2VAの最大設定（短辺2048pxが上限、小画像は原寸基準、32px単位に調整）。match: 出力動画の画素数に合わせて縮小。maxはVRAM使用量・処理時間が増える場合があります。"}))}
    RETURN_TYPES = ("CONDITIONING", "LATENT")
    FUNCTION = "condition"
    CATEGORY = CATEGORY

    def condition(self, clip, vae, audio_vae, prompt, width, height, length, compiled, registry, clip_index, runtime_root, ref_image_size="max"):
        require(ref_image_size in {"max", "match"}, "REF_IMAGE_SIZE", "ref_image_size",
                "参照画像の解像度にはmax（最大）又はmatch（出力サイズに合わせる）を指定してください。")
        active = conditioning_inputs(compiled, registry, clip_index, prompt, length, runtime_root)
        require(not negpip_requested(compiled.document) or getattr(clip, "vcd_negpip_active", False),
                "NEGPIP_REQUIRED", "/", "BGM抑制: negpipにはVC · Audio ControlのMODEL/CLIP出力を接続してください。")
        from comfy_execution.graph_utils import GraphBuilder
        graph = GraphBuilder()
        node = graph.node("MiniMaxH3ReferenceToVideo", clip=clip, vae=vae, audio_vae=audio_vae, prompt=prompt, width=width, height=height, length=length, ref_image_size=ref_image_size)
        counts = dict(image=0, video=0, audio=0)
        for asset in active:
            path = path_for(asset, registry["root"])
            index = counts[asset["kind"]]
            counts[asset["kind"]] += 1
            if asset["kind"] == "image":
                loader = graph.node("LoadImage", image=path)
                node.set_input(f"ref_images.ref_image_{index}", loader.out(0))
            elif asset["kind"] == "audio":
                loader = graph.node("LoadAudio", audio=path)
                node.set_input(f"ref_audios.ref_audio_{index}", loader.out(0))
            else:
                loader = graph.node("VCDirectorReferenceVideo", path=path, paired_audio=asset["paired_audio_enabled"])
                node.set_input(f"ref_videos.ref_video_{index}", loader.out(0))
                if asset["paired_audio_enabled"]:
                    node.set_input(f"ref_video_audios.ref_video_audio_{index}", loader.out(1))
        return {"result": (node.out(0), node.out(1)), "expand": graph.finalize()}
