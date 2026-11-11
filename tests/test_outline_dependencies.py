"""Neutral in-memory Scene ID collision and dependency recovery regressions."""
import copy
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from test_outline_required import neutral_case, NeutralBackend
from vc_director.core.emd.diagnostics import contextual_error
from vc_director.core.errors import VCError
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.planner.outline_dependencies import validate_outline_dependencies
from vc_director.core.protocols import StageRunner
from vc_director.core.protocols.dependency_repair import repair_outline_dependencies
from vc_director.core.protocols.grammar import record_grammar
from vc_director.core.protocols.llm import generation_schema
from vc_director.core.utilities import canonical


def dependency_case():
    payload, result = neutral_case(count=2)
    beat = payload["scenario"]["beats"][0]
    setup = dict(copy.deepcopy(beat), id="setup", transition="auto")
    payload["scenario"]["beats"].insert(0, setup)
    payload["enhanced"]["beats"].insert(0, dict(id="setup", intent="Wait beside the station.", required_events=[]))
    beat["transition"] = "continue"
    scene = result["scenes"][0]
    earlier = dict(copy.deepcopy(scene), id="scene_01", beat_id="setup", end_ms=5000, required_event_ids=[])
    payload["accepted_outlines"] = [earlier]
    scene.update(id="scene_01", start_ms=5000, transition="continue", depends_on=["scene_01"])
    return payload, result


def dependency_patch(index, *choices):
    return dict(dependencies=[dict(scene_index=index, choices=[dict(scene_id=id, include=include)
                                                              for id, include in choices])])


def check_graph(result, accepted=()):
    prior = list(accepted)
    for scene in result["scenes"]:
        validate_outline_dependencies(scene, prior)
        prior.append(scene)


class OutlineDependencyTests(unittest.TestCase):
    def test_duplicate_scene_counter_across_beats_is_fixed_without_an_extra_llm_call(self):
        payload, result = dependency_case()
        accepted = copy.deepcopy(payload["accepted_outlines"])
        original = copy.deepcopy((payload, result))
        backend = NeutralBackend([dict(scenes=accepted), result])
        runner = StageRunner(backend)
        planned = StoryTimelinePlanner(runner)._plan_outlines(payload["scenario"], payload["enhanced"])
        expected = dict(result["scenes"][0], id="scene_01_2")
        self.assertEqual(planned, accepted + [expected])
        self.assertEqual(len(backend.requests), 2)
        self.assertEqual((payload, result), original)

    def test_generated_ids_and_bound_references_change_together_without_touching_other_fields(self):
        payload, result = dependency_case()
        first = result["scenes"][0]
        first["id"] = "scene_new"
        first["end_ms"] = 7500
        second = dict(copy.deepcopy(first), id="scene_01", start_ms=7500, end_ms=10000,
                      depends_on=["scene_new"])
        third = dict(copy.deepcopy(second), id="scene_last", depends_on=["scene_01"])
        # 'scene_01' names both a prior accepted Scene and the second draft.
        # Continue fixes the immediate predecessor, but the older alias must not
        # be silently resolved to the first/nearest matching name.
        result["scenes"] = [first, second, third]
        trace = {}
        backend = NeutralBackend([dependency_patch(2, ("scene_01", False))])
        original = copy.deepcopy((payload, result))
        repaired = repair_outline_dependencies(backend, result, payload, trace)
        self.assertEqual([s["id"] for s in repaired["scenes"]], ["scene_new", "scene_01_2", "scene_last"])
        self.assertEqual(repaired["scenes"][2]["depends_on"], ["scene_01_2"])
        for before, after in zip(result["scenes"], repaired["scenes"]):
            self.assertEqual({k:v for k,v in before.items() if k not in {"id", "depends_on"}},
                             {k:v for k,v in after.items() if k not in {"id", "depends_on"}})
        self.assertEqual((payload, result), original)
        check_graph(repaired, payload["accepted_outlines"])

    def test_first_scene_self_or_future_reference_has_only_the_empty_predecessor_set(self):
        for reference in ("scene_arrival", "scene_future", "arrival"):
            payload, result = neutral_case(count=2)
            result["scenes"][0]["depends_on"] = [reference]
            backend = NeutralBackend([])
            with self.subTest(reference=reference):
                repaired = repair_outline_dependencies(backend, result, payload, {})
                self.assertEqual(repaired["scenes"][0]["depends_on"], [])
                self.assertEqual(len(backend.requests), 0)

    def test_repeated_local_scene_ids_rewrite_later_unambiguous_edges(self):
        payload, result = neutral_case(count=2)
        first = result["scenes"][0]
        first.update(id="scene_01", end_ms=5000)
        result["scenes"].append(dict(copy.deepcopy(first), start_ms=5000, end_ms=10000,
                                    transition="continue", depends_on=["scene_01"]))
        backend = NeutralBackend([])
        repaired = repair_outline_dependencies(backend, result, payload, {})
        self.assertEqual(repaired["scenes"][1]["id"], "scene_01_2")
        self.assertEqual(repaired["scenes"][1]["depends_on"], ["scene_01"])
        self.assertEqual(backend.requests, [])

    def test_allocator_does_not_take_another_drafts_original_id(self):
        payload, result = dependency_case()
        result["scenes"].append(dict(copy.deepcopy(result["scenes"][0]), id="scene_01_2", depends_on=[]))
        backend = NeutralBackend([])
        repaired = repair_outline_dependencies(backend, result, payload, {})
        self.assertEqual([s["id"] for s in repaired["scenes"]], ["scene_01_3", "scene_01_2"])
        self.assertEqual(repaired["scenes"][1]["depends_on"], ["scene_01_3"])

    def test_unknown_cut_dependency_is_selected_from_prior_scenes_not_nearest_name(self):
        payload, result = dependency_case()
        earlier = dict(copy.deepcopy(payload["accepted_outlines"][0]), id="scene_02", start_ms=2500)
        payload["accepted_outlines"][0]["end_ms"] = 2500
        payload["accepted_outlines"].append(earlier)
        result["scenes"][0].update(id="scene_arrival", transition="cut", depends_on=["old_missing"])
        response = dependency_patch(0, ("scene_01", True), ("scene_02", False))
        backend = NeutralBackend([response])
        repaired = repair_outline_dependencies(backend, result, payload, {})
        self.assertEqual(repaired["scenes"][0]["depends_on"], ["scene_01"])
        self.assertEqual(len(backend.requests), 1)
        from llama_cpp import LlamaGrammar
        LlamaGrammar.from_string(record_grammar("SCENES", payload_schema=backend.schemas[0]), verbose=False)

    def test_optional_invalid_cut_reference_can_be_removed_without_removing_valid_edges(self):
        payload, result = dependency_case()
        payload["accepted_outlines"].append(dict(copy.deepcopy(payload["accepted_outlines"][0]), id="scene_02"))
        result["scenes"][0].update(id="scene_arrival", transition="cut", depends_on=["scene_01", "unknown"])
        repaired = repair_outline_dependencies(NeutralBackend([dependency_patch(0, ("scene_02", False))]),
                                                result, payload, {})
        self.assertEqual(repaired["scenes"][0]["depends_on"], ["scene_01"])

    def test_ambiguous_duplicate_alias_requires_exactly_one_binding(self):
        payload, result = neutral_case(count=2)
        first = result["scenes"][0]
        first.update(id="scene_01", end_ms=3000)
        second = dict(copy.deepcopy(first), start_ms=3000, end_ms=6000)
        third = dict(copy.deepcopy(first), id="scene_tail", start_ms=6000, end_ms=10000, depends_on=["scene_01"])
        result["scenes"] = [first, second, third]
        invalid = dependency_patch(2, ("scene_01", True), ("scene_01_2", True))
        valid = dependency_patch(2, ("scene_01", False), ("scene_01_2", True))
        backend = NeutralBackend([invalid, valid])
        repaired = repair_outline_dependencies(backend, result, payload, {})
        self.assertEqual(repaired["scenes"][2]["depends_on"], ["scene_01_2"])
        self.assertIn("一つだけ", backend.requests[1]["repair_error"])

    def test_wrong_duplicate_missing_candidate_or_scene_rows_are_rejected(self):
        payload, result = dependency_case()
        result["scenes"][0].update(id="scene_arrival", transition="cut", depends_on=["unknown"])
        responses = [dependency_patch(0, ("scene_future", True)), dependency_patch(9, ("scene_01", True)),
                     dict(dependencies=[]), dependency_patch(0, ("scene_01", True), ("scene_01", False)),
                     dict(dependency_patch(0, ("scene_01", True)), scenes=[])]
        for invalid in responses:
            with self.subTest(invalid=invalid):
                backend = NeutralBackend([invalid, invalid])
                with self.assertRaisesRegex(VCError, "OUTLINE_DEPENDENCY_REPAIR"):
                    repair_outline_dependencies(backend, result, payload, {})
                self.assertEqual(len(backend.requests), 2)

    def test_valid_prior_dependencies_are_preserved_and_duplicates_collapsed(self):
        payload, result = dependency_case()
        result["scenes"][0].update(id="scene_arrival", depends_on=["scene_01", "scene_01"])
        repaired = repair_outline_dependencies(NeutralBackend([]), result, payload, {})
        self.assertEqual(repaired["scenes"][0]["depends_on"], ["scene_01"])

    def test_initial_native_schema_does_not_allow_unknown_or_future_first_dependencies(self):
        payload, result = dependency_case()
        payload["scenario"]["beats"][-1]["transition"] = "auto"
        schema = generation_schema("outline", payload)
        dependency_schema = schema["properties"]["scenes"]["prefixItems"][0]["properties"]["depends_on"]
        validator = Draft202012Validator(dependency_schema)
        self.assertTrue(validator.is_valid(["scene_01"]))
        self.assertTrue(validator.is_valid([]))
        for value in (["unknown"], ["scene_future"], ["scene_01", "scene_01"]):
            self.assertFalse(validator.is_valid(value))

    def test_final_isolated_attempt_can_fix_counter_restart_without_full_regeneration(self):
        payload, result = dependency_case()
        raw = 'SCENES\t1\t{"scenes":['
        backend = NeutralBackend([dict(scenes=payload["accepted_outlines"]), raw, raw, result])
        planned = StoryTimelinePlanner(StageRunner(backend))._plan_outlines(payload["scenario"], payload["enhanced"])
        self.assertEqual(planned[-1]["id"], "scene_01_2")
        self.assertEqual(backend.requests[-1]["retry"], "isolated")
        self.assertEqual(len(backend.requests), 4)

    def test_only_fully_valid_repair_is_cached_and_revalidated(self):
        payload, result = dependency_case()
        backend = NeutralBackend([result])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            runner = StageRunner(backend, directory)
            check = lambda value: check_graph(value, payload["accepted_outlines"])
            accepted = runner.run("outline", "arrival", payload, check)
            again = runner.run("outline", "arrival", payload, check)
            self.assertEqual(accepted, again)
            self.assertEqual(len(backend.requests), 1)
            self.assertEqual(runner.trace[-1]["cache"], "hit")

    def test_japanese_error_reports_duplicate_id_and_invalid_reference(self):
        payload, result = dependency_case()
        scene = dict(result["scenes"][0], depends_on=["future_scene"])
        with self.assertRaises(VCError) as caught:
            validate_outline_dependencies(scene, payload["accepted_outlines"])
        d = caught.exception.diagnostic
        formatted = contextual_error(VCError("LLM_RETRIES_EXHAUSTED", "arrival", canonical([
            dict(code=d.code.lower(), path=d.path, message=d.message)])), "# 形式\n", {})
        for text in ("scene_01", "future_scene", "確定済み", "先行Scene"):
            self.assertIn(text, str(formatted))

    def test_deterministic_collision_repair_remains_available_after_unrelated_retry(self):
        payload, result = dependency_case()
        backend = NeutralBackend([result, result])
        checked = []

        def check(value):
            check_graph(value, payload["accepted_outlines"])
            checked.append(value["scenes"][0]["id"])
            if len(checked) == 1:
                raise VCError("NEUTRAL_CHECK", "arrival", "中立テストの再試行。")

        fixed = StageRunner(backend).run("outline", "arrival", payload, check)
        self.assertEqual(fixed["scenes"][0]["id"], "scene_01_2")
        self.assertEqual(checked, ["scene_01_2", "scene_01_2"])
        self.assertEqual(len(backend.requests), 2)

    def test_failed_graph_validation_does_not_cache_an_id_repair(self):
        payload, result = dependency_case()
        backend = NeutralBackend([result, result, result])

        def check(value):
            check_graph(value, payload["accepted_outlines"])
            raise VCError("NEUTRAL_CHECK", "arrival", "中立テストの全体検証エラー。")

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            runner = StageRunner(backend, directory)
            with self.assertRaisesRegex(VCError, "LLM_RETRIES_EXHAUSTED"):
                runner.run("outline", "arrival", payload, check)
            self.assertEqual(len(backend.requests), 3)
            self.assertIsNone(runner.cache.get(runner.trace[-1]["key"]))


if __name__ == "__main__":
    unittest.main()
