"""Build one VC workflow from the pinned Context Loop standard Ref2V workflow."""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__:
    from ._bootstrap import load_project
else:
    from _bootstrap import load_project
load_project()
from vc_director.nodes import NODE_CLASS_MAPPINGS
from vc_director.core.h3_contract import verify_runtime


def generate(runtime_root):
    verify_runtime(runtime_root)
    source = ROOT / "tools/workflow_templates/Ref2V Basic - MiniMax H3 0.6.json"
    original = json.loads(source.read_text(encoding="utf-8"))
    workflow = copy.deepcopy(original)
    # Keep the stock sampler/decode/trim/save/review/loop graph intact.
    removed = {9, 25, 26, 27, 31}  # optional recovery branch and sample pictures/note
    workflow["nodes"] = [n for n in workflow["nodes"] if n["id"] not in removed]
    workflow["links"] = [l for l in workflow["links"] if l[1] not in removed and l[3] not in removed and l[0] not in {9, 10}]
    nodes = {n["id"]: n for n in workflow["nodes"]}

    def vc_node(kind, id, pos, values, group=None):
        cls = NODE_CLASS_MAPPINGS[kind]
        inputs, widgets = [], []
        for name, spec in cls.INPUT_TYPES()["required"].items():
            data_type = "COMBO" if isinstance(spec[0], list) else spec[0]
            options = spec[1] if len(spec) > 1 else {}
            primitive = data_type in {"STRING", "INT", "FLOAT", "BOOLEAN", "COMBO"}
            field = dict(name=name, type=data_type, link=None)
            if primitive:
                field["widget"] = dict(name=name)
                widgets.append(values.get(name, options.get("default", spec[0][0] if isinstance(spec[0], list) else "")))
            inputs.append(field)
        value = dict(id=id, type=kind, pos=pos, size=[540, 440], flags={}, order=len(nodes), mode=2 if group == "video" else 0, inputs=inputs,
                     outputs=[dict(name=name, type=type_, links=[]) for name, type_ in zip(getattr(cls, "RETURN_NAMES", cls.RETURN_TYPES), cls.RETURN_TYPES)],
                     properties={"Node name for S&R": kind, **({"vcd_group": group} if group else {})}, widgets_values=widgets)
        nodes[id] = value
        return value

    def link(source_id, source_slot, target_id, name, datatype):
        target = nodes[target_id]
        index = next((i for i, v in enumerate(target["inputs"]) if v["name"] == name), None)
        if index is None:
            index = len(target["inputs"])
            target["inputs"].append(dict(name=name, type=datatype, link=None, widget=dict(name=name)))
        id = max([0] + [l[0] for l in workflow["links"]]) + 1
        workflow["links"].append([id, source_id, source_slot, target_id, index, datatype])

    runtime_path = str(Path(runtime_root).resolve())
    vc_node("VCDirectorAssetRegistry", 32, [-1250, 180], dict(assets_json="[]", asset_root=""))
    vc_node("VCDirectorBuild", 33, [-600, 180], dict(input_mode="saved_scene", emd=(ROOT / "assets/emd/minimal.scene.emd").read_text(encoding="utf-8"), output_root="", image_facts_json="[]", model_name="none", seed=1), "language")
    vc_node("VCDirectorLoad", 34, [80, 180], dict(project_directory="minimal", runtime_root=runtime_path), "video")
    old = nodes[11]
    native = vc_node("VCDirectorRef2VA", 11, old["pos"], dict(prompt="", width=896, height=672, length=243, clip_index=1, runtime_root=runtime_path), "video")
    for l in workflow["links"]:
        if l[3] == 11:
            old_name = old["inputs"][l[4]]["name"]
            l[4] = next(i for i, field in enumerate(native["inputs"]) if field["name"] == old_name)
    # Store both modes in one graph; no live language->video dependency.
    for node in nodes.values():
        if node["id"] not in {32, 33}:
            node["properties"]["vcd_group"] = "video"
            node["mode"] = 2
    nodes[24]["widgets_values"][0] = '{"shots": []}'
    nodes[24]["widgets_values"][1] = "vc_director_minimal"
    nodes[24]["widgets_values"][2] = ""
    nodes[30]["widgets_values"] = ["Visual continuity", "Generate audio"]
    # Use the compiler prompt directly. The stock visual Scene Editor may not rewrite it.
    for l in workflow["links"]:
        if l[1] == 6:
            l[1], l[2] = 24, 0
    nodes.pop(6)
    workflow["links"] = [l for l in workflow["links"] if l[3] != 6]
    link(32, 0, 33, "registry", "VC_ASSET_REGISTRY")
    link(32, 0, 34, "registry", "VC_ASSET_REGISTRY")
    link(32, 0, 11, "registry", "VC_ASSET_REGISTRY")
    link(34, 0, 24, "plan_json_input", "STRING")
    link(34, 2, 24, "generation_fingerprint", "STRING")
    link(34, 1, 11, "compiled", "VC_COMPILED")
    link(8, 1, 11, "clip_index", "INT")
    # clip_index is Current Shot's one-based index (verified below against schema).
    # Rebuild redundant socket linkage arrays from the authoritative links table.
    for node in nodes.values():
        for field in node.get("inputs", []):
            field["link"] = None
        for field in node.get("outputs", []):
            field["links"] = []
    for l in workflow["links"]:
        nodes[l[3]]["inputs"][l[4]]["link"] = l[0]
        nodes[l[1]]["outputs"][l[2]]["links"].append(l[0])
    workflow["nodes"] = list(nodes.values())
    workflow["groups"] = [dict(title="共有素材", bounding=[-1300, 80, 600, 650], color="#477c73", font_size=24, flags={}),
                          dict(title="言語生成 · Compiler · Save", bounding=[-650, 80, 600, 850], color="#57689a", font_size=24, flags={}),
                          dict(title="動画生成 · Context Loop", bounding=[20, 80, 6000, 2200], color="#92704a", font_size=24, flags={})]
    workflow["last_node_id"] = max(nodes)
    workflow["last_link_id"] = max(l[0] for l in workflow["links"])
    workflow["extra"] = dict(workflow.get("extra", {}), vc_director=dict(schema="VCD_WORKFLOW_V1", base_workflow_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), runtime_contract="VCD_CL_GUIDE_24FPS_V1"))
    return workflow


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-loop-root", type=Path, required=True)
    args = parser.parse_args()
    value = generate(args.context_loop_root)
    target = ROOT / "workflows/VC Director.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)
