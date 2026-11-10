import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import ROOT, english_document, temporary_directory
from vc_director.core.artifacts.assets import registry
from vc_director.core.emd import parse, render_scene
from vc_director.core.emd.diagnostics import contextual_error, parse_checked, SourceIndex
from vc_director.core.emd.preflight import preflight, read_image_facts
from vc_director.core.errors import VCError
from vc_director.nodes import VCDirectorBuild, VCDirectorCompileSave, VCDirectorEnhancer


SOURCE = (ROOT / "assets/emd/minimal.scenario.emd").read_text(encoding="utf-8")
BUILD = "vc_director.nodes.node_story_builder.node"


def line_of(text, row):
    return text.splitlines().index(row) + 1


class PreflightTests(unittest.TestCase):
    def assert_build_rejected_early(self, text, shared=None, facts="[]"):
        with patch(BUILD + "._backend") as backend, \
             patch("vc_director.core.vision.resolve_vision_model_pair") as vision, \
             patch(BUILD + ".save") as save:
            with self.assertRaises(VCError) as caught:
                VCDirectorBuild().build("scenario", text, shared or {"assets": []}, "unused", facts,
                                        "gemma.gguf", 1, vision_model_name="vision.gguf")
            backend.assert_not_called()
            vision.assert_not_called()
            save.assert_not_called()
        return caught.exception

    def test_collects_missing_definitions_materials_and_duplicate_speech_before_vision(self):
        text = SOURCE.replace("* 画像: なし", "* 画像: missing_picture", 1)
        text += "\n## 展開 reply\n* 出演: @ghost\n* 背景: missing_background\n* 台詞 dlg_home: @ghost | 「はい。」\n@ghostが立つ。\n"
        error = self.assert_build_rejected_early(text)
        message = str(error)
        for value in ("DUPLICATE_ID", "UNKNOWN_ACTOR", "UNKNOWN_BACKGROUND", "UNKNOWN_ENTITY", "MISSING_ASSET",
                      "missing_picture", "@ghost", "事前検査", "Vision・LLMは実行していません"):
            self.assertIn(value, message)
        for row in ("* 画像: missing_picture", "* 台詞 dlg_home: @hero | 「ただいま。」", "* 台詞 dlg_home: @ghost | 「はい。」", "@ghostが立つ。"):
            self.assertIn(f"{line_of(text, row)}行 | {row}", message)

    def test_missing_at_in_actor_definition_has_exact_heading_and_japanese_hint(self):
        text = SOURCE.replace("# 人物 @hero", "# 人物 hero")
        error = self.assert_build_rejected_early(text)
        self.assertIn("# 人物 @ID", str(error))
        self.assertIn(f"{line_of(text, '# 人物 hero')}行 | # 人物 hero", str(error))

    def test_missing_required_field_points_to_its_own_block(self):
        text = SOURCE.replace("* 出演: @hero\n", "")
        with self.assertRaises(VCError) as caught:
            parse_checked(text)
        self.assertEqual(caught.exception.diagnostic.line, line_of(text, "## 展開 return"))
        self.assertIn("必須項目", str(caught.exception))
        self.assertIn("## 展開 return", str(caught.exception))

    def test_duplicate_field_reports_both_declarations_in_the_same_block(self):
        text = SOURCE + "* 台詞 dlg_home: @hero | 「またね。」\n"
        with self.assertRaises(VCError) as caught:
            parse_checked(text)
        for row in ("* 台詞 dlg_home: @hero | 「ただいま。」", "* 台詞 dlg_home: @hero | 「またね。」"):
            self.assertIn(f"{line_of(text, row)}行 | {row}", str(caught.exception))

    def test_missing_file_changed_hash_and_wrong_kind_are_found_before_backend(self):
        text = SOURCE.replace("* 画像: なし", "* 画像: hero_image", 1)
        with temporary_directory() as directory:
            image = Path(directory, "hero.png")
            image.write_bytes(b"original image")
            shared = registry([dict(id="hero_image", kind="image", locator="hero.png")], directory)
            image.write_bytes(b"changed image")
            error = self.assert_build_rejected_early(text, shared)
            self.assertIn("ASSET_HASH", str(error))
            self.assertIn("登録後に変更", str(error))
            image.unlink()
            error = self.assert_build_rejected_early(text, shared)
            self.assertIn("MISSING_ASSET", str(error))
            self.assertIn("ファイルが存在しません", str(error))
            image.write_bytes(b"original image")
            shared["assets"][0]["kind"] = "audio"
            error = self.assert_build_rejected_early(text, shared)
            self.assertIn("ASSET_KIND", str(error))

    def test_valid_files_and_unreferenced_assets_do_not_modify_input_or_require_images(self):
        with temporary_directory() as directory:
            image = Path(directory, "hero.png")
            image.write_bytes(b"image")
            shared = registry([dict(id="hero_image", kind="image", locator="hero.png")], directory)
            text = SOURCE.replace("* 画像: なし", "* 画像: hero_image", 1)
            document = parse(text)
            original = copy.deepcopy((document, shared))
            self.assertIs(preflight(document, text, shared), document)
            self.assertEqual((document, shared), original)
            image.unlink()
            # Unrelated registry entries are not input requirements for this EMD.
            preflight(parse(SOURCE), SOURCE, shared)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_explicit_none_and_literal_dialogue_tags_do_not_require_references(self):
        text = SOURCE.replace("「ただいま。」", "「@someoneと書いてある。」")
        text = text.replace("少女が駅を出て", "<visible>@printed_label</visible>の横で少女が駅を出て")
        preflight(parse(text), text, {"assets": []})

    def test_saved_scene_dialogue_definition_and_event_share_id_legitimately(self):
        text = render_scene(english_document())
        preflight(parse(text), text, {"assets": []})
        declarations = SourceIndex(text).declarations
        self.assertEqual(len(declarations["source"]["dlg_home"]), 1)
        self.assertEqual(len(declarations["timeline"]["dlg_home"]), 1)

    def test_timeline_duplicate_and_source_table_do_not_confuse_actual_emd_lines(self):
        document = english_document()
        document["beats"][0]["dialogues"][0]["source"].update(first_line=999, last_line=999)
        scene = document["scenes"][0]
        scene["shots"][0]["events"][0]["id"] = scene["shots"][0]["events"][1]["id"]
        text = render_scene(document)
        with self.assertRaises(VCError) as caught:
            preflight(parse(text), text, {"assets": []})
        self.assertIn("DUPLICATE_ID", str(caught.exception))
        rows = [row for row in text.splitlines() if row.startswith(("* ACTION dlg_home", "* DIALOGUE dlg_home"))]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertIn(f"{line_of(text, row)}行 | {row}", str(caught.exception))
        self.assertNotIn("999行 |", str(caught.exception))

    def test_invalid_manual_image_facts_and_missing_description_stop_early(self):
        self.assert_build_rejected_early(SOURCE, facts='[{"entity_kind":"actor","entity_id":"missing","text":"facts"}]')
        self.assert_build_rejected_early(SOURCE, facts="{}")
        for text in ("[\ninvalid]", "[\n"):
            with self.assertRaisesRegex(VCError, "2行"):
                read_image_facts(text)
        text = SOURCE.replace("* 特徴: 赤いコートを着た旅人。\n", "").replace("* 画像: なし", "* 画像: hero_image", 1)
        with self.assertRaisesRegex(VCError, "VISION_DESCRIPTION"):
            preflight(parse(text), text)
        preflight(parse(text), text, image_facts=[dict(entity_kind="actor", entity_id="hero", text="Red coat.")])
        preflight(parse(text), text, vision=True)

    def test_compile_save_and_enhancer_also_validate_before_backend(self):
        text = render_scene(english_document()).replace("* 画像: なし", "* 画像: missing_picture", 1)
        with patch("vc_director.nodes.node_emd_compiler.node._backend") as backend:
            with self.assertRaisesRegex(VCError, "MISSING_ASSET"):
                VCDirectorCompileSave().compile_save(text, {"assets": []}, "unused", "model.gguf", 1)
            backend.assert_not_called()
        with patch("vc_director.nodes.node_direction_enhancer.node._backend") as backend:
            with self.assertRaisesRegex(VCError, "UNKNOWN_ACTOR"):
                VCDirectorEnhancer().enhance(SOURCE.replace("* 出演: @hero", "* 出演: @missing"), "[]", "model.gguf", 1)
            backend.assert_not_called()

    def test_generated_failure_is_localized_without_inventing_a_source_line(self):
        errors = [dict(code="dialogue_coverage", path="generated_scene", message="Missing authored dialogue")]
        error = VCError("LLM_RETRIES_EXHAUSTED", "generated_scene", json.dumps(errors) + "; 応答ログ: diagnostic.json")
        formatted = contextual_error(error, SOURCE)
        self.assertIn("台詞が欠落または重複", str(formatted))
        self.assertIn("diagnostic.json", str(formatted))
        self.assertIn("該当行は特定できません", str(formatted))
        self.assertIsNone(formatted.diagnostic.line)
        self.assertIn("Missing authored dialogue", error.diagnostic.message)


if __name__ == "__main__":
    unittest.main()
