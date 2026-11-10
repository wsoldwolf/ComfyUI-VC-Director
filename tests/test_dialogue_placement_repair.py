"""Neutral in-memory regression for truncated DETAIL followed by duplicate speech."""
import copy
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from test_vocal_timing_repair import BudgetBackend
from test_vocal_prose_repair import neutral_payload
from vc_director.core.emd.diagnostics import contextual_error
from vc_director.core.errors import VCError
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.planner.identifiers import resolve_generated_ids
from vc_director.core.protocols import StageRunner
from vc_director.core.protocols.dialogue_repair import duplicate_dialogues, repair_duplicate_dialogue
from vc_director.core.protocols.llm import generation_schema
from vc_director.core.protocols.grammar import record_grammar


def duplicate_case(*, nonverbal=False):
    payload, outlines, scene = neutral_payload(nonverbal=nonverbal)
    event = next(event for event in scene["shots"][0]["events"] if event["kind"] == "dialogue")
    scene["shots"][0]["events"].append(dict(event, start_ms=3000, end_ms=3800))
    return payload, outlines, scene


def selection(scene, *, index=-1):
    return dict(selections=[dict(dialogue_id=id, keep_occurrence=occurrences[index]["path"])
                            for id, occurrences in duplicate_dialogues(scene).items()])


class DialoguePlacementRepairTests(unittest.TestCase):
    def test_final_attempt_after_two_truncations_selects_one_existing_dialogue(self):
        payload, outlines, scene = duplicate_case(nonverbal=True)
        original = copy.deepcopy(scene)
        incomplete = 'DETAIL\t1\t{"summary":"unfinished'
        backend = BudgetBackend([outlines, incomplete, incomplete, scene, selection(scene)],
                                reasons=["stop", "length", "length", "stop", "stop"])
        planned = StoryTimelinePlanner(StageRunner(backend)).plan(payload["scenario"], payload["enhanced"])
        expected = copy.deepcopy(scene)
        expected["shots"][0]["events"] = [e for e in expected["shots"][0]["events"]
                                          if e["kind"] != "dialogue" or e["start_ms"] == 3000]
        self.assertEqual(planned["scenes"][0], expected)
        self.assertEqual(scene, original)
        self.assertEqual(len(backend.requests), 5)
        self.assertEqual(backend.requests[-1]["task"], "select_authored_dialogue_placement")
        self.assertEqual(len(backend.requests[-1]["duplicate_dialogues"][0]["occurrences"]), 2)
        self.assertIn("station_line", record_grammar("DETAIL", payload_schema=backend.schemas[-1]))

    def test_selection_is_not_arbitrary_first_occurrence_and_preserves_all_other_events(self):
        payload, _, scene = duplicate_case(nonverbal=True)
        backend = BudgetBackend([selection(scene)])
        repaired = repair_duplicate_dialogue(backend, scene, payload, {})
        speech = [e for shot in repaired["shots"] for e in shot["events"] if e["kind"] == "dialogue"]
        self.assertEqual(speech, [scene["shots"][0]["events"][-1]])
        for old, new in zip(scene["shots"], repaired["shots"]):
            self.assertEqual([e for e in old["events"] if e["kind"] != "dialogue"],
                             [e for e in new["events"] if e["kind"] != "dialogue"])
        self.assertEqual(repaired["audio"], scene["audio"])
        self.assertEqual(repaired["sound_events"], scene["sound_events"])

    def test_different_authored_ids_with_identical_words_are_both_kept(self):
        payload, _, scene = duplicate_case()
        other = dict(payload["scenario"]["beats"][0]["dialogues"][0], id="station_reply")
        payload["scenario"]["beats"][0]["dialogues"].append(other)
        scene["shots"][0]["events"].append(dict(scene["shots"][0]["events"][-1],
            id="station_reply", dialogue_id="station_reply", start_ms=4000, end_ms=4500))
        repaired = repair_duplicate_dialogue(BudgetBackend([selection(scene)]), scene, payload, {})
        ids = [e["id"] for e in repaired["shots"][0]["events"] if e["kind"] == "dialogue"]
        self.assertEqual(ids, ["station_line", "station_reply"])

    def test_duplicates_in_different_shots_can_be_resolved_without_merging_shots(self):
        payload, _, scene = duplicate_case()
        second = dict(id="station_second", start_ms=2500, end_ms=5000,
                      events=[scene["shots"][0]["events"].pop()])
        scene["shots"][0]["end_ms"] = 2500
        scene["shots"].append(second)
        backend = BudgetBackend([selection(scene)])
        repaired = repair_duplicate_dialogue(backend, scene, payload, {})
        self.assertEqual(len(repaired["shots"]), 2)
        self.assertEqual(repaired["shots"][1], second)
        self.assertFalse(any(e["kind"] == "dialogue" for e in repaired["shots"][0]["events"]))

    def test_wrong_id_missing_extra_and_invented_occurrence_are_rejected(self):
        payload, _, scene = duplicate_case()
        good = selection(scene)
        cases = [dict(selections=[]), dict(selections=good["selections"] * 2),
                 dict(selections=[dict(dialogue_id="new_id", keep_occurrence="shots/0/events/0")]),
                 dict(selections=[dict(dialogue_id="station_line", keep_occurrence="shots/9/events/9")]),
                 dict(good, summary="changed")]
        for response in cases:
            with self.subTest(response=response), self.assertRaisesRegex(VCError, "DIALOGUE_REPAIR"):
                repair_duplicate_dialogue(BudgetBackend([response]), scene, payload, {})

    def test_selected_event_cannot_be_rewritten_by_the_patch(self):
        payload, _, scene = duplicate_case()
        response = selection(scene)
        response["selections"][0]["text"] = "Changed dialogue."
        with self.assertRaisesRegex(VCError, "DIALOGUE_REPAIR"):
            repair_duplicate_dialogue(BudgetBackend([response]), scene, payload, {})

    def test_corrupted_words_speaker_language_or_dialogue_id_cannot_be_hidden(self):
        for key, value in (("text", "「別の言葉。」"), ("actor_id", "guide"),
                           ("language", "English"), ("dialogue_id", "another_line")):
            with self.subTest(key=key):
                payload, _, scene = duplicate_case()
                scene["shots"][0]["events"][-1][key] = value
                backend = BudgetBackend([])
                with self.assertRaisesRegex(VCError, "DIALOGUE_CHANGED"):
                    repair_duplicate_dialogue(backend, scene, payload, {})
                self.assertEqual(backend.requests, [])

    def test_dialogue_already_used_in_a_previous_scene_is_not_silently_discarded(self):
        payload, _, scene = duplicate_case()
        payload["previous_scenes"] = [copy.deepcopy(scene)]
        backend = BudgetBackend([])
        with self.assertRaisesRegex(VCError, "DIALOGUE_REPAIR"):
            repair_duplicate_dialogue(backend, scene, payload, {})
        self.assertEqual(backend.requests, [])

    def test_fixed_sound_trigger_restricts_which_duplicate_can_be_selected(self):
        payload, _, scene = duplicate_case()
        scene["sound_events"] = [dict(id="gate_sound", trigger_event_id="station_line", start_ms=3200, end_ms=3500)]
        backend = BudgetBackend([selection(scene)])
        repair_duplicate_dialogue(backend, scene, payload, {})
        self.assertEqual(backend.requests[0]["duplicate_dialogues"][0]["eligible_paths"],
                         [duplicate_dialogues(scene)["station_line"][-1]["path"]])
        Draft202012Validator(backend.schemas[0]).validate(selection(scene))
        self.assertFalse(Draft202012Validator(backend.schemas[0]).is_valid(selection(scene, index=0)))

    def test_missing_speech_is_not_treated_as_duplicate_repair(self):
        payload, _, scene = neutral_payload()
        backend = BudgetBackend([])
        with self.assertRaisesRegex(VCError, "DIALOGUE_REPAIR"):
            repair_duplicate_dialogue(backend, scene, payload, {})
        self.assertEqual(backend.requests, [])

    def test_failed_selection_is_bounded_and_not_cached(self):
        payload, _, scene = duplicate_case()
        backend = BudgetBackend([scene, dict(selections=[]), scene, scene])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            runner = StageRunner(backend, Path(directory) / "cache")
            with self.assertRaisesRegex(VCError, "LLM_RETRIES_EXHAUSTED"):
                runner.run("detail", scene["id"], payload, lambda s: resolve_generated_ids(s, []))
            self.assertEqual(len(backend.requests), 4)
            self.assertIsNone(runner.cache.get(runner.trace[-1]["key"]))

    def test_successful_selection_is_revalidated_before_cache_reuse(self):
        payload, _, scene = duplicate_case()
        backend = BudgetBackend([scene, selection(scene)])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            runner = StageRunner(backend, Path(directory) / "cache")
            check = lambda candidate: resolve_generated_ids(candidate, [])
            accepted = runner.run("detail", scene["id"], payload, check)
            cached = runner.run("detail", scene["id"], payload, check)
            self.assertEqual(accepted, cached)
            self.assertEqual(len(backend.requests), 2)
            self.assertEqual(runner.trace[-1]["cache"], "hit")

    def test_japanese_error_identifies_generated_scene_and_authored_id(self):
        _, _, scene = duplicate_case()
        with self.assertRaises(VCError) as caught:
            resolve_generated_ids(scene, [])
        formatted = contextual_error(caught.exception, "# 形式\n", {})
        self.assertIn("生成されたScene", str(formatted))
        self.assertIn("station_line", str(formatted))


class DetailStringLimitTests(unittest.TestCase):
    def test_prose_is_bounded_initially_and_more_compact_after_truncation(self):
        payload, _, _ = neutral_payload()
        normal = generation_schema("detail", payload)
        retry = generation_schema("detail", payload, errors=[dict(code="compact_detail")])
        self.assertEqual(normal["$defs"]["scene"]["properties"]["summary"]["maxLength"], 1024)
        self.assertEqual(retry["$defs"]["scene"]["properties"]["summary"]["maxLength"], 512)
        for narrowed in (normal, retry):
            Draft202012Validator.check_schema(narrowed)
            grammar = record_grammar("DETAIL", payload_schema=narrowed)
            self.assertIn("vcd-json-char{1,", grammar)
            self.assertNotIn('[a-z0-9_]*', grammar)
            self.assertLess(len(grammar), 20000)
            from llama_cpp import LlamaGrammar
            LlamaGrammar.from_string(grammar, verbose=False)

    def test_generated_long_prose_is_rejected_without_truncating_the_input(self):
        payload, _, scene = neutral_payload()
        original = copy.deepcopy(payload)
        narrowed = generation_schema("detail", payload)
        scene["summary"] = "x" * 1025
        self.assertFalse(Draft202012Validator(narrowed).is_valid(scene))
        self.assertEqual(payload, original)

    def test_long_authored_dialogue_and_outline_constants_are_not_limited(self):
        payload, _, _ = neutral_payload()
        text = "「" + "おはよう。" * 500 + "」"
        payload["scenario"]["beats"][0]["dialogues"][0]["text"] = text
        payload["outline"]["initial_state"] = [dict(owner_id="traveler", key="clothing", value="青い服。" * 400)]
        narrowed = generation_schema("detail", payload, errors=[dict(code="compact_detail")])
        spoken = next(event for event in narrowed["$defs"]["event"]["oneOf"]
                      if event["properties"]["kind"].get("const") == "dialogue")
        self.assertEqual(spoken["properties"]["text"], {"const": text})
        self.assertEqual(narrowed["$defs"]["scene"]["properties"]["initial_state"],
                         {"const": payload["outline"]["initial_state"]})

    def test_long_authored_visible_text_expands_the_generated_prose_limit(self):
        payload, _, _ = neutral_payload()
        words = "駅の案内" * 400
        payload["scenario"]["beats"][0]["text"] += f"<visible>{words}</visible>"
        narrowed = generation_schema("detail", payload, errors=[dict(code="compact_detail")])
        self.assertGreater(narrowed["$defs"]["scene"]["properties"]["summary"]["maxLength"], len(words))

    def test_bounded_json_string_keeps_escapes_and_excludes_literal_controls(self):
        grammar = record_grammar("DETAIL", payload_schema=dict(type="string", minLength=1, maxLength=8))
        self.assertIn("vcd-json-char{1,8}", grammar)
        self.assertIn(r'[^"\\\x00-\x1F]', grammar)
        self.assertIn(r'["\\/bfnrt]', grammar)
        from llama_cpp import LlamaGrammar
        LlamaGrammar.from_string(grammar, verbose=False)


if __name__ == "__main__":
    unittest.main()
