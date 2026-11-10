"""Neutral JSON/truncation recovery; all responses are injected in memory."""
import copy
import json
import tempfile
import types
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from test_build_cache import SCENARIO
from vc_director.core.artifacts.schema import validate
from vc_director.core.direction import StoryEnhancer
from vc_director.core.emd import parse
from vc_director.core.emd.diagnostics import contextual_error
from vc_director.core.errors import VCError
from vc_director.core.inference.gemma_backend import GemmaBackend
from vc_director.core.inference.llama_cpp_backend import LlamaCppLifecycle
from vc_director.core.inference.runtime import LlamaRuntimeConfig
from vc_director.core.protocols import StageRunner
from vc_director.core.protocols.grammar import record_grammar
from vc_director.core.protocols.llm import generation_schema
from vc_director.core.protocols.response_errors import json_failure
from vc_director.core.utilities import canonical
from vc_director.core.utilities.progress import progress_scope


def neutral_case():
    text = SCENARIO.replace("00:05.000", "00:10.000") + """
## 展開 departure
* 出演: @traveler
* 背景: station
@travelerが駅の入口へ歩く。
# 固定条件 daylight
* 内容: 明るい昼間。
"""
    scenario = parse(text)
    expected = dict(schema="VCD_ENHANCED_STORY_V1", project_id=scenario["project_id"],
        scenario_sha256=scenario["source_sha256"], intent="駅で待ち、入口へ移動する。",
        constraints=copy.deepcopy(scenario["constraints"]), beats=[dict(id=beat["id"], intent="駅での動作。",
            required_events=[dict(id="req_" + beat["id"], beat_id=beat["id"], text=beat["text"],
                                  source=copy.deepcopy(beat["source"]))]) for beat in scenario["beats"]])
    parts = [dict(copy.deepcopy(expected), beats=[copy.deepcopy(beat)]) for beat in expected["beats"]]
    return text, scenario, expected, parts


def unfinished(value):
    return "ENHANCED\t1\t" + canonical(value)[:-1]


class NeutralBackend:
    def __init__(self, responses, *, reasons=(), identity="neutral-enhance"):
        self.responses = iter(responses)
        self.reasons = list(reasons)
        self.requests, self.schemas, self.systems, self.trace = [], [], [], []
        self.model_identity = identity

    def identity(self):
        return dict(model=self.model_identity)

    def complete_record(self, system, request, record_type, *, payload_schema):
        index = len(self.requests)
        self.requests.append(json.loads(request))
        self.schemas.append(copy.deepcopy(payload_schema))
        self.systems.append(system)
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        reason = self.reasons[index] if index < len(self.reasons) else None
        self.trace.append(dict(performance=dict(finish_reason=reason, max_tokens=4096)))
        return response if isinstance(response, str) else record_type + "\t1\t" + canonical(response)


class EnhanceRecoveryTests(unittest.TestCase):
    def test_stream_preserves_length_stop_without_loading_a_model(self):
        metrics = {}
        stream = [dict(choices=[dict(delta=dict(content='ENHANCED\t1\t{"beats":['), finish_reason=None)]),
                  dict(choices=[dict(delta={}, finish_reason="length")]), dict(choices=[])]
        result = LlamaCppLifecycle._collect_chat_stream(stream, None, metrics)
        self.assertTrue(result.endswith("["))
        self.assertEqual(metrics["finish_reason"], "length")

    def test_backend_trace_preserves_stop_reason_and_actual_output_budget(self):
        backend = GemmaBackend.__new__(GemmaBackend)
        backend.config = LlamaRuntimeConfig(n_ctx=8192, max_tokens=256, gpu_layers=0)
        backend.model_path, backend.trace = "neutral-not-a-real-model", []
        backend.lifecycle = types.SimpleNamespace(ensure_loaded=lambda *args: None,
            count_serialized_prompt=lambda text: types.SimpleNamespace(count=100, estimated=False),
            effective_n_ctx=8192, complete_chat=lambda *args, **kwargs: 'ENHANCED\t1\t{"beats":[',
            last_completion_metrics=dict(finish_reason="length"), clear=lambda: None)
        backend.complete("neutral system", "neutral request")
        self.assertEqual(backend.trace[-1]["performance"]["finish_reason"], "length")
        self.assertEqual(backend.trace[-1]["performance"]["max_tokens"], 256)

    def test_incomplete_whole_response_splits_without_repeating_the_same_large_request(self):
        _, scenario, expected, parts = neutral_case()
        original = copy.deepcopy(scenario)
        backend = NeutralBackend([unfinished(expected), *parts], reasons=["length"])
        events = []
        with progress_scope(lambda event, **data: events.append((event, data))):
            result = StoryEnhancer(StageRunner(backend)).enhance(scenario)
        self.assertEqual(result, expected)
        self.assertEqual(scenario, original)
        self.assertEqual(len(backend.requests), 3)
        self.assertNotIn("enhance_beat_id", backend.requests[0]["payload"])
        for request, source in zip(backend.requests[1:], scenario["beats"]):
            self.assertEqual(request["payload"]["scenario"], scenario)
            self.assertEqual(request["payload"]["enhance_beat_id"], source["id"])
        self.assertTrue(any(event == "restart" for event, _ in events))
        self.assertIn("出力を分割", backend.systems[1])

    def test_eof_without_finish_metadata_also_recovers_by_scope(self):
        _, scenario, expected, parts = neutral_case()
        backend = NeutralBackend([unfinished(expected), *parts])
        self.assertEqual(StoryEnhancer(StageRunner(backend)).enhance(scenario), expected)
        self.assertEqual(len(backend.requests), 3)

    def test_partial_success_resumes_at_failed_beat_after_new_runner(self):
        _, scenario, expected, parts = neutral_case()
        with tempfile.TemporaryDirectory(prefix="neutral_enhance_resume_", dir=Path(__file__).resolve().parent) as directory:
            first = NeutralBackend([unfinished(expected), parts[0], RuntimeError("neutral interruption")])
            with self.assertRaisesRegex(RuntimeError, "neutral interruption"):
                StoryEnhancer(StageRunner(first, directory)).enhance(scenario)
            resumed = NeutralBackend([parts[1]])
            self.assertEqual(StoryEnhancer(StageRunner(resumed, directory)).enhance(scenario), expected)
            self.assertEqual(len(resumed.requests), 1)
            self.assertEqual(resumed.requests[0]["payload"]["enhance_beat_id"], "departure")

    def test_failure_before_any_completed_scope_does_not_save_a_success_hint(self):
        _, scenario, expected, _ = neutral_case()
        with tempfile.TemporaryDirectory(prefix="neutral_enhance_failure_", dir=Path(__file__).resolve().parent) as directory:
            failed = NeutralBackend([unfinished(expected), RuntimeError("neutral interruption")])
            with self.assertRaises(RuntimeError):
                StoryEnhancer(StageRunner(failed, directory)).enhance(scenario)
            self.assertEqual(list(Path(directory).glob("[0-9a-f][0-9a-f]/*.json")), [])
            resumed = NeutralBackend([expected])
            self.assertEqual(StoryEnhancer(StageRunner(resumed, directory)).enhance(scenario), expected)
            self.assertNotIn("enhance_beat_id", resumed.requests[0]["payload"])

    def test_scope_is_enforced_in_native_schema_and_semantic_checks(self):
        _, scenario, expected, parts = neutral_case()
        payload = dict(scenario=scenario, image_facts=[], enhance_beat_id="departure")
        schema = generation_schema("enhance", payload)
        validator = Draft202012Validator(schema)
        self.assertTrue(validator.is_valid(parts[1]))
        self.assertFalse(validator.is_valid(expected))
        self.assertFalse(validator.is_valid(parts[0]))
        self.assertIn("departure", record_grammar("ENHANCED", payload_schema=schema))
        backend = NeutralBackend([unfinished(expected), parts[1], parts[1], parts[1]])
        with self.assertRaisesRegex(VCError, "enhanced_beats"):
            StoryEnhancer(StageRunner(backend)).enhance(scenario)

    def test_semantic_author_constraint_error_is_not_bypassed_by_splitting(self):
        _, scenario, expected, _ = neutral_case()
        wrong = copy.deepcopy(expected)
        wrong["constraints"][0]["text"] = "Changed authored lighting."
        backend = NeutralBackend([wrong] * 3)
        with self.assertRaisesRegex(VCError, "author_constraint"):
            StoryEnhancer(StageRunner(backend)).enhance(scenario)
        self.assertEqual(len(backend.requests), 3)
        self.assertTrue(all("enhance_beat_id" not in request["payload"] for request in backend.requests))

    def test_scoped_requirement_collisions_are_resolved_only_when_assembled(self):
        _, scenario, expected, parts = neutral_case()
        for part in parts:
            part["beats"][0]["required_events"][0]["id"] = "req_shared"
        original = copy.deepcopy(parts)
        backend = NeutralBackend([unfinished(expected), *parts])
        result = StoryEnhancer(StageRunner(backend)).enhance(scenario)
        events = [beat["required_events"][0] for beat in result["beats"]]
        self.assertEqual(len({event["id"] for event in events}), 2)
        for event, part in zip(events, original):
            source_event = part["beats"][0]["required_events"][0]
            self.assertEqual(dict(event, id=source_event["id"]), source_event)
        self.assertEqual(parts, original)

    def test_single_beat_length_stop_fails_early_with_specific_japanese_guidance(self):
        text, scenario, expected, parts = neutral_case()
        scenario["beats"] = scenario["beats"][:1]
        backend = NeutralBackend([unfinished(parts[0])], reasons=["length"])
        with self.assertRaises(VCError) as caught:
            StoryEnhancer(StageRunner(backend)).enhance(scenario)
        error = contextual_error(caught.exception, text)
        self.assertEqual(error.diagnostic.code, "GENERATION_TRUNCATED")
        for detail in ("4096", "JSON", "出力トークン上限", "入力EMDの行番号ではありません"):
            self.assertIn(detail, str(error))
        self.assertEqual(len(backend.requests), 1)

    def test_complete_valid_json_is_accepted_even_if_stop_reason_is_length(self):
        _, scenario, expected, _ = neutral_case()
        backend = NeutralBackend([expected], reasons=["length"])
        self.assertEqual(StoryEnhancer(StageRunner(backend)).enhance(scenario), expected)
        self.assertEqual(len(backend.requests), 1)

    def test_middle_syntax_error_remains_distinct_and_can_use_bounded_scoped_fallback(self):
        _, scenario, expected, parts = neutral_case()
        invalid = 'ENHANCED\t1\t{"intent":"neutral" "beats":[]}'
        backend = NeutralBackend([invalid] * 3 + parts)
        self.assertEqual(StoryEnhancer(StageRunner(backend)).enhance(scenario), expected)
        self.assertEqual(backend.requests[1]["errors"][0]["code"], "json")
        self.assertIn("構文", backend.requests[1]["errors"][0]["message"])
        self.assertEqual(len(backend.requests), 5)

    def test_json_diagnostic_positions_describe_model_response_not_input_emd(self):
        for source, metadata, code, incomplete in (
            ('{"intent":"neutral" "beats":[]}', {}, "JSON", False),
            ('{"intent":}', {}, "JSON", False),
            ('{"intent":1]', {}, "JSON", False),
            ('{"intent":"neutral"', {}, "JSON_INCOMPLETE", True),
            ('{"intent":"neutral"', dict(finish_reason="length", max_tokens=4096), "GENERATION_TRUNCATED", True),
        ):
            with self.subTest(code=code):
                try:
                    json.loads(source)
                except json.JSONDecodeError as error:
                    diagnostic, probable = json_failure(error, metadata)
                    self.assertEqual((diagnostic.code, probable), (code, incomplete))
                    self.assertIn(f"文字位置{error.pos}", diagnostic.message)
                    self.assertIn("入力EMDの行番号ではありません", diagnostic.message)

    def test_invalid_author_source_stops_before_model_or_scoped_recovery(self):
        _, scenario, _, _ = neutral_case()
        scenario["beats"][0]["actor_ids"] = ["not_defined"]
        backend = NeutralBackend([])
        with self.assertRaisesRegex(VCError, "UNKNOWN_ACTOR"):
            StoryEnhancer(StageRunner(backend)).enhance(scenario)
        self.assertEqual(backend.requests, [])

    def test_runtime_and_documented_schemas_stay_identical(self):
        root = Path(__file__).resolve().parents[1]
        runtime = json.loads((root / "core/artifacts/schemas/vc-artifacts.schema.json").read_text(encoding="utf-8"))
        documented = json.loads((root / "docs/spec/schemas/vc-artifacts.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(runtime, documented)
        _, scenario, _, _ = neutral_case()
        request = dict(schema="VCD_LLM_REQUEST_V1", request_id="neutral", stage="enhance",
            targets=[dict(slot=1, target_id="arrival")], retry="initial", errors=[],
            payload=dict(scenario=scenario, image_facts=[], enhance_beat_id="arrival"))
        validate(request, "enhance_request")


if __name__ == "__main__":
    unittest.main()
