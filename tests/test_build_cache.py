"""Neutral Build resumption tests. Every artifact is created in a fresh test dir."""
import copy
import json
import math
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from test_translation_alias_repair import NEUTRAL_SCENE, NeutralBackend as TranslationBackend, record
from vc_director.core.artifacts.build_cache import BuildCheckpoints, pipeline_signature
from vc_director.core.emd import parse
from vc_director.core.errors import VCError
from vc_director.core.inference.cache_policy import PipelineCache, cache_scope
from vc_director.core.utilities import digest, canonical
from vc_director.nodes.node_story_builder.node import VCDirectorBuild


SCENARIO = """# 形式
* schema: VCD_SCENARIO_EMD_V1
# 作品
* id: neutral_alias_compile
* 尺: 00:05.000
# 人物 @traveler
* 名前: Alex
* 特徴: 青い上着の成人。
# 背景 station
* 情景: A bright station.
# シナリオ
## 展開 arrival
* 出演: @traveler
* 背景: station
@travelerが門の前に立つ。
"""


def neutral_planning():
    scenario = parse(SCENARIO)
    source = scenario["beats"][0]["source"]
    enhanced = dict(schema="VCD_ENHANCED_STORY_V1", project_id=scenario["project_id"],
        scenario_sha256=scenario["source_sha256"], intent="At a station.", constraints=[],
        beats=[dict(id="arrival", intent="Stand by the gate.", required_events=[dict(
            id="req_arrival", beat_id="arrival", text="@traveler stands by the gate.", source=source)])])
    scene = parse(NEUTRAL_SCENE)["scenes"][0]
    scene["summary"] = "@travelerが門の前に立つ。"
    scene["required_event_ids"] = ["req_arrival"]
    keys = ("id", "beat_id", "start_ms", "end_ms", "actor_ids", "background_id", "transition",
            "depends_on", "locked", "required_event_ids", "initial_state", "terminal_state")
    return enhanced, dict(scenes=[{key: scene[key] for key in keys}]), scene


class PlanningBackend:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.closed = False

    def identity(self):
        return dict(model="in-memory-planning-model")

    def complete_record(self, system, request, record_type, *, payload_schema):
        self.requests.append(json.loads(request))
        result = next(self.responses)
        if isinstance(result, BaseException):
            raise result
        return record_type + "\t1\t" + canonical(result)

    def close(self):
        self.closed = True


class ControlledTranslation(TranslationBackend):
    def complete_chat(self, messages, config, **kwargs):
        result = super().complete_chat(messages, config, **kwargs)
        if isinstance(result, BaseException):
            raise result
        return result


class BuildCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="neutral_build_cache_", dir=Path(__file__).resolve().parent)
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.registry = dict(root=str(self.root), assets=[])

    def build(self, planning, translation=None, *, mode="再利用", scenario=SCENARIO, seed=1, facts="[]", settings=None, vision="disabled"):
        def backend(name, *, stage="planner", **kwargs):
            return translation if stage == "compiler" else planning
        with patch("vc_director.nodes.node_story_builder.node._backend", side_effect=backend), \
             patch("vc_director.nodes.node_story_builder.node.cache_root", side_effect=AssertionError("No temporary global cache")):
            return VCDirectorBuild().build("scenario", scenario, self.registry, str(self.root / "out"),
                                          facts, "neutral-model", seed, inference_settings=settings, cache_mode=mode,
                                          vision_model_name=vision)

    def test_detail_failure_resumes_after_cached_enhance_and_outline(self):
        enhanced, outline, scene = neutral_planning()
        first = PlanningBackend([enhanced, outline, RuntimeError("neutral detail interrupted")])
        with self.assertRaisesRegex(RuntimeError, "neutral detail interrupted"):
            self.build(first)
        self.assertEqual([r["stage"] for r in first.requests], ["enhance", "outline", "detail"])
        resumed = PlanningBackend([scene])
        translation = ControlledTranslation([record("An adult in a blue coat."), record("", " stands by the gate.")])
        _, revision, _ = self.build(resumed, translation)
        self.assertEqual([r["stage"] for r in resumed.requests], ["detail"])
        self.assertTrue(Path(revision).is_dir())
        self.assertTrue(first.closed and resumed.closed and translation.closed)

    def test_compile_failure_resumes_with_prior_translation_cached(self):
        enhanced, outline, scene = neutral_planning()
        planning = PlanningBackend([enhanced, outline, scene])
        first = ControlledTranslation([record("An adult in a blue coat."), RuntimeError("neutral translation interrupted")])
        with self.assertRaisesRegex(RuntimeError, "neutral translation interrupted"):
            self.build(planning, first)
        resumed = PlanningBackend([])
        translation = ControlledTranslation([record("", " stands by the gate.")])
        _, revision, _ = self.build(resumed, translation)
        self.assertEqual(resumed.requests, [])
        self.assertEqual(len(translation.requests), 1)
        metadata = json.loads((Path(revision) / "run_metadata.json").read_text(encoding="utf-8"))
        statuses = {row["stage"]: row["cache"] for row in metadata["run_details"]["build_cache"]["stages"]}
        self.assertEqual(statuses, dict(enhance="hit", scenes="hit", compile="generated"))
        # Simulate fresh node/backend instances after restart: no generation.
        full_hit, no_translation = PlanningBackend([]), ControlledTranslation([])
        _, next_revision, _ = self.build(full_hit, no_translation)
        self.assertEqual(full_hit.requests, [])
        self.assertEqual(no_translation.requests, [])
        metadata = json.loads((Path(next_revision) / "run_metadata.json").read_text(encoding="utf-8"))
        self.assertTrue(all(row["cache"] == "hit" for row in metadata["run_details"]["build_cache"]["stages"]))

    def test_second_scene_failure_reuses_first_scene_success(self):
        scenario = SCENARIO.replace("00:05.000", "00:10.000")
        enhanced, _, first = neutral_planning()
        enhanced["scenario_sha256"] = parse(scenario)["source_sha256"]
        enhanced["beats"][0]["required_events"].append(dict(
            enhanced["beats"][0]["required_events"][0], id="req_arrival_2"))
        second = copy.deepcopy(first)
        second.update(id="scene_arrival_2", start_ms=5000, end_ms=10000,
                      transition="continue", depends_on=[first["id"]], required_event_ids=["req_arrival_2"])
        second["shots"][0]["id"] = "station_shot_2"
        second["shots"][0]["events"][0]["id"] = "station_camera_2"
        keys = ("id", "beat_id", "start_ms", "end_ms", "actor_ids", "background_id", "transition",
                "depends_on", "locked", "required_event_ids", "initial_state", "terminal_state")
        outline = dict(scenes=[{key: scene[key] for key in keys} for scene in (first, second)])
        interrupted = PlanningBackend([enhanced, outline, first, RuntimeError("neutral second scene interrupted")])
        with self.assertRaisesRegex(RuntimeError, "neutral second scene interrupted"):
            self.build(interrupted, scenario=scenario)
        resumed = PlanningBackend([second])
        translator = ControlledTranslation([record("An adult in a blue coat."), record("", " stands by the gate.")])
        self.build(resumed, translator, scenario=scenario)
        self.assertEqual(len(resumed.requests), 1)
        self.assertEqual(resumed.requests[0]["targets"][0]["target_id"], "scene_arrival_2")

    def test_refresh_regenerates_all_stages_and_updates_reusable_cache(self):
        enhanced, outline, scene = neutral_planning()
        for mode in ("再利用", "再生成", "再生成"):
            planning = PlanningBackend([enhanced, outline, scene])
            translation = ControlledTranslation([record("An adult in a blue coat."), record("", " stands by the gate.")])
            self.build(planning, translation, mode=mode)
            self.assertEqual(len(planning.requests), 3)
            self.assertEqual(len(translation.requests), 2)
        self.build(PlanningBackend([]), ControlledTranslation([]))

    def test_disabled_does_not_read_or_write_any_pipeline_cache(self):
        enhanced, outline, scene = neutral_planning()
        for _ in range(2):
            planning = PlanningBackend([enhanced, outline, scene])
            translation = ControlledTranslation([record("An adult in a blue coat."), record("", " stands by the gate.")])
            self.build(planning, translation, mode="無効")
            self.assertEqual(len(planning.requests), 3)
        self.assertFalse((self.root / "out/neutral_alias_compile/.build_cache").exists())

    def test_disabling_existing_cache_leaves_it_unchanged(self):
        enhanced, outline, scene = neutral_planning()
        replies = [record("An adult in a blue coat."), record("", " stands by the gate.")]
        self.build(PlanningBackend([enhanced, outline, scene]), ControlledTranslation(replies))
        directory = self.root / "out/neutral_alias_compile/.build_cache"
        before = {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*.json")}
        planning = PlanningBackend([enhanced, outline, scene])
        self.build(planning, ControlledTranslation(replies), mode="無効")
        after = {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*.json")}
        self.assertEqual(before, after)
        self.assertEqual(len(planning.requests), 3)
        self.build(PlanningBackend([]), ControlledTranslation([]))

    def test_vision_checkpoint_and_cached_progress_skip_completed_phases(self):
        enhanced, outline, scene = neutral_planning()
        pair = types.SimpleNamespace(signature=lambda: dict(model="neutral-vision"))
        with patch("vc_director.core.vision.resolve_vision_model_pair", return_value=pair), \
             patch("vc_director.core.vision.image_facts.scenario_image_facts", return_value=[]) as vision:
            self.build(PlanningBackend([enhanced, outline, scene]),
                       ControlledTranslation([record("An adult in a blue coat."), record("", " stands by the gate.")]),
                       vision="neutral-vision")
            events = []
            with patch("vc_director.nodes.node_story_builder.node.create_build_progress",
                       return_value=lambda event, **data: events.append((event, data))):
                self.build(PlanningBackend([]), ControlledTranslation([]), vision="neutral-vision")
            vision.assert_called_once()
        cached = {data["stage"] for event, data in events
                  if event == "stage_done" and data.get("label") == "完了済みキャッシュを使用"}
        self.assertEqual(cached, {"vision", "enhance", "outline", "detail", "compile"})
        self.assertEqual(events[-1][0], "finish")

    def test_seed_change_invalidates_completed_stages(self):
        enhanced, outline, scene = neutral_planning()
        for seed in (1, 2):
            planning = PlanningBackend([enhanced, outline, scene])
            translation = ControlledTranslation([record("An adult in a blue coat."), record("", " stands by the gate.")])
            self.build(planning, translation, seed=seed)
            self.assertEqual(len(planning.requests), 3)
        self.build(PlanningBackend([]), ControlledTranslation([]), seed=1)

    def test_save_failure_reuses_completed_compilation(self):
        enhanced, outline, scene = neutral_planning()
        planning = PlanningBackend([enhanced, outline, scene])
        translation = ControlledTranslation([record("An adult in a blue coat."), record("", " stands by the gate.")])
        with patch("vc_director.nodes.node_story_builder.node.save", side_effect=OSError("neutral save interrupted")):
            with self.assertRaisesRegex(OSError, "neutral save interrupted"):
                self.build(planning, translation)
        self.build(PlanningBackend([]), ControlledTranslation([]))

    def test_cached_build_still_runs_preflight_before_using_checkpoints(self):
        enhanced, outline, scene = neutral_planning()
        self.build(PlanningBackend([enhanced, outline, scene]), ControlledTranslation([
            record("An adult in a blue coat."), record("", " stands by the gate.")]))
        planning = PlanningBackend([])
        with patch("vc_director.nodes.node_story_builder.node.preflight",
                   side_effect=VCError("MISSING_ASSET", "traveler", "中立テストの参照素材がありません。")):
            with self.assertRaisesRegex(VCError, "MISSING_ASSET"):
                self.build(planning, ControlledTranslation([]))
        self.assertEqual(planning.requests, [])

    def test_saved_scene_without_model_reuses_compile_cache_and_normalizes_newlines(self):
        scene = NEUTRAL_SCENE.replace("青い上着の成人。", "An adult in a blue coat.")
        with patch("vc_director.nodes.node_story_builder.node._backend") as backend:
            outputs = []
            for text in (scene, "\ufeff" + scene.replace("\n", "\r\n")):
                outputs.append(VCDirectorBuild().build("saved_scene", text, self.registry,
                    str(self.root / "out"), "[]", "none", 1))
            backend.assert_not_called()
        self.assertEqual(outputs[0][0], outputs[1][0])
        metadata = json.loads((Path(outputs[1][1]) / "run_metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["run_details"]["build_cache"]["stages"][0]["cache"], "hit")

    def test_policy_scope_is_restored_after_error_and_namespaces_do_not_mix(self):
        cache = PipelineCache(self.root / "policy")
        key = digest("neutral")
        with cache_scope("再利用", "one"):
            cache.put_success(key, dict(value=1))
        with cache_scope("再利用", "two"):
            self.assertIsNone(cache.get(key))
        with self.assertRaises(RuntimeError):
            with cache_scope("無効", "one"):
                self.assertIsNone(cache.get(key))
                cache.put_success(key, dict(value=2))
                raise RuntimeError("neutral")
        with cache_scope("再利用", "one"):
            self.assertEqual(cache.get(key), dict(value=1))

    def test_failed_and_corrupt_checkpoints_are_never_accepted(self):
        trace = []
        checkpoints = BuildCheckpoints(self.root / "checkpoints", trace)
        def checked(value):
            if value != dict(ok=True):
                raise VCError("NEUTRAL", "/", "invalid")
        with self.assertRaises(VCError):
            checkpoints.run("neutral", {}, lambda: dict(ok=False), checked)
        self.assertEqual(list((self.root / "checkpoints").rglob("*.json")), [])
        checkpoints.run("neutral", {}, lambda: dict(ok=True), checked)
        path = next((self.root / "checkpoints").rglob("*.json"))
        value = json.loads(path.read_text(encoding="utf-8"))
        value["payload"]["result"] = dict(ok=False)
        path.write_text(json.dumps(value), encoding="utf-8")
        calls = []
        result = checkpoints.run("neutral", {}, lambda: calls.append(True) or dict(ok=True), checked)
        self.assertEqual(result, dict(ok=True))
        self.assertEqual(calls, [True])

    def test_source_and_input_signatures_cover_relevant_settings(self):
        inputs = dict(emd="neutral", seed=1, registry=self.registry, model="test", settings=None, facts=[])
        initial = pipeline_signature(inputs)
        self.assertEqual(initial, pipeline_signature(copy.deepcopy(inputs)))
        for field, value in (("emd", "neutral changed"), ("seed", 2), ("registry", dict(root="other", assets=[])),
                             ("model", "test-2"), ("settings", dict(n_ctx=4096)), ("facts", [dict(text="blue coat")])):
            self.assertNotEqual(initial, pipeline_signature(dict(inputs, **{field: value})), field)

    def test_ui_exposes_modes_and_comfy_cannot_bypass_forced_regeneration(self):
        with patch("vc_director.nodes.node_story_builder.node._model_fields", return_value={}), \
             patch("vc_director.core.vision.discover_vision_model_pairs", return_value=[]), \
             patch("vc_director.nodes.node_story_builder.node.model_roots", return_value={}):
            inputs = VCDirectorBuild.INPUT_TYPES()
        self.assertEqual(inputs["optional"]["cache_mode"][0], ["再利用", "再生成", "無効"])
        for mode in ("再利用", "再生成", "無効"):
            self.assertTrue(math.isnan(VCDirectorBuild.IS_CHANGED(cache_mode=mode)))


if __name__ == "__main__":
    unittest.main()
