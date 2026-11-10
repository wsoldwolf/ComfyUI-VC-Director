"""Validated checkpoints for completed Build stages, separate from revisions."""
import logging
import hashlib
from dataclasses import asdict
from pathlib import Path

from ..errors import VCError, require
from ..inference.cache_policy import PipelineCache
from ..utilities import canonical, digest, normalize
from ..utilities.progress import emit_progress
from .schema import validate
from .validation import validate_story

VERSION = "vcd-build-checkpoints-v1"
LOGGER = logging.getLogger("vc_director.build_cache")


def pipeline_signature(inputs):
    """Fingerprint runtime source/config only; never scan project output/data."""
    root = Path(__file__).resolve().parents[2]
    files = []
    for directory in ("core", "prompts", "profiles", "nodes/node_story_builder"):
        for path in sorted((root / directory).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".txt", ".json"}:
                files.append([path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()])
    return digest(canonical(dict(version=VERSION, source=files, inputs=inputs)))


class BuildCheckpoints:
    def __init__(self, root, trace):
        self.cache = PipelineCache(root)
        self.trace = trace

    def run(self, stage, inputs, work, check, *, phases=()):
        key = digest(canonical(dict(version=VERSION, stage=stage, inputs=inputs)))
        saved = self.cache.get(key)
        if saved is not None:
            try:
                value = saved["result"]
                require(saved.get("result_sha256") == digest(canonical(value)), "CHECKPOINT_HASH", stage,
                        "完了済みキャッシュの内容が一致しません。再生成します。")
                check(value)
            except (VCError, ValueError, KeyError, TypeError, AttributeError):
                LOGGER.warning("VC Build / %s: 完了済みキャッシュを検証できないため再生成します", stage)
            else:
                self.trace.append(dict(stage=stage, cache="hit", key=key))
                LOGGER.info("VC Build / %s: 完了済みキャッシュを使用", stage)
                for phase in phases:
                    emit_progress("stage_done", stage=phase, label="完了済みキャッシュを使用")
                return value
        value = work()
        check(value)
        self.cache.put_success(key, dict(result=value, result_sha256=digest(canonical(value))))
        self.trace.append(dict(stage=stage, cache="generated", key=key))
        return value


def compiled_payload(result):
    return asdict(result)


def checked_compile(value, scene_emd, assets):
    """Recheck cached pairs without running translation or loading a model."""
    from ..compiler.api import CompileResult, PROMPT_POLICY
    from ..emd import parse
    result = CompileResult(**value)
    require(result.scene_emd == normalize(scene_emd) and parse(scene_emd) == result.document,
            "CHECKPOINT_SCENE", "/", "キャッシュのScene EMDが現在の入力と一致しません。")
    validate_story(result.document)
    validate(result.execution, "execution")
    require(set(result.plan) == {"shots"} and isinstance(result.plan["shots"], list)
            and len(result.plan["shots"]) == len(result.document["scenes"])
            and result.metadata["schema"] == "VCD_RUN_METADATA_V1"
            and isinstance(result.metadata["translation_trace"], list),
            "CHECKPOINT_FORMAT", "/", "キャッシュのコンパイル結果の形式が不正です。")
    require(result.execution["assets"] == assets and result.metadata["prompt_policy_version"] == PROMPT_POLICY,
            "CHECKPOINT_INPUT", "/", "キャッシュの素材またはコンパイル方針が一致しません。")
    require(result.metadata["plan_sha256"] == result.execution["plan_sha256"] == digest(canonical(result.plan))
            and result.metadata["scene_emd_sha256"] == result.execution["scene_emd_sha256"] == digest(result.scene_emd)
            and result.metadata["execution_sha256"] == digest(canonical(result.execution)),
            "CHECKPOINT_PAIR", "/", "キャッシュの生成物同士の署名が一致しません。")
    return result
