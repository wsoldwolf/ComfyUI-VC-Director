import copy
import json
import types
import unittest
from pathlib import Path

from test_core import ROOT, FakeBackend, english_document, fixture, temporary_directory, with_camera
from vc_director.core.artifacts.validation import _prose
from vc_director.core.emd import parse
from vc_director.core.errors import VCError
from vc_director.core.inference import GemmaBackend, GemmaTranslator
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.protocols import StageRunner
from vc_director.core.utilities import canonical


class TranslationSemanticsTests(unittest.TestCase):
    @staticmethod
    def backend(directory, responses):
        path = Path(directory) / "test.gguf"
        path.write_bytes(b"test model; injected lifecycle")
        backend = GemmaBackend(path)
        state = dict(requests=[], loads=0, clears=0)
        replies = iter(responses)
        def complete(messages, config, **kwargs):
            state["requests"].append(copy.deepcopy(messages))
            return next(replies)
        backend.lifecycle = types.SimpleNamespace(
            ensure_loaded=lambda *args: state.update(loads=state["loads"] + 1),
            count_serialized_prompt=lambda text: types.SimpleNamespace(count=100, estimated=False),
            effective_n_ctx=24576, complete_chat=complete,
            clear=lambda: state.update(clears=state["clears"] + 1))
        return backend, state

    def test_japanese_vocal_description_is_checked_before_translation(self):
        for text in ("主人公が帰宅の言葉を口にし、帽子を脱ぐ。", "挨拶を口にする。", "主人公が話し、声を出す。"):
            with self.subTest(text=text), self.assertRaises(VCError) as caught:
                _prose(text, "scene_home/summary", [], cues=True)
            self.assertEqual(caught.exception.diagnostic.code, "VOCAL_CUE")
        for text in ("果物を口にする。", "口を開く。", "言葉を口にしない。", "無言で頭を下げる。"):
            _prose(text, "summary", [], cues=True)

    def test_planner_repairs_summary_without_removing_authored_dialogue(self):
        scenario = parse((ROOT / "assets/emd/minimal.scenario.emd").read_text(encoding="utf-8"))
        enhanced = fixture("enhanced-story")
        enhanced["scenario_sha256"] = scenario["source_sha256"]
        outline = fixture("outline-response")["records"][0]["payload"]
        good = english_document()["scenes"][0]
        good["shots"][0]["events"][1]["text"] = scenario["beats"][0]["dialogues"][0]["text"]
        good = with_camera(good)
        bad = copy.deepcopy(good)
        bad["summary"] = "主人公が帰宅の言葉を口にし、帽子を脱ぐ。"
        backend = FakeBackend(["SCENES\t1\t" + canonical(outline), "DETAIL\t1\t" + canonical(bad), "DETAIL\t1\t" + canonical(good)])
        result = StoryTimelinePlanner(StageRunner(backend)).plan(scenario, enhanced)
        self.assertEqual(result["scenes"][0]["summary"], good["summary"])
        self.assertEqual(next(e for e in result["scenes"][0]["shots"][0]["events"] if e["kind"] == "dialogue")["text"], "「ただいま。」")
        error = backend.requests[2]["errors"][0]
        self.assertEqual(error["code"], "vocal_cue")
        self.assertTrue(error["path"].endswith("/summary"))
        self.assertIn("言葉を口にし", error["message"])

    def test_invented_vocal_translation_is_repaired_with_markers_and_cached(self):
        with temporary_directory() as directory:
            backend, state = self.backend(directory, ["TRANSLATION\t1\t__MVD_KEEP_0001__ whispers goodbye and turns around.",
                                                       "TRANSLATION\t1\t__MVD_KEEP_0001__ turns around."])
            translator = GemmaTranslator(backend, Path(directory) / "cache")
            for _ in range(2):
                self.assertEqual(translator.translate_protected(("", "が振り返る。"), ("<Subject 1>",)), "<Subject 1> turns around.")
            self.assertEqual(len(state["requests"]), 2)
            repair = json.loads(state["requests"][1][1]["content"].removeprefix("/no_think\n"))
            self.assertIn("VOCAL_CUE", repair["retry_reason"])
            self.assertIn("whispers", repair["repair_context"]["previous_translation"])
            self.assertEqual(repair["japanese_text"], "__MVD_KEEP_0001__が振り返る。")
            diagnostic = json.loads(next((Path(directory) / "cache/diagnostics").glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(diagnostic["status"], "success")
            self.assertEqual(len(diagnostic["attempts"]), 2)
            self.assertIn("VOCAL_CUE", diagnostic["attempts"][0]["error"])
            self.assertEqual(translator.trace[-1]["cache"], "hit")
            backend.close()

    def test_protocol_and_semantic_failures_share_two_attempts_and_save_raw_responses(self):
        with temporary_directory() as directory:
            backend, state = self.backend(directory, ["not a record", "TRANSLATION\t1\tShe speaks before turning around."])
            translator = GemmaTranslator(backend, Path(directory) / "cache")
            with self.assertRaises(VCError) as caught:
                translator("人物が振り返る。")
            self.assertEqual(caught.exception.diagnostic.code, "TRANSLATION_RETRIES_EXHAUSTED")
            self.assertEqual(len(state["requests"]), 2)
            self.assertEqual(state["clears"], 1)
            path = next((Path(directory) / "cache/diagnostics").glob("*.json"))
            self.assertIn(str(path), str(caught.exception))
            diagnostic = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(diagnostic["status"], "error")
            self.assertEqual(diagnostic["source"], "人物が振り返る。")
            self.assertEqual([row["response"] for row in diagnostic["attempts"]], ["not a record", "TRANSLATION\t1\tShe speaks before turning around."])
            self.assertEqual(len(translator.trace), 2)
            self.assertEqual(list((Path(directory) / "cache").glob("[0-9a-f][0-9a-f]/*.json")), [])

    def test_invalid_source_is_logged_without_loading_or_rewriting_it(self):
        with temporary_directory() as directory:
            backend, state = self.backend(directory, [])
            translator = GemmaTranslator(backend, diagnostics_root=directory)
            with self.assertRaises(VCError) as caught:
                translator("主人公が帰宅の言葉を口にし、帽子を脱ぐ。")
            self.assertEqual(caught.exception.diagnostic.path, "translation/source")
            self.assertEqual(state["loads"], 0)
            self.assertEqual(state["requests"], [])
            diagnostic = json.loads(next(Path(directory).glob("translation-*.json")).read_text(encoding="utf-8"))
            self.assertEqual(diagnostic["attempts"], [])
            self.assertEqual(diagnostic["status"], "error")


if __name__ == "__main__":
    unittest.main()
