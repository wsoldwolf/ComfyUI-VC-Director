"""Validated, stage-specific inference settings shared by ComfyUI adapters."""
from dataclasses import replace
from ..errors import require
from .profile_loader import runtime_config
from .runtime import LlamaRuntimeConfig

SETTINGS_SCHEMA = "VCD_INFERENCE_SETTINGS_V1"
STAGES = ("enhancer", "planner", "compiler", "vision")


def _validate(config):
    config.validate()
    for name in ("n_ctx", "max_tokens", "gpu_layers", "n_batch", "seed"):
        value = getattr(config, name)
        require(type(value) is int, "INFERENCE_SETTINGS", name, "Expected an integer")
    require(config.gpu_layers >= -1, "INFERENCE_SETTINGS", "gpu_layers", "Use -1 for all layers, 0 for CPU, or a positive layer count")
    require(config.n_ctx == 0 or config.max_tokens < config.n_ctx, "INFERENCE_SETTINGS", "max_tokens", "Output tokens must leave room for the prompt inside n_ctx")
    for name in ("flash_attn", "op_offload", "keep_model_loaded"):
        require(type(getattr(config, name)) is bool, "INFERENCE_SETTINGS", name, "Expected a boolean")
    return config


def make_inference_settings(*, planning_n_ctx, planning_max_tokens, compiler_n_ctx, compiler_max_tokens,
                            vision_n_ctx, vision_max_tokens, gpu_layers, n_batch, temperature, top_p,
                            repetition_penalty, flash_attn, kv_cache_type, op_offload,
                            vision_gpu_layers, vision_n_batch):
    shared = dict(gpu_layers=gpu_layers, n_batch=n_batch, temperature=temperature, top_p=top_p,
                  repetition_penalty=repetition_penalty, flash_attn=flash_attn,
                  kv_cache_type=kv_cache_type, op_offload=op_offload)
    stages = {}
    for stage in STAGES:
        prefix = "planning" if stage in {"enhancer", "planner"} else stage
        limits = {
            "planning": (planning_n_ctx, planning_max_tokens),
            "compiler": (compiler_n_ctx, compiler_max_tokens),
            "vision": (vision_n_ctx, vision_max_tokens),
        }[prefix]
        overrides = dict(shared, n_ctx=limits[0], max_tokens=limits[1])
        if stage == "vision":
            overrides.update(gpu_layers=vision_gpu_layers, n_batch=vision_n_batch)
        stages[stage] = _validate(replace(runtime_config(stage), **overrides)).to_dict()
    return dict(schema=SETTINGS_SCHEMA, stages=stages)


def resolve_inference_config(stage, settings=None, *, seed=None, n_ctx=None):
    if settings is None:
        return runtime_config(stage, seed=seed, n_ctx=n_ctx)
    require(isinstance(settings, dict) and settings.get("schema") == SETTINGS_SCHEMA,
            "INFERENCE_SETTINGS", "/", "Expected VC Gemma Settings output")
    stages = settings.get("stages")
    require(isinstance(stages, dict) and set(stages) == set(STAGES),
            "INFERENCE_SETTINGS", "stages", "Expected settings for all four inference stages")
    require(stage in stages and isinstance(stages[stage], dict), "INFERENCE_SETTINGS", stage, "Unknown inference stage")
    values = dict(stages[stage])
    require(set(values) == set(runtime_config(stage).to_dict()), "INFERENCE_SETTINGS", stage, "Incomplete or unknown runtime fields")
    if seed is not None:
        values["seed"] = seed
    if n_ctx is not None:
        values["n_ctx"] = n_ctx
    return _validate(LlamaRuntimeConfig(**values))
