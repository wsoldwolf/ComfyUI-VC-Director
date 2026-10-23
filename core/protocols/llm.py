"""Versioned planning protocol, request validation and bounded retries."""
import json
import re
from pathlib import Path

from ..errors import VCError, require
from ..artifacts.schema import schema, validate
from ..utilities import canonical, digest
from ..inference.cache import SuccessCache

PROMPT_VERSION = "vcd-story-v1"
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


def system_prompt(stage):
    template = (Path(__file__).resolve().parents[2] / "prompts/story_system_prompt.txt").read_text(encoding="utf-8")
    return template.format(stage=stage, record_type=RECORDS[stage], payload_schema=canonical(_payload_schema(stage)))


class StageRunner:
    def __init__(self, backend, cache_root=None):
        self.backend = backend
        self.cache = SuccessCache(cache_root) if cache_root else None
        self.trace = []
        self._identity = None

    def run(self, stage, target_id, payload, check):
        if self._identity is None:
            self._identity = self.backend.identity()
        system = system_prompt(stage)
        base = dict(schema="VCD_LLM_REQUEST_V1", request_id="req_" + digest(canonical([stage, target_id, payload]))[:24], stage=stage,
                    targets=[dict(slot=1, target_id=target_id)], retry="initial", errors=[], payload=payload)
        key = digest(canonical(dict(prompt_version=PROMPT_VERSION, system=system, model=self._identity, request=base)))
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
        errors = []
        for attempt in ("initial", "repair", "isolated"):
            request = dict(base, retry=attempt, errors=errors)
            validate(request, stage + "_request")
            raw = self.backend.complete(system, canonical(request))
            self.trace.append(dict(stage=stage, target_id=target_id, attempt=attempt, raw=raw, key=key))
            try:
                records = []
                for line in raw.splitlines():
                    if not line.strip():
                        continue
                    parts = line.split("\t", 2)
                    require(len(parts) == 3 and parts[1].isdigit(), "PROTOCOL", target_id, "Expected TYPE<TAB>SLOT<TAB>JSON")
                    records.append(dict(record_type=parts[0], slot=int(parts[1]), payload=json.loads(parts[2])))
                response = dict(schema="VCD_LLM_RESPONSE_V1", request_id=base["request_id"], stage=stage, records=records)
                validate(response, stage + "_response")
                self._check_records(response, stage)
                result = records[0]["payload"]
                check(result)
                if self.cache:
                    self.cache.put_success(key, dict(response=response))
                return result
            except (VCError, json.JSONDecodeError) as exc:
                d = exc.diagnostic if isinstance(exc, VCError) else None
                errors = [dict(target_id=target_id, path=d.path if d else "/", code=d.code.lower() if d else "json", message=d.message if d else str(exc))]
        raise VCError("LLM_RETRIES_EXHAUSTED", target_id, canonical(errors))

    @staticmethod
    def _check_records(response, stage):
        require(len(response["records"]) == 1 and response["records"][0]["slot"] == 1 and response["records"][0]["record_type"] == RECORDS[stage], "PROTOCOL", stage, "Expected exactly the requested record and slot")
