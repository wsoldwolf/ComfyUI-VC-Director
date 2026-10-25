"""Versioned planning protocol, request validation and bounded retries."""
import copy
import json
import logging
import re
import uuid
from pathlib import Path

from ..errors import VCError, require
from ..artifacts.schema import schema, validate
from ..utilities import canonical, digest, normalize
from ..utilities.diagnostics import save_diagnostic
from ..inference.cache import SuccessCache

PROMPT_VERSION = "vcd-story-v4-complete-beats"
STAGE_PROMPT_VERSIONS = dict(detail="vcd-story-v7-explicit-camera")
LOGGER = logging.getLogger("vc_director.planning")
RECORDS = dict(enhance="ENHANCED", outline="SCENES", detail="DETAIL", retime="TIMING")
DEFINITIONS = dict(enhance="enhanced_story", outline="scene_outline", detail="scene", retime="timing_patch")

def _payload_schema(stage):
    name = DEFINITIONS[stage]
    # Include only reachable definitions, preserving the exact published contract.
    needed, pending = {}, [name]
    while pending:
        key = pending.pop()
        if key in needed:
            continue
        needed[key] = schema()["$defs"][key]
        pending.extend(re.findall(r'"\$ref":"#/\$defs/([^\"]+)"', canonical(needed[key])))
    result = {"$ref": "#/$defs/" + name, "$defs": needed}
    if stage == "outline":
        result = dict(type="object", properties=dict(scenes=dict(type="array", items={"$ref": "#/$defs/scene_outline"}, minItems=1)), required=["scenes"], additionalProperties=False, **{"$defs": needed})
    return result


def _remaining_dialogues(payload):
    beat = next(beat for beat in payload["scenario"]["beats"] if beat["id"] == payload["outline"]["beat_id"])
    used = {e["dialogue_id"] for previous in payload["previous_scenes"] for shot in previous["shots"] for e in shot["events"] if e["kind"] == "dialogue"}
    return beat, [d for d in beat["dialogues"] if d["id"] not in used]


def system_prompt(stage, payload=None):
    directory = Path(__file__).resolve().parents[2] / "prompts"
    template = (directory / "story_system_prompt.txt").read_text(encoding="utf-8")
    instructions = (directory / f"story_{stage}_prompt.txt").read_text(encoding="utf-8")
    if stage == "detail" and payload is not None:
        outline = payload["outline"]
        duration = outline["end_ms"] - outline["start_ms"]
        beat, dialogues = _remaining_dialogues(payload)
        instructions += (f"\n今回のSceneは{outline['id']}。Scene自体の絶対時刻は{outline['start_ms']}〜{outline['end_ms']}ms、"
                         f"長さは{duration}msです。Shot/event/sound_eventは全て0〜{duration}ms内の相対時刻です。"
                         f"各start_ms < end_msを守り、長さ0のShot/eventを作りません。"
                         f"一つのShotを0〜{duration}msにして、その中へ複数eventを置いて構いません。\n"
                         f"Shot IDは{outline['id']}_shot_1等、動作IDは{outline['id']}_action_1等を使い、"
                         "previous_scenesのIDを再使用しません。作者の台詞IDだけはそのまま使います。\n"
                         "今回の音響許可の上限: " + canonical(beat["audio"]) + "\n"
                         "今回配置できる未使用台詞ID: " + canonical([d["id"] for d in dialogues]) + "\n")
    return template.format(stage=stage, record_type=RECORDS[stage], stage_instructions=instructions, payload_schema=canonical(_payload_schema(stage)))


def generation_schema(stage, payload):
    """Narrow generation to immutable author/planner values; invent no content."""
    result = copy.deepcopy(_payload_schema(stage))
    if stage == "enhance":
        definitions = result["$defs"]
        scenario = payload["scenario"]
        story = definitions["enhanced_story"]["properties"]
        for key, value in dict(project_id=scenario["project_id"], scenario_sha256=scenario["source_sha256"],
                               constraints=scenario["constraints"]).items():
            story[key] = {"const": value}
        beats = []
        for source in scenario["beats"]:
            beat = copy.deepcopy(definitions["enhanced_beat"])
            beat["properties"]["id"] = {"const": source["id"]}
            event = copy.deepcopy(definitions["required_event"])
            event["properties"]["beat_id"] = {"const": source["id"]}
            span = copy.deepcopy(definitions["source_span"])
            span["properties"]["document"] = {"const": "scenario"}
            for key in ("first_line", "last_line"):
                span["properties"][key] = dict(type="integer", minimum=source["source"]["first_line"],
                                              maximum=source["source"]["last_line"])
            event["properties"]["source"] = span
            beat["properties"]["required_events"]["items"] = event
            beats.append(beat)
        # Tuple items require every authored beat exactly once, in source order.
        # llama.cpp supports prefixItems; items=false also forbids extra entries
        # when validating this narrowing with the JSON Schema 2020-12 validator.
        story["beats"] = dict(type="array", prefixItems=beats, items=False,
                              minItems=len(beats), maxItems=len(beats))
        return result
    if stage != "detail":
        return result
    definitions = result["$defs"]
    scene = definitions["scene"]
    for key, value in payload["outline"].items():
        scene["properties"][key] = {"const": value}
    duration = payload["outline"]["end_ms"] - payload["outline"]["start_ms"]
    for name in ("shot", "event", "sound_event"):
        properties = definitions[name]["properties"]
        properties["start_ms"] = dict(type="integer", minimum=0, maximum=duration - 1)
        properties["end_ms"] = dict(type="integer", minimum=1, maximum=duration)
    beat, dialogues = _remaining_dialogues(payload)
    audio = definitions["audio_policy"]["properties"]
    for key in ("vocal", "bgm", "ambience", "effects"):
        if beat["audio"][key] == "none" or key == "vocal" and not dialogues:
            audio[key] = {"const": "none"}
            if key != "vocal":
                audio[key + "_description"] = {"const": None}
    references = beat["audio"]["reference_audio"]
    audio["reference_audio"] = (dict(type="array", items={"enum": references}, maxItems=len(references), uniqueItems=True)
                                if references else {"const": []})
    kinds = [kind for kind, key in (("effect", "effects"), ("ambience", "ambience")) if beat["audio"][key] != "none"]
    if kinds:
        definitions["sound_event"]["properties"]["kind"] = {"enum": kinds}
    else:
        scene["properties"]["sound_events"] = {"const": []}
    # Keep the converter's JSON string rule. Its pattern-string rules do not escape
    # embedded quotes, so a broad negated pattern can accidentally permit broken JSON.
    prose = dict(type="string", minLength=1)
    scene["properties"]["summary"] = prose
    event = definitions["event"]
    event["properties"] = dict(kind=event["properties"]["kind"], **{key: value for key, value in event["properties"].items() if key != "kind"})
    visual = copy.deepcopy(event)
    visual["properties"].update(kind={"enum": ["action", "camera"]}, dialogue_id={"const": None}, language={"const": None}, text=prose)
    alternatives = [visual]
    for dialogue in dialogues:
        spoken = copy.deepcopy(event)
        fixed = dict(kind="dialogue", id=dialogue["id"], dialogue_id=dialogue["id"], actor_id=dialogue["actor_id"], text=dialogue["text"], language=dialogue["language"])
        spoken["properties"].update({key: {"const": value} for key, value in fixed.items()})
        alternatives.append(spoken)
    definitions["event"] = {"oneOf": alternatives}
    # Prefix enforcement works with the native grammar converter and leaves the
    # remaining authored dialogue/action events available in the same Shot.
    camera = copy.deepcopy(visual)
    camera["properties"].update(kind={"const": "camera"},
                                 actor_id={"enum": payload["outline"]["actor_ids"] + ["environment"]})
    definitions["shot"]["properties"]["events"] = dict(
        type="array", prefixItems=[camera], items={"$ref": "#/$defs/event"}, minItems=1)
    return result


def parse_response(raw, stage, request_id, target_id):
    """Remove only a complete transport fence; never trim arbitrary JSON suffixes."""
    text = normalize(raw).strip()
    fenced = re.fullmatch(r"```(?:json|text)?[ \t]*\n(.*)\n```", text, re.S)
    if fenced:
        text = fenced[1].strip()
    header = re.match(r"([A-Z_]+)\t([0-9]+)\t", text)
    require(header is not None, "PROTOCOL", target_id, "Expected TYPE<TAB>SLOT<TAB>JSON")
    payload_text = text[header.end():].lstrip()
    payload, end = json.JSONDecoder().raw_decode(payload_text)
    require(not payload_text[end:].strip(), "PROTOCOL_TRAILING", target_id,
            f"JSONの後ろに余分な出力があります（JSON終端: {end}）。指定record一件だけを返してください。")
    return dict(schema="VCD_LLM_RESPONSE_V1", request_id=request_id, stage=stage,
                records=[dict(record_type=header[1], slot=int(header[2]), payload=payload)])


class StageRunner:
    def __init__(self, backend, cache_root=None, *, diagnostics_root=None):
        self.backend = backend
        self.cache = SuccessCache(cache_root) if cache_root else None
        self.trace = []
        self._identity = None
        self.diagnostics_root = Path(diagnostics_root) if diagnostics_root is not None else (Path(cache_root) / "diagnostics" if cache_root else None)

    @staticmethod
    def _save_diagnostic(path, diagnostic):
        return save_diagnostic(path, diagnostic, LOGGER)

    def run(self, stage, target_id, payload, check):
        prompt_version = STAGE_PROMPT_VERSIONS.get(stage, PROMPT_VERSION)
        base = dict(schema="VCD_LLM_REQUEST_V1", request_id="req_" + digest(canonical([stage, target_id, payload]))[:24], stage=stage,
                    targets=[dict(slot=1, target_id=target_id)], retry="initial", errors=[], payload=payload)
        validate(base, stage + "_request")
        system = system_prompt(stage, payload)
        if self._identity is None:
            self._identity = self.backend.identity()
        key = digest(canonical(dict(prompt_version=prompt_version, system=system, model=self._identity, request=base)))
        cached = self.cache.get(key) if self.cache else None
        if cached is not None:
            try:
                response = validate(cached["response"], stage + "_response")
                self._check_records(response, stage)
                check(response["records"][0]["payload"])
                self.trace.append(dict(stage=stage, target_id=target_id, cache="hit", key=key))
                return response["records"][0]["payload"]
            except (VCError, KeyError, TypeError):
                pass
        diagnostic_path = self.diagnostics_root / f"{stage}-{key[:12]}-{uuid.uuid4().hex}.json" if self.diagnostics_root else None
        diagnostic = dict(schema="VCD_PLANNING_DIAGNOSTIC_V1", prompt_version=prompt_version,
                          model=self._identity, system_prompt=system, request=base, attempts=[], status="running")
        errors, saved_diagnostic = [], None
        for attempt in ("initial", "repair", "isolated"):
            request = dict(base, retry=attempt, errors=errors)
            validate(request, stage + "_request")
            attempt_trace = dict(attempt=attempt, input_errors=errors)
            diagnostic["attempts"].append(attempt_trace)
            diagnostic["status"] = "running"
            self._save_diagnostic(diagnostic_path, diagnostic)
            LOGGER.info("Planning %s / %s (%s)", stage, target_id, attempt)
            try:
                complete_record = getattr(self.backend, "complete_record", None)
                raw = complete_record(system, canonical(request), RECORDS[stage], payload_schema=generation_schema(stage, payload)) if callable(complete_record) else self.backend.complete(system, canonical(request))
            except Exception as exc:
                diagnostic["status"] = "backend_error"
                attempt_trace["backend_error"] = dict(type=type(exc).__name__, message=str(exc))
                self._save_diagnostic(diagnostic_path, diagnostic)
                raise
            attempt_trace["raw"] = raw
            self.trace.append(dict(stage=stage, target_id=target_id, attempt=attempt, raw=raw, key=key))
            try:
                response = parse_response(raw, stage, base["request_id"], target_id)
                attempt_trace["response"] = response
                validate(response, stage + "_response")
                self._check_records(response, stage)
                result = response["records"][0]["payload"]
                check(result)
                if self.cache:
                    self.cache.put_success(key, dict(response=response))
                diagnostic["status"] = "success"
                self._save_diagnostic(diagnostic_path, diagnostic)
                return result
            except (VCError, json.JSONDecodeError) as exc:
                d = exc.diagnostic if isinstance(exc, VCError) else None
                errors = [dict(target_id=target_id, path=d.path if d else "/", code=d.code.lower() if d else "json", message=d.message if d else str(exc))]
                attempt_trace["errors"] = errors
                diagnostic["status"] = "invalid_response"
                saved_diagnostic = self._save_diagnostic(diagnostic_path, diagnostic)
                LOGGER.warning("Planning %s / %s (%s): %s", stage, target_id, attempt, canonical(errors))
        diagnostic["status"] = "retries_exhausted"
        saved_diagnostic = self._save_diagnostic(diagnostic_path, diagnostic) or saved_diagnostic
        suffix = f"; 応答ログ: {saved_diagnostic}" if saved_diagnostic else ""
        raise VCError("LLM_RETRIES_EXHAUSTED", target_id, canonical(errors) + suffix)

    @staticmethod
    def _check_records(response, stage):
        require(len(response["records"]) == 1 and response["records"][0]["slot"] == 1 and response["records"][0]["record_type"] == RECORDS[stage], "PROTOCOL", stage, "Expected exactly the requested record and slot")
