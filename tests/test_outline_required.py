"""Neutral, in-memory regression tests; no scenario/output/model files are read."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from tools._bootstrap import load_project

load_project()

from vc_director.core.artifacts.coverage import validate_required_coverage
from vc_director.core.emd.parser import parse
from vc_director.core.emd.diagnostics import contextual_error
from vc_director.core.errors import VCError
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.protocols import StageRunner
from vc_director.core.protocols.llm import generation_schema
from vc_director.core.protocols.outline_repair import assignment_schema, repair_required_assignments
from vc_director.core.protocols.grammar import record_grammar
from vc_director.core.utilities import canonical


NEUTRAL_EMD = """# 形式
* schema: VCD_SCENARIO_EMD_V1
# 作品
* id: neutral_station_test
* 尺: 00:10.000
# 人物 @traveler
* 名前: 旅人
* 特徴: 青い上着を着た成人。
# 背景 station
* 情景: 明るい駅前。
# シナリオ
## 展開 arrival
* 出演: @traveler
* 背景: station
@travelerが駅前で手帳を開き、地図を確認してから閉じる。
"""


def neutral_case(count=8):
    scenario = parse(NEUTRAL_EMD)
    beat = scenario["beats"][0]
    actions = ["駅前に着く", "立ち止まる", "時計を見る", "手帳を取り出す",
               "手帳を開く", "地図を確認する", "手帳を閉じる", "改札へ進む"]
    requirements = [dict(id=f"req_arrival_{i + 1}", beat_id=beat["id"],
                         text="@travelerが" + actions[i] + "。", source=copy.deepcopy(beat["source"]))
                    for i in range(count)]
    enhanced = dict(schema="VCD_ENHANCED_STORY_V1", project_id=scenario["project_id"],
                    scenario_sha256=scenario["source_sha256"], intent="駅で地図を確認する。",
                    beats=[dict(id=beat["id"], intent="移動の準備。", required_events=requirements)], constraints=[])
    outline = dict(id="scene_arrival", beat_id=beat["id"], start_ms=0, end_ms=10000,
                   actor_ids=["traveler"], background_id="station", transition="cut", depends_on=[],
                   locked=[], required_event_ids=[e["id"] for e in requirements], initial_state=[], terminal_state=[])
    return dict(scenario=scenario, enhanced=enhanced, accepted_outlines=[]), dict(scenes=[outline])


def split_scenes(result):
    first = copy.deepcopy(result["scenes"][0])
    second = dict(copy.deepcopy(first), id="scene_arrival_2", start_ms=5000,
                  transition="continue", depends_on=[first["id"]])
    first["end_ms"] = 5000
    first["required_event_ids"], second["required_event_ids"] = first["required_event_ids"][:4], second["required_event_ids"][4:]
    return dict(scenes=[first, second])


def assignment_patch(result):
    return dict(assignments=[dict(required_event_id=identifier, scene_id=scene["id"])
                             for scene in result["scenes"] for identifier in scene["required_event_ids"]])


class NeutralBackend:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests, self.schemas = [], []

    def identity(self):
        return dict(backend="neutral_outline_test")

    def complete_record(self, system, request, record_type, *, payload_schema):
        self.requests.append(json.loads(request))
        self.schemas.append(copy.deepcopy(payload_schema))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else record_type + "\t1\t" + canonical(response)


class OutlineRequiredTests(unittest.TestCase):
    def test_single_scene_keeps_all_eight_events_without_extra_generation(self):
        payload, expected = neutral_case()
        partial = copy.deepcopy(expected)
        partial["scenes"][0]["required_event_ids"] = partial["scenes"][0]["required_event_ids"][:2]
        original = copy.deepcopy((payload, partial))
        backend = NeutralBackend([partial])
        with tempfile.TemporaryDirectory(prefix="vcd_neutral_required_", dir=Path(__file__).resolve().parent) as directory:
            runner = StageRunner(backend, directory)
            planner = StoryTimelinePlanner(runner)
            self.assertEqual(planner._plan_outlines(payload["scenario"], payload["enhanced"]), expected["scenes"])
            # The corrected response is success-cached, after full validation.
            self.assertEqual(planner._plan_outlines(payload["scenario"], payload["enhanced"]), expected["scenes"])
            self.assertEqual(len(backend.requests), 1)
            diagnostics = list((Path(directory) / "diagnostics").glob("*.json"))
            diagnostic = json.loads(diagnostics[0].read_text(encoding="utf-8"))
            repair = diagnostic["attempts"][0]["required_assignment_repair"]
            self.assertEqual((repair["method"], repair["status"]), ("single_scene", "accepted"))
        self.assertEqual((payload, partial), original)

    def test_multi_scene_repairs_only_references_and_locks_valid_assignments(self):
        payload, expected = neutral_case()
        expected = split_scenes(expected)
        partial = copy.deepcopy(expected)
        partial["scenes"][0]["required_event_ids"] = ["req_arrival_1", "req_arrival_2", "foreign_event"]
        partial["scenes"][1]["required_event_ids"] = ["req_arrival_2", "req_arrival_5"]
        # The duplicate may only go to one of its existing owners.
        original = copy.deepcopy((payload, partial))
        backend = NeutralBackend([partial, assignment_patch(expected)])
        result = StoryTimelinePlanner(StageRunner(backend))._plan_outlines(payload["scenario"], payload["enhanced"])
        self.assertEqual(result, expected["scenes"])
        self.assertEqual(len(backend.requests), 2)
        self.assertEqual(backend.requests[1]["task"], "required_event_assignment")
        rows = backend.schemas[1]["properties"]["assignments"]["prefixItems"]
        self.assertEqual(rows[0]["properties"]["scene_id"], {"const": "scene_arrival"})
        self.assertEqual(rows[4]["properties"]["scene_id"], {"const": "scene_arrival_2"})
        self.assertEqual((payload, partial), original)
        for before, after in zip(partial["scenes"], result):
            self.assertEqual(dict(after, required_event_ids=before["required_event_ids"]), before)

    def test_partial_repair_retries_protocol_and_schema_with_specific_feedback(self):
        payload, expected = neutral_case()
        expected = split_scenes(expected)
        partial = copy.deepcopy(expected)
        partial["scenes"][1]["required_event_ids"] = []
        error = VCError("OUTLINE_REQUIRED", "arrival", "未割り当て: req_arrival_5").diagnostic
        for invalid in ("not a record", dict(assignments=[])):
            with self.subTest(invalid=invalid):
                backend = NeutralBackend([invalid, assignment_patch(expected)])
                trace = {}
                self.assertEqual(repair_required_assignments(backend, partial, payload, "arrival", error, trace), expected)
                self.assertIn("repair_error", backend.requests[1])
                self.assertEqual(trace["attempts"][0]["status"], "invalid")

    def test_plain_transport_gets_exact_assignment_contract(self):
        payload, expected = neutral_case()
        expected = split_scenes(expected)
        partial = copy.deepcopy(expected)
        partial["scenes"][1]["required_event_ids"] = []

        class PlainBackend:
            def complete(self, system, request):
                self.request = json.loads(request)
                return "SCENES\t1\t" + canonical(assignment_patch(expected))

        backend = PlainBackend()
        error = VCError("OUTLINE_REQUIRED", "arrival", "必須イベントが未割り当てです。").diagnostic
        self.assertEqual(repair_required_assignments(backend, partial, payload, "arrival", error, {}), expected)
        self.assertTrue(Draft202012Validator(backend.request["output_schema"]).is_valid(assignment_patch(expected)))

    def test_backend_failure_is_not_converted_to_an_allocation_retry(self):
        payload, expected = neutral_case()
        partial = split_scenes(expected)
        partial["scenes"][1]["required_event_ids"] = []
        backend = NeutralBackend([partial, RuntimeError("neutral backend unavailable")])
        with self.assertRaisesRegex(RuntimeError, "neutral backend unavailable"):
            StoryTimelinePlanner(StageRunner(backend))._plan_outlines(payload["scenario"], payload["enhanced"])
        self.assertEqual(len(backend.requests), 2)

    def test_failed_partial_repair_has_bounded_calls_and_no_silent_event_drop(self):
        payload, expected = neutral_case()
        partial = split_scenes(expected)
        partial["scenes"][1]["required_event_ids"] = []
        backend = NeutralBackend([partial, dict(assignments=[]), dict(assignments=[]), partial, partial])
        with self.assertRaises(VCError) as caught:
            StoryTimelinePlanner(StageRunner(backend))._plan_outlines(payload["scenario"], payload["enhanced"])
        self.assertEqual(len(backend.requests), 5)  # three full attempts plus two allocation attempts
        self.assertEqual(sum(r.get("task") == "required_event_assignment" for r in backend.requests), 2)
        error = contextual_error(caught.exception, NEUTRAL_EMD)
        self.assertIn("未割り当て", str(error))
        self.assertIn("req_arrival_5", str(error))
        self.assertIn("## 展開 arrival", str(error))
        self.assertEqual(error.diagnostic.line, NEUTRAL_EMD.splitlines().index("## 展開 arrival") + 1)

    def test_other_errors_still_fail_and_never_trigger_assignment_calls(self):
        payload, expected = neutral_case()
        for key, value in (("start_ms", 1), ("end_ms", 9000), ("actor_ids", [])):
            with self.subTest(key=key):
                partial = copy.deepcopy(expected)
                partial["scenes"][0][key] = value
                backend = NeutralBackend([partial] * 3)
                with self.assertRaises(VCError):
                    StoryTimelinePlanner(StageRunner(backend))._plan_outlines(payload["scenario"], payload["enhanced"])
                self.assertEqual(len(backend.requests), 3)
                self.assertTrue(all("task" not in r for r in backend.requests))

    def test_repaired_allocation_does_not_hide_wrong_total(self):
        payload, expected = neutral_case()
        partial = copy.deepcopy(expected)
        partial["scenes"][0].update(end_ms=9000, required_event_ids=[])
        backend = NeutralBackend([partial] * 3)
        with self.assertRaisesRegex(VCError, "outline_duration"):
            StoryTimelinePlanner(StageRunner(backend))._plan_outlines(payload["scenario"], payload["enhanced"])
        self.assertEqual(len(backend.requests), 3)

    def test_valid_success_cache_is_reused_without_new_inference(self):
        payload, expected = neutral_case()
        with tempfile.TemporaryDirectory(prefix="vcd_neutral_cache_", dir=Path(__file__).resolve().parent) as directory:
            first = NeutralBackend([expected])
            StoryTimelinePlanner(StageRunner(first, directory))._plan_outlines(payload["scenario"], payload["enhanced"])
            second = NeutralBackend([])
            result = StoryTimelinePlanner(StageRunner(second, directory))._plan_outlines(payload["scenario"], payload["enhanced"])
            self.assertEqual(result, expected["scenes"])
            self.assertEqual(second.requests, [])

    def test_assignment_schema_rejects_missing_duplicate_unknown_and_changed_owners(self):
        _, expected = neutral_case()
        expected = split_scenes(expected)
        partial = copy.deepcopy(expected)
        partial["scenes"][1]["required_event_ids"] = []
        ids = [identifier for scene in expected["scenes"] for identifier in scene["required_event_ids"]]
        validator = Draft202012Validator(assignment_schema(partial["scenes"], ids))
        valid = assignment_patch(expected)
        self.assertTrue(validator.is_valid(valid))
        for mode in ("missing", "extra", "duplicate", "foreign_event", "foreign_scene", "move_fixed", "rewrite"):
            invalid = copy.deepcopy(valid)
            rows = invalid["assignments"]
            if mode == "missing":
                rows.pop()
            elif mode == "extra":
                rows.append(copy.deepcopy(rows[0]))
            elif mode == "duplicate":
                rows[1]["required_event_id"] = rows[0]["required_event_id"]
            elif mode == "foreign_event":
                rows[0]["required_event_id"] = "unregistered"
            elif mode == "foreign_scene":
                rows[-1]["scene_id"] = "other_scene"
            elif mode == "move_fixed":
                rows[0]["scene_id"] = "scene_arrival_2"
            else:
                rows[0]["text"] = "改変"
            with self.subTest(mode=mode):
                self.assertFalse(validator.is_valid(invalid))

    def test_schema_limits_known_ids_and_fixes_one_scene_budget(self):
        payload, expected = neutral_case()
        narrowed = generation_schema("outline", payload)
        validator = Draft202012Validator(narrowed)
        self.assertTrue(validator.is_valid(expected))
        invalid = copy.deepcopy(expected)
        invalid["scenes"][0]["required_event_ids"] = ["unregistered"]
        self.assertFalse(validator.is_valid(invalid))
        payload["beat_windows"] = [dict(beat_id="arrival", start_ms=0, end_ms=10000)]
        bounded = generation_schema("outline", payload)
        fixed = bounded["properties"]["scenes"]["prefixItems"][0]["properties"]["required_event_ids"]
        self.assertEqual(fixed, {"const": expected["scenes"][0]["required_event_ids"]})

    def test_empty_requirements_do_not_invent_events_or_empty_enum(self):
        payload, expected = neutral_case(0)
        narrowed = generation_schema("outline", payload)
        self.assertEqual(narrowed["$defs"]["scene_outline"]["properties"]["required_event_ids"], {"const": []})
        backend = NeutralBackend([expected])
        self.assertEqual(StoryTimelinePlanner(StageRunner(backend))._plan_outlines(payload["scenario"], payload["enhanced"]), expected["scenes"])
        self.assertEqual(len(backend.requests), 1)

    def test_japanese_error_reports_missing_duplicate_and_unknown_ids(self):
        with self.assertRaises(VCError) as caught:
            validate_required_coverage([dict(required_event_ids=["req_a", "req_a", "unknown"])],
                                       ["req_a", "req_b"], "arrival")
        for text in ("未割り当て: req_b", "重複: req_a（2回）", "未定義ID: unknown", "必要2件／出力3件"):
            self.assertIn(text, str(caught.exception))
        with self.assertRaisesRegex(VCError, "DUPLICATE_ID"):
            validate_required_coverage([], ["req_a", "req_a"], "arrival")

    @unittest.skipUnless(importlib.util.find_spec("llama_cpp"), "grammar converter unavailable")
    def test_neutral_schemas_convert_to_native_grammar_without_loading_models(self):
        payload, expected = neutral_case()
        split = split_scenes(expected)
        ids = [identifier for scene in split["scenes"] for identifier in scene["required_event_ids"]]
        for output_schema in (generation_schema("outline", payload), assignment_schema(split["scenes"], ids)):
            grammar = record_grammar("SCENES", payload_schema=output_schema)
            self.assertIn("root ::=", grammar)
            self.assertIn("req_arrival_8", grammar)


if __name__ == "__main__":
    unittest.main()
