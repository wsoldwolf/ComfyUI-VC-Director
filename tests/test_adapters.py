import copy
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import compile_fixture, english_document, fixture, temporary_directory
from vc_director.core.artifacts.assets import registry
from vc_director.core.emd import parse, render_scene
from vc_director.core.errors import VCError
from vc_director.nodes import VCDirectorRef2VA
from vc_director.nodes.common import _guard
from vc_director.core.protocols import StageRunner
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.h3_contract.runtime import conditioning_inputs
from vc_director.core.utilities import canonical


class RuntimeTests(unittest.TestCase):
    def test_native_graph_keeps_literal_speech(self):
        with temporary_directory() as temp:
            Path(temp, "person.png").write_bytes(b"image bytes hashed but not decoded in this graph test")
            shared = registry([dict(id="hero_image", kind="image", locator="person.png")], temp)
            doc = english_document()
            doc["actors"][0]["image_asset_id"] = "hero_image"
            result = compile_fixture(doc, shared["assets"])
            shot = result.plan["shots"][0]
            graph_nodes = []

            class Node:
                def __init__(self, type_, **inputs):
                    self.type = type_
                    self.inputs = inputs
                    graph_nodes.append(self)

                def set_input(self, name, value):
                    self.inputs[name] = value

                def out(self, index):
                    return [self.type, index]

            class Graph:
                def node(self, type_, **inputs):
                    return Node(type_, **inputs)

                def finalize(self):
                    return {n.type: n.inputs for n in graph_nodes}

            module = types.ModuleType("comfy_execution.graph_utils")
            module.GraphBuilder = Graph
            with patch.dict(sys.modules, {"comfy_execution": types.ModuleType("comfy_execution"), "comfy_execution.graph_utils": module}), patch("vc_director.core.h3_contract.runtime.verify_runtime"):
                graph = VCDirectorRef2VA().condition("clip", "video_vae", "audio_vae", shot["prompt"], 896, 672, shot["length"], result, shared, 1, "runtime")
            native = graph["expand"]["MiniMaxH3ReferenceToVideo"]
            self.assertEqual(native["prompt"], shot["prompt"])
            self.assertIn("@hero @recipient</d>", native["prompt"])
            self.assertEqual(native["ref_images.ref_image_0"], ["LoadImage", 0])
            self.assertFalse(any("Scheduled" in n.type or "Tagged" in n.type for n in graph_nodes))

    def test_tampered_current_prompt_or_registry_stops(self):
        result = compile_fixture()
        shared = registry([], ".")
        with patch("vc_director.core.h3_contract.runtime.verify_runtime"):
            with self.assertRaisesRegex(VCError, "CURRENT_SCENE"):
                conditioning_inputs(result, shared, 1, "edited prompt", 243, "runtime")
            shared["fingerprint"] = "a" * 64
            with self.assertRaisesRegex(VCError, "REGISTRY_HASH"):
                conditioning_inputs(result, shared, 1, result.plan["shots"][0]["prompt"], 243, "runtime")

    def test_both_modes_active_are_rejected(self):
        _guard({})
        _guard({"1": dict(class_type="VCDirectorBuild")})
        _guard({"1": dict(class_type="VCDirectorLoad")})
        with self.assertRaisesRegex(VCError, "WORKFLOW_CONFLICT"):
            _guard({"1": dict(class_type="VCDirectorBuild"), "2": dict(class_type="VCDirectorLoad")})

    def test_global_actor_order_owns_subject_numbers(self):
        doc = english_document()
        other = copy.deepcopy(doc["actors"][0])
        other.update(id="friend", name="Friend", description="A friend.")
        doc["actors"].append(other)
        doc["beats"][0]["actor_ids"] = ["friend", "hero"]
        doc["scenes"][0]["actor_ids"] = ["friend", "hero"]
        doc["scenes"][0]["subject_descriptions"].append(dict(actor_id="friend", text="A friend."))
        doc["scenes"][0]["retention"].append(dict(actor_id="friend", mode="fully_preserved"))
        result = compile_fixture(doc)
        self.assertEqual(result.execution["segments"][0]["local_subjects"], [dict(actor_id="hero", subject_number=1), dict(actor_id="friend", subject_number=2)])

    def test_no_actor_and_text_background(self):
        doc = english_document()
        doc["actors"] = []
        doc["beats"][0].update(actor_ids=[], dialogues=[])
        scene = doc["scenes"][0]
        scene.update(actor_ids=[], subject_descriptions=[], retention=[], initial_state=[], terminal_state=[], summary="A quiet square.")
        for p in (doc["audio"], doc["beats"][0]["audio"], scene["audio"]):
            p["vocal"] = "none"
        scene["shots"][0]["events"] = [dict(id="ev_square", kind="camera", start_ms=0, end_ms=10000, actor_id="environment", text="A static wide view.", dialogue_id=None, language=None)]
        result = compile_fixture(doc)
        self.assertEqual(result.execution["segments"][0]["local_subjects"], [])
        self.assertNotIn("<Subject", result.plan["shots"][0]["prompt"])


class RetimeTests(unittest.TestCase):
    def test_retime_changes_only_numbers(self):
        from test_core import FakeBackend
        doc = fixture("scene-document")
        patch_ = dict(scene_id=doc["scenes"][0]["id"], start_ms=0, end_ms=9000,
                      shots=[dict(id=doc["scenes"][0]["shots"][0]["id"], start_ms=0, end_ms=9000, events=[dict(id="ev_walk", start_ms=0, end_ms=9000)])], sound_events=[])
        planner = StoryTimelinePlanner(StageRunner(FakeBackend(["TIMING\t1\t" + canonical(patch_)])))
        result = planner.retime(doc, [doc["scenes"][0]["id"]])
        self.assertEqual(result["duration_ms"], 9000)
        self.assertEqual(result["scenes"][0]["shots"][0]["events"][0]["text"], doc["scenes"][0]["shots"][0]["events"][0]["text"])
        self.assertEqual(parse(render_scene(result)), result)

    def test_timing_lock_stops_before_model(self):
        from test_core import FakeBackend
        doc = fixture("scene-document")
        doc["scenes"][0]["locked"] = ["timing"]
        backend = FakeBackend([])
        with self.assertRaisesRegex(VCError, "LOCKED_TIMING"):
            StoryTimelinePlanner(StageRunner(backend)).retime(doc, [doc["scenes"][0]["id"]])
        self.assertEqual(backend.requests, [])


if __name__ == "__main__":
    unittest.main()
