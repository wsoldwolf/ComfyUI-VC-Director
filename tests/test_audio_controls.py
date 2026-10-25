import copy
import types
import unittest
from unittest.mock import patch

from test_core import ROOT, compile_fixture, english_document, temporary_directory
from test_beat_transitions import continued_story
from vc_director.core.artifacts.storage import load, save
from vc_director.core.audio import DEFAULT_AUDIO, dialogue, parse_audio
from vc_director.core.audio.continuity import NEGPIP_MUSIC, context_frames
from vc_director.core.compiler import compile_scene_emd
from vc_director.core.emd import parse, render_scene
from vc_director.core.errors import VCError
from vc_director.core.protocols.llm import generation_schema
from vc_director.core.utilities import canonical, digest
from vc_director.nodes import VCDirectorAudioControl


class FakeNegPiP:
    calls = []

    def apply(self, model, clip, **kwargs):
        self.calls.append(kwargs)
        return "patched-model", types.SimpleNamespace()


class AudioControlTests(unittest.TestCase):
    def test_controls_inherit_and_roundtrip_without_changing_legacy_defaults(self):
        text = (ROOT / "assets/emd/minimal.scenario.emd").read_text(encoding="utf-8")
        legacy = parse(text)
        self.assertNotIn("continuity", legacy["audio"])
        updated = text.replace("# 作品\n", "# 作品\n* 音声接続: continue\n* BGM抑制: negpip\n")
        scenario = parse(updated)
        self.assertEqual(scenario["beats"][0]["audio"]["continuity"], "continue")
        self.assertEqual(scenario["beats"][0]["audio"]["bgm_suppression"], "negpip")
        overridden = parse(updated.replace("## 展開 return\n", "## 展開 return\n* 音声接続: cut\n"))
        self.assertEqual(overridden["beats"][0]["audio"]["continuity"], "cut")
        for field in ("音声接続", "BGM抑制"):
            with self.assertRaisesRegex(VCError, "AUDIO_POLICY"):
                parse(text.replace("# 作品\n", "# 作品\n* " + field + ": unknown\n"))
        policy = parse_audio([("音声接続", "continue", 1), ("音響", "無音", 2), ("BGM抑制", "negpip", 3)])
        self.assertEqual(policy["continuity"], "continue")
        self.assertEqual(policy["bgm_suppression"], "negpip")

    def test_audio_only_cut_continuation_has_no_picture_overlap_or_event_shift(self):
        _, _, _, document = continued_story()
        for beat in document["beats"]:
            beat["transition"] = "cut"
            beat["audio"]["continuity"] = "continue"
        for scene in document["scenes"]:
            scene["transition"] = "cut"
            scene["audio"]["continuity"] = "continue"
        result = compile_fixture(document)
        self.assertEqual([s["context_length"] for s in result.plan["shots"]], [0, 0, 0])
        self.assertEqual([s["audio_context_length"] for s in result.plan["shots"]], [0, 22, 22])
        self.assertEqual([s["length"] for s in result.plan["shots"]], [243, 243, 243])
        self.assertEqual(result.execution["runtime_contract"]["output_sample_rate"], 32000)
        timing = result.execution["segments"][1]["event_timing"][0]
        self.assertEqual(timing["raw_start_frame"], 0)
        self.assertIn("[00:00.000 --> 00:10.000] CAMERA", result.plan["shots"][1]["prompt"])
        self.assertEqual(result.execution["segments"][1]["start_frame"], 243)
        self.assertEqual(parse(result.scene_emd), document)
        with temporary_directory() as directory:
            revision = save(result, directory)
            self.assertEqual(load(revision.parents[1]).execution, result.execution)
            legacy_rate = compile_scene_emd(result.scene_emd, replay_output_sample_rate=48000)
            revision = save(legacy_rate, directory)
            self.assertEqual(load(revision.parents[1]).execution["runtime_contract"]["output_sample_rate"], 48000)

    def test_visual_continuation_can_explicitly_cut_audio(self):
        _, _, _, document = continued_story()
        for beat in document["beats"]: beat["audio"]["continuity"] = "cut"
        for scene in document["scenes"]: scene["audio"]["continuity"] = "cut"
        result = compile_fixture(document)
        self.assertEqual([s["context_length"] for s in result.plan["shots"]], [0, 22, 22])
        self.assertEqual([s["audio_context_length"] for s in result.plan["shots"]], [0, 0, 0])

    def test_explicit_audio_carry_does_not_ignore_narrowed_permission(self):
        previous = dict(audio=dict(DEFAULT_AUDIO, vocal="dialogue"), bindings=[])
        with self.assertRaisesRegex(VCError, "AUDIO_CONTINUITY_PERMISSION"):
            context_frames(dict(DEFAULT_AUDIO, continuity="continue"), previous, 0, [])
        # Silence may precede a new permitted line, even at a picture cut.
        previous["audio"]["vocal"] = "none"
        self.assertEqual(context_frames(dict(DEFAULT_AUDIO, vocal="dialogue", continuity="continue"), previous, 0, []), 22)

    def test_explicit_auto_keeps_legacy_semantics(self):
        previous = dict(audio=copy.deepcopy(DEFAULT_AUDIO), bindings=[])
        self.assertEqual(context_frames(dict(DEFAULT_AUDIO, continuity="auto"), previous, 22, []), 22)
        self.assertEqual(context_frames(dict(DEFAULT_AUDIO, continuity="auto"), previous, 0, []), 0)

    def test_compiler_adds_owned_audio_only_negative_without_translating_dialogue(self):
        document = english_document()
        for policy in (document["audio"], document["beats"][0]["audio"], document["scenes"][0]["audio"]):
            policy.update(bgm_suppression="negpip", continuity="continue")
        result = compile_fixture(document)
        self.assertTrue(result.plan["shots"][0]["prompt"].endswith(NEGPIP_MUSIC))
        self.assertIn(dialogue(document["beats"][0]["dialogues"][0]["text"])[0], result.plan["shots"][0]["prompt"])
        self.assertEqual(result.plan["shots"][0]["audio_context_length"], 0)
        self.assertEqual(parse(result.scene_emd), document)
        with self.assertRaisesRegex(VCError, "AUDIO_SUPPRESSION"):
            parse_audio([("BGM", "生成:器楽曲", 1), ("BGM抑制", "negpip", 2)])

    def test_compact_policy_keeps_silence_dialogue_and_permitted_physical_sounds_distinct(self):
        document = english_document()
        document["beats"][0]["dialogues"] = []
        document["scenes"][0]["shots"][0]["events"].pop()
        for policy in (document["audio"], document["beats"][0]["audio"], document["scenes"][0]["audio"]):
            policy["vocal"] = "none"
        prompt = compile_fixture(document).plan["shots"][0]["prompt"]
        self.assertTrue(prompt.endswith("overall_soundscape:\nComplete silence.\n\nnon_diegetic_music:\nN/A"))
        for policy in (document["audio"], document["beats"][0]["audio"], document["scenes"][0]["audio"]):
            policy.update(ambience="explicit", ambience_description="Soft wind.")
        prompt = compile_fixture(document).plan["shots"][0]["prompt"]
        self.assertIn("Ambience: Soft wind.", prompt)
        self.assertIn("No character vocalization is present.", prompt)
        self.assertNotIn("Complete silence.", prompt)

    def test_generation_cannot_invent_or_change_audio_controls(self):
        scenario, enhanced, outlines, doc = continued_story()
        for field, value in (("continuity", "continue"), ("bgm_suppression", "negpip")):
            scenario["beats"][0]["audio"][field] = value
        payload = dict(scenario=scenario, enhanced=enhanced, outline=outlines[0], previous_scenes=[], locked_scene=None)
        generated = generation_schema("detail", payload)
        props = generated["$defs"]["audio_policy"]["properties"]
        self.assertEqual(props["continuity"], {"const": "continue"})
        self.assertEqual(props["bgm_suppression"], {"const": "negpip"})
        self.assertTrue({"continuity", "bgm_suppression"} <= set(generated["$defs"]["audio_policy"]["required"]))
        for field in ("continuity", "bgm_suppression"): scenario["beats"][0]["audio"].pop(field)
        props = generation_schema("detail", payload)["$defs"]["audio_policy"]["properties"]
        self.assertNotIn("continuity", props)
        self.assertNotIn("bgm_suppression", props)

    def test_audio_adapter_is_noop_for_legacy_and_fingerprints_strength_and_source(self):
        result = compile_fixture()
        node = VCDirectorAudioControl()
        model, clip = object(), object()
        actual = node.apply(model, clip, result)
        self.assertIs(actual[0], model)
        self.assertIs(actual[1], clip)
        self.assertEqual(actual[2], digest(canonical(dict(plan=result.plan, execution=result.execution))))
        doc = english_document()
        for policy in (doc["audio"], doc["beats"][0]["audio"], doc["scenes"][0]["audio"]): policy["bgm_suppression"]="negpip"
        compiled = compile_fixture(doc)
        fake_nodes = types.SimpleNamespace(NODE_CLASS_MAPPINGS={"ApplyMiniMaxH3NegPiP": FakeNegPiP})
        FakeNegPiP.calls = []
        with patch.dict("sys.modules", nodes=fake_nodes):
            first = node.apply(model, clip, compiled, value_strength=1)
            second = node.apply(model, clip, compiled, value_strength=2)
        self.assertTrue(first[1].vcd_negpip_active)
        self.assertNotEqual(first[2], second[2])
        self.assertFalse(FakeNegPiP.calls[0]["apply_positive_weights"])
        with patch.dict("sys.modules", nodes=types.SimpleNamespace(NODE_CLASS_MAPPINGS={})):
            with self.assertRaisesRegex(VCError, "NEGPIP_REQUIRED"): node.apply(model, clip, compiled)


if __name__ == "__main__": unittest.main()
