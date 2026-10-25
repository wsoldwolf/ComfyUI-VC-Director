import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import temporary_directory
from vc_director.core.inference import GemmaBackend, runtime_config
from vc_director.core.inference.settings import resolve_inference_config
from vc_director.nodes import (VCDirectorInferenceSettings, VCDirectorBuild, VCDirectorEnhancer,
                               VCDirectorTimelinePlanner, VCDirectorCompileSave, VCDirectorImageToSubjectEMD)
from vc_director.nodes.common.runtime import _backend


def settings(**overrides):
    values = {name: spec[1]["default"] for name, spec in VCDirectorInferenceSettings.INPUT_TYPES()["required"].items()}
    values.update(overrides)
    return VCDirectorInferenceSettings().configure(**values)[0]


class InferenceSettingsTests(unittest.TestCase):
    def test_default_settings_and_missing_connection_preserve_each_stage(self):
        value = settings()
        for stage in ("enhancer", "planner", "compiler", "vision"):
            self.assertEqual(resolve_inference_config(stage, value), runtime_config(stage))
            self.assertEqual(resolve_inference_config(stage), runtime_config(stage))
        for cls in (VCDirectorBuild, VCDirectorEnhancer, VCDirectorTimelinePlanner,
                    VCDirectorCompileSave, VCDirectorImageToSubjectEMD):
            self.assertEqual(cls.INPUT_TYPES()["optional"]["inference_settings"], ("VC_INFERENCE_SETTINGS",))

    def test_stage_limits_seed_and_vision_offload_are_independent(self):
        value = settings(planning_n_ctx=32768, planning_max_tokens=6000,
                         compiler_n_ctx=8192, compiler_max_tokens=2000,
                         vision_n_ctx=12288, vision_max_tokens=1500,
                         gpu_layers=40, n_batch=128, vision_gpu_layers=20, vision_n_batch=64,
                         temperature=0.4, kv_cache_type="f16")
        before = copy.deepcopy(value)
        planning = resolve_inference_config("planner", value, seed=42)
        self.assertEqual((planning.n_ctx, planning.max_tokens, planning.gpu_layers, planning.n_batch, planning.seed),
                         (32768, 6000, 40, 128, 42))
        compiler = resolve_inference_config("compiler", value, seed=17)
        self.assertEqual((compiler.n_ctx, compiler.max_tokens, compiler.seed), (8192, 2000, 17))
        vision = resolve_inference_config("vision", value)
        self.assertEqual((vision.n_ctx, vision.max_tokens, vision.gpu_layers, vision.n_batch), (12288, 1500, 20, 64))
        self.assertEqual(vision.temperature, 0.4)
        self.assertEqual(vision.kv_cache_type, "f16")
        self.assertEqual(value, before)

    def test_settings_reach_backend_and_change_cache_identity(self):
        with temporary_directory() as temp:
            model = Path(temp) / "fake.gguf"
            model.write_bytes(b"test identity without loading weights")
            first = _backend(str(model), seed=71, inference_settings=settings(planning_n_ctx=32768, gpu_layers=30))
            second = _backend(str(model), seed=71, inference_settings=settings(planning_n_ctx=16384, gpu_layers=30))
            self.assertIsInstance(first, GemmaBackend)
            self.assertEqual(first.config.n_ctx, 32768)
            self.assertEqual(first.config.gpu_layers, 30)
            self.assertEqual(first.identity()["runtime"]["seed"], 71)
            self.assertNotEqual(first.identity(), second.identity())
            self.assertIsNone(first.lifecycle._model)

    def test_invalid_settings_fail_before_model_resolution_or_loading(self):
        for overrides in (dict(planning_n_ctx=4096, planning_max_tokens=4096), dict(gpu_layers=-2),
                          dict(n_batch=0), dict(temperature=3), dict(kv_cache_type="invalid")):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                settings(**overrides)
        with patch("vc_director.nodes.common.runtime.model_path") as path:
            for invalid in ({}, {"schema": "VCD_INFERENCE_SETTINGS_V1", "stages": {}}):
                with self.assertRaises(ValueError):
                    _backend("unused.gguf", inference_settings=invalid)
            path.assert_not_called()


if __name__ == "__main__":
    unittest.main()
