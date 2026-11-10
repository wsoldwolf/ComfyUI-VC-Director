"""Deterministic scene compiler; all creative expansion happens upstream."""
import copy
import re
from dataclasses import dataclass
from collections import Counter

from .. import __version__
from ..audio import dialogue, directives
from ..audio.nonverbal import vocal_text
from ..audio.continuity import context_frames as audio_context_frames, NEGPIP_MUSIC
from ..audio.prompt import narrow_silent_direction, scene_soundscape
from ..audio.roles import scene_roles, referenced_actors
from ..artifacts.props import visible_props
from ..artifacts.visible_text import VISIBLE_TEXT, outside_visible_text, map_outside_visible_text, render_visible_text
from ..emd import parse
from ..errors import VCError, require
from ..artifacts.schema import validate
from ..h3_contract import CONTRACT, event_timing, lengths
from ..utilities import canonical, digest, normalize, timestamp
from ..utilities.progress import emit_progress
from ..artifacts.validation import unique, validate_story
from ..artifacts.actor_references import ActorReferences, bind_scene_actor_references, no_person_pronouns, bound_pronoun_owner

NATIVE = re.compile(r"<(?:Subject [1-9][0-9]*|Picture [1-9]|Video [1-3]|Audio [1-6])>")
PROTECTED_PROSE = re.compile(VISIBLE_TEXT.pattern + "|" + NATIVE.pattern)
SYMBOL = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_-]*)")
JAPANESE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
LEGACY_PROMPT_POLICY = "vcd-audio-v1"
EXPLICIT_PROMPT_POLICY = "vcd-audio-v2"
COMPACT_PROMPT_POLICY = "vcd-audio-v3"
RESOLVED_AUDIO_PROMPT_POLICY = "vcd-audio-v4"
VISUAL_PROMPT_POLICY = "vcd-audio-v5"
ROLE_PROMPT_POLICY = "vcd-audio-v6"
IDENTITY_PROMPT_POLICY = "vcd-audio-v7"
OWNERSHIP_PROMPT_POLICY = "vcd-audio-v8"
REFERENCE_PROMPT_POLICY = "vcd-audio-v9"
TYPED_PROMPT_POLICY = "vcd-audio-v10"
PROMPT_POLICY = "vcd-audio-v11-continuity"
NO_MUSIC = "No non-diegetic music. No background music, soundtrack, score, melody or instrumental accompaniment. No diegetic music."


class EnglishOnlyTranslator:
    """Offline path for already-English prose. Never silently passes Japanese."""
    def __call__(self, text):
        require(not JAPANESE.search(outside_visible_text(text)), "TRANSLATOR_REQUIRED", "/", "Japanese prose requires a configured translation model")
        return text


@dataclass(frozen=True)
class CompileResult:
    scene_emd: str
    document: dict
    plan: dict
    execution: dict
    metadata: dict


def bind_references(document, scene, registry, *, separate_roles=False, used_actor_ids=None):
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

    speakers = {e["actor_id"] for sh in scene["shots"] for e in sh["events"] if e["kind"] in {"dialogue", "vocal"}}
    visible, _ = scene_roles(scene)
    paired_speakers = set()
    for actor_id in scene["actor_ids"]:
        if used_actor_ids is not None and actor_id not in used_actor_ids:
            continue
        actor = actors[actor_id]
        if not separate_roles or actor_id in visible:
            add(actor["image_asset_id"], "identity", "image")
            add(actor["video_asset_id"], "motion", "video")
        if actor_id in speakers and actor["video_asset_id"]:
            paired_speakers.add(actor["video_asset_id"])
        if actor_id in speakers:
            add(actor["voice_asset_id"], "voice", "audio")
    add(background["image_asset_id"], "background", "image")
    for prop in document.get("props", []):
        if prop["id"] in visible_props(scene):
            add(prop["image_asset_id"], "prop", "image")
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
        if kind == "video" and asset["paired_audio_enabled"] and (not separate_roles or asset_id in paired_speakers):
            counts["audio"] += 1
            paired.append(dict(asset_id=asset_id, role="video_audio", token=f"<Audio {counts['audio']}>"))
        counts[kind] += 1
        tokens[asset_id] = f"<{dict(image='Picture', video='Video', audio='Audio')[kind]} {counts[kind]}>"
    require(counts["image"] <= 9 and counts["video"] <= 3 and sum(registry[a]["kind"] == "audio" for a in ordered) <= 3, "REFERENCE_LIMIT", scene["id"], "Native H3 reference capacity exceeded")
    bindings = [dict(asset_id=a, role=r, token=tokens[a]) for a in ordered for aa, r in requested if aa == a]
    return bindings + paired


def compile_scene_emd(text, assets=(), translator=None, *, prompt_policy=PROMPT_POLICY, replay_output_sample_rate=None):
    text = normalize(text)
    document = parse(text)
    require(document["schema"] == "VCD_SCENE_DOCUMENT_V1", "INPUT_KIND", "/", "Compiler requires scene EMD")
    return compile_document(document, text, assets, translator, prompt_policy=prompt_policy, replay_output_sample_rate=replay_output_sample_rate)


def compile_document(document, scene_emd, assets=(), translator=None, *, prompt_policy=PROMPT_POLICY, replay_output_sample_rate=None):
    require(prompt_policy in {LEGACY_PROMPT_POLICY, EXPLICIT_PROMPT_POLICY, COMPACT_PROMPT_POLICY, RESOLVED_AUDIO_PROMPT_POLICY, VISUAL_PROMPT_POLICY, ROLE_PROMPT_POLICY, IDENTITY_PROMPT_POLICY, OWNERSHIP_PROMPT_POLICY, REFERENCE_PROMPT_POLICY, TYPED_PROMPT_POLICY, PROMPT_POLICY}, "PROMPT_POLICY", "/", "Unknown audio prompt policy")
    separate_roles = prompt_policy in {ROLE_PROMPT_POLICY, IDENTITY_PROMPT_POLICY, OWNERSHIP_PROMPT_POLICY, REFERENCE_PROMPT_POLICY, TYPED_PROMPT_POLICY, PROMPT_POLICY}
    explicit_identity = prompt_policy in {IDENTITY_PROMPT_POLICY, OWNERSHIP_PROMPT_POLICY, REFERENCE_PROMPT_POLICY, TYPED_PROMPT_POLICY, PROMPT_POLICY}
    bound_ownership = prompt_policy in {OWNERSHIP_PROMPT_POLICY, REFERENCE_PROMPT_POLICY, TYPED_PROMPT_POLICY, PROMPT_POLICY}
    document = copy.deepcopy(validate_story(document))
    require(document["schema"] == "VCD_SCENE_DOCUMENT_V1", "INPUT_KIND", "/", "Scene document required")
    scene_emd = normalize(scene_emd)
    require(parse(scene_emd) == document, "ARTIFACT_MISMATCH", "/", "Scene EMD and typed document differ")
    translator = translator or EnglishOnlyTranslator()
    for asset in assets:
        validate(asset, "asset")
    registry = unique(assets, "assets")
    trace, translation_cache, shots, segments, diagnostics = [], {}, [], [], []
    output_start, previous = 0, None
    background_map = {b["id"]: b for b in document["backgrounds"]}
    actor_map = {a["id"]: a for a in document["actors"]}
    prop_map = {p["id"]: p for p in document.get("props", [])}
    beat_map = {b["id"]: b for b in document["beats"]}
    actor_references = ActorReferences(document["actors"])

    for index, scene in enumerate(document["scenes"], 1):
        emit_progress("unit", stage="compile", index=index - 1, total=len(document["scenes"]), label=scene["id"])
        scene = copy.deepcopy(scene)
        if explicit_identity:
            # Reject an unresolved multi-person source before any translation calls.
            bind_scene_actor_references(scene, document["actors"])
        scene["actor_ids"] = [a["id"] for a in document["actors"] if a["id"] in scene["actor_ids"]]
        visible, speakers = scene_roles(scene)
        used = referenced_actors(scene) if prompt_policy in {REFERENCE_PROMPT_POLICY, TYPED_PROMPT_POLICY, PROMPT_POLICY} else None
        selected = used if used is not None else (visible | speakers if separate_roles else scene["actor_ids"])
        active_ids = [a for a in scene["actor_ids"] if a in selected]
        prop_ids = [p for p in prop_map if p in visible_props(scene)]
        subject_map = {a: f"<Subject {i}>" for i, a in enumerate(active_ids + prop_ids, 1)}
        subject_roles = ({subject_map[identifier]: "actor" if identifier in active_ids else "prop"
                          for identifier in active_ids + prop_ids} if prompt_policy in {TYPED_PROMPT_POLICY, PROMPT_POLICY} else None)
        role_key = tuple(sorted(subject_roles.items())) if subject_roles is not None else None
        bindings = bind_references(document, scene, registry, separate_roles=separate_roles, used_actor_ids=used)
        if separate_roles and "visible_actor_ids" not in scene:
            diagnostics.append(dict(code="LEGACY_SCENE_VISIBILITY", path=scene["id"],
                message="No visible_actor_ids; cast treated as visible. Regenerate Scene details to separate visible actors and speakers."))

        def translate(source, *, visual=False, owner_id=None):
            explicit_people = explicit_identity and visual and len(scene["actor_ids"]) > 1
            if explicit_identity:
                source = actor_references.bind(source, scene["id"])
                if explicit_people:
                    no_person_pronouns(source, scene["id"])
                allowed = (visible if visual else set(active_ids)) | set(prop_ids)
                require(set(SYMBOL.findall(outside_visible_text(source))) <= allowed, "INACTIVE_PROMPT_ACTOR", scene["id"],
                        "Visual prose must use visible actors only; keep off-screen speakers in DIALOGUE. Regenerate Scene details without changing authored dialogue.")
            require(all(m[1] in subject_map for m in SYMBOL.finditer(outside_visible_text(source))), "INACTIVE_PROMPT_ACTOR", scene["id"],
                    "Prompt prose references an actor who is neither visible nor speaking in this Scene. Regenerate visual descriptions using visible actors only.")
            resolved = map_outside_visible_text(source, lambda text: SYMBOL.sub(lambda m: subject_map[m[1]], text))
            owner_subject = subject_map.get(owner_id) if bound_ownership else None
            pronoun_owner = bound_pronoun_owner(resolved, owner_subject, subject_roles=subject_roles) if bound_ownership else None
            cache_key = (resolved, owner_subject, explicit_people, role_key) if bound_ownership else resolved
            if cache_key not in translation_cache:
                parts, values, last = [], [], 0
                for match in PROTECTED_PROSE.finditer(resolved):
                    parts.append(resolved[last:match.start()])
                    values.append(match[0])
                    last = match.end()
                parts.append(resolved[last:])
                # MV's whole-field implementation preserves reference ownership and word order.
                try:
                    if explicit_identity and hasattr(translator, "translate_actor_protected"):
                        options = dict(owner_subject=owner_subject, allow_bound_pronouns=True) if bound_ownership else {}
                        if subject_roles is not None:
                            options["subject_roles"] = subject_roles
                        result = translator.translate_actor_protected(tuple(parts), tuple(values), document["actors"], explicit_people=explicit_people, **options)
                    elif hasattr(translator, "translate_protected"):
                        result = translator.translate_protected(tuple(parts), tuple(values))
                    else:
                        require(not values or not JAPANESE.search("".join(parts)), "PROTECTED_TRANSLATOR_REQUIRED", scene["id"], "Japanese with references requires a protected-field translator")
                        result = translator(resolved)
                except VCError as exc:
                    d = exc.diagnostic
                    if d.path != "translation":
                        raise
                    reason, separator, log = d.message.partition("; 応答ログ:")
                    location = f"Scene {scene['id']}" + (f" / 対象 @{owner_id}" if owner_id else "")
                    reason += "\n翻訳箇所: " + location
                    raise VCError(d.code, owner_id or scene["beat_id"], reason + separator + log, d.line) from exc
                require(isinstance(result, str) and bool(result.strip()), "TRANSLATION", scene["id"], "Empty translation")
                require(not JAPANESE.search(NATIVE.sub("", outside_visible_text(result))), "TRANSLATION", scene["id"], "Untranslated Japanese")
                require(sorted(NATIVE.findall(outside_visible_text(result))) == sorted(v for v in values if NATIVE.fullmatch(v)), "TRANSLATION_REFERENCE", scene["id"], "Translator changed references")
                require(Counter(VISIBLE_TEXT.findall(result)) == Counter(VISIBLE_TEXT.findall(resolved)),
                        "VISIBLE_TEXT_CHANGED", scene["id"], "Translator changed authored visible text")
                # Translation may not introduce speech, music permissions, or raw aliases.
                translation_cache[cache_key] = result.strip()
                trace.append(dict(source=resolved, translated=result.strip(),
                    **(dict(owner_subject=owner_subject, explicit_people=explicit_people) if bound_ownership else {}),
                    **(dict(subject_roles=subject_roles) if subject_roles is not None else {})))
            from ..artifacts.validation import _prose
            if explicit_identity:
                actor_references.validate_translation(translation_cache[cache_key], scene["id"], explicit_people=explicit_people, pronoun_owner=pronoun_owner,
                                                     subject_roles=subject_roles)
            _prose(NATIVE.sub("", translation_cache[cache_key]), scene["id"], [], cues=True, music_allowed=scene["audio"]["bgm"] != "none")
            return render_visible_text(translation_cache[cache_key])

        raw, context = lengths(scene["end_ms"] - scene["start_ms"], scene["transition"] == "continue")
        audio_context = audio_context_frames(scene["audio"], previous, context, bindings)
        all_events = [e for shot in scene["shots"] for e in shot["events"]] + scene["sound_events"]
        timings = [event_timing(e, context, output_start) for e in all_events]
        timing_map = {e["event_id"]: e for e in timings}

        def interval(e):
            t = timing_map[e["id"]]
            # Integer half-up frame->ms conversion, same raw boundaries as metadata.
            return f"[{timestamp((t['raw_start_frame'] * 1000 + 12) // 24)} --> {timestamp((t['raw_end_frame'] * 1000 + 12) // 24)}]"

        definitions = []
        descriptions = {s["actor_id"]: s["text"] for s in scene["subject_descriptions"]}
        for actor_id in active_ids:
            actor = actor_map[actor_id]
            if separate_roles and actor_id not in visible:
                # Off-screen references never activate visual assets. The
                # character's identity text does not itself permit speech.
                voice = [b["token"] for b in bindings if b["role"] == "voice" and b["asset_id"] == actor["voice_asset_id"]]
                identity = "An off-screen speaker." if actor_id in speakers else "An off-screen person."
                if prompt_policy in {REFERENCE_PROMPT_POLICY, TYPED_PROMPT_POLICY, PROMPT_POLICY}:
                    # This actor is actually referenced. Retain the Scene's
                    # identity description (e.g. woman), rather than replacing
                    # it with an anonymous speaker. Visibility and speech
                    # permissions remain structural and independent.
                    identity += " " + translate(descriptions[actor_id], owner_id=actor_id)
                definitions.append(f"{subject_map[actor_id]}: {identity}" +
                                   (" Voice timbre reference: " + " ".join(voice) + "." if voice else ""))
                continue
            refs = [b["token"] for b in bindings if b["asset_id"] in {actor["image_asset_id"], actor["video_asset_id"], actor["voice_asset_id"]} and b["role"] != "video_audio"]
            if separate_roles:
                refs = [b["token"] for b in bindings if b["asset_id"] in {actor["image_asset_id"], actor["video_asset_id"]} and b["role"] in {"identity", "motion"}]
            definition = f"{subject_map[actor_id]}: {translate(descriptions[actor_id], owner_id=actor_id)}" + (f" Reference: {' '.join(refs)}." if refs else "")
            if separate_roles:
                voice = [b["token"] for b in bindings if b["role"] == "voice" and b["asset_id"] == actor["voice_asset_id"]]
                if voice:
                    definition += " Voice timbre reference: " + " ".join(voice) + "."
            definitions.append(definition)
        for prop_id in prop_ids:
            prop = prop_map[prop_id]
            refs = [b["token"] for b in bindings if b["role"] == "prop" and b["asset_id"] == prop["image_asset_id"]]
            definition = f"{subject_map[prop_id]}: Prop. {translate(prop['description'], visual=True, owner_id=prop_id)}"
            if prop["size"]:
                definition += " Fixed size and scale: " + translate(prop["size"], visual=True, owner_id=prop_id)
            if refs:
                definition += " Appearance reference: " + " ".join(refs) + "."
                if prop.get("reference_hint"):
                    definition += " Object within that reference image: " + translate(prop["reference_hint"])
                    definition += ". This selects the reference object; it does not set placement in the target Scene."
            definitions.append(definition)
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
                elif e["kind"] == "vocal":
                    details.append(f"{interval(e)} NONVERBAL {owner}: {vocal_text(e, scene['audio'])}")
                else:
                    details.append(f"{interval(e)} {e['kind'].upper()} {owner}: {translate(e['text'], visual=True, owner_id=e['actor_id'])}")
        compact = prompt_policy in {COMPACT_PROMPT_POLICY, RESOLVED_AUDIO_PROMPT_POLICY, VISUAL_PROMPT_POLICY, ROLE_PROMPT_POLICY, IDENTITY_PROMPT_POLICY, OWNERSHIP_PROMPT_POLICY, REFERENCE_PROMPT_POLICY, TYPED_PROMPT_POLICY, PROMPT_POLICY} and scene["audio"]["bgm"] == "none"
        sounds = [scene_soundscape(scene, bindings) if prompt_policy in {RESOLVED_AUDIO_PROMPT_POLICY, VISUAL_PROMPT_POLICY, ROLE_PROMPT_POLICY, IDENTITY_PROMPT_POLICY, OWNERSHIP_PROMPT_POLICY, REFERENCE_PROMPT_POLICY, TYPED_PROMPT_POLICY, PROMPT_POLICY} else
                  directives(scene["audio"], explicit=prompt_policy in {EXPLICIT_PROMPT_POLICY, COMPACT_PROMPT_POLICY}, compact=compact)]
        if compact and any(b["role"] in {"voice", "music", "ambience", "effects", "video_audio"} for b in bindings):
            sounds.append("Audio references condition timbre and sound character only; do not copy or recite their content.")
        for key in ("ambience", "effects"):
            if scene["audio"][key + "_description"]:
                sounds.append(key.capitalize() + ": " + translate(scene["audio"][key + "_description"]))
        sounds.extend(f"{interval(e)} {e['kind'].upper()}: {translate(e['text'])}" for e in scene["sound_events"])
        sounds.extend(f"{b['role']} reference: {b['token']}" for b in bindings if b["role"] in {"music", "ambience", "effects", "video_audio"})
        music = (NO_MUSIC if prompt_policy == EXPLICIT_PROMPT_POLICY else "N/A") if scene["audio"]["bgm"] == "none" else translate(scene["audio"]["bgm_description"]) + ". Instrumental only."
        summary = translate(scene["summary"], visual=True)
        def direction_text(d):
            if prompt_policy == RESOLVED_AUDIO_PROMPT_POLICY:
                # DIALOGUE owns who speaks and when, including Scenes where
                # both ends of a telephone call have authored lines.
                narrowed = narrow_silent_direction(d)
                if narrowed != d:
                    diagnostics.append(dict(code="AUDIO_DIRECTION_NARROWED", path=scene["id"],
                                            message=canonical(dict(source=d, prompt_direction=narrowed))))
                d = narrowed
            return translate(d, visual=True) if d else ""

        if prompt_policy in {VISUAL_PROMPT_POLICY, ROLE_PROMPT_POLICY, IDENTITY_PROMPT_POLICY, OWNERSHIP_PROMPT_POLICY, REFERENCE_PROMPT_POLICY, TYPED_PROMPT_POLICY, PROMPT_POLICY} and "visual_directions" in scene:
            directions = [translate(d, visual=True) for d in scene["visual_directions"]]
        else:
            if prompt_policy in {VISUAL_PROMPT_POLICY, ROLE_PROMPT_POLICY, IDENTITY_PROMPT_POLICY, OWNERSHIP_PROMPT_POLICY, REFERENCE_PROMPT_POLICY, TYPED_PROMPT_POLICY, PROMPT_POLICY}:
                diagnostics.append(dict(code="LEGACY_VISUAL_DIRECTIONS", path=scene["id"],
                    message="No generated visual_directions; source directions retained. Regenerate Scene details to separate visual and audio instructions."))
            directions = [value for d in document["direction"] if (value := direction_text(d))]
            directions.extend("Scene-specific visual direction: " + value
                              if prompt_policy in {RESOLVED_AUDIO_PROMPT_POLICY, VISUAL_PROMPT_POLICY, ROLE_PROMPT_POLICY, IDENTITY_PROMPT_POLICY, OWNERSHIP_PROMPT_POLICY, REFERENCE_PROMPT_POLICY, TYPED_PROMPT_POLICY, PROMPT_POLICY} else "Scene-specific direction (takes precedence): " + value
                              for d in beat_map[scene["beat_id"]].get("direction", []) if (value := direction_text(d)))
        retention_lines = [f"{subject_map[r['actor_id']]}: {r['mode']}" for r in scene["retention"]
                           if r["actor_id"] in subject_map and (not separate_roles or r["actor_id"] in visible)]
        retention_lines += [f"{subject_map[p]}: fully_preserved - preserve the defined shape, color and scale." for p in prop_ids]
        continuity = []
        if prompt_policy == PROMPT_POLICY and context:
            continuity = ["Continue seamlessly from the preceding video context at its ending composition. "
                          "Carry forward the visible subjects' positions, poses, held objects, lighting and camera axis. "
                          "Continue the ongoing motion from that endpoint into the timed actions below."]
        sections = [("subject_definitions", definitions), ("summary", ["[reference generation] " + summary]),
                    ("retention_analysis", retention_lines or ["N/A"]),
                    ("detailed_description", continuity + directions + details), ("overall_soundscape", sounds), ("non_diegetic_music", [music])]
        prompt = "\n\n".join(title + ":\n" + "\n".join(values) for title, values in sections)
        if scene["audio"].get("bgm_suppression", "prompt") == "negpip":
            prompt += "\n\n" + NEGPIP_MUSIC
        require(re.findall(r"<d>.*?</d>", prompt, re.S) == speech, "DIALOGUE_INTEGRITY", scene["id"], "Compiled speech differs from authored speech")
        plan_shot = dict(id=scene["id"], length=raw, prompt=prompt, context_length=context, audio_context_length=audio_context)
        if context or audio_context:
            plan_shot["continuation_mode"] = "guide"
        shots.append(plan_shot)
        segments.append(dict(id=scene["id"], scene_id=scene["id"], index=index, planned_start_ms=scene["start_ms"], planned_end_ms=scene["end_ms"],
                             start_frame=output_start, end_frame=output_start + raw - context, raw_length=raw, context_frames=context, audio_context_frames=audio_context,
                             local_subjects=[dict(actor_id=a, subject_number=i) for i, a in enumerate(active_ids, 1)], reference_bindings=bindings, event_timing=timings,
                             audio=scene["audio"], audio_output="silence" if all(scene["audio"][k] == "none" for k in ("vocal", "bgm", "ambience", "effects")) else "generated"))
        if prop_ids:
            segments[-1]["local_props"] = [dict(prop_id=p, subject_number=len(active_ids) + i)
                                            for i, p in enumerate(prop_ids, 1)]
        output_start += raw - context
        previous = dict(audio=scene["audio"], bindings=bindings)
        emit_progress("done", stage="compile", index=index - 1, total=len(document["scenes"]))
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
    if any(s["audio"].get("continuity", "auto") != "auto" for s in document["scenes"]):
        runtime.update(independent_audio_contract=CONTRACT["independent_audio"]["contract_id"],
                       independent_audio_fingerprint=CONTRACT["independent_audio"]["source_fingerprint"],
                       output_sample_rate=CONTRACT["independent_audio"]["native_sample_rate"])
    if replay_output_sample_rate is not None:
        # Older saved revisions declared 48kHz; retain their metadata on replay.
        require(type(replay_output_sample_rate) is int and replay_output_sample_rate > 0,
                "SAMPLE_RATE", "/", "Saved output sample rate must be a positive integer")
        runtime["output_sample_rate"] = replay_output_sample_rate
    execution = dict(schema="VCD_EXECUTION_V1", project_id=document["project_id"], scene_emd_sha256=digest(scene_emd), plan_sha256=digest(canonical(plan)),
                     runtime_contract=runtime, assets=list(assets), segments=segments, reference_schedule=schedule, output_audio="generated")
    validate(execution, "execution")
    metadata = dict(schema="VCD_RUN_METADATA_V1", compiler_version=__version__, prompt_policy_version=prompt_policy,
                    scene_emd_sha256=digest(scene_emd), plan_sha256=execution["plan_sha256"],
                    execution_sha256=digest(canonical(execution)), translation_trace=trace, output_frames=output_start, diagnostics=diagnostics)
    return CompileResult(scene_emd, document, plan, execution, metadata)
