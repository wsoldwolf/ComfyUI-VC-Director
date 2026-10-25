import copy
import importlib.util
import json
import types
import unittest
from pathlib import Path

from test_core import ROOT, FakeBackend, english_document, fixture, temporary_directory, with_camera
from vc_director.core.errors import VCError
from vc_director.core.artifacts.validation import validate_story
from vc_director.core.inference import GemmaBackend
from vc_director.core.protocols import StageRunner, system_prompt
from vc_director.core.audio.control import DEFAULT_AUDIO, validate_audio
from vc_director.core.protocols.grammar import record_grammar
from vc_director.core.protocols.llm import parse_response
from vc_director.core.protocols.llm import _payload_schema, generation_schema
from vc_director.core.emd import parse
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.direction import StoryEnhancer
from vc_director.core.utilities import canonical, digest


class PlanningTransportTests(unittest.TestCase):
    @staticmethod
    def silent_detail():
        scenario = parse((ROOT / "assets/emd/minimal.scenario.emd").read_text(encoding="utf-8"))
        scenario["beats"][0].update(dialogues=[], audio=copy.deepcopy(DEFAULT_AUDIO))
        outline = fixture("outline-response")["records"][0]["payload"]["scenes"][0]
        outline.update(start_ms=10000, end_ms=20000)
        scene = english_document()["scenes"][0]
        scene.update(outline)
        scene["audio"] = copy.deepcopy(DEFAULT_AUDIO)
        scene["shots"][0]["events"] = scene["shots"][0]["events"][:1]
        return dict(scenario=scenario, outline=outline, previous_scenes=[]), with_camera(scene)

    def test_detail_generation_cannot_enable_forbidden_audio(self):
        from jsonschema import Draft202012Validator
        payload, scene = self.silent_detail()
        validator = Draft202012Validator(generation_schema("detail", payload))
        self.assertTrue(validator.is_valid(scene))
        for key, mode in (("vocal", "dialogue"), ("bgm", "generate"), ("ambience", "explicit"), ("effects", "explicit")):
            changed = copy.deepcopy(scene)
            changed["audio"][key] = mode
            if key != "vocal":
                changed["audio"][key + "_description"] = "Wind, footsteps or music."
            self.assertFalse(validator.is_valid(changed), key)
        for field, value in (("sound_events", [dict(id="snd_gate", kind="effect", start_ms=0, end_ms=1000, trigger_event_id="ev_walk", text="Gate creaks.")]),
                             ("audio", dict(DEFAULT_AUDIO, reference_audio=[dict(asset_id="unapproved", purpose="effects")]))):
            changed = copy.deepcopy(scene)
            changed[field] = value
            self.assertFalse(validator.is_valid(changed))
        # Even silent modes must not carry hidden free-text sound instructions.
        changed = copy.deepcopy(scene)
        changed["audio"]["ambience_description"] = "Wind."
        self.assertFalse(validator.is_valid(changed))

    def test_detail_generation_keeps_authorized_effects_and_reference_subset(self):
        from jsonschema import Draft202012Validator
        payload, scene = self.silent_detail()
        policy = payload["scenario"]["beats"][0]["audio"]
        policy.update(effects="expand", reference_audio=[dict(asset_id="steps", purpose="effects")])
        scene["audio"] = copy.deepcopy(policy)
        scene["audio"].update(effects="explicit", effects_description="Footsteps.")
        sound = dict(id="snd_steps", kind="effect", start_ms=0, end_ms=1000, trigger_event_id="ev_walk", text="Footsteps.")
        scene["sound_events"] = [sound]
        narrowed = generation_schema("detail", payload)
        validator = Draft202012Validator(narrowed)
        self.assertTrue(validator.is_valid(scene))
        changed = copy.deepcopy(scene)
        changed["sound_events"][0]["kind"] = "ambience"
        self.assertFalse(validator.is_valid(changed))
        changed = copy.deepcopy(scene)
        changed["audio"]["reference_audio"][0]["asset_id"] = "unapproved"
        self.assertFalse(validator.is_valid(changed))
        changed = copy.deepcopy(scene)
        changed["sound_events"][0]["end_ms"] = 20000
        self.assertFalse(validator.is_valid(changed))
        scene["audio"]["reference_audio"] = []
        self.assertTrue(validator.is_valid(scene))
        if importlib.util.find_spec("llama_cpp"):
            self.assertIn("vcd-payload-root ::=", record_grammar("DETAIL", payload_schema=narrowed))

    def test_detail_generation_bounds_local_times_for_later_scene(self):
        from jsonschema import Draft202012Validator
        payload, scene = self.silent_detail()
        validator = Draft202012Validator(generation_schema("detail", payload))
        for field in ("start_ms", "end_ms"):
            for event in (False, True):
                changed = copy.deepcopy(scene)
                owner = changed["shots"][0]["events"][0] if event else changed["shots"][0]
                owner[field] = 20000
                self.assertFalse(validator.is_valid(changed))
        prompt = system_prompt("detail", payload)
        self.assertIn("10000〜20000ms", prompt)
        self.assertIn("0〜10000ms", prompt)
        self.assertIn('"ambience":"none"', prompt)

    def test_audio_permission_feedback_names_the_forbidden_field_and_fix(self):
        policy = dict(DEFAULT_AUDIO, ambience="explicit", ambience_description="Wind.")
        with self.assertRaises(VCError) as caught:
            validate_audio(policy, "scene_journey", DEFAULT_AUDIO)
        self.assertEqual(caught.exception.diagnostic.path, "scene_journey/audio/ambience")
        self.assertIn("audio.ambience='none'", caught.exception.diagnostic.message)
        self.assertIn("description=null", caught.exception.diagnostic.message)

    def test_zero_length_trailing_shot_feedback_explains_how_to_repartition(self):
        document = english_document()
        scene = document["scenes"][0]
        event = copy.deepcopy(scene["shots"][0]["events"][0])
        event.update(id="ev_zero", start_ms=10000, end_ms=10000)
        scene["shots"].append(dict(id="shot_zero", start_ms=10000, end_ms=10000, events=[event]))
        with self.assertRaises(VCError) as caught:
            validate_story(document)
        self.assertEqual(caught.exception.diagnostic.code, "SHOT_COVERAGE")
        self.assertIn("received 10000..10000ms", caught.exception.diagnostic.message)
        self.assertIn("one Shot spanning 0..10000ms", caught.exception.diagnostic.message)

    def test_detail_request_is_validated_before_dynamic_prompt_reads_it(self):
        backend = FakeBackend([])
        with self.assertRaises(VCError) as caught:
            StageRunner(backend).run("detail", "scene_journey", {}, lambda value: None)
        self.assertEqual(caught.exception.diagnostic.code, "SCHEMA")
        self.assertEqual(backend.requests, [])

    @staticmethod
    def multi_beat_story():
        scenario, enhanced = fixture("scenario"), fixture("enhanced-story")
        source, result = scenario["beats"][0], enhanced["beats"][0]
        scenario["beats"], enhanced["beats"] = [], []
        for index, beat_id in enumerate(("return", "journey", "home")):
            beat, enriched = copy.deepcopy(source), copy.deepcopy(result)
            beat["id"], enriched["id"] = beat_id, beat_id
            span = dict(document="scenario", first_line=10 * index + 1, last_line=10 * index + 3)
            beat["source"] = span
            enriched["required_events"][0].update(id="req_" + beat_id, beat_id=beat_id, source=copy.deepcopy(span))
            scenario["beats"].append(beat)
            enhanced["beats"].append(enriched)
        return scenario, enhanced

    def test_enhance_generation_preserves_all_beats_and_their_sources(self):
        from jsonschema import Draft202012Validator
        scenario, enhanced = self.multi_beat_story()
        published = copy.deepcopy(_payload_schema("enhance"))
        narrowed = generation_schema("enhance", dict(scenario=scenario, image_facts=[]))
        Draft202012Validator.check_schema(narrowed)
        validator = Draft202012Validator(narrowed)
        self.assertTrue(validator.is_valid(enhanced))
        invalid = []
        for beats in (enhanced["beats"][:1], enhanced["beats"][::-1], enhanced["beats"] + enhanced["beats"][:1],
                      [enhanced["beats"][0]] * 3):
            changed = copy.deepcopy(enhanced)
            changed["beats"] = beats
            invalid.append(changed)
        for key in ("project_id", "scenario_sha256", "constraints"):
            changed = copy.deepcopy(enhanced)
            changed[key] = [{"id": "extra"}] if key == "constraints" else "changed"
            invalid.append(changed)
        for change in (dict(beat_id="return"), dict(source=dict(document="scenario", first_line=1, last_line=3)),
                       dict(source=dict(document="scene", first_line=11, last_line=13))):
            changed = copy.deepcopy(enhanced)
            changed["beats"][1]["required_events"][0].update(change)
            invalid.append(changed)
        for changed in invalid:
            self.assertFalse(validator.is_valid(changed))
        self.assertEqual(_payload_schema("enhance"), published)
        # No references or objects in the author's input were mutated by narrowing.
        self.assertEqual((scenario, enhanced), self.multi_beat_story())

    def test_omitted_beats_are_retried_with_expected_order_and_not_silently_filled(self):
        scenario, enhanced = self.multi_beat_story()
        missing = copy.deepcopy(enhanced)
        missing["beats"] = missing["beats"][:1]
        backend = FakeBackend(["ENHANCED\t1\t" + canonical(missing), "ENHANCED\t1\t" + canonical(enhanced)])
        self.assertEqual(StoryEnhancer(StageRunner(backend)).enhance(scenario), enhanced)
        error = backend.requests[1]["errors"][0]
        self.assertEqual(error["path"], "/beats")
        self.assertIn("['return', 'journey', 'home']", error["message"])
        self.assertIn("received: ['return']", error["message"])

    def test_detail_generation_keeps_outline_and_exact_author_dialogue(self):
        from jsonschema import Draft202012Validator
        scenario = parse((ROOT / "assets/emd/minimal.scenario.emd").read_text(encoding="utf-8"))
        outline = fixture("outline-response")["records"][0]["payload"]["scenes"][0]
        payload = dict(scenario=scenario, outline=outline, previous_scenes=[])
        published = copy.deepcopy(_payload_schema("detail"))
        narrowed = generation_schema("detail", payload)
        validator = Draft202012Validator(narrowed)
        scene = english_document()["scenes"][0]
        scene["shots"][0]["events"][1]["text"] = scenario["beats"][0]["dialogues"][0]["text"]
        scene = with_camera(scene)
        self.assertTrue(validator.is_valid(scene))
        for field in ("dialogue_id", "id", "text", "actor_id"):
            changed = copy.deepcopy(scene)
            next(e for e in changed["shots"][0]["events"] if e["kind"] == "dialogue")[field] = "changed"
            self.assertFalse(validator.is_valid(changed), field)
        changed = copy.deepcopy(scene)
        changed["summary"] = "少女は「ただいま」と呟く。"
        with self.assertRaises(VCError) as caught:
            validate_story(StoryTimelinePlanner._document(scenario, [changed], fixture("enhanced-story")["beats"][0]["required_events"]))
        self.assertEqual(caught.exception.diagnostic.code, "STRAY_DIALOGUE")
        self.assertTrue(caught.exception.diagnostic.path.endswith("/summary"))
        changed = copy.deepcopy(scene)
        changed["id"] = "changed_scene"
        self.assertFalse(validator.is_valid(changed))
        payload["previous_scenes"] = [scene]
        self.assertFalse(Draft202012Validator(generation_schema("detail", payload)).is_valid(scene))
        self.assertEqual(_payload_schema("detail"), published)

    @unittest.skipUnless(importlib.util.find_spec("llama_cpp"), "Optional llama.cpp grammar converter unavailable")
    def test_all_published_stage_schemas_convert_without_loading_model(self):
        for stage, record in (("enhance", "ENHANCED"), ("outline", "SCENES"), ("detail", "DETAIL"), ("retime", "TIMING")):
            with self.subTest(stage=stage):
                grammar = record_grammar(record, payload_schema=_payload_schema(stage))
                self.assertEqual(sum(line.startswith("root ::=") for line in grammar.splitlines()), 1)
                self.assertIn("vcd-payload-root ::=", grammar)
        scenario = parse((ROOT / "assets/emd/minimal.scenario.emd").read_text(encoding="utf-8"))
        payload = dict(scenario=scenario, outline=fixture("outline-response")["records"][0]["payload"]["scenes"][0], previous_scenes=[])
        self.assertIn("vcd-payload-root ::=", record_grammar("DETAIL", payload_schema=generation_schema("detail", payload)))
        scenario, _ = self.multi_beat_story()
        grammar = record_grammar("ENHANCED", payload_schema=generation_schema("enhance", dict(scenario=scenario, image_facts=[])))
        from llama_cpp import LlamaGrammar
        self.assertIsNotNone(LlamaGrammar.from_string(grammar, verbose=False))
        for index in range(3):
            self.assertIn(f"beats-tuple-{index}-id ::=", grammar)

    def test_first_scene_continuation_feedback_gives_exact_correction(self):
        scenario, enhanced = fixture("scenario"), fixture("enhanced-story")
        outline = fixture("outline-response")["records"][0]["payload"]
        bad = copy.deepcopy(outline)
        bad["scenes"][0]["transition"] = "continue"
        detail = with_camera(fixture("detail-response")["records"][0]["payload"])
        backend = FakeBackend(["SCENES\t1\t" + canonical(bad), "SCENES\t1\t" + canonical(outline), "DETAIL\t1\t" + canonical(detail)])
        document = StoryTimelinePlanner(StageRunner(backend)).plan(scenario, enhanced)
        self.assertEqual(document["scenes"][0]["transition"], "cut")
        self.assertIn("transition='cut'", backend.requests[1]["errors"][0]["message"])

    def test_complete_fence_and_pretty_json_preserve_payload_strings(self):
        value = {"description": "First line\nSecond line", "dialogue": "<d>[Japanese]ただいま。</d>"}
        raw = "```text\r\nDETAIL\t1\t" + json.dumps(value, ensure_ascii=False, indent=2) + "\r\n```"
        response = parse_response(raw, "detail", "request_a", "scene_a")
        self.assertEqual(response["records"][0]["payload"], value)
        self.assertEqual(response["records"][0]["slot"], 1)

    def test_trailing_text_or_competing_records_are_not_discarded(self):
        first = 'ENHANCED\t1\t{"intent":"valid object"}'
        for extra in ("}", " explanation", '{}', '\tENHANCED\t1\t{}', '\nENHANCED\t1\t{}'):
            with self.subTest(extra=extra):
                with self.assertRaisesRegex(VCError, "PROTOCOL_TRAILING"):
                    parse_response(first + extra, "enhance", "request_a", "scenario_a")

    def test_failure_diagnostic_contains_all_raw_attempts_and_request(self):
        raw = "ENHANCED\t1\t" + canonical(fixture("enhanced-story")) + "}"
        backend = FakeBackend([raw, raw, raw])
        scenario = fixture("scenario")
        with temporary_directory() as temp:
            directory = Path(temp) / "diagnostics"
            runner = StageRunner(backend, diagnostics_root=directory)
            with self.assertRaisesRegex(VCError, "LLM_RETRIES_EXHAUSTED") as caught:
                runner.run("enhance", scenario["project_id"], dict(scenario=scenario, image_facts=[]), lambda value: None)
            files = list(directory.glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertIn(str(files[0]), str(caught.exception))
            diagnostic = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(diagnostic["status"], "retries_exhausted")
            self.assertEqual(diagnostic["request"]["payload"]["scenario"], scenario)
            self.assertEqual([item["attempt"] for item in diagnostic["attempts"]], ["initial", "repair", "isolated"])
            self.assertEqual([item["raw"] for item in diagnostic["attempts"]], [raw] * 3)
            self.assertEqual(diagnostic["attempts"][-1]["errors"][0]["code"], "protocol_trailing")
            self.assertEqual(backend.requests[1]["errors"][0]["code"], "protocol_trailing")

    def test_constrained_backend_is_used_and_repair_is_validated(self):
        class Backend(FakeBackend):
            def complete_record(self, system, request, record_type, **kwargs):
                self.record_type = record_type
                self.payload_schema = kwargs["payload_schema"]
                return self.complete(system, request)

        enhanced = fixture("enhanced-story")
        backend = Backend(["ENHANCED\t1\t" + canonical(enhanced) + "}", "ENHANCED\t1\t" + canonical(enhanced)])
        scenario = fixture("scenario")
        with temporary_directory() as temp:
            runner = StageRunner(backend, temp)
            actual = runner.run("enhance", scenario["project_id"], dict(scenario=scenario, image_facts=[]), lambda value: None)
            self.assertEqual(actual, enhanced)
            self.assertEqual(backend.record_type, "ENHANCED")
            self.assertIn("enhanced_story", backend.payload_schema["$defs"])
            self.assertEqual(len(backend.requests), 2)
            diagnostic = json.loads(next((Path(temp) / "diagnostics").glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(diagnostic["status"], "success")
            self.assertEqual(diagnostic["attempts"][0]["raw"][-1], "}")
            runner.run("enhance", scenario["project_id"], dict(scenario=scenario, image_facts=[]), lambda value: None)
            self.assertEqual(len(backend.requests), 2)  # Only a validated response is cached.

    def test_grammar_reaches_llama_cpp_with_exact_record_and_slot(self):
        with temporary_directory() as temp:
            path = Path(temp) / "fake.gguf"
            path.write_bytes(b"test fixture")
            backend = GemmaBackend(path)
            captured = {}
            def complete(messages, config, **kwargs):
                captured.update(kwargs)
                return "ENHANCED\t1\t{}"
            backend.lifecycle = types.SimpleNamespace(
                ensure_loaded=lambda *args: None,
                count_serialized_prompt=lambda prompt: types.SimpleNamespace(count=20, estimated=False),
                effective_n_ctx=24576, complete_chat=complete, clear=lambda: None)
            self.assertEqual(backend.complete_record("system", "{}", "ENHANCED"), "ENHANCED\t1\t{}")
            grammar = record_grammar("ENHANCED")
            self.assertEqual(captured["grammar"], grammar)
            self.assertIn('root ::= "ENHANCED\\t1\\t" object', grammar)
            self.assertEqual(backend.trace[-1]["grammar_sha256"], digest(grammar))
            backend.close()

    def test_backend_exception_is_logged_and_not_retried(self):
        class Backend(FakeBackend):
            def complete(self, system, request):
                self.requests.append(request)
                raise RuntimeError("model allocation failed")

        backend = Backend([])
        scenario = fixture("scenario")
        with temporary_directory() as temp:
            runner = StageRunner(backend, diagnostics_root=temp)
            with self.assertRaisesRegex(RuntimeError, "allocation failed"):
                runner.run("enhance", scenario["project_id"], dict(scenario=scenario, image_facts=[]), lambda value: None)
            diagnostic = json.loads(next(Path(temp).glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(diagnostic["status"], "backend_error")
            self.assertEqual(len(backend.requests), 1)


if __name__ == "__main__":
    unittest.main()
