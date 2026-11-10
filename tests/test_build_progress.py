import copy
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_core import FakeBackend, english_document, temporary_directory
from test_beat_transitions import continued_story
from vc_director.core.utilities import canonical
from vc_director.core.utilities.progress import emit_progress, progress_scope
from vc_director.core.direction import StoryEnhancer
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.protocols import StageRunner
from vc_director.core.protocols.llm import RECORDS
from vc_director.core.compiler import compile_scene_emd
from vc_director.core.emd import render_scene
from vc_director.core.inference.llama_cpp_backend import LlamaCppLifecycle
from vc_director.core.vision.llama_cpp_vision import LlamaCppVisionLifecycle
from vc_director.nodes.common.progress import BuildProgress, create_build_progress
from vc_director.nodes import VCDirectorBuild


class BuildProgressTests(unittest.TestCase):
    def test_activity_retries_and_replanning_never_regress_or_finish_early(self):
        messages, bar = [], Mock()
        progress = BuildProgress("scenario", bar=bar, send=messages.append)
        progress("done", stage="prepare")
        progress("unit", stage="outline", total=2, index=0, label="beat_a")
        progress("activity")
        before = progress.value
        progress("status", label="修正・再試行 2/3")
        self.assertIn("2/3", messages[-1]["message"])
        self.assertEqual(progress.value, before)
        progress("done", stage="outline", total=2, index=0)
        progress("restart")
        progress("unit", stage="outline", total=2, index=0)
        progress("done", stage="outline", total=2, index=0)
        progress("done", stage="outline", total=2, index=1)
        progress("unit", stage="detail", total=3, index=1, label="scene_2")
        self.assertIn("2/3", messages[-1]["message"])
        for _ in range(5000):
            progress("activity")
        progress("stage_done", stage="save")
        self.assertLess(progress.value, 1000)
        progress("finish")
        self.assertEqual(messages[-1]["value"], 1000)
        self.assertEqual(messages[-1]["state"], "complete")
        values = [m["value"] for m in messages]
        self.assertEqual(values, sorted(values))
        bar.update_absolute.assert_called_with(1000, 1000)

    def test_failure_preserves_partial_progress_and_skips_cancellation_hook(self):
        messages, bar = [], Mock()
        progress = BuildProgress("saved_scene", bar=bar, send=messages.append)
        progress("unit", stage="compile")
        progress("activity")
        before = progress.value
        bar.reset_mock()
        progress("failed")
        self.assertEqual(messages[-1]["value"], before)
        self.assertLess(before, 1000)
        self.assertEqual(messages[-1]["state"], "error")
        bar.update_absolute.assert_not_called()

    def test_mode_ranges_and_stream_throttle(self):
        self.assertNotIn("vision", BuildProgress("scenario").ranges)
        self.assertIn("vision", BuildProgress("scenario", vision=True).ranges)
        progress = BuildProgress("saved_scene")
        self.assertEqual(set(progress.ranges), {"prepare", "compile", "save"})
        with patch("vc_director.nodes.common.progress.monotonic", return_value=1):
            messages = []
            progress = BuildProgress("scenario", send=messages.append)
            progress("unit", stage="detail")
            for _ in range(100):
                progress("activity")
            self.assertEqual(len(messages), 2)
            self.assertGreater(progress.value, messages[-1]["value"])
            progress("status", label="生成中")
            self.assertGreater(messages[-1]["value"], messages[-2]["value"])

    def test_context_isolation_and_exception_cleanup(self):
        outer, inner = Mock(), Mock()
        with progress_scope(outer):
            emit_progress("status", label="outer")
            with self.assertRaisesRegex(RuntimeError, "test"), progress_scope(inner):
                emit_progress("activity")
                raise RuntimeError("test")
            emit_progress("activity")
        emit_progress("activity")
        self.assertEqual(outer.call_count, 2)
        inner.assert_called_once_with("activity")

    def test_comfy_bridge_targets_node_and_captures_client(self):
        bar, server = Mock(), Mock(client_id="client_a")
        factory = Mock(return_value=bar)
        modules = {"comfy.utils": types.SimpleNamespace(ProgressBar=factory),
                   "server": types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=server))}
        with patch.dict(sys.modules, modules):
            progress = create_build_progress("scenario", False, 42)
            server.client_id = "client_b"
            progress("finish")
        factory.assert_called_once_with(1000, node_id="42")
        event, payload, client = server.send_sync.call_args.args
        self.assertEqual((event, client), ("vc_director.build_progress", "client_a"))
        self.assertEqual((payload["node_id"], payload["state"]), ("42", "complete"))

    def test_real_planner_compiler_and_cached_runner_report_exact_unit_counts(self):
        scenario, enhanced, outlines, document = continued_story()
        responses = [RECORDS["enhance"] + "\t1\t" + canonical(enhanced)]
        responses += ["SCENES\t1\t" + canonical(dict(scenes=items)) for items in (outlines[:2], outlines[2:])]
        responses += ["DETAIL\t1\t" + canonical(s) for s in document["scenes"]]
        events = []
        def report(event, **data):
            events.append((event, data))
        original = copy.deepcopy(scenario)
        with temporary_directory() as directory, progress_scope(report):
            backend = FakeBackend(responses)
            runner = StageRunner(backend, directory)
            enhanced = StoryEnhancer(runner).enhance(scenario)
            planned = StoryTimelinePlanner(runner).plan(scenario, enhanced)
            compile_scene_emd(render_scene(planned))
            cached_runner = StageRunner(FakeBackend([]), directory)
            cached_enhanced = StoryEnhancer(cached_runner).enhance(scenario)
            StoryTimelinePlanner(cached_runner).plan(scenario, cached_enhanced)
        for stage, expected in (("outline", 2), ("detail", 3), ("compile", 3)):
            done = [data for event, data in events if event == "done" and data["stage"] == stage]
            self.assertEqual([d["index"] for d in done[:expected]], list(range(expected)))
            self.assertTrue(all(d["total"] == expected for d in done))
        self.assertTrue(any(data.get("label") == "キャッシュを使用" for _, data in events))
        self.assertEqual(scenario, original)

    def test_saved_scene_build_finishes_only_after_save_and_failure_never_finishes(self):
        module = "vc_director.nodes.node_story_builder.node"
        messages = []
        progress = BuildProgress("saved_scene", node_id="7", send=messages.append)
        emd = render_scene(english_document())
        with temporary_directory() as directory, patch(module + ".create_build_progress", return_value=progress):
            result = VCDirectorBuild().build("saved_scene", emd, {"assets": []}, directory, "[]", "none", 1, unique_id="7")
            self.assertTrue(Path(result[1], "plan.json").is_file())
        self.assertEqual(messages[-1]["state"], "complete")
        self.assertTrue(any(m["stage"] == "compile" for m in messages))
        self.assertTrue(any(m["stage"] == "save" and m["value"] < 1000 for m in messages))
        self.assertTrue(all(m["value"] < 1000 for m in messages[:-1]))
        messages.clear()
        progress = BuildProgress("saved_scene", send=messages.append)
        with temporary_directory() as directory, patch(module + ".create_build_progress", return_value=progress), \
             patch(module + ".save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                VCDirectorBuild().build("saved_scene", emd, {"assets": []}, directory, "[]", "none", 1)
        self.assertEqual(messages[-1]["state"], "error")
        self.assertTrue(all(m["value"] < 1000 for m in messages))

    def test_real_stream_collectors_report_only_nonempty_output_and_keep_interrupts(self):
        def stream():
            return [dict(choices=[dict(delta=dict(content=t))]) for t in (None, "", "hello", " world")]
        for collect in (LlamaCppLifecycle._collect_chat_stream, LlamaCppVisionLifecycle._collect):
            report = Mock()
            with progress_scope(report):
                self.assertEqual(collect(stream(), None), "hello world")
            self.assertEqual(report.call_count, 2)
            report.assert_called_with("activity")
            with self.assertRaisesRegex(RuntimeError, "cancelled"), progress_scope(report):
                collect(stream(), Mock(side_effect=RuntimeError("cancelled")))

    def test_cancellation_in_final_native_hook_is_reported_without_false_completion(self):
        messages, bar = [], Mock()
        progress = BuildProgress("saved_scene", bar=bar, send=messages.append)
        progress("unit", stage="save")
        before = progress.value
        def update(value, total):
            if value == total:
                raise InterruptedError("cancelled")
        bar.update_absolute.side_effect = update
        module = "vc_director.nodes.node_story_builder.node"
        with patch(module + ".create_build_progress", return_value=progress), \
             patch.object(VCDirectorBuild, "_build", return_value=("emd", "revision", "{}")):
            with self.assertRaisesRegex(InterruptedError, "cancelled"):
                VCDirectorBuild().build("saved_scene", "", {}, "", "[]", "none", 1)
        self.assertEqual(messages[-1]["state"], "error")
        self.assertEqual(messages[-1]["value"], before)
        self.assertFalse(any(m["state"] == "complete" for m in messages))


if __name__ == "__main__":
    unittest.main()
