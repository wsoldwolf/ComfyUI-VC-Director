import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from tools._bootstrap import load_project
load_project()

from vc_director.core.artifacts.assets import registry
from vc_director.core.audio import dialogue, directives
from vc_director.core.compiler import compile_scene_emd, compile_document, bind_references
from vc_director.core.emd import parse, render_scene
from vc_director.core.errors import VCError
from vc_director.core.protocols import StageRunner, system_prompt
from vc_director.core.direction import StoryEnhancer
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.artifacts.schema import validate
from vc_director.core.artifacts.storage import save, load
from vc_director.core.h3_contract import event_timing, lengths, quantize, verify_runtime
from vc_director.core.utilities import canonical, digest
from vc_director.core.artifacts.validation import validate_story

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs/spec/examples/artifacts"
TEMP_ROOT = ROOT / "output/test-temp"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def temporary_directory():
    return tempfile.TemporaryDirectory(dir=TEMP_ROOT)


def fixture(name="scene-document-dialogue"):
    return json.loads((EXAMPLES / (name + ".json")).read_text(encoding="utf-8"))


def english_document():
    doc = fixture()
    doc["direction"] = ["Quiet cinema."]
    doc["actors"][0].update(description="A traveler wearing a red coat.", image_asset_id=None, voice_asset_id=None)
    doc["backgrounds"][0]["description"] = "A station square at dusk."
    scene = doc["scenes"][0]
    scene["summary"] = "The traveler returns to town."
    scene["subject_descriptions"][0]["text"] = "A traveler wearing a red coat."
    scene["shots"][0]["events"][0]["text"] = "Walks out of the station and looks at the street."
    return doc


def compile_fixture(doc=None, assets=(), translator=None):
    doc = doc or english_document()
    emd = render_scene(doc)
    return compile_document(doc, emd, assets, translator)


def with_camera(scene):
    result = copy.deepcopy(scene)
    for shot in result["shots"]:
        shot["events"].insert(0, dict(id=shot["id"] + "_camera", kind="camera",
            start_ms=shot["start_ms"], end_ms=shot["end_ms"], actor_id="environment",
            text="Static medium-wide camera; retain the same position and framing throughout.",
            dialogue_id=None, language=None))
    return result


class FakeBackend:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def identity(self):
        return {"test_model": "fixed"}

    def complete(self, system, request):
        self.requests.append(json.loads(request))
        return next(self.responses)


class EMDTests(unittest.TestCase):
    def test_packaged_contracts_match_spec(self):
        for source, packaged in (("docs/spec/schemas/vc-artifacts.schema.json", "core/artifacts/schemas/vc-artifacts.schema.json"),
                                 ("docs/spec/contracts/context-loop-timing.json", "core/h3_contract/contracts/context-loop-timing.json")):
            self.assertEqual((ROOT / source).read_bytes(), (ROOT / packaged).read_bytes())

    def test_integrated_upstream_source_records(self):
        import hashlib
        snapshot = json.loads((ROOT / "docs/implementation/mv-director-sources.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["license"], "GPL-3.0-only")
        for record in snapshot["files"]:
            if record["target"]:
                self.assertEqual(hashlib.sha256((ROOT / record["target"]).read_bytes()).hexdigest(), record["integrated_sha256"], record["target"])

    def test_published_artifacts_validate(self):
        from jsonschema import Draft202012Validator
        from vc_director.core.artifacts.schema import schema
        Draft202012Validator.check_schema(schema())
        for path in EXAMPLES.glob("*.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator(schema()).validate(value)

    def test_scene_roundtrip_preserves_every_field(self):
        for name in ("scene-document", "scene-document-dialogue"):
            doc = fixture(name)
            self.assertEqual(parse(render_scene(doc)), doc)
            validate_story(doc)

    def test_multiline_and_escaped_pipe_roundtrip(self):
        doc = fixture()
        doc["scenes"][0]["summary"] = "First line\nSecond \\ line | with separators."
        self.assertEqual(parse(render_scene(doc)), doc)

    def test_scenario_parser(self):
        text = (ROOT / "assets/emd/homecoming.scenario.emd").read_text(encoding="utf-8")
        doc = validate_story(parse(text))
        self.assertEqual([b["id"] for b in doc["beats"]], ["return", "discovery"])
        self.assertEqual(doc["source_sha256"], digest(text))
        self.assertEqual(doc["beats"][0]["dialogues"][0]["language"], "Japanese")
        self.assertEqual(doc["beats"][1]["audio"]["vocal"], "none")

    def test_duplicate_and_unknown_fields_have_positions(self):
        text = render_scene(fixture())
        for insertion, code in (("* id: duplicate\n", "DUPLICATE_FIELD"), ("* unknown: nope\n", "UNKNOWN_FIELD")):
            bad = text.replace("# 作品\n", "# 作品\n" + insertion)
            with self.assertRaises(VCError) as caught:
                parse(bad)
            self.assertEqual(caught.exception.diagnostic.code, code)
            self.assertIsNotNone(caught.exception.diagnostic.line)

    def test_bad_times_and_missing_sections_rejected(self):
        text = render_scene(fixture())
        for bad in (text.replace("00:10.000", "00:60.000"), text.replace("## retention_analysis", "## missing")):
            with self.assertRaises(VCError):
                parse(bad)


class CompilerTests(unittest.TestCase):
    def test_music_none_is_explicit_in_the_dedicated_section(self):
        result = compile_fixture()
        prompt = result.plan["shots"][0]["prompt"]
        self.assertIn("non_diegetic_music:\nNo non-diegetic music.", prompt)
        self.assertIn("Dry, isolated spoken dialogue only.", prompt)
        self.assertNotIn("non_diegetic_music:\nN/A", prompt)
        doc = english_document()
        for audio in (doc["audio"], doc["beats"][0]["audio"], doc["scenes"][0]["audio"]):
            audio.update(bgm="generate", bgm_description="Soft instrumental piano.")
        prompt = compile_fixture(doc).plan["shots"][0]["prompt"]
        self.assertIn("non_diegetic_music:\nSoft instrumental piano.", prompt)
        self.assertNotIn("Dry, isolated spoken dialogue only.", prompt)
        self.assertNotIn("No non-diegetic music.", prompt)

    def test_dialogue_is_preserved_and_never_translated(self):
        seen = []

        def translator(text):
            seen.append(text)
            self.assertNotIn("ただいま", text)
            self.assertNotIn("@recipient", text)
            return text

        result = compile_fixture(translator=translator)
        prompt = result.plan["shots"][0]["prompt"]
        self.assertEqual(prompt.count("<d>[Japanese]ただいま。 @hero @recipient</d>"), 1)
        self.assertIn("Only the assigned speaker", prompt)
        self.assertIn("No background music", prompt)
        self.assertEqual(result.execution["segments"][0]["raw_length"], 243)
        self.assertEqual(result.execution["segments"][0]["event_timing"][1]["raw_start_frame"], 120)

    def test_existing_d_tag_is_verbatim(self):
        doc = english_document()
        for value, language in (("<d>hello @hero_image</d>", None), ("<d>[English]Hi, @hero</d>", "English")):
            doc["beats"][0]["dialogues"][0].update(text=value, language=language)
            doc["scenes"][0]["shots"][0]["events"][1].update(text=value, language=language)
            self.assertIn(value, compile_fixture(doc).plan["shots"][0]["prompt"])

    def test_japanese_requires_translation(self):
        doc = english_document()
        doc["scenes"][0]["summary"] = "旅人が帰ってくる。"
        with self.assertRaisesRegex(VCError, "TRANSLATOR_REQUIRED"):
            compile_fixture(doc)

    def test_translation_cannot_inject_dialogue_or_change_tokens(self):
        for bad in ("<d>[English]extra</d>", "<Subject 2>", "She sings."):
            with self.assertRaises(VCError):
                compile_fixture(translator=lambda text: bad)

    def test_silence_has_strong_explicit_directives(self):
        doc = english_document()
        doc["beats"][0]["dialogues"] = []
        for audio in (doc["audio"], doc["beats"][0]["audio"], doc["scenes"][0]["audio"]):
            audio["vocal"] = "none"
        doc["scenes"][0]["shots"][0]["events"].pop()
        result = compile_fixture(doc)
        self.assertIn("Complete silence", result.plan["shots"][0]["prompt"])
        self.assertEqual(result.execution["segments"][0]["audio_output"], "silence")

    def test_speech_and_policy_violations(self):
        mutators = [lambda d: d["scenes"][0]["audio"].update(vocal="none"),
                    lambda d: d["scenes"][0]["shots"][0]["events"][1].update(text="「改変」"),
                    lambda d: d["scenes"][0].update(summary="She says hello."),
                    lambda d: d["scenes"][0].update(summary="A quote 「accidental speech」."),
                    lambda d: d["scenes"][0].update(summary="Music starts."),
                    lambda d: d["scenes"][0].update(actor_ids=[]),
                    lambda d: d["scenes"][0].update(depends_on=["future"]),
                    lambda d: d["scenes"][0]["shots"][0]["events"][0].update(end_ms=10001)]
        for mutate in mutators:
            doc = english_document()
            mutate(doc)
            with self.assertRaises(VCError):
                compile_fixture(doc)

    def test_visual_using_does_not_match_singing(self):
        doc = english_document()
        doc["scenes"][0]["summary"] = "The traveler returns using a walking stick."
        self.assertIn("using a walking stick", compile_fixture(doc).plan["shots"][0]["prompt"])

    def test_inconsistent_audio_description_rejected(self):
        doc = english_document()
        doc["scenes"][0]["audio"]["bgm_description"] = "Piano"
        with self.assertRaisesRegex(VCError, "AUDIO_DESCRIPTION"):
            validate_story(doc)

    def test_reference_audio_never_sets_duration(self):
        doc = english_document()
        doc["actors"][0]["voice_asset_id"] = "voice"
        asset = dict(id="voice", kind="audio", sha256="a" * 64, locator="voice.wav", sample_rate=48000, sample_count=2400, fps=None, frame_count=None, paired_audio_enabled=False)
        result = compile_fixture(doc, [asset])
        self.assertEqual(result.plan["shots"][0]["length"], 243)
        self.assertEqual(result.execution["segments"][0]["reference_bindings"], [dict(asset_id="voice", role="voice", token="<Audio 1>")])
        self.assertNotIn("audio_operations", canonical(result.execution))

    def test_reference_order_and_capacity(self):
        doc = english_document()
        doc["actors"][0].update(image_asset_id="hero_image", video_asset_id="hero_video", voice_asset_id="hero_voice")
        doc["backgrounds"][0]["image_asset_id"] = "station"
        def asset(id, kind, paired=False):
            return dict(id=id, kind=kind, sha256="a" * 64, locator=id, sample_rate=None, sample_count=None, fps=None, frame_count=None, paired_audio_enabled=paired)
        assets = [asset("hero_image", "image"), asset("station", "image"), asset("hero_video", "video", True), asset("hero_voice", "audio")]
        result = compile_fixture(doc, assets)
        bindings = result.execution["segments"][0]["reference_bindings"]
        self.assertEqual({b["asset_id"]: b["token"] for b in bindings if b["role"] != "video_audio"}, dict(hero_image="<Picture 1>", station="<Picture 2>", hero_video="<Video 1>", hero_voice="<Audio 2>"))
        self.assertIn(dict(asset_id="hero_video", role="video_audio", token="<Audio 1>"), bindings)

    def test_timing_grid_and_zero_frame(self):
        for duration in range(1, 15001, 13):
            for continued in (False, True):
                raw, context = lengths(duration, continued)
                self.assertEqual(raw % 17, 5)
                self.assertTrue(0 <= raw - context - (duration * 24 + 999) // 1000 <= 16)
        self.assertEqual(quantize(125), 3)
        with self.assertRaisesRegex(VCError, "ZERO_FRAME_EVENT"):
            event_timing(dict(id="short", start_ms=1, end_ms=2), 0, 0)

    def test_continuation_audio_context(self):
        doc = english_document()
        doc["beats"][0]["dialogues"] = []
        for p in (doc["audio"], doc["beats"][0]["audio"], doc["scenes"][0]["audio"]):
            p["vocal"] = "none"
        first = doc["scenes"][0]
        first["shots"][0]["events"].pop()
        second = copy.deepcopy(first)
        second.update(id="second", start_ms=10000, end_ms=20000, transition="continue", depends_on=[first["id"]], required_event_ids=[], initial_state=copy.deepcopy(first["terminal_state"]))
        second["shots"][0]["id"] = "second_shot"
        second["shots"][0]["events"][0]["id"] = "second_event"
        doc["scenes"].append(second)
        doc["duration_ms"] = 20000
        result = compile_fixture(doc)
        self.assertEqual(result.execution["segments"][1]["audio_context_frames"], 22)
        self.assertEqual(result.execution["segments"][1]["event_timing"][0]["raw_start_frame"], 22)
        doc["scenes"][1]["audio"].update(effects="explicit", effects_description="Soft footsteps.")
        doc["beats"][0]["audio"].update(effects="expand")
        self.assertEqual(compile_fixture(doc).execution["segments"][1]["audio_context_frames"], 0)


class StorageTests(unittest.TestCase):
    def test_old_audio_prompt_revision_without_policy_metadata_remains_readable(self):
        from vc_director.core.compiler.api import LEGACY_PROMPT_POLICY
        doc = english_document()
        result = compile_document(doc, render_scene(doc), prompt_policy=LEGACY_PROMPT_POLICY)
        result.metadata.pop("prompt_policy_version")
        self.assertIn("non_diegetic_music:\nN/A", result.plan["shots"][0]["prompt"])
        with temporary_directory() as temp:
            target = save(result, temp)
            replay = load(target.parents[1])
            self.assertEqual(replay.plan, result.plan)
            self.assertEqual(replay.execution, result.execution)
            upgraded = compile_document(replay.document, replay.scene_emd)
            self.assertNotEqual(upgraded.plan, replay.plan)
            fresh = save(upgraded, temp)
            self.assertNotEqual(fresh, target)
            self.assertEqual(load(fresh.parents[1]).plan, upgraded.plan)

    def test_readable_json_preserves_values_and_hash_contracts(self):
        result = compile_fixture()
        with temporary_directory() as temp:
            target = save(result, temp)
            project = target.parents[1]
            for path in [*target.glob("*.json"), project / "run_metadata.json"]:
                raw = path.read_text(encoding="utf-8")
                value = json.loads(raw)
                self.assertEqual(raw, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")
                self.assertIn('\n  "', raw)
            raw_plan = (target / "plan.json").read_text(encoding="utf-8")
            self.assertIn("ただいま", raw_plan)
            self.assertEqual(json.loads(raw_plan), result.plan)
            execution = json.loads((target / "execution.json").read_text(encoding="utf-8"))
            metadata = json.loads((target / "run_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(execution["plan_sha256"], digest(canonical(result.plan)))
            self.assertEqual(metadata["files"]["plan.json"], digest(raw_plan))
            self.assertNotEqual(metadata["files"]["plan.json"], execution["plan_sha256"])
            restored = load(project)
            self.assertEqual(restored.plan, result.plan)
            self.assertEqual(restored.execution, result.execution)

    def test_previous_compact_revision_remains_readable(self):
        result = compile_fixture()
        with temporary_directory() as temp:
            # Construct the previous on-disk format, including its original hash chain.
            contents = {"scenes.emd": result.scene_emd, "plan.json": canonical(result.plan), "execution.json": canonical(result.execution)}
            metadata = dict(result.metadata, run_details={}, files={name: digest(raw) for name, raw in contents.items()})
            contents["run_metadata.json"] = canonical(metadata)
            revision = digest(contents["run_metadata.json"])
            project = Path(temp) / result.document["project_id"]
            target = project / "revisions" / revision
            target.mkdir(parents=True)
            for name, raw in contents.items():
                (target / name).write_bytes(raw.encode("utf-8"))
            (project / "run_metadata.json").write_bytes(canonical(dict(schema="VCD_REVISION_POINTER_V1", active_revision=revision)).encode("utf-8"))
            self.assertEqual(load(project).execution, result.execution)
            readable = save(result, temp)
            self.assertNotEqual(readable, target)
            self.assertEqual((target / "plan.json").read_text(encoding="utf-8"), canonical(result.plan))
            self.assertEqual(load(project).execution, result.execution)

    def test_save_load_and_hash_tampering(self):
        result = compile_fixture()
        with temporary_directory() as temp:
            target = save(result, temp)
            self.assertEqual(load(target.parents[1]).plan, result.plan)
            self.assertEqual(save(result, temp), target)
            (target / "plan.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(VCError, "FILE_HASH"):
                load(target.parents[1])

    def test_asset_hash(self):
        with temporary_directory() as temp:
            path = Path(temp) / "image.png"
            path.write_bytes(b"test fixture; no media decode required")
            value = registry([dict(id="image", kind="image", locator="image.png")], temp)
            path.write_bytes(b"changed")
            from vc_director.core.artifacts.storage import verify_assets
            with self.assertRaisesRegex(VCError, "ASSET_HASH"):
                verify_assets(value["assets"], temp)


class PlannerTests(unittest.TestCase):
    def test_prompt_schemas(self):
        for stage in ("enhance", "outline", "detail", "retime"):
            self.assertIn("JSON payload schema", system_prompt(stage))

    def test_repair_and_cache_only_valid_success(self):
        scenario = fixture("scenario")
        enhanced = fixture("enhanced-story")
        good = "ENHANCED\t1\t" + canonical(enhanced)
        backend = FakeBackend(["broken", "ENHANCED\t2\t" + canonical(enhanced), good])
        with temporary_directory() as temp:
            runner = StageRunner(backend, temp)
            result = runner.run("enhance", scenario["project_id"], dict(scenario=scenario, image_facts=[]), lambda v: None)
            self.assertEqual(result, enhanced)
            self.assertEqual([r["retry"] for r in backend.requests], ["initial", "repair", "isolated"])
            again = runner.run("enhance", scenario["project_id"], dict(scenario=scenario, image_facts=[]), lambda v: None)
            self.assertEqual(again, enhanced)
            self.assertEqual(len(backend.requests), 3)

    def test_invalid_model_output_never_succeeds(self):
        scenario = fixture("scenario")
        backend = FakeBackend(["", "{}", "SCENES\t1\t{}"])
        with self.assertRaisesRegex(VCError, "LLM_RETRIES_EXHAUSTED"):
            StageRunner(backend).run("enhance", scenario["project_id"], dict(scenario=scenario, image_facts=[]), lambda v: None)

    def test_all_stages_with_published_payloads(self):
        scenario = fixture("scenario")
        enhanced = fixture("enhanced-story")
        outline_response = fixture("outline-response")["records"][0]["payload"]
        detail = with_camera(fixture("detail-response")["records"][0]["payload"])
        backend = FakeBackend(["ENHANCED\t1\t" + canonical(enhanced), "SCENES\t1\t" + canonical(outline_response), "DETAIL\t1\t" + canonical(detail)])
        runner = StageRunner(backend)
        actual = StoryEnhancer(runner).enhance(scenario)
        document = StoryTimelinePlanner(runner).plan(scenario, actual)
        self.assertEqual(parse(render_scene(document)), document)


if __name__ == "__main__":
    unittest.main()
