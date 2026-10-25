import copy
import importlib.util
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
from test_core import ROOT, FakeBackend, english_document, fixture, temporary_directory, with_camera
from vc_director.core.artifacts.schema import schema
from vc_director.core.artifacts.storage import load, save
from vc_director.core.artifacts.validation import validate_story
from vc_director.core.compiler import compile_scene_emd
from vc_director.core.emd import parse, render_scene
from vc_director.core.errors import VCError
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.protocols import StageRunner, system_prompt
from vc_director.core.protocols.grammar import record_grammar
from vc_director.core.protocols.llm import generation_schema
from vc_director.core.utilities import canonical
from vc_director.nodes import VCDirectorBuild


def continued_story():
    """Two beats, three Scenes: one beat splits, the next continues it."""
    doc = english_document()
    first_beat = doc["beats"][0]
    first_beat.update(dialogues=[], transition="continue", direction=["Keep a slow tracking camera."])
    for policy in (doc["audio"], first_beat["audio"], doc["scenes"][0]["audio"]):
        policy["vocal"] = "none"
    first = doc["scenes"][0]
    first["shots"][0]["events"].pop()
    first = doc["scenes"][0] = with_camera(first)
    second = copy.deepcopy(first)
    second.update(id="scene_middle", start_ms=10000, end_ms=20000, transition="continue",
                  depends_on=[first["id"]], initial_state=copy.deepcopy(first["terminal_state"]), required_event_ids=[])
    third = copy.deepcopy(second)
    third.update(id="scene_next", beat_id="next", start_ms=20000, end_ms=30000,
                 depends_on=[second["id"]], required_event_ids=["req_next"])
    for scene in (second, third):
        for shot in scene["shots"]:
            shot["id"] = scene["id"] + "_shot"
            for event in shot["events"]:
                event["id"] = scene["id"] + "_" + event["kind"]
    next_beat = copy.deepcopy(first_beat)
    next_beat.update(id="next", direction=["Hold a static camera."])
    doc["beats"].append(next_beat)
    doc["scenes"].extend([second, third])
    doc["required_events"].append(dict(doc["required_events"][0], id="req_next", beat_id="next"))
    doc["duration_ms"] = 30000
    validate_story(doc)
    scenario = {k: copy.deepcopy(v) for k, v in doc.items() if k not in {"schema", "scenario_sha256", "scenes", "required_events"}}
    scenario.update(schema="VCD_SCENARIO_V1", source_sha256=doc["scenario_sha256"])
    enhanced = fixture("enhanced-story")
    enhanced.update(project_id=scenario["project_id"], scenario_sha256=scenario["source_sha256"], constraints=scenario["constraints"])
    enhanced["beats"] = [dict(enhanced["beats"][0], id=beat["id"], required_events=[
        copy.deepcopy(event) for event in doc["required_events"] if event["beat_id"] == beat["id"]]) for beat in scenario["beats"]]
    keys = schema()["$defs"]["scene_outline"]["required"]
    outlines = [{key: copy.deepcopy(scene[key]) for key in keys} for scene in doc["scenes"]]
    return scenario, enhanced, outlines, doc


class BeatTransitionTests(unittest.TestCase):
    def test_beat_directions_and_connection_parse_and_preserve_legacy_defaults(self):
        text = (ROOT / "assets/emd/minimal.scenario.emd").read_text(encoding="utf-8")
        legacy = parse(text)
        self.assertNotIn("direction", legacy["beats"][0])
        self.assertNotIn("transition", legacy["beats"][0])
        updated = text.replace("## 展開 return\n", "## 展開 return\n* 方針: カットせず動作を継続する。\n* 方針: 横から追従する。\n* 接続: continue\n")
        beat = parse(updated)["beats"][0]
        self.assertEqual(beat["direction"], ["カットせず動作を継続する。", "横から追従する。"])
        self.assertEqual(beat["transition"], "continue")
        self.assertEqual(beat["text"], legacy["beats"][0]["text"])
        for mode in ("auto", "cut", "continue"):
            self.assertEqual(parse(updated.replace("* 接続: continue", "* 接続: " + mode))["beats"][0]["transition"], mode)
        for insertion, code in (("* 接続: unknown\n", "BEAT_TRANSITION"),
                                ("* 接続: cut\n* 接続: continue\n", "DUPLICATE_FIELD")):
            with self.assertRaises(VCError) as caught:
                parse(text.replace("## 展開 return\n", "## 展開 return\n" + insertion))
            self.assertEqual(caught.exception.diagnostic.code, code)
            self.assertIsNotNone(caught.exception.diagnostic.line)

    def test_background_or_cast_change_is_rejected_before_model_creation(self):
        text = (ROOT / "assets/emd/minimal.scenario.emd").read_text(encoding="utf-8")
        for fields in ("* 出演: なし\n* 背景: station", "* 出演: @hero\n* 背景: room"):
            extra_bg = "\n# 背景 room\n* 情景: 室内。\n" if "room" in fields else ""
            emd = text + extra_bg + "\n## 展開 next\n" + fields + "\n* 接続: continue\n続きの動作。\n"
            with patch("vc_director.nodes.node_story_builder.node._backend") as backend:
                with self.assertRaises(VCError) as caught:
                    VCDirectorBuild().build("scenario", emd, {"assets": []}, "unused", "[]", "gemma.gguf", 1)
                self.assertEqual(caught.exception.diagnostic.code, "BEAT_TRANSITION")
                self.assertIn("cut", caught.exception.diagnostic.message)
                backend.assert_not_called()

    def test_generation_grammar_forces_connections_with_first_scene_exception(self):
        scenario, enhanced, outlines, doc = continued_story()
        payload = dict(scenario=scenario, enhanced=enhanced, accepted_outlines=[])
        validator = Draft202012Validator(generation_schema("outline", payload))
        self.assertTrue(validator.is_valid(dict(scenes=outlines[:2])))
        for index, mode in ((0, "continue"), (1, "cut")):
            changed = copy.deepcopy(outlines[:2])
            changed[index]["transition"] = mode
            self.assertFalse(validator.is_valid(dict(scenes=changed)))
        payload["accepted_outlines"] = outlines[:2]
        later = Draft202012Validator(generation_schema("outline", payload))
        self.assertTrue(later.is_valid(dict(scenes=outlines[2:])))
        self.assertFalse(later.is_valid(dict(scenes=[dict(outlines[2], transition="cut")])))
        scenario["beats"][1]["transition"] = "cut"
        cut = Draft202012Validator(generation_schema("outline", payload))
        self.assertTrue(cut.is_valid(dict(scenes=[dict(outlines[2], transition="cut")])))
        self.assertFalse(cut.is_valid(dict(scenes=outlines[2:])))
        scenario["beats"][1]["transition"] = "auto"
        auto = Draft202012Validator(generation_schema("outline", payload))
        for mode in ("cut", "continue"):
            self.assertTrue(auto.is_valid(dict(scenes=[dict(outlines[2], transition=mode)])))
        prompt = system_prompt("outline", payload)
        self.assertIn("Hold a static camera.", prompt)
        self.assertNotIn("今回の展開だけの方針: [\"Keep a slow tracking camera.\"]", prompt)

    @unittest.skipUnless(importlib.util.find_spec("llama_cpp"), "native grammar converter unavailable")
    def test_native_outline_grammar_keeps_first_cut_and_continue_tail(self):
        scenario, enhanced, outlines, doc = continued_story()
        payload = dict(scenario=scenario, enhanced=enhanced, accepted_outlines=[])
        grammar = record_grammar("SCENES", payload_schema=generation_schema("outline", payload))
        self.assertIn('scenes-prefix-0-transition ::= "\\\"cut\\\""', grammar)
        self.assertIn('scene-outline-transition ::= "\\\"continue\\\""', grammar)
        from llama_cpp import LlamaGrammar
        LlamaGrammar.from_string(grammar, verbose=False)

    def test_planner_repairs_policy_and_state_before_detail_then_saves_guide_plan(self):
        scenario, enhanced, outlines, doc = continued_story()
        bad_cut = copy.deepcopy(outlines[:2])
        bad_cut[1]["transition"] = "cut"
        bad_state = copy.deepcopy(outlines[:2])
        bad_state[1]["initial_state"][0]["value"] = "unrelated position"
        backend = FakeBackend(["SCENES\t1\t" + canonical(dict(scenes=items)) for items in
            (bad_cut, bad_state, outlines[:2], outlines[2:])] +
            ["DETAIL\t1\t" + canonical(scene) for scene in doc["scenes"]])
        document = StoryTimelinePlanner(StageRunner(backend)).plan(scenario, enhanced)
        self.assertEqual([s["transition"] for s in document["scenes"]], ["cut", "continue", "continue"])
        self.assertEqual(backend.requests[1]["errors"][0]["code"], "beat_transition")
        self.assertEqual(backend.requests[2]["errors"][0]["code"], "state_continuity")
        emd = render_scene(document)
        self.assertEqual(parse(emd), document)
        result = compile_scene_emd(emd)
        self.assertEqual([s["context_length"] for s in result.plan["shots"]], [0, 22, 22])
        self.assertEqual([s.get("continuation_mode") for s in result.plan["shots"]], [None, "guide", "guide"])
        for index, shot in enumerate(result.plan["shots"]):
            self.assertIn("Keep a slow tracking camera." if index < 2 else "Hold a static camera.", shot["prompt"])
            self.assertNotIn("Hold a static camera." if index < 2 else "Keep a slow tracking camera.", shot["prompt"])
        with temporary_directory() as directory:
            revision = save(result, directory)
            self.assertEqual(load(revision.parents[1]).plan, result.plan)

    def test_cut_policy_applies_to_every_scene_in_beat_and_saved_scene_checks_it(self):
        scenario, enhanced, outlines, doc = continued_story()
        scenario["beats"][0]["transition"] = doc["beats"][0]["transition"] = "cut"
        with self.assertRaisesRegex(VCError, "BEAT_TRANSITION"):
            compile_scene_emd(render_scene(doc))
        outlines[1]["transition"] = doc["scenes"][1]["transition"] = "cut"
        backend = FakeBackend(["SCENES\t1\t" + canonical(dict(scenes=items)) for items in
            (outlines[:2], outlines[2:])] + ["DETAIL\t1\t" + canonical(scene) for scene in doc["scenes"]])
        result = StoryTimelinePlanner(StageRunner(backend)).plan(scenario, enhanced)
        self.assertEqual([scene["transition"] for scene in result["scenes"]], ["cut", "cut", "continue"])


if __name__ == "__main__":
    unittest.main()
