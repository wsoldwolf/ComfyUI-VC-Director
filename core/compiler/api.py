"""Deterministic scene compiler; all creative expansion happens upstream."""
import copy
import re
from dataclasses import dataclass

from .. import __version__
from ..audio import dialogue, directives
from ..emd import parse
from ..errors import require
from ..artifacts.schema import validate
from ..h3_contract import CONTRACT, event_timing, lengths
from ..utilities import canonical, digest, normalize, timestamp
from ..artifacts.validation import unique, validate_story

NATIVE = re.compile(r"<(?:Subject [1-4]|Picture [1-9]|Video [1-3]|Audio [1-6])>")
SYMBOL = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_-]*)")
JAPANESE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
LEGACY_PROMPT_POLICY = "vcd-audio-v1"
PROMPT_POLICY = "vcd-audio-v2"
NO_MUSIC = "No non-diegetic music. No background music, soundtrack, score, melody or instrumental accompaniment. No diegetic music."


class EnglishOnlyTranslator:
    """Offline path for already-English prose. Never silently passes Japanese."""
    def __call__(self, text):
        require(not JAPANESE.search(text), "TRANSLATOR_REQUIRED", "/", "Japanese prose requires a configured translation model")
        return text


@dataclass(frozen=True)
class CompileResult:
    scene_emd: str
    document: dict
    plan: dict
    execution: dict
    metadata: dict


def bind_references(document, scene, registry):
    actors = {a["id"]: a for a in document["actors"]}
    background = next(b for b in document["backgrounds"] if b["id"] == scene["background_id"])
    requested = []

    def add(asset_id, role, kind):
        if not asset_id:
            return
        require(asset_id in registry, "MISSING_ASSET", scene["id"], f"Asset not registered: {asset_id}")
        require(registry[asset_id]["kind"] == kind, "ASSET_KIND", asset_id, f"Expected {kind}")
        if (asset_id, role) not in requested:
            requested.append((asset_id, role))

    speakers = {e["actor_id"] for sh in scene["shots"] for e in sh["events"] if e["kind"] == "dialogue"}
    for actor_id in scene["actor_ids"]:
        actor = actors[actor_id]
        add(actor["image_asset_id"], "identity", "image")
        add(actor["video_asset_id"], "motion", "video")
        if actor_id in speakers:
            add(actor["voice_asset_id"], "voice", "audio")
    add(background["image_asset_id"], "background", "image")
    for ref in scene["audio"]["reference_audio"]:
        add(ref["asset_id"], ref["purpose"], "audio")
    # Runtime presentation order is Pictures, paired Audio+Video, standalone Audio.
    asset_ids = list(dict.fromkeys(a for a, _ in requested))
    ordered = [a for k in ("image", "video", "audio") for a in asset_ids if registry[a]["kind"] == k]
    counts = {"image": 0, "video": 0, "audio": 0}
    tokens, paired = {}, []
    for asset_id in ordered:
        asset = registry[asset_id]
        kind = asset["kind"]
        if kind == "video" and asset["paired_audio_enabled"]:
            counts["audio"] += 1
            paired.append(dict(asset_id=asset_id, role="video_audio", token=f"<Audio {counts['audio']}>"))
        counts[kind] += 1
        tokens[asset_id] = f"<{dict(image='Picture', video='Video', audio='Audio')[kind]} {counts[kind]}>"
    require(counts["image"] <= 9 and counts["video"] <= 3 and sum(registry[a]["kind"] == "audio" for a in ordered) <= 3, "REFERENCE_LIMIT", scene["id"], "Native H3 reference capacity exceeded")
    bindings = [dict(asset_id=a, role=r, token=tokens[a]) for a in ordered for aa, r in requested if aa == a]
    return bindings + paired


def compile_scene_emd(text, assets=(), translator=None, *, prompt_policy=PROMPT_POLICY):
    text = normalize(text)
    document = parse(text)
    require(document["schema"] == "VCD_SCENE_DOCUMENT_V1", "INPUT_KIND", "/", "Compiler requires scene EMD")
    return compile_document(document, text, assets, translator, prompt_policy=prompt_policy)


def compile_document(document, scene_emd, assets=(), translator=None, *, prompt_policy=PROMPT_POLICY):
    require(prompt_policy in {LEGACY_PROMPT_POLICY, PROMPT_POLICY}, "PROMPT_POLICY", "/", "Unknown audio prompt policy")
    document = copy.deepcopy(validate_story(document))
    require(document["schema"] == "VCD_SCENE_DOCUMENT_V1", "INPUT_KIND", "/", "Scene document required")
    scene_emd = normalize(scene_emd)
    require(parse(scene_emd) == document, "ARTIFACT_MISMATCH", "/", "Scene EMD and typed document differ")
    translator = translator or EnglishOnlyTranslator()
    for asset in assets:
        validate(asset, "asset")
    registry = unique(assets, "assets")
    trace, translation_cache, shots, segments = [], {}, [], []
    output_start, previous = 0, None
    background_map = {b["id"]: b for b in document["backgrounds"]}
    actor_map = {a["id"]: a for a in document["actors"]}

    for index, scene in enumerate(document["scenes"], 1):
        scene = copy.deepcopy(scene)
        scene["actor_ids"] = [a["id"] for a in document["actors"] if a["id"] in scene["actor_ids"]]
        subject_map = {a: f"<Subject {i}>" for i, a in enumerate(scene["actor_ids"], 1)}
        bindings = bind_references(document, scene, registry)

        def translate(source):
            resolved = SYMBOL.sub(lambda m: subject_map[m[1]], source)
            if resolved not in translation_cache:
                parts, values, last = [], [], 0
                for match in NATIVE.finditer(resolved):
                    parts.append(resolved[last:match.start()])
                    values.append(match[0])
                    last = match.end()
                parts.append(resolved[last:])
                # MV's whole-field implementation preserves reference ownership and word order.
                if hasattr(translator, "translate_protected"):
                    result = translator.translate_protected(tuple(parts), tuple(values))
                else:
                    require(not values or not JAPANESE.search(resolved), "PROTECTED_TRANSLATOR_REQUIRED", scene["id"], "Japanese with references requires a protected-field translator")
                    result = translator(resolved)
                require(isinstance(result, str) and bool(result.strip()), "TRANSLATION", scene["id"], "Empty translation")
                require(not JAPANESE.search(NATIVE.sub("", result)), "TRANSLATION", scene["id"], "Untranslated Japanese")
                require(sorted(NATIVE.findall(result)) == sorted(values), "TRANSLATION_REFERENCE", scene["id"], "Translator changed references")
                # Translation may not introduce speech, music permissions, or raw aliases.
                translation_cache[resolved] = result.strip()
                trace.append(dict(source=resolved, translated=result.strip()))
            from ..artifacts.validation import _prose
            _prose(NATIVE.sub("", translation_cache[resolved]), scene["id"], [], cues=True, music_allowed=scene["audio"]["bgm"] != "none")
            return translation_cache[resolved]

        raw, context = lengths(scene["end_ms"] - scene["start_ms"], scene["transition"] == "continue")
        same_audio = previous is not None and scene["audio"] == previous["audio"] and bindings == previous["bindings"]
        audio_context = 22 if context and same_audio and scene["audio"]["vocal"] == previous["audio"]["vocal"] == "none" else 0
        all_events = [e for shot in scene["shots"] for e in shot["events"]] + scene["sound_events"]
        timings = [event_timing(e, context, output_start) for e in all_events]
        timing_map = {e["event_id"]: e for e in timings}

        def interval(e):
            t = timing_map[e["id"]]
            # Integer half-up frame->ms conversion, same raw boundaries as metadata.
            return f"[{timestamp((t['raw_start_frame'] * 1000 + 12) // 24)} --> {timestamp((t['raw_end_frame'] * 1000 + 12) // 24)}]"

        definitions = []
        descriptions = {s["actor_id"]: s["text"] for s in scene["subject_descriptions"]}
        for actor_id in scene["actor_ids"]:
            actor = actor_map[actor_id]
            refs = [b["token"] for b in bindings if b["asset_id"] in {actor["image_asset_id"], actor["video_asset_id"], actor["voice_asset_id"]} and b["role"] != "video_audio"]
            definitions.append(f"{subject_map[actor_id]}: {translate(descriptions[actor_id])}" + (f" Reference: {' '.join(refs)}." if refs else ""))
        bg = background_map[scene["background_id"]]
        bg_refs = [b["token"] for b in bindings if b["role"] == "background"]
        definitions.append("Environment: " + translate(bg["description"]) + (" Reference: " + " ".join(bg_refs) + "." if bg_refs else "") + " The specified environment and lighting take precedence over backgrounds in identity reference images.")
        details, speech = [], []
        for shot in scene["shots"]:
            for e in shot["events"]:
                owner = subject_map.get(e["actor_id"], "Environment")
                if e["kind"] == "dialogue":
                    protected, _ = dialogue(e["text"])
                    details.append(f"{interval(e)} {owner} speaks {protected}")
                    speech.append(protected)
                else:
                    details.append(f"{interval(e)} {e['kind'].upper()} {owner}: {translate(e['text'])}")
        sounds = [directives(scene["audio"], explicit=prompt_policy == PROMPT_POLICY)]
        for key in ("ambience", "effects"):
            if scene["audio"][key + "_description"]:
                sounds.append(key.capitalize() + ": " + translate(scene["audio"][key + "_description"]))
        sounds.extend(f"{interval(e)} {e['kind'].upper()}: {translate(e['text'])}" for e in scene["sound_events"])
        sounds.extend(f"{b['role']} reference: {b['token']}" for b in bindings if b["role"] in {"music", "ambience", "effects", "video_audio"})
        music = (NO_MUSIC if prompt_policy == PROMPT_POLICY else "N/A") if scene["audio"]["bgm"] == "none" else translate(scene["audio"]["bgm_description"]) + ". Instrumental only."
        sections = [("subject_definitions", definitions), ("summary", ["[reference generation] " + translate(scene["summary"])]),
                    ("retention_analysis", [f"{subject_map[r['actor_id']]}: {r['mode']}" for r in scene["retention"]] or ["N/A"]),
                    ("detailed_description", [translate(d) for d in document["direction"]] + details), ("overall_soundscape", sounds), ("non_diegetic_music", [music])]
        prompt = "\n\n".join(title + ":\n" + "\n".join(values) for title, values in sections)
        require(re.findall(r"<d>.*?</d>", prompt, re.S) == speech, "DIALOGUE_INTEGRITY", scene["id"], "Compiled speech differs from authored speech")
        plan_shot = dict(id=scene["id"], length=raw, prompt=prompt, context_length=context, audio_context_length=audio_context)
        if context:
            plan_shot["continuation_mode"] = "guide"
        shots.append(plan_shot)
        segments.append(dict(id=scene["id"], scene_id=scene["id"], index=index, planned_start_ms=scene["start_ms"], planned_end_ms=scene["end_ms"],
                             start_frame=output_start, end_frame=output_start + raw - context, raw_length=raw, context_frames=context, audio_context_frames=audio_context,
                             local_subjects=[dict(actor_id=a, subject_number=i) for i, a in enumerate(scene["actor_ids"], 1)], reference_bindings=bindings, event_timing=timings,
                             audio=scene["audio"], audio_output="silence" if all(scene["audio"][k] == "none" for k in ("vocal", "bgm", "ambience", "effects")) else "generated"))
        output_start += raw - context
        previous = dict(audio=scene["audio"], bindings=bindings)
    plan = dict(shots=shots)
    schedule = []
    for segment in segments:
        for binding in segment["reference_bindings"]:
            item = next((r for r in schedule if (r["asset_id"], r["role"]) == (binding["asset_id"], binding["role"])), None)
            if item is None:
                item = dict(asset_id=binding["asset_id"], role=binding["role"], segment_ids=[])
                schedule.append(item)
            item["segment_ids"].append(segment["id"])
    runtime = {key: CONTRACT[key] for key in ("contract_id", "context_loop_version", "context_loop_commit", "source_fingerprint", "fps")}
    runtime.update(output_sample_rate=48000, compiler_version=__version__)
    execution = dict(schema="VCD_EXECUTION_V1", project_id=document["project_id"], scene_emd_sha256=digest(scene_emd), plan_sha256=digest(canonical(plan)),
                     runtime_contract=runtime, assets=list(assets), segments=segments, reference_schedule=schedule, output_audio="generated")
    validate(execution, "execution")
    metadata = dict(schema="VCD_RUN_METADATA_V1", compiler_version=__version__, prompt_policy_version=prompt_policy,
                    scene_emd_sha256=digest(scene_emd), plan_sha256=execution["plan_sha256"],
                    execution_sha256=digest(canonical(execution)), translation_trace=trace, output_frames=output_start, diagnostics=[])
    return CompileResult(scene_emd, document, plan, execution, metadata)
