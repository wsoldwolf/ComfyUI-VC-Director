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


class LayoutTests(unittest.TestCase):
    def test_compiler_matches_outputs_before_layout_change(self):
        result = compile_fixture()
        fixture = ROOT / "tests/fixtures/compiler"
        self.assertEqual(result.plan, json.loads((fixture / "plan.json").read_text(encoding="utf-8")))
        self.assertEqual(result.execution, json.loads((fixture / "execution.json").read_text(encoding="utf-8")))

    def test_each_node_has_an_independent_wrapper(self):
        self.assertEqual(len(NODE_CLASS_MAPPINGS), 9)
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
        self.assertEqual(len(modules), 9)

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
assert len(module.NODE_CLASS_MAPPINGS) == 9
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

    def test_core_does_not_import_node_wrappers(self):
        import ast
        for path in (ROOT / "core").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("nodes", (node.module or "").split("."), str(path))


if __name__ == "__main__":
    unittest.main()
