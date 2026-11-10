"""ComfyUI adapter; public node surface is preserved."""
from pathlib import Path
from ...core.compiler import compile_scene_emd
from ...core.emd import render_scene
from ...core.emd.diagnostics import parse_checked, contextual_error
from ...core.emd.preflight import preflight, read_image_facts
from ...core.errors import VCError, require
from ...core.inference import GemmaTranslator
from ...core.inference.settings import resolve_inference_config
from ...core.protocols import StageRunner
from ...core.direction import StoryEnhancer
from ...core.planner import StoryTimelinePlanner
from ...core.artifacts.storage import save
from ...core.artifacts.schema import validate
from ...core.artifacts.validation import validate_story
from ...core.artifacts.build_cache import BuildCheckpoints, pipeline_signature, compiled_payload, checked_compile
from ...core.inference.cache_policy import cache_scope, cache_mode as resolve_cache_mode
from ...core.utilities import pretty_json, digest, normalize
from ...core.utilities.progress import progress_scope, emit_progress
from ..common.progress import create_build_progress
from ..common import CATEGORY, _backend, _guard, _model_fields, cache_root, model_roots


class VCDirectorBuild:
    DESCRIPTION = "scenarioはVCD_SCENARIO_EMD_V1、saved_sceneはVCD_SCENE_EMD_V1を入力します。シナリオ生成にはmodel_nameからGGUFを選択してください。"

    @classmethod
    def INPUT_TYPES(cls):
        from ...core.vision import discover_vision_model_pairs
        vision_models = ["disabled"] + [pair.selection_id for pair in discover_vision_model_pairs(model_roots())]
        return {"required": dict(input_mode=(["scenario", "saved_scene"], {"default": "scenario", "tooltip": "scenarioには粗いシナリオEMD、saved_sceneには時刻付きシーンEMDを入力します。モードを切り替えても本文は変わりません。"}), emd=("STRING", {"multiline": True}), registry=("VC_ASSET_REGISTRY",),
                                  output_root=("STRING", {"default": ""}), image_facts_json=("STRING", {"default": "[]", "multiline": True}), **_model_fields()),
                "optional": {"vision_model_name": (vision_models, {"default": "disabled", "tooltip": "scenario時に人物・背景の画像をVisionで分析します。手入力のimage_facts_jsonは同じ人物・背景の自動分析より優先します。"}),
                             "inference_settings": ("VC_INFERENCE_SETTINGS",),
                             "cache_mode": (["再利用", "再生成", "無効"], {"default": "再利用", "tooltip": "再利用: 正常完了済みの段階・Scene・翻訳を使って中断箇所から再開します。再生成: 既存キャッシュを読まず再計算し、成功結果を更新します。無効: キャッシュの読み書きを行いません。"})},
                "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"}}
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("scene_emd", "revision_directory", "plan_json")
    FUNCTION = "build"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Recheck external references on every queue. The persistent stage cache
        # owns reuse, including refresh/disabled on consecutive identical runs.
        return float("nan")

    def build(self, input_mode, emd, registry, output_root, image_facts_json, model_name, seed, prompt=None, vision_model_name="disabled", inference_settings=None, unique_id=None, cache_mode="再利用"):
        progress = create_build_progress(input_mode, input_mode == "scenario" and vision_model_name != "disabled", unique_id)
        with progress_scope(progress):
            try:
                result = self._build(input_mode, emd, registry, output_root, image_facts_json, model_name, seed,
                                     prompt, vision_model_name, inference_settings, cache_mode)
                progress("finish")
            except VCError as error:
                progress("failed")
                formatted = contextual_error(error, emd)
                if formatted is error:
                    raise
                raise formatted from error
            except BaseException:
                progress("failed")
                raise
            return result

    def _build(self, input_mode, emd, registry, output_root, image_facts_json, model_name, seed, prompt, vision_model_name, inference_settings, cache_mode="再利用"):
        _guard(prompt)
        mode = resolve_cache_mode(cache_mode)
        require(input_mode in {"scenario", "saved_scene"}, "INPUT_MODE", "/", "Unknown language input mode")
        document = parse_checked(emd)
        if input_mode == "scenario":
            require(document["schema"] == "VCD_SCENARIO_V1", "INPUT_KIND", "emd",
                    "シナリオモードにシーンEMD（VCD_SCENE_EMD_V1）が入力されています。既存Sceneを使う場合はinput_modeをsaved_sceneへ変更してください。Gemma4でシーンを作る場合はVCD_SCENARIO_EMD_V1のシナリオ本文へ置き換えてください。")
            require(model_name != "none", "MODEL_REQUIRED", "model_name",
                    "シナリオモードにはGGUFモデルが必要です。VC · Buildのmodel_nameから選択してください。noneはsaved_scene専用です。")
        else:
            require(document["schema"] == "VCD_SCENE_DOCUMENT_V1", "INPUT_KIND", "emd",
                    "saved_sceneにはシーンEMD（VCD_SCENE_EMD_V1）が必要です。シナリオEMDを使う場合はinput_modeをscenarioへ変更し、GGUFを選択してください。")
        facts = read_image_facts(image_facts_json) if input_mode == "scenario" else []
        emit_progress("status", label="EMD・参照素材を事前検査中")
        preflight(document, emd, registry, image_facts=facts,
                  vision=input_mode == "scenario" and vision_model_name != "disabled")
        if not output_root:
            import folder_paths
            output_root = str(Path(folder_paths.get_output_directory()) / "vc_director")
        emit_progress("stage_done", stage="prepare")
        signature = pipeline_signature(dict(emd_sha256=digest(normalize(emd)), input_mode=input_mode,
            registry=registry, image_facts=facts, model_name=model_name, seed=seed,
            vision_model_name=vision_model_name, inference_settings=inference_settings))
        with cache_scope(mode, signature):
            return self._build_stages(document, facts, input_mode, emd, registry, output_root, model_name,
                                      seed, vision_model_name, inference_settings, mode, signature)

    def _build_stages(self, document, facts, input_mode, emd, registry, output_root, model_name,
                      seed, vision_model_name, inference_settings, mode, signature):
        cache_directory = Path(output_root) / document["project_id"] / ".build_cache"
        trace = []
        checkpoints = BuildCheckpoints(cache_directory / "stages", trace)
        scenario_emd = None
        details = dict(build_cache=dict(mode=mode, input_signature=signature, stages=trace))
        if input_mode == "scenario":
            if vision_model_name != "disabled":
                emit_progress("stage", stage="vision")
                from ...core.vision import resolve_vision_model_pair
                from ...core.vision.image_facts import scenario_image_facts
                # Finish and release Vision before loading the language model.
                pair = resolve_vision_model_pair(vision_model_name, model_roots())
                manual = facts
                def check_facts(value):
                    require(isinstance(value, list), "IMAGE_FACT", "/", "画像分析結果には配列が必要です。")
                    for fact in value:
                        validate(fact, "image_fact")
                facts = checkpoints.run("vision", dict(model=pair.signature()),
                    lambda: scenario_image_facts(document, registry, pair, cache_directory / "vision", manual,
                                                config=resolve_inference_config("vision", inference_settings)),
                    check_facts, phases=("vision",))
            backend = _backend(model_name, seed=seed, inference_settings=inference_settings)
            try:
                runner = StageRunner(backend, cache_directory / "story", diagnostics_root=Path(output_root) / document["project_id"] / "diagnostics")
                identity = runner._identity = backend.identity()
                scenario = document
                def check_enhanced(value):
                    validate(value, "enhanced_story")
                    require(value["scenario_sha256"] == scenario["source_sha256"]
                            and value["project_id"] == scenario["project_id"]
                            and value["constraints"] == scenario["constraints"]
                            and [b["id"] for b in value["beats"]] == [b["id"] for b in scenario["beats"]],
                            "CHECKPOINT_SOURCE", "/", "補強キャッシュの元シナリオが一致しません。")
                enhanced = checkpoints.run("enhance", dict(model=identity, facts=facts),
                    lambda: StoryEnhancer(runner).enhance(scenario, facts), check_enhanced, phases=("enhance",))
                def check_scenes(value):
                    validate_story(value)
                    require(value["scenario_sha256"] == scenario["source_sha256"]
                            and value["project_id"] == scenario["project_id"],
                            "CHECKPOINT_SOURCE", "/", "Sceneキャッシュの元シナリオが一致しません。")
                document = checkpoints.run("scenes", dict(model=identity, enhanced=enhanced, facts=facts),
                    lambda: StoryTimelinePlanner(runner).plan(scenario, enhanced, image_facts=facts),
                    check_scenes, phases=("outline", "detail"))
                scene_emd = render_scene(document)
                scenario_emd = emd
                details.update(planning_model=identity, planning_trace=runner.trace, enhanced_story=enhanced, image_facts=facts)
                if vision_model_name != "disabled":
                    details["vision_runtime"] = resolve_inference_config("vision", inference_settings).to_dict()
            finally:
                backend.close()
        else:
            scene_emd = normalize(emd)
        backend = None if model_name == "none" else _backend(model_name, seed=seed, stage="compiler", inference_settings=inference_settings)
        try:
            identity = backend.identity() if backend else None
            value = checkpoints.run("compile", dict(model=identity, scene_emd=scene_emd, assets=registry["assets"]),
                lambda: compiled_payload(compile_scene_emd(scene_emd, registry["assets"],
                    GemmaTranslator(backend, cache_directory / "translation",
                        diagnostics_root=Path(output_root) / document["project_id"] / "diagnostics") if backend else None)),
                lambda item: checked_compile(item, scene_emd, registry["assets"]), phases=("compile",))
            result = checked_compile(value, scene_emd, registry["assets"])
            details["translation_model"] = identity
            emit_progress("unit", stage="save", index=0, total=1)
            directory = save(result, output_root, scenario_emd, details)
            return scene_emd, str(directory), pretty_json(result.plan)
        finally:
            if backend:
                backend.close()
