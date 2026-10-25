"""Expose llama.cpp execution and sampling settings without loading a model."""
from ...core.inference.profile_loader import runtime_config
from ...core.inference.settings import make_inference_settings


class VCDirectorInferenceSettings:
    RETURN_TYPES = ("VC_INFERENCE_SETTINGS",)
    RETURN_NAMES = ("inference_settings",)
    FUNCTION = "configure"
    CATEGORY = "VC Director"
    DESCRIPTION = "計画・翻訳・Visionの推論設定。Build / Enhancer / TimelinePlanner / CompileSave / ImageToSubjectEMDへ接続します。未接続時は従来の既定値です。"

    @classmethod
    def INPUT_TYPES(cls):
        fields = {}
        for label, stage in (("planning", "planner"), ("compiler", "compiler"), ("vision", "vision")):
            config = runtime_config(stage)
            fields[label + "_n_ctx"] = ("INT", dict(default=config.n_ctx, min=0, max=131072, step=1024,
                tooltip="入力＋出力のcontext上限。0はモデル既定値。大きくするとKVメモリも増えます。"))
            fields[label + "_max_tokens"] = ("INT", dict(default=config.max_tokens, min=1, max=131071,
                tooltip="一回の応答の最大出力トークン数。n_ctxには入力と出力の両方が収まる必要があります。"))
        config = runtime_config("planner")
        fields.update(
            gpu_layers=("INT", dict(default=config.gpu_layers, min=-1, max=1000, tooltip="計画・翻訳のGPU層数。-1=全層、0=CPU、一部をCPUへ逃がす場合は正の層数。")),
            n_batch=("INT", dict(default=config.n_batch, min=1, max=8192, tooltip="計画・翻訳のprompt処理バッチ。小さくすると一時メモリを抑えられます。")),
            temperature=("FLOAT", dict(default=config.temperature, min=0.0, max=2.0, step=0.01)),
            top_p=("FLOAT", dict(default=config.top_p, min=0.0, max=1.0, step=0.01)),
            repetition_penalty=("FLOAT", dict(default=config.repetition_penalty, min=0.01, max=4.0, step=0.01)),
            flash_attn=("BOOLEAN", dict(default=config.flash_attn)),
            kv_cache_type=(["q8_0", "q4_0", "f16"], dict(default=config.kv_cache_type)),
            op_offload=("BOOLEAN", dict(default=config.op_offload)),
            vision_gpu_layers=("INT", dict(default=runtime_config("vision").gpu_layers, min=-1, max=1000, tooltip="Vision本体のGPU層数。mmprojの配置設定とは別です。")),
            vision_n_batch=("INT", dict(default=runtime_config("vision").n_batch, min=1, max=8192)),
        )
        return {"required": fields}

    def configure(self, **settings):
        return (make_inference_settings(**settings),)
