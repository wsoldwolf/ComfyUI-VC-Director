"""Scenario interpretation and author-owned requirements."""
import copy
import json

from ..errors import VCError, require
from ..utilities import canonical, digest
from ..utilities.progress import emit_progress
from ..artifacts.schema import validate
from ..artifacts.validation import unique, validate_story
from ..artifacts.identifiers import resolve_required_event_ids
from ..vision.image_facts import apply_image_facts


class StoryEnhancer:
    def __init__(self, runner):
        self.runner = runner

    def enhance(self, scenario, image_facts=()):
        require(scenario.get("schema") == "VCD_SCENARIO_V1", "INPUT_KIND", "/schema",
                "EnhancerにはVCD_SCENARIO_EMD_V1のシナリオが必要です。シーンEMDはsaved_sceneでコンパイルしてください。")
        scenario = apply_image_facts(scenario, image_facts)
        validate_story(scenario)
        actors = {a["id"] for a in scenario["actors"]}
        backgrounds = {b["id"] for b in scenario["backgrounds"]}
        for fact in image_facts:
            validate(fact, "image_fact")
            require(fact["entity_id"] in (actors if fact["entity_kind"] == "actor" else backgrounds), "IMAGE_FACT", fact["entity_id"], "Unknown entity")

        def check(result, sources=None):
            sources = scenario["beats"] if sources is None else sources
            require(result["project_id"] == scenario["project_id"] and result["scenario_sha256"] == scenario["source_sha256"], "ENHANCED_SOURCE", "/", "Wrong scenario identity")
            require(result["constraints"] == scenario["constraints"], "AUTHOR_CONSTRAINT", "/", "Author constraints changed")
            expected = [b["id"] for b in sources]
            received = [b["id"] for b in result["beats"]]
            require(received == expected, "ENHANCED_BEATS", "/beats",
                    f"Return every authored beat exactly once in this order: {expected}; received: {received}. "
                    "Do not stop after the first beat, omit beats, add beats or reorder them.")
            remapped = resolve_required_event_ids(scenario, result)
            if remapped:
                self.runner.trace.append(dict(stage="required_event_ids", source="enhance", remapped=remapped))
            unique([e for b in result["beats"] for e in b["required_events"]], "required_events")
            for beat, source in zip(result["beats"], sources):
                for event in beat["required_events"]:
                    span = event["source"]
                    first, last = source["source"]["first_line"], source["source"]["last_line"]
                    reasons = []
                    if event["beat_id"] != source["id"]:
                        reasons.append(f"展開ID: 必要={source['id']}、実際={event['beat_id']}")
                    if span["document"] != "scenario":
                        reasons.append(f"文書種別: 必要=scenario、実際={span['document']}")
                    if not first <= span["first_line"] <= span["last_line"] <= last:
                        reasons.append(f"出典行: 許容={first}〜{last}行、実際={span['first_line']}〜{span['last_line']}行"
                                       + ("（開始行と終了行が逆転）" if span["first_line"] > span["last_line"] else ""))
                    require(not reasons, "ENHANCED_EVENT_SOURCE", event["id"],
                            f"必須出来事 {event['id']} の出典が対象展開 {source['id']} と一致しません。"
                            + "；".join(reasons) + "。入力EMD全体の行番号で指定し、別の展開の出来事を混ぜないでください。")

        payload = dict(scenario=scenario, image_facts=list(image_facts))
        splittable = len(scenario["beats"]) > 1
        split_key, saved_strategy = None, None
        if splittable and self.runner.cache:
            from ..protocols.llm import system_prompt
            identity = self.runner.backend.identity()
            split_key = digest(canonical(dict(version="enhance-per-beat-v1", payload=payload, model=identity,
                system=system_prompt("enhance", payload, native_schema=callable(getattr(self.runner.backend, "complete_record", None))))))
            saved_strategy = self.runner.cache.get(split_key)
            if saved_strategy and saved_strategy.get("strategy") != "per_beat":
                saved_strategy = None

        def per_beat():
            hint_saved = bool(saved_strategy)
            self.runner.trace.append(dict(stage="enhance_strategy", strategy="per_beat",
                                          cache="hit" if saved_strategy else "recovery"))
            combined = dict(schema="VCD_ENHANCED_STORY_V1", project_id=scenario["project_id"],
                scenario_sha256=scenario["source_sha256"], constraints=copy.deepcopy(scenario["constraints"]),
                intent="", beats=[])
            for index, beat in enumerate(scenario["beats"]):
                emit_progress("unit", stage="enhance", index=index, total=len(scenario["beats"]), label=beat["id"])
                part = self.runner.run("enhance", beat["id"], dict(payload, enhance_beat_id=beat["id"]),
                                       lambda value, source=beat: check(value, [source]))
                if not combined["intent"]:
                    combined["intent"] = part["intent"]
                combined["beats"].extend(copy.deepcopy(part["beats"]))
                if split_key is not None and not hint_saved:
                    # This is a strategy hint backed by a completed, validated
                    # unit, never an invalid/incomplete EnhancedStory artifact.
                    self.runner.cache.put_success(split_key, dict(strategy="per_beat"))
                    hint_saved = True
                emit_progress("done", stage="enhance", index=index, total=len(scenario["beats"]))
            validate(combined, "enhanced_story")
            check(combined)
            return combined

        if saved_strategy and saved_strategy.get("strategy") == "per_beat":
            result = per_beat()
        else:
            emit_progress("unit", stage="enhance", label=scenario["project_id"])
            try:
                result = self.runner.run("enhance", scenario["project_id"], payload, check,
                                         split_on_incomplete=splittable)
            except VCError as error:
                recoverable = error.diagnostic.code == "ENHANCE_SPLIT_REQUIRED"
                if error.diagnostic.code == "LLM_RETRIES_EXHAUSTED":
                    try:
                        failures = json.loads(error.diagnostic.message.split("; 応答ログ:", 1)[0])
                        recoverable = any(item.get("code") in {"json", "json_incomplete", "generation_truncated"}
                                          for item in failures)
                    except (ValueError, TypeError, AttributeError):
                        pass
                if not splittable or not recoverable:
                    raise
                emit_progress("restart", label="補強を展開ごとに分割して再開")
                result = per_beat()
        emit_progress("stage_done", stage="enhance")
        return result
