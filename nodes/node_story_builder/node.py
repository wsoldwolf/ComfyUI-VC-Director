"""ComfyUI adapter; public node surface is preserved."""
import json
from pathlib import Path
from ...core.artifacts.assets import registry
from ...core.compiler import compile_scene_emd
from ...core.emd import parse, render_scene
from ...core.errors import require
from ...core.inference import GemmaTranslator
from ...core.inference.settings import resolve_inference_config
from ...core.protocols import StageRunner
from ...core.direction import StoryEnhancer
from ...core.planner import StoryTimelinePlanner
from ...core.artifacts.storage import save
from ...core.utilities import pretty_json
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
                             "inference_settings": ("VC_INFERENCE_SETTINGS",)},
                "hidden": {"prompt": "PROMPT"}}
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("scene_emd", "revision_directory", "plan_json")
    FUNCTION = "build"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY

    def build(self, input_mode, emd, registry, output_root, image_facts_json, model_name, seed, prompt=None, vision_model_name="disabled", inference_settings=None):
        _guard(prompt)
        require(input_mode in {"scenario", "saved_scene"}, "INPUT_MODE", "/", "Unknown language input mode")
        document = parse(emd)
        if input_mode == "scenario":
            require(document["schema"] == "VCD_SCENARIO_V1", "INPUT_KIND", "emd",
                    "シナリオモードにシーンEMD（VCD_SCENE_EMD_V1）が入力されています。既存Sceneを使う場合はinput_modeをsaved_sceneへ変更してください。Gemma4でシーンを作る場合はVCD_SCENARIO_EMD_V1のシナリオ本文へ置き換えてください。")
            require(model_name != "none", "MODEL_REQUIRED", "model_name",
                    "シナリオモードにはGGUFモデルが必要です。VC · Buildのmodel_nameから選択してください。noneはsaved_scene専用です。")
        else:
            require(document["schema"] == "VCD_SCENE_DOCUMENT_V1", "INPUT_KIND", "emd",
                    "saved_sceneにはシーンEMD（VCD_SCENE_EMD_V1）が必要です。シナリオEMDを使う場合はinput_modeをscenarioへ変更し、GGUFを選択してください。")
        if not output_root:
            import folder_paths
            output_root = str(Path(folder_paths.get_output_directory()) / "vc_director")
        scenario_emd, details = None, {}
        if input_mode == "scenario":
            facts = json.loads(image_facts_json)
            require(isinstance(facts, list), "IMAGE_FACT", "image_facts_json", "Expected a JSON array")
            if vision_model_name != "disabled":
                from ...core.vision import resolve_vision_model_pair
                from ...core.vision.image_facts import scenario_image_facts
                # Finish and release Vision before loading the language model.
                pair = resolve_vision_model_pair(vision_model_name, model_roots())
                facts = scenario_image_facts(document, registry, pair, cache_root() / "vision", facts,
                                             config=resolve_inference_config("vision", inference_settings))
            backend = _backend(model_name, seed=seed, inference_settings=inference_settings)
            try:
                runner = StageRunner(backend, cache_root() / "story", diagnostics_root=Path(output_root) / document["project_id"] / "diagnostics")
                scenario = document
                enhanced = StoryEnhancer(runner).enhance(scenario, facts)
                document = StoryTimelinePlanner(runner).plan(scenario, enhanced, image_facts=facts)
                scene_emd = render_scene(document)
                scenario_emd = emd
                details = dict(planning_model=runner._identity, planning_trace=runner.trace, enhanced_story=enhanced, image_facts=facts)
                if vision_model_name != "disabled":
                    details["vision_runtime"] = resolve_inference_config("vision", inference_settings).to_dict()
            finally:
                backend.close()
        else:
            scene_emd = emd
        backend = None if model_name == "none" else _backend(model_name, seed=seed, stage="compiler", inference_settings=inference_settings)
        try:
            result = compile_scene_emd(scene_emd, registry["assets"], GemmaTranslator(backend, cache_root() / "translation",
                                       diagnostics_root=Path(output_root) / document["project_id"] / "diagnostics") if backend else None)
            details["translation_model"] = backend.identity() if backend else None
            directory = save(result, output_root, scenario_emd, details)
            return scene_emd, str(directory), pretty_json(result.plan)
        finally:
            if backend:
                backend.close()
