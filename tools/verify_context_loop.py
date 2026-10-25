"""Offline compatibility checks against installed, pinned runtime source; no sampling."""
import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__:
    from ._bootstrap import load_project
else:
    from _bootstrap import load_project
load_project()
from vc_director.core.compiler import compile_scene_emd, compile_document
from vc_director.core.emd import render_scene
from vc_director.nodes import NODE_CLASS_MAPPINGS
from vc_director.core.h3_contract import verify_runtime


def verify(runtime_root, object_info=None):
    verify_runtime(runtime_root)
    path = Path(runtime_root) / "tools/workflow_schema.py"
    spec = importlib.util.spec_from_file_location("vcd_runtime_schema", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schemas = module.load_schemas()
    # Sanitized registration snapshot for MV's external model/acceleration stack.
    schemas.update(json.loads((ROOT / "tools/workflow_templates/video_model_schemas.json").read_text(encoding="utf-8")))
    schemas.update(json.loads((ROOT / "tools/workflow_templates/resolution_selector_schema.json").read_text(encoding="utf-8")))
    live_schemas = json.loads(Path(object_info).read_text(encoding="utf-8")) if object_info else {}
    schemas.update(live_schemas)
    for name, cls in NODE_CLASS_MAPPINGS.items():
        if name == "VCDirectorImageToSubjectEMD":
            continue  # GGUF inventory is not needed to verify this workflow.
        schemas[name] = dict(input=cls.INPUT_TYPES(), output=list(cls.RETURN_TYPES))
    workflow = json.loads((ROOT / "workflows/VC Director.json").read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in workflow["nodes"]}
    ids = [l[0] for l in workflow["links"]]
    assert len(ids) == len(set(ids)), "Duplicate link ID"
    targets = set()
    for node in nodes.values():
        assert node["type"] in schemas, node["type"]
    for link_id, source, slot, target, target_slot, type_ in workflow["links"]:
        key = target, target_slot
        assert key not in targets, "Multiple links to one input"
        targets.add(key)
        assert source in nodes and target in nodes
        origin, dest = nodes[source], nodes[target]
        assert origin["outputs"][slot]["type"] == type_, (source, slot, type_)
        assert dest["inputs"][target_slot]["type"] == type_, (target, target_slot, type_)
        assert schemas[origin["type"]]["output"][slot] == type_
        inputs = schemas[dest["type"]]["input"]
        fields = {**inputs.get("required", {}), **inputs.get("optional", {})}
        name = dest["inputs"][target_slot]["name"]
        assert name in fields, (dest["type"], name)
        assert fields[name][0] == type_, (dest["type"], name, fields[name][0], type_)
        assert dest["inputs"][target_slot]["link"] == link_id
        assert link_id in origin["outputs"][slot]["links"]
    model_types = {n["type"] for n in json.loads((ROOT / "tools/workflow_templates/mv_video_model_stack.json").read_text(encoding="utf-8"))}
    model_types.add("ResolutionSelector")
    for node in nodes.values():
        if node["type"] not in model_types:
            continue
        if node["type"] == "VAELoader":
            continue  # Model inventory requires a full live snapshot for this stock node.
        schema = schemas[node["type"]]
        fields = dict(module.widget_fields(schema, node.get("widgets_values_named", {})))
        assert len(fields) == len(node["widgets_values"]), (node["type"], list(fields))
        values = dict(zip(fields, node["widgets_values"]))
        fields = dict(module.widget_fields(schema, values))
        for name, spec in fields.items():
            # Snapshot inventories are sanitized; a supplied live snapshot checks actual files.
            if node["type"] not in live_schemas and name in {"base_model", "overlay_model", "lora_name", "clip_name"}:
                continue
            module.validate_value(values[name], spec, f"{node['type']}.{name}")
    assert all(n["mode"] == 2 for n in nodes.values() if n["properties"].get("vcd_group") == "video")
    assert all(n["mode"] == 0 for n in nodes.values() if n["properties"].get("vcd_group") == "language")
    chain = sys.modules["_h3_release_workflow_schema.chain_nodes"]

    def invoke(node, **overrides):
        cls = getattr(chain, node["type"])
        required = cls.INPUT_TYPES()["required"]
        widget_names = [name for name, spec in required.items() if isinstance(spec[0], list) or spec[0] in {"STRING", "INT", "FLOAT", "BOOLEAN"}]
        kwargs = dict(zip(widget_names, node["widgets_values"]))
        kwargs.update(overrides)
        return getattr(cls(), cls.FUNCTION)(**kwargs)

    result = compile_scene_emd((ROOT / "assets/emd/minimal.scene.emd").read_text(encoding="utf-8"))
    policy = invoke(nodes[30])[0]
    normalized = invoke(nodes[24], plan_json_input=json.dumps(result.plan, ensure_ascii=False), chain_policy=policy)[0]
    assert normalized["shots"][0]["raw_frames"] == result.plan["shots"][0]["length"]
    assert normalized["shots"][0]["prompt"] == result.plan["shots"][0]["prompt"]
    assert normalized["shots"][0]["audio_context_length"] == 0
    assert normalized["compatibility"]["audio_mode"] == "generated_audio"
    # Exercise a real head-overlap continuation as well as the opening cut.
    doc = copy.deepcopy(result.document)
    doc["beats"][0]["dialogues"] = []
    first = doc["scenes"][0]
    first["shots"][0]["events"] = [e for e in first["shots"][0]["events"] if e["kind"] != "dialogue"]
    for audio in (doc["audio"], doc["beats"][0]["audio"], first["audio"]):
        audio["vocal"] = "none"
    second = copy.deepcopy(first)
    second.update(id="continue_scene", start_ms=10000, end_ms=20000, transition="continue", depends_on=[first["id"]], required_event_ids=[], initial_state=first["terminal_state"])
    second["shots"][0]["id"] = "continue_shot"
    second["shots"][0]["events"][0]["id"] = "continue_event"
    doc["scenes"].append(second)
    doc["duration_ms"] = 20000
    continued = compile_document(doc, render_scene(doc))
    normalized = invoke(nodes[24], plan_json_input=json.dumps(continued.plan), chain_policy=policy)[0]
    for shot, segment in zip(normalized["shots"], continued.execution["segments"]):
        assert shot["raw_frames"] == segment["raw_length"]
        assert shot["delivered_frames"] == segment["end_frame"] - segment["start_frame"]
        assert shot["generation_start_frame"] == segment["start_frame"] - segment["context_frames"]
        assert shot["audio_context_length"] == segment["audio_context_frames"]
    print(f"Context Loop contract + {len(nodes)} nodes / {len(ids)} links + cut/continue Plan normalization: OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-loop-root", type=Path, required=True)
    parser.add_argument("--object-info", type=Path, help="Optional live /object_info snapshot, including installed model choices")
    args = parser.parse_args()
    verify(args.context_loop_root, args.object_info)
