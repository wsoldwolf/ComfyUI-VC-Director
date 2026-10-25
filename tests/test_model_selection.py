import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import ROOT, english_document, temporary_directory
from vc_director.core.emd import render_scene
from vc_director.core.errors import VCError
from vc_director.core.inference import default_model_name
from vc_director.nodes import VCDirectorBuild, VCDirectorEnhancer, VCDirectorTimelinePlanner
from vc_director.nodes.common.runtime import NO_GGUF_MODELS, _model_fields, model_path


def folder_paths_module(models_dir, registered=None):
    module = types.ModuleType("folder_paths")
    module.models_dir = str(models_dir)
    def get_folder_paths(category):
        if category not in (registered or {}):
            raise KeyError(category)
        return registered[category]
    module.get_folder_paths = get_folder_paths
    return module


def gguf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF model-discovery test fixture; no inference")
    return path.resolve()


class ModelSelectionTests(unittest.TestCase):
    def test_dropdown_discovers_models_and_prefers_runtime_profile(self):
        with temporary_directory() as temp:
            models_dir = Path(temp) / "models"
            root = models_dir / "LLM/GGUF"
            preferred = gguf(root / default_model_name())
            gguf(root / "other.gguf")
            gguf(root / "mmproj-f16.gguf")
            with patch.dict(sys.modules, {"folder_paths": folder_paths_module(models_dir)}):
                field = VCDirectorBuild.INPUT_TYPES()["required"]["model_name"]
                self.assertIsInstance(field[0], list)
                self.assertIn(default_model_name(), field[0])
                self.assertIn("other.gguf", field[0])
                self.assertIn("none", field[0])
                self.assertNotIn("mmproj-f16.gguf", field[0])
                self.assertEqual(field[1]["default"], default_model_name())
                self.assertEqual(model_path(field[1]["default"]), preferred)
                self.assertEqual(model_path(str(preferred)), preferred)
                for cls in (VCDirectorEnhancer, VCDirectorTimelinePlanner):
                    self.assertNotIn("none", cls.INPUT_TYPES()["required"]["model_name"][0])

    def test_registered_llm_parent_is_deduplicated_against_gguf_root(self):
        with temporary_directory() as temp:
            models_dir = Path(temp) / "models"
            preferred = gguf(models_dir / "LLM/GGUF" / default_model_name())
            folder_paths = folder_paths_module(models_dir, {"LLM": [str(models_dir / "LLM")]})
            with patch.dict(sys.modules, {"folder_paths": folder_paths}):
                choices = _model_fields()["model_name"][0]
                self.assertEqual(choices, [default_model_name(), "none"])
                self.assertEqual(model_path(default_model_name()), preferred)

    def test_same_relative_name_in_distinct_roots_uses_resolvable_ids(self):
        with temporary_directory() as temp:
            models_dir = Path(temp) / "models"
            first = gguf(models_dir / "LLM/GGUF/gemma/model.gguf")
            extra = Path(temp) / "extra"
            second = gguf(extra / "gemma/model.gguf")
            folder_paths = folder_paths_module(models_dir, {"gguf": [str(extra)]})
            with patch.dict(sys.modules, {"folder_paths": folder_paths}):
                choices = [value for value in _model_fields()["model_name"][0] if value != "none"]
                self.assertEqual(len(choices), 2)
                self.assertTrue(all("::" in value for value in choices))
                self.assertEqual({model_path(value) for value in choices}, {first, second})
                with self.assertRaisesRegex(VCError, "MODEL_SELECTION"):
                    model_path("gemma/model.gguf")

    def test_no_models_explains_required_selection(self):
        with temporary_directory() as temp:
            with patch.dict(sys.modules, {"folder_paths": folder_paths_module(Path(temp) / "models")}):
                self.assertEqual(_model_fields()["model_name"][0], ["none"])
                self.assertEqual(_model_fields(allow_none=False)["model_name"][0], [NO_GGUF_MODELS])
                for selection in ("none", NO_GGUF_MODELS, ""):
                    with self.assertRaisesRegex(VCError, "MODEL_REQUIRED"):
                        model_path(selection)

    def test_offline_runtime_schema_stub_needs_no_model_inventory(self):
        with patch.dict(sys.modules, {"folder_paths": types.ModuleType("folder_paths")}):
            self.assertEqual(_model_fields()["model_name"][0], ["none"])

    def test_scenario_with_none_stops_before_model_loading(self):
        emd = (ROOT / "assets/emd/minimal.scenario.emd").read_text(encoding="utf-8")
        with patch("vc_director.nodes.node_story_builder.node._backend") as backend:
            with self.assertRaisesRegex(VCError, "MODEL_REQUIRED.*model_name"):
                VCDirectorBuild().build("scenario", emd, {"assets": []}, "", "[]", "none", 1)
            backend.assert_not_called()

    def test_saved_scene_with_none_still_compiles_without_model(self):
        with temporary_directory() as temp:
            with patch("vc_director.nodes.node_story_builder.node._backend") as backend:
                scene, directory, plan = VCDirectorBuild().build(
                    "saved_scene", render_scene(english_document()), {"assets": []}, temp, "[]", "none", 1)
                backend.assert_not_called()
            self.assertTrue(Path(directory, "plan.json").is_file())
            self.assertEqual(json.loads(plan)["shots"][0]["id"], english_document()["scenes"][0]["id"])
            self.assertIn("VCD_SCENE_EMD_V1", scene)


if __name__ == "__main__":
    unittest.main()
