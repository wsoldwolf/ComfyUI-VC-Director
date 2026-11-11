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
from ..inference.cache_policy import PipelineCache as SuccessCache
from .context import compact_schema, detail_request
from ..artifacts.props import visible_props
from ..artifacts.coverage import beat_requirements
from ..audio.nonverbal import permits_nonverbal, VOICE_TEXT

from ..utilities.progress import emit_progress

PROMPT_VERSION = "vcd-story-v4-complete-beats"
STAGE_PROMPT_VERSIONS = dict(enhance="vcd-story-v5-required-event-ids", detail="vcd-story-v27-shot-timing", outline="vcd-story-v10-dependencies")
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


def _required_dialogues(payload, remaining):
    ids = payload.get("required_dialogue_ids", [])
    require(len(ids) == len(set(ids)) and set(ids) <= {d["id"] for d in remaining},
            "DIALOGUE_ASSIGNMENT", payload["outline"]["id"],
            "Required dialogue IDs must be unique, authored in this beat and not used in previous Scenes")
    return [d for d in remaining if d["id"] in ids]


def _outline_beat(payload):
    accepted = {scene["beat_id"] for scene in payload["accepted_outlines"]}
    beat = next((beat for beat in payload["scenario"]["beats"] if beat["id"] not in accepted), None)
    require(beat is not None, "OUTLINE_TARGET", "/", "No unplanned beat remains")
    return beat


def system_prompt(stage, payload=None, *, native_schema=False):
    directory = Path(__file__).resolve().parents[2] / "prompts"
    template = (directory / "story_system_prompt.txt").read_text(encoding="utf-8")
    instructions = (directory / f"story_{stage}_prompt.txt").read_text(encoding="utf-8")
    if stage == "enhance" and payload is not None and payload.get("enhance_beat_id"):
        source = next(beat for beat in payload["scenario"]["beats"] if beat["id"] == payload["enhance_beat_id"])
        instructions += ("\n今回は出力を分割しています。scenarioには作品全体の原文を保持していますが、"
            "出力beatsには次の展開IDだけを一件含めてください: " + payload["enhance_beat_id"]
            + "。他の展開は文脈の参照だけに使い、今回の必須出来事へ混ぜません。"
            "project_id・scenario_sha256・constraintsは作品全体の値をコピーし、intentは作品全体の意図を簡潔に示します。"
            "後続の展開は別の呼び出しで処理するため、今回へ統合・省略しません。\n"
            f"出典sourceはdocument=scenario、入力EMD全体の{source['source']['first_line']}〜{source['source']['last_line']}行です。"
            "分割対象の先頭を1行目へ振り直しません。first_line <= last_lineを守り、"
            "別の展開から出来事・出典をコピーしません。\n")
    if stage == "outline" and payload is not None:
        beat = _outline_beat(payload)
        previous = payload["accepted_outlines"][-1] if payload["accepted_outlines"] else None
        instructions += ("\n今回の展開の接続指定: " + beat.get("transition", "auto")
                         + "\n今回の展開だけの方針: " + canonical(beat.get("direction", []))
                         + "\n直前Sceneの終端状態: " + canonical(previous["terminal_state"] if previous else []) + "\n")
        if previous and (set(previous["actor_ids"]) != set(beat["actor_ids"])
                         or previous["background_id"] != beat["background_id"]):
            instructions += "直前Sceneと出演者集合または背景が異なるため、今回の最初のSceneは必ずtransition=cutです。continueは使用できません。\n"
        total = payload["scenario"]["duration_ms"]
        window = next((w for w in payload.get("beat_windows", []) if w["beat_id"] == beat["id"]), None)
        if window:
            instructions += (f"全体の時間配分を修正済みです。今回の展開の確定枠は{window['start_ms']}〜{window['end_ms']}msです。"
                             "本文に同じ時間帯が反復されていても加算しません。この枠を隙間なく満たし、"
                             "各Sceneを1〜15000msに分割します。全required_event_idsを一度ずつ配置し、作者の台詞は変更しません。\n")
        if beat is payload["scenario"]["beats"][-1] and total is not None:
            start = previous["end_ms"] if previous else 0
            remaining = total - start
            minimum = max(1, (remaining + 14999) // 15000)
            instructions += (f"今回が最後の展開です。開始は{start}ms、作品の終了は{total}ms、"
                             f"残りは{remaining}msです。1Sceneは最大15000msなので最低{minimum}Scene必要です。"
                             f"全Sceneを隙間なく並べ、最後のend_msを必ず{total}にしてください。"
                             "不足時間を余白として残さず、既存の出来事・動作・間の配分で満たします。新しい台詞や出来事は追加しません。\n")
    if stage == "detail" and payload is not None:
        outline = payload["outline"]
        duration = outline["end_ms"] - outline["start_ms"]
        beat, dialogues = _remaining_dialogues(payload)
        required_dialogues = _required_dialogues(payload, dialogues)
        instructions += (f"\n今回のSceneは{outline['id']}。Scene自体の絶対時刻は{outline['start_ms']}〜{outline['end_ms']}ms、"
                         f"長さは{duration}msです。Shot/event/sound_eventは全て0〜{duration}ms内の相対時刻です。"
                         f"各start_ms < end_msを守り、長さ0のShot/eventを作りません。"
                         f"一つのShotを0〜{duration}msにして、その中へ複数eventを置いて構いません。\n"
                         f"Shot IDは{outline['id']}_shot_1等、動作IDは{outline['id']}_action_1等を使い、"
                         "previous_scenesのIDを再使用しません。作者の台詞IDだけはそのまま使います。\n"
                         "今回の音響許可の上限: " + canonical(beat["audio"]) + "\n"
                         "今回配置できる未使用台詞ID: " + canonical([d["id"] for d in dialogues]) + "\n")
        instructions += ("今回のSceneで必ず一度ずつ配置する台詞（省略不可）: "
                         + canonical([dict(id=d["id"], actor_id=d["actor_id"], text=d["text"], language=d["language"])
                                      for d in required_dialogues])
                         + "。これは作者の発話であり、CAMERAや要約だけで代用しません。台詞を説明文へ移しません。\n")
        instructions += ("subject_descriptionsとretentionに一件ずつ必ず含める人物ID（この順序）: "
                         + canonical(outline["actor_ids"])
                         + "。画面外・電話越しという演出でも人物定義を省略しません。人物定義は画面に映す指示ではありません。\n")
        if beat.get("direction"):
            instructions += "今回の展開だけの方針: " + canonical(beat["direction"]) + "\n"
        if outline["transition"] == "continue":
            previous = next((scene for scene in reversed(payload["previous_scenes"])
                             if scene["end_ms"] == outline["start_ms"]), None)
            if previous:
                cameras = [event for shot in previous["shots"] for event in shot["events"] if event["kind"] == "camera"]
                last_camera = max(cameras, key=lambda event: event["end_ms"], default=None)
                instructions += ("今回の冒頭は直前Sceneの終了位置から連続します。直前CAMERAの開始位置や動きを最初からやり直さず、"
                    "終了時の構図・カメラ軸・人物の姿勢・位置・保持物を起点に、今回の動作へ進めてください。"
                    "作者の明示指定がないハードカットや視点のリセットを追加しません。\n"
                    + "継続開始状態: " + canonical(outline["initial_state"])
                    + "\n直前の最後のCAMERA（終了構図を参照）: " + canonical(last_camera) + "\n")
        if permits_nonverbal(beat["audio"]):
            instructions += ("非言語発声を許可しています。作者の台詞を一字も変更せず、台詞とは別のkind=vocalイベントをshots.eventsへ計画してください。"
                "textはaudio.nonverbal.allowed内のコードだけ、actor_idは発声する人物、dialogue_idとlanguageはnullです。"
                "nonverbal設定は作者指定のままコピーします。instruction、frequency、intensityに従い、場面に合う声だけを必要に応じて選びます。"
                "同じ人物の台詞や他の発声と時間を重ねません。声の説明をACTIONやsummaryへ混ぜません。"
                "台詞がないSceneも非言語発声を許可でき、vocalはnonverbalを使用できます。声の種類: " + canonical(VOICE_TEXT) + "\n")
    contract = _payload_schema(stage)
    if native_schema:
        instructions += "\n出力の型・field・固定値はネイティブ生成Schemaが強制します。末尾はそのfieldと型の略記です。意味・人物・台詞・時刻の検証規則は全て維持します。\n"
        contract = compact_schema(contract)
    return template.format(stage=stage, record_type=RECORDS[stage], stage_instructions=instructions, payload_schema=canonical(contract))


def generation_schema(stage, payload, *, errors=(), visible_actor_ids=None, outline_intervals=None):
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
        sources = scenario["beats"]
        if payload.get("enhance_beat_id"):
            sources = [beat for beat in sources if beat["id"] == payload["enhance_beat_id"]]
            require(len(sources) == 1, "ENHANCE_TARGET", "/", "分割補強の対象となる展開IDがありません。")
        for source in sources:
            beat = copy.deepcopy(definitions["enhanced_beat"])
            beat["properties"]["id"] = {"const": source["id"]}
            event = copy.deepcopy(definitions["required_event"])
            event["properties"]["id"] = dict(type="string", pattern="^req_[a-z0-9_]+$")
            event["properties"]["beat_id"] = {"const": source["id"]}
            first, last = source["source"]["first_line"], source["source"]["last_line"]
            require(first <= last, "ENHANCED_EVENT_SOURCE", source["id"], "入力展開の出典行範囲が逆転しています。")
            spans = []
            for line in range(first, last + 1):
                span = copy.deepcopy(definitions["source_span"])
                span["properties"].update(document={"const": "scenario"}, first_line={"const": line},
                    last_line=dict(type="integer", minimum=line, maximum=last))
                spans.append(span)
            event["properties"]["source"] = {"oneOf": spans}
            beat["properties"]["required_events"]["items"] = event
            beats.append(beat)
        # Tuple items require every authored beat exactly once, in source order.
        # llama.cpp supports prefixItems; items=false also forbids extra entries
        # when validating this narrowing with the JSON Schema 2020-12 validator.
        story["beats"] = dict(type="array", prefixItems=beats, items=False,
                              minItems=len(beats), maxItems=len(beats))
        return result
    if stage == "outline":
        beat = _outline_beat(payload)
        definition = result["$defs"]["scene_outline"]
        required_ids = [event["id"] for event in beat_requirements(payload, beat["id"])]
        definition["properties"]["required_event_ids"] = (
            dict(type="array", items={"enum": required_ids}, uniqueItems=True, maxItems=len(required_ids))
            if required_ids else {"const": []})
        for key, value in dict(beat_id=beat["id"], actor_ids=beat["actor_ids"], background_id=beat["background_id"]).items():
            definition["properties"][key] = {"const": value}
        if "prop_ids" in beat:
            definition["properties"]["prop_ids"] = {"const": beat["prop_ids"]}
            definition["required"].append("prop_ids")
        else:
            definition["properties"].pop("prop_ids", None)
        mode = beat.get("transition", "auto")
        if mode != "auto":
            definition["properties"]["transition"] = {"const": mode}
        first = copy.deepcopy(definition)
        accepted_ids = [scene["id"] for scene in payload["accepted_outlines"]]
        first["properties"]["depends_on"] = (dict(type="array", items={"enum": accepted_ids},
            uniqueItems=True, maxItems=len(accepted_ids)) if accepted_ids else {"const": []})
        previous = payload["accepted_outlines"][-1] if payload["accepted_outlines"] else None
        start = previous["end_ms"] if previous else 0
        first["properties"].update(start_ms={"const": start},
                                    end_ms=dict(type="integer", minimum=start + 1, maximum=start + 15000))
        if not payload["accepted_outlines"]:
            first["properties"].update(transition={"const": "cut"}, depends_on={"const": []})
        elif mode == "auto":
            previous = payload["accepted_outlines"][-1]
            if (set(previous["actor_ids"]) != set(beat["actor_ids"])
                    or previous["background_id"] != beat["background_id"]):
                # Only the boundary Scene is forced to cut; later Scenes of
                # this beat retain the ordinary auto connection choices.
                first["properties"]["transition"] = {"const": "cut"}
        if previous is not None and mode == "continue":
            from ..artifacts.transitions import inherited_states
            first["properties"]["depends_on"] = {"const": [previous["id"]]}
            states = inherited_states(previous, beat)
            if states:
                first["properties"]["initial_state"] = dict(type="array", minItems=len(states),
                    prefixItems=[{"const": state} for state in states], items={"$ref": "#/$defs/state"})
        result["properties"]["scenes"] = dict(type="array", prefixItems=[first],
                                               items={"$ref": "#/$defs/scene_outline"}, minItems=1)
        if outline_intervals is not None:
            prefix = [first] + [copy.deepcopy(definition) for _ in outline_intervals[1:]]
            for item, (begin, end) in zip(prefix, outline_intervals):
                item["properties"].update(start_ms={"const": begin}, end_ms={"const": end})
            if len(prefix) == 1:
                prefix[0]["properties"]["required_event_ids"] = {"const": required_ids}
            result["properties"]["scenes"] = dict(type="array", prefixItems=prefix, items=False,
                                                   minItems=len(prefix), maxItems=len(prefix))
            return result
        total = payload["scenario"]["duration_ms"]
        window = next((w for w in payload.get("beat_windows", []) if w["beat_id"] == beat["id"]), None)
        if window:
            start, end = window["start_ms"], window["end_ms"]
            count = (end - start + 14999) // 15000
            require(count > 0, "OUTLINE_BUDGET_TIMING", beat["id"], "Beat window must have positive duration")
            prefix = [first] + [copy.deepcopy(definition) for _ in range(count - 1)]
            for item in prefix:
                item["properties"].update(start_ms=dict(type="integer", minimum=start, maximum=end - 1),
                                          end_ms=dict(type="integer", minimum=start + 1, maximum=end))
            prefix[0]["properties"]["start_ms"] = {"const": start}
            prefix[0]["properties"]["end_ms"] = dict(type="integer", minimum=start + 1,
                                                         maximum=min(start + 15000, end))
            prefix[-1]["properties"]["end_ms"] = {"const": end}
            if count == 1:
                prefix[0]["properties"]["required_event_ids"] = {"const": required_ids}
            result["properties"]["scenes"] = dict(type="array", prefixItems=prefix, items=False,
                                                   minItems=count, maxItems=count)
            return result
        if beat is payload["scenario"]["beats"][-1] and total is not None:
            start = payload["accepted_outlines"][-1]["end_ms"] if payload["accepted_outlines"] else 0
            minimum = max(1, (total - start + 14999) // 15000)
            first["properties"]["start_ms"] = {"const": start}
            # The native prefix converter needs an explicit required prefix.
            # Reserve the minimum number of slots without assigning LLM timing.
            prefix = [first] + [copy.deepcopy(definition) for _ in range(minimum - 1)]
            result["properties"]["scenes"].update(prefixItems=prefix, minItems=minimum)
        return result
    if stage != "detail":
        return result
    definitions = result["$defs"]
    scene = definitions["scene"]
    scene["required"].extend(["visual_directions", "visible_actor_ids"])
    # Bound generation, never truncate author text or completed output.
    scene["properties"]["visual_directions"]["maxItems"] = 8
    for key, value in payload["outline"].items():
        scene["properties"][key] = {"const": value}
        if key not in scene["required"]:
            scene["required"].append(key)
    # Exact tuples prevent a voice-only direction from dropping an active actor.
    # Keep descriptions/model choices authored by the LLM; constrain identity and
    # coverage, rather than inventing missing descriptions after generation.
    actor_ids = payload["outline"]["actor_ids"]
    prop_ids = payload["outline"].get("prop_ids", [])
    if prop_ids:
        scene["required"].append("visible_prop_ids")
        scene["properties"]["visible_prop_ids"] = dict(type="array", items={"enum": prop_ids}, uniqueItems=True,
                                                       maxItems=len(prop_ids))
    else:
        scene["properties"].pop("visible_prop_ids", None)
        if "prop_ids" not in payload["outline"]:
            scene["properties"].pop("prop_ids", None)
    if visible_actor_ids is not None:
        require(set(visible_actor_ids) <= set(actor_ids) and len(visible_actor_ids) == len(set(visible_actor_ids)),
                "VISIBLE_ACTOR", payload["outline"]["id"], "Recovery visibility must be a unique subset of the approved cast")
    scene["properties"]["visible_actor_ids"] = (dict(type="array", items={"enum": actor_ids},
        maxItems=len(actor_ids), uniqueItems=True) if actor_ids else {"const": []})
    if visible_actor_ids is not None:
        scene["properties"]["visible_actor_ids"] = {"const": visible_actor_ids}
    for name in ("subject_descriptions", "retention"):
        item_schema = scene["properties"][name]["items"]
        entries = []
        for actor_id in actor_ids:
            entry = copy.deepcopy(item_schema)
            entry["properties"]["actor_id"] = {"const": actor_id}
            entries.append(entry)
        scene["properties"][name] = (dict(type="array", prefixItems=entries, items=False,
                                               minItems=len(entries), maxItems=len(entries))
                                       if entries else {"const": []})
    duration = payload["outline"]["end_ms"] - payload["outline"]["start_ms"]
    for name in ("shot", "event", "sound_event"):
        properties = definitions[name]["properties"]
        properties["start_ms"] = dict(type="integer", minimum=0, maximum=duration - 1)
        properties["end_ms"] = dict(type="integer", minimum=1, maximum=duration)
    beat, dialogues = _remaining_dialogues(payload)
    required_dialogues = _required_dialogues(payload, dialogues)
    audio = definitions["audio_policy"]["properties"]
    if "nonverbal" in beat["audio"]:
        audio["nonverbal"] = {"const": beat["audio"]["nonverbal"]}
        definitions["audio_policy"]["required"].append("nonverbal")
    else:
        audio.pop("nonverbal", None)
    for key in ("continuity", "bgm_suppression"):
        if key in beat["audio"]:
            audio[key] = {"const": beat["audio"][key]}
            definitions["audio_policy"]["required"].append(key)
        else:
            audio.pop(key, None)
    for key in ("vocal", "bgm", "ambience", "effects"):
        if beat["audio"][key] == "none" or key == "vocal" and not dialogues and not permits_nonverbal(beat["audio"]):
            audio[key] = {"const": "none"}
            if key != "vocal":
                audio[key + "_description"] = {"const": None}
    modes = {"none"}
    if dialogues and beat["audio"]["vocal"] in {"dialogue", "dialogue_nonverbal"}:
        modes.add("dialogue")
    if permits_nonverbal(beat["audio"]):
        modes.add("nonverbal")
        if dialogues and "dialogue" in modes:
            modes.add("dialogue_nonverbal")
    audio["vocal"] = {"enum": sorted(modes)}
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
    visual_owners = (visible_actor_ids if visible_actor_ids is not None else actor_ids) + prop_ids + ["environment"]
    visual["properties"]["actor_id"] = {"enum": visual_owners}
    alternatives = [visual]
    for dialogue in dialogues:
        spoken = copy.deepcopy(event)
        fixed = dict(kind="dialogue", id=dialogue["id"], dialogue_id=dialogue["id"], actor_id=dialogue["actor_id"], text=dialogue["text"], language=dialogue["language"])
        spoken["properties"].update({key: {"const": value} for key, value in fixed.items()})
        alternatives.append(spoken)
    if permits_nonverbal(beat["audio"]) and actor_ids:
        vocal = copy.deepcopy(event)
        vocal["properties"].update(kind={"const": "vocal"}, actor_id={"enum": actor_ids},
            text={"enum": beat["audio"]["nonverbal"]["allowed"]},
            dialogue_id={"const": None}, language={"const": None})
        alternatives.append(vocal)
    definitions["event"] = {"oneOf": alternatives}
    # Prefix enforcement works with the native grammar converter and leaves the
    # remaining authored dialogue/action events available in the same Shot.
    camera = copy.deepcopy(visual)
    camera["properties"].update(kind={"const": "camera"},
                                 actor_id={"enum": visual_owners})
    definitions["shot"]["properties"]["events"] = dict(
        type="array", prefixItems=[camera], items={"$ref": "#/$defs/event"}, minItems=1)
    dialogue_recovery = bool(required_dialogues) and any(e.get("code", "").lower() == "dialogue_coverage" for e in errors)
    if dialogue_recovery or any(error.get("code", "").lower() == "shot_coverage" for error in errors):
        # Regenerate a valid temporal scaffold after a coverage failure.
        # Multiple ACTION/DIALOGUE events still fit inside this one Shot.
        definitions["shot"]["properties"].update(start_ms={"const": 0}, end_ms={"const": duration})
        scene["properties"]["shots"].update(minItems=1, maxItems=1)
        camera["properties"].update(start_ms={"const": 0}, end_ms={"const": duration})
        # One full-span CAMERA suffices; do not permit overlapping tail cameras.
        definitions["event"] = {"oneOf": [dict(copy.deepcopy(visual), properties=dict(
            visual["properties"], kind={"const": "action"}))] + alternatives[1:]}
        if required_dialogues:
            required_ids = {d["id"] for d in required_dialogues}
            required_events = [s for s in alternatives[1:] if s["properties"]["id"].get("const") in required_ids]
            between = {"oneOf": [definitions["event"]["oneOf"][0]] +
                       [s for s in alternatives[1:] if s["properties"]["id"].get("const") not in required_ids]}
            array = definitions["shot"]["properties"]["events"]
            # Standard Schema checks presence/count. The native converter's
            # sequence rule also allows actions between every required speech.
            array["allOf"] = [dict(contains=s, minContains=1, maxContains=1) for s in required_events]
            array["x-vcd-event-sequence"] = dict(required=required_events, between=between)
    from .detail_limits import bound_detail_strings
    return bound_detail_strings(result, payload, compact=any(
        error.get("code", "").lower() in {"generation_truncated", "compact_detail"} for error in errors))


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

    def run(self, stage, target_id, payload, check, *, split_on_incomplete=False):
        prompt_version = STAGE_PROMPT_VERSIONS.get(stage, PROMPT_VERSION)
        base = dict(schema="VCD_LLM_REQUEST_V1", request_id="req_" + digest(canonical([stage, target_id, payload]))[:24], stage=stage,
                    targets=[dict(slot=1, target_id=target_id)], retry="initial", errors=[], payload=payload)
        validate(base, stage + "_request")
        complete_record = getattr(self.backend, "complete_record", None)
        native_schema = callable(complete_record)
        system = system_prompt(stage, payload, native_schema=native_schema)
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
                emit_progress("status", label="キャッシュを使用")
                return response["records"][0]["payload"]
            except (VCError, KeyError, TypeError):
                pass
        diagnostic_path = self.diagnostics_root / f"{stage}-{key[:12]}-{uuid.uuid4().hex}.json" if self.diagnostics_root else None
        diagnostic = dict(schema="VCD_PLANNING_DIAGNOSTIC_V1", prompt_version=prompt_version,
                          model=self._identity, system_prompt=system, request=base, attempts=[], status="running")
        errors, saved_diagnostic = [], None
        visual_repair_used = False
        vocal_timing_repair_used = False
        dialogue_placement_repair_used = False
        sound_trigger_repair_used = False
        shot_timing_repair_used = False
        outline_repair_used = False
        dependency_repair_used = False
        accepted_visual = None
        prose_repairs_used, accepted_prose = set(), {}
        shot_coverage_repair = False
        dialogue_coverage_repair = False
        recovery_visibility = None
        outline_intervals = None
        compact_detail = False
        for attempt in ("initial", "repair", "isolated"):
            emit_progress("status", label={"initial": "生成中", "repair": "修正・再試行 2/3", "isolated": "分離・再試行 3/3"}[attempt])
            generation_errors = copy.deepcopy(errors)
            if compact_detail:
                generation_errors.append(dict(target_id=target_id, path=target_id, code="compact_detail",
                    message="The previous DETAIL response exhausted its output budget. Return a complete, concise Scene. "
                            "Keep all required actions, exact authored dialogue/speakers, visible words, timing and audio permissions. "
                            "Do not repeat narrative in summary, directions and every action. Use short factual descriptions. "
                            "Use one Shot and one full-span CAMERA unless authored cuts or camera changes require more. "
                            "Do not invent extra camera/action/vocal events or repeat actions to fill time. "
                            "Preserve authored nonverbal frequency/intensity/instructions. Do not omit required events or silence permitted vocals to save tokens."))
            if outline_intervals is not None and not any(e["code"] == "outline_timing" for e in generation_errors):
                generation_errors.append(dict(target_id=target_id, path=target_id, code="outline_timing",
                    message=f"時間修正済みのScene区間を維持してください: {outline_intervals}ms。合計尺・出来事・台詞を変更しません。"))
            if dialogue_coverage_repair and not any(e["code"] == "dialogue_coverage" for e in generation_errors):
                generation_errors.append(dict(target_id=target_id, path=target_id, code="dialogue_coverage",
                    message="Keep every required_dialogue_ids event exactly once with authored speech and speaker unchanged. "
                            "Retain this requirement while fixing other fields; do not remove dialogue."))
            if shot_coverage_repair and not any(e["code"] == "shot_coverage" for e in generation_errors):
                duration = payload["outline"]["end_ms"] - payload["outline"]["start_ms"]
                generation_errors.append(dict(target_id=target_id, path=target_id, code="shot_coverage",
                    message=f"Keep the accepted temporal repair constraint: exactly one Shot from 0 to {duration}ms "
                            "with one full-span CAMERA. Preserve all required ACTION and authored DIALOGUE. "
                            "This constraint stays active while fixing other fields; do not recreate an extra or overlapping Shot."))
            if recovery_visibility is not None:
                for constraint in generation_errors:
                    if constraint["code"] == "shot_coverage":
                        constraint["message"] += (" Keep visible_actor_ids=" + canonical(recovery_visibility)
                            + "; ACTION/CAMERA can target only those actors or environment. "
                              "Do not move an off-screen speaker into a visual ACTION to avoid an audio error. "
                              "Keep the exact authored DIALOGUE and its speakers unchanged.")
            request = dict(base, retry=attempt, errors=generation_errors)
            validate(request, stage + "_request")
            attempt_trace = dict(attempt=attempt, input_errors=generation_errors,
                active_constraints=(["single_shot_full_scene"] if shot_coverage_repair else [])
                                   + (["required_dialogue_events"] if dialogue_coverage_repair else [])
                                   + (["fixed_visible_cast"] if recovery_visibility is not None else [])
                                   + (["compact_detail"] if compact_detail else []))
            if outline_intervals is not None:
                attempt_trace["active_constraints"].append("fixed_outline_intervals")
                attempt_trace["outline_intervals"] = outline_intervals
            diagnostic["attempts"].append(attempt_trace)
            diagnostic["status"] = "running"
            self._save_diagnostic(diagnostic_path, diagnostic)
            LOGGER.info("Planning %s / %s (%s)", stage, target_id, attempt)
            try:
                model_request = detail_request(request) if native_schema and stage == "detail" else request
                if model_request is not request:
                    attempt_trace["inference_request"] = model_request
                before = len(getattr(self.backend, "trace", []))
                raw = complete_record(system, canonical(model_request), RECORDS[stage], payload_schema=generation_schema(stage, payload, errors=generation_errors, visible_actor_ids=recovery_visibility, outline_intervals=outline_intervals)) if native_schema else self.backend.complete(system, canonical(request))
                backend_trace = getattr(self.backend, "trace", [])
                if len(backend_trace) > before and "performance" in backend_trace[-1]:
                    attempt_trace["performance"] = copy.deepcopy(backend_trace[-1]["performance"])
            except Exception as exc:
                diagnostic["status"] = "backend_error"
                attempt_trace["backend_error"] = dict(type=type(exc).__name__, message=str(exc))
                self._save_diagnostic(diagnostic_path, diagnostic)
                raise
            attempt_trace["raw"] = raw
            self.trace.append(dict(stage=stage, target_id=target_id, attempt=attempt, raw=raw, key=key))
            parsing_response = True
            try:
                response = parse_response(raw, stage, base["request_id"], target_id)
                parsing_response = False
                attempt_trace["response"] = response
                validate(response, stage + "_response")
                self._check_records(response, stage)
                result = response["records"][0]["payload"]
                if (accepted_visual is not None and set(result.get("visible_actor_ids", [])) == accepted_visual["visible"]
                        and visible_props(result) == accepted_visual["props"]):
                    result["visual_directions"] = copy.deepcopy(accepted_visual["directions"])
                    attempt_trace["reused_fields"] = ["visual_directions"]
                if stage == "detail":
                    from .prose_repair import prose_targets, target_context, set_text
                    for target in prose_targets(result):
                        saved = accepted_prose.get(target["key"])
                        if saved and target_context(result, target) == saved["context"]:
                            set_text(result, target, saved["text"])
                            attempt_trace.setdefault("reused_fields", []).append(target["key"])
                outline_repaired = False
                dependency_repaired = False
                while True:
                    try:
                        if stage == "outline" and outline_intervals is not None:
                            from ..planner.outline_timing import validate_intervals
                            validate_intervals(result, outline_intervals, target_id)
                        check(result)
                        break
                    except VCError as exc:
                        if (stage == "outline" and exc.diagnostic.code == "OUTLINE_DEPENDENCY"
                                and not dependency_repaired and not dependency_repair_used):
                            from .dependency_repair import repair_outline_dependencies
                            dependency_repaired = True
                            repair_trace = attempt_trace["dependency_repair"] = {}
                            LOGGER.info("Planning outline / %s: repairing Scene IDs and dependencies only", target_id)
                            try:
                                repaired = repair_outline_dependencies(self.backend, result, payload, repair_trace)
                            except Exception:
                                repair_trace["status"] = "failed"
                                self._save_diagnostic(diagnostic_path, diagnostic)
                                raise
                            finally:
                                dependency_repair_used |= repair_trace.get("method") == "dependency_choices"
                            result = response["records"][0]["payload"] = repaired
                            validate(response, stage + "_response")
                            continue
                        if (stage == "outline" and exc.diagnostic.code == "OUTLINE_REQUIRED" and not outline_repaired
                                and (len(result["scenes"]) == 1 or not outline_repair_used)):
                            from .outline_repair import repair_required_assignments
                            outline_repaired = True
                            outline_repair_used |= len(result["scenes"]) > 1
                            repair_trace = attempt_trace["required_assignment_repair"] = {}
                            LOGGER.info("Planning outline / %s: repairing required_event_ids only", target_id)
                            try:
                                repaired = repair_required_assignments(self.backend, result, payload, target_id,
                                                                       exc.diagnostic, repair_trace)
                            except VCError as repair_error:
                                repair_trace["status"] = "failed"
                                raise VCError("OUTLINE_REQUIRED", target_id,
                                              exc.diagnostic.message + " 部分修正失敗: " + str(repair_error)) from repair_error
                            except Exception:
                                repair_trace["status"] = "failed"
                                self._save_diagnostic(diagnostic_path, diagnostic)
                                raise
                            result = response["records"][0]["payload"] = repaired
                            validate(response, stage + "_response")
                            continue
                        if stage != "detail":
                            raise
                        from .visual_repair import is_visual_direction_error, repair_visual_directions
                        from .prose_repair import target_for_error, repair_prose_field, get_text
                        from .dialogue_repair import duplicate_dialogues, repair_duplicate_dialogue
                        target = target_for_error(result, exc.diagnostic)
                        if (exc.diagnostic.code == "DUPLICATE_ID" and not dialogue_placement_repair_used
                                and duplicate_dialogues(result)):
                            dialogue_placement_repair_used = True
                            repair_trace = attempt_trace["dialogue_placement_repair"] = {}
                            LOGGER.info("Planning detail / %s: selecting one placement per authored dialogue", target_id)
                            try:
                                repaired = repair_duplicate_dialogue(self.backend, result, payload, repair_trace)
                            except Exception:
                                repair_trace["status"] = "failed"
                                self._save_diagnostic(diagnostic_path, diagnostic)
                                raise
                        elif exc.diagnostic.code == "SHOT_COVERAGE" and not shot_timing_repair_used:
                            from .shot_timing_repair import repair_shot_timing
                            shot_timing_repair_used = True
                            repair_trace = attempt_trace["shot_timing_repair"] = {}
                            LOGGER.info("Planning detail / %s: repairing Shot and visual timing only", target_id)
                            try:
                                repaired = repair_shot_timing(self.backend, result, payload, exc.diagnostic, repair_trace)
                            except Exception:
                                repair_trace["status"] = "failed"
                                self._save_diagnostic(diagnostic_path, diagnostic)
                                raise
                        elif exc.diagnostic.code == "SOUND_TRIGGER" and not sound_trigger_repair_used:
                            from .sound_trigger_repair import repair_sound_triggers
                            sound_trigger_repair_used = True
                            repair_trace = attempt_trace["sound_trigger_repair"] = {}
                            LOGGER.info("Planning detail / %s: repairing sound bindings only", target_id)
                            try:
                                repaired = repair_sound_triggers(self.backend, result, payload, repair_trace)
                            except Exception:
                                repair_trace["status"] = "failed"
                                self._save_diagnostic(diagnostic_path, diagnostic)
                                raise
                        elif exc.diagnostic.code == "NONVERBAL_OVERLAP" and not vocal_timing_repair_used:
                            from .vocal_timing_repair import repair_vocal_timing
                            vocal_timing_repair_used = True
                            repair_trace = attempt_trace["vocal_timing_repair"] = {}
                            LOGGER.info("Planning detail / %s: repairing nonverbal timing only", target_id)
                            try:
                                repaired = repair_vocal_timing(self.backend, result, payload, exc.diagnostic, repair_trace)
                            except Exception:
                                repair_trace["status"] = "failed"
                                self._save_diagnostic(diagnostic_path, diagnostic)
                                raise
                        elif not visual_repair_used and is_visual_direction_error(exc.diagnostic, result):
                            visual_repair_used = True
                            repair_trace = attempt_trace["visual_direction_repair"] = {}
                            LOGGER.info("Planning detail / %s: repairing visual_directions only", target_id)
                            try:
                                repaired = repair_visual_directions(self.backend, result, payload, exc.diagnostic, repair_trace)
                            except Exception:
                                repair_trace["status"] = "failed"
                                self._save_diagnostic(diagnostic_path, diagnostic)
                                raise
                            accepted_visual = dict(visible=set(repaired["visible_actor_ids"]),
                                                   props=visible_props(repaired),
                                                   directions=copy.deepcopy(repaired["visual_directions"]))
                            repair_trace["status"] = "field_accepted"
                        elif target is not None and (target["key"], target_context(result, target)) not in prose_repairs_used:
                            context = target_context(result, target)
                            prose_repairs_used.add((target["key"], context))
                            repair_trace = {}
                            attempt_trace.setdefault("prose_repairs", []).append(repair_trace)
                            LOGGER.info("Planning detail / %s: repairing %s only", target_id, target["key"])
                            try:
                                repaired = repair_prose_field(self.backend, result, payload, target, exc.diagnostic, repair_trace)
                            except Exception:
                                repair_trace["status"] = "failed"
                                self._save_diagnostic(diagnostic_path, diagnostic)
                                raise
                            accepted_prose[target["key"]] = dict(context=context, text=get_text(repaired, target))
                        else:
                            raise
                        result = response["records"][0]["payload"] = repaired
                if "visual_direction_repair" in attempt_trace:
                    attempt_trace["visual_direction_repair"]["status"] = "accepted"
                if "required_assignment_repair" in attempt_trace:
                    attempt_trace["required_assignment_repair"]["status"] = "accepted"
                if "dependency_repair" in attempt_trace:
                    attempt_trace["dependency_repair"]["status"] = "accepted"
                if "vocal_timing_repair" in attempt_trace:
                    attempt_trace["vocal_timing_repair"]["status"] = "accepted"
                if "dialogue_placement_repair" in attempt_trace:
                    attempt_trace["dialogue_placement_repair"]["status"] = "accepted"
                if "sound_trigger_repair" in attempt_trace:
                    attempt_trace["sound_trigger_repair"]["status"] = "accepted"
                if "shot_timing_repair" in attempt_trace:
                    attempt_trace["shot_timing_repair"]["status"] = "accepted"
                for repair_trace in attempt_trace.get("prose_repairs", []):
                    repair_trace["status"] = "accepted"
                if self.cache:
                    self.cache.put_success(key, dict(response=response))
                diagnostic["status"] = "success"
                self._save_diagnostic(diagnostic_path, diagnostic)
                return result
            except (VCError, json.JSONDecodeError) as exc:
                d = exc.diagnostic if isinstance(exc, VCError) else None
                incomplete = False
                if isinstance(exc, json.JSONDecodeError):
                    from .response_errors import json_failure
                    d, incomplete = json_failure(exc, attempt_trace.get("performance", {}) if parsing_response else {})
                errors = [dict(target_id=target_id, path=d.path if d else "/", code=d.code.lower() if d else "json", message=d.message if d else str(exc))]
                if stage == "detail" and parsing_response and d is not None and d.code == "GENERATION_TRUNCATED":
                    compact_detail = True
                if incomplete and parsing_response and split_on_incomplete and stage == "enhance":
                    attempt_trace["errors"] = errors
                    diagnostic["status"] = "split_requested"
                    saved = self._save_diagnostic(diagnostic_path, diagnostic)
                    suffix = f"; 応答ログ: {saved}" if saved else ""
                    LOGGER.info("Planning enhance / %s: JSON未完了のため展開単位へ分割します", target_id)
                    raise VCError("ENHANCE_SPLIT_REQUIRED", target_id, d.message + suffix) from exc
                if stage == "enhance" and d is not None and d.code == "GENERATION_TRUNCATED":
                    attempt_trace["errors"] = errors
                    diagnostic["status"] = "generation_truncated"
                    saved = self._save_diagnostic(diagnostic_path, diagnostic)
                    suffix = f"; 応答ログ: {saved}" if saved else ""
                    message = d.message + " 対象の展開を分けるか、contextの空き範囲内で推論設定の出力トークン上限を増やしてください。"
                    raise VCError(d.code, target_id, message + suffix) from exc
                if stage == "outline" and d is not None and d.code == "OUTLINE_TIMING" and outline_intervals is None:
                    from ..planner.outline_timing import recovery_intervals
                    outline_intervals = recovery_intervals(payload, result, target_id)
                    if outline_intervals is not None:
                        errors[0]["message"] += f" 次の応答では区間{outline_intervals}msの{len(outline_intervals)}Sceneへ再構成してください。"
                if stage == "detail" and d is not None and d.code == "DIALOGUE_COVERAGE" and payload.get("required_dialogue_ids"):
                    dialogue_coverage_repair = True
                    shot_coverage_repair = True
                if stage == "detail" and d is not None and d.code == "SHOT_COVERAGE":
                    shot_coverage_repair = True
                    if payload.get("required_dialogue_ids"):
                        dialogue_coverage_repair = True
                    if recovery_visibility is None and "visible_actor_ids" in result:
                        recovery_visibility = copy.deepcopy(result["visible_actor_ids"])
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
