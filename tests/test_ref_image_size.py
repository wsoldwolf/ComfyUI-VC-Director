"""Neutral graph-expansion tests; never load models or decode reference media."""
import copy
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools._bootstrap import load_project

load_project()

from vc_director.core.errors import VCError
from vc_director.nodes import VCDirectorRef2VA


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ["clip", "vae", "audio_vae", "prompt", "width", "height", "length", "compiled", "registry", "clip_index", "runtime_root"]


class Graph:
    def __init__(self):
        self.nodes = {}

    def node(self, kind, **inputs):
        id = str(len(self.nodes))
        record = dict(class_type=kind, inputs=inputs)
        self.nodes[id] = record
        return types.SimpleNamespace(out=lambda slot: [id, slot],
                                     set_input=lambda name, value: record["inputs"].__setitem__(name, value))

    def finalize(self):
        return self.nodes


def expand(*, mode=None, explicit=False, assets=()):
    graph_utils = types.ModuleType("comfy_execution.graph_utils")
    graph_utils.GraphBuilder = Graph
    compiled = types.SimpleNamespace(document=dict(scenes=[]))
    registry = dict(root="X:/neutral_reference_assets")
    with patch.dict(sys.modules, {"comfy_execution": types.ModuleType("comfy_execution"),
                                 "comfy_execution.graph_utils": graph_utils}), patch(
        "vc_director.nodes.node_ref2va.node.conditioning_inputs", return_value=list(assets)) as checked:
        args = ("neutral_clip", "neutral_video_vae", "neutral_audio_vae", "A traveler at a station.",
                896, 672, 243, compiled, registry, 1, "X:/neutral_runtime")
        result = VCDirectorRef2VA().condition(*args, **(dict(ref_image_size=mode) if explicit else {}))
        checked.assert_called_once_with(compiled, registry, 1, args[3], 243, "X:/neutral_runtime")
    return result


class ReferenceImageSizeTests(unittest.TestCase):
    def test_ui_default_is_max_and_existing_socket_order_is_preserved(self):
        schema = VCDirectorRef2VA.INPUT_TYPES()
        self.assertEqual(list(schema["required"]), REQUIRED)
        choices, options = schema["optional"]["ref_image_size"]
        self.assertEqual(choices, ["max", "match"])
        self.assertEqual(options["default"], "max")
        self.assertIn("2048", options["tooltip"])

    def test_legacy_call_without_new_input_defaults_to_native_max(self):
        graph = expand()
        native = graph["expand"]["0"]
        self.assertEqual(native["class_type"], "MiniMaxH3ReferenceToVideo")
        self.assertEqual(native["inputs"]["ref_image_size"], "max")
        self.assertEqual(native["inputs"]["width"], 896)
        self.assertEqual(native["inputs"]["height"], 672)
        self.assertEqual(native["inputs"]["length"], 243)

    def test_explicit_mode_changes_only_native_sizing_and_keeps_reference_numbering(self):
        assets = [dict(id="traveler", kind="image", locator="traveler.png"),
                  dict(id="station", kind="image", locator="station.png"),
                  dict(id="voice", kind="audio", locator="voice.wav"),
                  dict(id="motion", kind="video", locator="motion.mp4", paired_audio_enabled=True),
                  dict(id="silent_motion", kind="video", locator="silent.mp4", paired_audio_enabled=False)]
        original = copy.deepcopy(assets)
        maximum = expand(mode="max", explicit=True, assets=assets)
        matched = expand(mode="match", explicit=True, assets=assets)
        self.assertEqual(matched["expand"]["0"]["inputs"]["ref_image_size"], "match")
        matched["expand"]["0"]["inputs"]["ref_image_size"] = "max"
        self.assertEqual(matched, maximum)
        native = maximum["expand"]["0"]["inputs"]
        self.assertEqual(native["ref_images.ref_image_0"], ["1", 0])
        self.assertEqual(native["ref_images.ref_image_1"], ["2", 0])
        self.assertEqual(native["ref_audios.ref_audio_0"], ["3", 0])
        self.assertEqual(native["ref_videos.ref_video_0"], ["4", 0])
        self.assertEqual(native["ref_video_audios.ref_video_audio_0"], ["4", 1])
        self.assertEqual(native["ref_videos.ref_video_1"], ["5", 0])
        self.assertNotIn("ref_video_audios.ref_video_audio_1", native)
        self.assertEqual(assets, original)

    def test_invalid_setting_fails_before_reading_registry_or_building_graph(self):
        with patch("vc_director.nodes.node_ref2va.node.conditioning_inputs") as checked:
            for mode in ("", "original", "2048", None):
                with self.subTest(mode=mode), self.assertRaisesRegex(VCError, "REF_IMAGE_SIZE"):
                    VCDirectorRef2VA().condition(None, None, None, "neutral", 896, 672, 243,
                                               None, None, 1, "neutral", ref_image_size=mode)
            checked.assert_not_called()

    def test_distributed_workflow_appends_max_without_shifting_old_widgets_or_links(self):
        workflow = json.loads((ROOT / "workflows/VC Director.json").read_text(encoding="utf-8"))
        node = next(node for node in workflow["nodes"] if node["type"] == "VCDirectorRef2VA")
        self.assertEqual([field["name"] for field in node["inputs"]], REQUIRED + ["ref_image_size"])
        self.assertEqual(node["widgets_values"][:5], ["", 864, 480, 243, 1])
        self.assertIsInstance(node["widgets_values"][5], str)
        self.assertEqual(node["widgets_values"][6], "max")
        self.assertEqual(node["inputs"][-1], dict(name="ref_image_size", type="COMBO", link=None,
                                                widget=dict(name="ref_image_size")))
        for wire in workflow["links"]:
            if wire[3] == node["id"]:
                self.assertLess(wire[4], len(REQUIRED))
                self.assertEqual(node["inputs"][wire[4]]["link"], wire[0])

    def test_generator_uses_max_without_runtime_or_model_discovery(self):
        from tools.generate_workflow import generate

        with patch("tools.generate_workflow.verify_runtime") as verified, patch(
            "vc_director.nodes.common.runtime.model_roots", return_value={}):
            workflow = generate("X:/neutral_runtime")
        verified.assert_called_once_with("X:/neutral_runtime")
        node = next(node for node in workflow["nodes"] if node["type"] == "VCDirectorRef2VA")
        names = [field["name"] for field in node["inputs"] if "widget" in field]
        self.assertEqual(dict(zip(names, node["widgets_values"]))["ref_image_size"], "max")


if __name__ == "__main__":
    unittest.main()
