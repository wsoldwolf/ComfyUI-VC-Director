import importlib
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

from test_core import ROOT, compile_fixture
from vc_director.core.inference import runtime_config, default_model_name
from vc_director.nodes import NODE_CLASS_MAPPINGS
from vc_director.core.compiler import compile_document
from vc_director.core.compiler.api import LEGACY_PROMPT_POLICY


class LayoutTests(unittest.TestCase):
    def test_compiler_matches_outputs_before_layout_change(self):
        result = compile_fixture()
        result = compile_document(result.document, result.scene_emd, prompt_policy=LEGACY_PROMPT_POLICY)
        fixture = ROOT / "tests/fixtures/compiler"
        self.assertEqual(result.plan, json.loads((fixture / "plan.json").read_text(encoding="utf-8")))
        self.assertEqual(result.execution, json.loads((fixture / "execution.json").read_text(encoding="utf-8")))

    def test_each_node_has_an_independent_wrapper(self):
        self.assertEqual(len(NODE_CLASS_MAPPINGS), 12)
        modules = set()
        for name, cls in NODE_CLASS_MAPPINGS.items():
            module = importlib.import_module(cls.__module__)
            file = Path(module.__file__).resolve()
            self.assertEqual(file.name, "node.py")
            self.assertEqual(file.parent.parent, ROOT / "nodes")
            self.assertTrue(file.parent.name.startswith("node_"))
            self.assertTrue((file.parent / "__init__.py").is_file())
            self.assertEqual(cls.__name__, name)
            modules.add(cls.__module__)
        self.assertEqual(len(modules), 12)

    def test_runtime_profile_preserves_distribution_defaults(self):
        self.assertEqual(runtime_config("enhancer").n_ctx, 24576)
        self.assertEqual(runtime_config("planner").n_ctx, 24576)
        self.assertEqual(runtime_config("compiler").n_ctx, 16384)
        self.assertEqual(runtime_config("vision").max_tokens, 1024)
        self.assertEqual(runtime_config("compiler").max_tokens, 4096)
        self.assertEqual(runtime_config("planner").kv_cache_type, "q8_0")
        self.assertEqual(runtime_config("compiler", seed=42, n_ctx=8192).seed, 42)
        self.assertEqual(runtime_config("compiler", seed=42, n_ctx=8192).n_ctx, 8192)
        self.assertEqual(default_model_name(), "gemma-4-31b-it-heretic-ara-GGUF/gemma-4-31b-it-heretic-ara.Q4_K_S.gguf")

    def test_comfyui_package_import_avoids_global_nodes_and_gpu_imports(self):
        # Match ComfyUI's loader: a package path, not a global 'nodes' import.
        script = r'''
import importlib.util, sys, types
from pathlib import Path
root = Path(sys.argv[1])
sentinel = types.ModuleType("nodes")
sys.modules["nodes"] = sentinel
spec = importlib.util.spec_from_file_location("_vcd_layout_probe", root / "__init__.py", submodule_search_locations=[str(root)])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
assert len(module.NODE_CLASS_MAPPINGS) == 12
assert module.WEB_DIRECTORY == "./web"
assert sys.modules["nodes"] is sentinel
assert "torch" not in sys.modules
assert "llama_cpp" not in sys.modules
assert all(cls.__module__.startswith("_vcd_layout_probe.nodes.node_") for cls in module.NODE_CLASS_MAPPINGS.values())
print("ComfyUI package import: OK")
'''
        result = subprocess.run([sys.executable, "-c", script, str(ROOT)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_web_extension_and_frozen_workflow_template(self):
        import hashlib
        extension = ROOT / "web/js/vc_director.js"
        self.assertIn('from "../../../scripts/app.js"', extension.read_text(encoding="utf-8"))
        template = ROOT / "tools/workflow_templates/Ref2V Basic - MiniMax H3 0.6.json"
        workflow = json.loads((ROOT / "workflows/VC Director.json").read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(template.read_bytes()).hexdigest(), workflow["extra"]["vc_director"]["base_workflow_sha256"])
        production = next(n for n in workflow["nodes"] if n["type"] == "MiniMaxH3ChainPlanModern")
        # A connected external plan does not exempt the local editor from validation.
        self.assertTrue(json.loads(production["widgets_values"][0])["shots"])
        self.assertIsNotNone(next(i for i in production["inputs"] if i["name"] == "plan_json_input")["link"])
        for name in ("width", "height", "base_seed"):
            socket = next(i for i in production["inputs"] if i["name"] == name)
            self.assertEqual(socket["type"], "INT")
            self.assertEqual(socket["widget"], {"name": name})
            self.assertIsNotNone(socket["link"], "Context Loop hides unconnected setting sockets")
            wire = next(l for l in workflow["links"] if l[0] == socket["link"])
            source = next(n for n in workflow["nodes"] if n["id"] == wire[1])
            self.assertEqual(source["type"], "VCDirectorSeed32" if name == "base_seed" else "ResolutionSelector")
            self.assertEqual(wire[2], 1 if name == "height" else 0)
            self.assertEqual(source["outputs"][wire[2]]["links"], [wire[0]])
            self.assertEqual(source["properties"]["vcd_group"], "video")
        resolution = next(n for n in workflow["nodes"] if n["type"] == "ResolutionSelector")
        self.assertEqual(resolution["widgets_values"], ["16:9 (Widescreen)", 0.4, 32])
        self.assertEqual(resolution["widgets_values_named"], dict(aspect_ratio="16:9 (Widescreen)", megapixels=0.4, multiple=32))
        self.assertFalse(any(n["type"] == "PrimitiveInt" for n in workflow["nodes"]))
        self.assertEqual(production["widgets_values"][3:5], [864, 480])
        self.assertEqual(production["widgets_values"][9], 0)
        build = next(n for n in workflow["nodes"] if n["type"] == "VCDirectorBuild")
        socket = next(i for i in build["inputs"] if i["name"] == "inference_settings")
        wire = next(l for l in workflow["links"] if l[0] == socket["link"])
        settings = next(n for n in workflow["nodes"] if n["id"] == wire[1])
        self.assertEqual(settings["type"], "VCDirectorInferenceSettings")
        self.assertEqual(settings["properties"]["vcd_group"], "language")

    def test_core_does_not_import_node_wrappers(self):
        import ast
        for path in (ROOT / "core").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("nodes", (node.module or "").split("."), str(path))

    def test_video_model_stack_matches_mv_snapshot_and_reaches_sampler(self):
        import hashlib
        workflow = json.loads((ROOT / "workflows/VC Director.json").read_text(encoding="utf-8"))
        nodes = {n["id"]: n for n in workflow["nodes"]}
        by_type = {n["type"]: n for n in nodes.values()}
        source = ROOT / "tools/workflow_templates/mv_video_model_stack.json"
        record = json.loads((ROOT / "docs/implementation/mv-video-workflow-sources.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(digest, record["snapshot_sha256"])
        self.assertEqual(digest, workflow["extra"]["vc_director"]["video_model_stack_sha256"])
        for template in json.loads(source.read_text(encoding="utf-8")):
            node = nodes[template["id"]] if template["type"] == "VAELoader" else by_type[template["type"]]
            self.assertEqual(node["widgets_values"], template["widgets_values"])
            self.assertEqual(node["mode"], 2)
            self.assertEqual(node["properties"]["vcd_group"], "video")
            if node["type"] not in {"CLIPLoader", "VAELoader"}:
                self.assertEqual(node["properties"]["vcd_active_mode"], template["mode"])
        self.assertNotIn("UNETLoader", by_type)
        chain = ["MiniMaxH3HybridLoader", "LoraLoaderModelOnly", "ModelAttentionBackend",
                 "BlockSparseAttention", "SpectrumApplyMiniMaxH3", "ModelPatchTorchSettings",
                 "MiniMaxH3SigmaShift"]
        for origin, target in zip(chain, chain[1:]):
            socket = next(i for i in by_type[target]["inputs"] if i["name"] == "model")
            wire = next(l for l in workflow["links"] if l[0] == socket["link"])
            self.assertEqual(wire[1:3], [by_type[origin]["id"], 0])
        for target in ("BasicGuider", "BasicScheduler"):
            socket = next(i for i in by_type[target]["inputs"] if i["name"] == "model")
            wire = next(l for l in workflow["links"] if l[0] == socket["link"])
            self.assertEqual(wire[1], by_type["MiniMaxH3SigmaShift"]["id"])


if __name__ == "__main__":
    unittest.main()
