import copy
import json
import unittest

from test_core import english_document, compile_fixture, with_camera, ROOT, temporary_directory, FakeBackend, fixture
from vc_director.core.emd import parse, render_scene
from vc_director.core.artifacts.validation import validate_story
from vc_director.core.errors import VCError
from vc_director.core.protocols.llm import generation_schema, system_prompt
from jsonschema import Draft202012Validator


def nonverbal_document():
    doc = english_document()
    config = dict(allowed=["gasp", "sigh"], frequency="控えめ", intensity="弱め", instruction="", pattern="natural")
    doc["vocal_patterns"] = {"natural": dict(config, pattern=None)}
    for policy in [doc["audio"], doc["beats"][0]["audio"], doc["scenes"][0]["audio"]]:
        policy.update(vocal="dialogue_nonverbal", nonverbal=copy.deepcopy(config))
    scene = doc["scenes"][0]
    scene["shots"][0]["events"].append(dict(id="scene_gasp", kind="vocal", actor_id=scene["actor_ids"][0],
        start_ms=500, end_ms=1000, text="gasp", dialogue_id=None, language=None))
    return doc


class NonverbalTests(unittest.TestCase):
    def test_roundtrip_and_compilation_preserve_speech(self):
        doc = nonverbal_document()
        validate_story(doc)
        emd = render_scene(doc)
        self.assertIn("* NONVERBAL scene_gasp", emd)
        self.assertEqual(parse(emd), doc)
        result = compile_fixture(doc)
        prompt = result.plan["shots"][0]["prompt"]
        self.assertIn("NONVERBAL <Subject 1>: gasps briefly", prompt)
        self.assertIn("<d>[Japanese]ただいま。 @hero @recipient</d>", prompt)
        self.assertNotIn("<d>gasp", prompt)
        self.assertNotIn("No vocalization outside dialogue intervals.", prompt)

    def test_forbidden_unknown_overlap_and_forged_dialogue(self):
        for change in [dict(text="scream"), dict(text="<d>hello</d>"),
                       dict(actor_id="environment"), dict(start_ms=5000, end_ms=5500),
                       dict(dialogue_id="new_words")]:
            with self.subTest(change=change):
                doc = nonverbal_document()
                doc["scenes"][0]["shots"][0]["events"][-1].update(change)
                with self.assertRaises(VCError):
                    validate_story(doc)
        doc = nonverbal_document()
        doc["scenes"][0]["audio"]["vocal"] = "dialogue"
        with self.assertRaises(VCError):
            validate_story(doc)

    def test_no_dialogue_and_no_permitted_sound(self):
        doc = nonverbal_document()
        doc["beats"][0]["dialogues"] = []
        scene = doc["scenes"][0]
        scene["shots"][0]["events"] = [e for e in scene["shots"][0]["events"] if e["kind"] != "dialogue"]
        scene["audio"]["vocal"] = "nonverbal"
        validate_story(doc)
        prompt = compile_fixture(doc).plan["shots"][0]["prompt"]
        self.assertNotIn("<d>", prompt)
        self.assertIn("No spoken words or dialogue.", prompt)

    def test_scenario_pattern_forward_reference_and_override(self):
        validate_story(parse((ROOT / "assets/emd/nonverbal.scenario.emd").read_text(encoding="utf-8")))
        text = (ROOT / "assets/emd/minimal.scenario.emd").read_text(encoding="utf-8")
        text = text.replace("* 発声: 指定台詞のみ", "* 発声: 指定台詞と非言語発声\n* 発声パターン: natural\n* 発声強度: 中程度")
        text += "\n# 発声パターン natural\n* 許可: 息を呑む、溜め息\n* 頻度: 控えめ\n* 強さ: 弱め\n"
        doc = parse(text)
        validate_story(doc)
        self.assertEqual(doc["audio"]["nonverbal"]["allowed"], ["gasp", "sigh"])
        self.assertEqual(doc["beats"][0]["audio"]["nonverbal"]["intensity"], "中程度")
        with self.assertRaises(VCError):
            parse(text.replace("発声パターン: natural", "発声パターン: missing"))

    def test_detail_grammar_allows_only_authorized_vocals(self):
        doc = nonverbal_document()
        scenario = copy.deepcopy(doc)
        scenario.update(schema="VCD_SCENARIO_V1", source_sha256="a" * 64)
        for key in ["scenes", "required_events", "scenario_sha256"]:
            scenario.pop(key)
        scene = with_camera(doc["scenes"][0])
        outline = {k: v for k, v in scene.items() if k not in {"subject_descriptions", "summary", "retention", "shots", "sound_events", "audio", "visual_directions", "visible_actor_ids"}}
        scene.update(visual_directions=[], visible_actor_ids=scene["actor_ids"])
        payload = dict(scenario=scenario, outline=outline, previous_scenes=[], required_dialogue_ids=[d["id"] for d in scenario["beats"][0]["dialogues"]])
        contract = generation_schema("detail", payload)
        Draft202012Validator(contract).validate(scene)
        changed = copy.deepcopy(scene)
        changed["shots"][0]["events"][-1]["text"] = "scream"
        self.assertFalse(Draft202012Validator(contract).is_valid(changed))
        recovery = generation_schema("detail", payload, errors=[dict(code="SHOT_COVERAGE")])
        Draft202012Validator(recovery).validate(scene)
        self.assertIn("kind=vocal", system_prompt("detail", payload))
        from vc_director.core.protocols.grammar import record_grammar
        from llama_cpp import LlamaGrammar
        LlamaGrammar.from_string(record_grammar("DETAIL", payload_schema=recovery), verbose=False)

    def test_planning_and_saved_revision(self):
        from vc_director.core.protocols import StageRunner
        from vc_director.core.planner import StoryTimelinePlanner
        from vc_director.core.artifacts.storage import save, load
        doc = nonverbal_document()
        scenario = copy.deepcopy(doc)
        scenario.update(schema="VCD_SCENARIO_V1", source_sha256=doc["scenario_sha256"])
        for key in ["scenes", "required_events", "scenario_sha256"]:
            scenario.pop(key)
        scene = with_camera(doc["scenes"][0])
        scene.update(visual_directions=[], visible_actor_ids=scene["actor_ids"])
        outline = {k: v for k, v in scene.items() if k not in {"subject_descriptions", "summary", "retention", "shots", "sound_events", "audio", "visual_directions", "visible_actor_ids"}}
        runner = StageRunner(FakeBackend(["SCENES\t1\t" + json.dumps(dict(scenes=[outline])), "DETAIL\t1\t" + json.dumps(scene)]))
        planned = StoryTimelinePlanner(runner).plan(scenario, fixture("enhanced-story"))
        self.assertEqual(planned["vocal_patterns"], doc["vocal_patterns"])
        compiled = compile_fixture(planned)
        with temporary_directory() as root:
            revision = save(compiled, root)
            restored = load(revision.parents[1])
            self.assertEqual(restored.plan, compiled.plan)

    def test_voice_reference_for_offscreen_nonverbal_actor(self):
        from vc_director.core.compiler.api import bind_references
        from vc_director.core.audio.roles import scene_roles
        doc = nonverbal_document()
        doc["actors"][0]["voice_asset_id"] = "voice"
        scene = doc["scenes"][0]
        scene["visible_actor_ids"] = []
        scene["shots"][0]["events"] = [scene["shots"][0]["events"][-1]]
        bindings = bind_references(doc, scene, {"voice": dict(kind="audio", paired_audio_enabled=False)}, separate_roles=True)
        self.assertEqual(scene_roles(scene)[1], {doc["actors"][0]["id"]})
        self.assertEqual(bindings[0]["role"], "voice")

    def test_parent_permission_and_audio_carry_cannot_expand_voice(self):
        from vc_director.core.audio.continuity import context_frames
        doc = nonverbal_document()
        doc["beats"][0]["audio"]["vocal"] = "dialogue"
        with self.assertRaises(VCError):
            validate_story(doc)
        doc = nonverbal_document()
        policy = copy.deepcopy(doc["audio"])
        policy.update(vocal="dialogue", continuity="continue")
        with self.assertRaises(VCError):
            context_frames(policy, dict(audio=doc["audio"], bindings=[]), 22, [])


if __name__ == "__main__":
    unittest.main()
