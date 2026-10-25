"""Build one VC workflow from the pinned Context Loop standard Ref2V workflow."""
import argparse
import base64
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
from vc_director.core.inference import default_model_name


def apply_video_model_stack(workflow, nodes, link):
    """Port MV's FL2VA/Ref2VA tensor overlay and acceleration chain."""
    source = ROOT / "tools/workflow_templates/mv_video_model_stack.json"
    templates = json.loads(source.read_text(encoding="utf-8"))
    placements = {
        "MiniMaxH3HybridLoader": (1, [1200, 180]),
        "LoraLoaderModelOnly": (39, [1200, 550]),
        "ModelAttentionBackend": (22, [1200, 730]),
        "BlockSparseAttention": (40, [1200, 900]),
        "SpectrumApplyMiniMaxH3": (41, [1200, 1250]),
        "ModelPatchTorchSettings": (42, [1200, 2050]),
    }
    for template in templates:
        if template["type"] in {"CLIPLoader", "VAELoader"}:
            nodes[template["id"]]["widgets_values"] = copy.deepcopy(template["widgets_values"])
            continue
        node = copy.deepcopy(template)
        node_id, position = placements[node["type"]]
        node.update(id=node_id, pos=position, order=len(nodes), mode=2)
        node["properties"].update(vcd_group="video", vcd_active_mode=template["mode"])
        for field in node["inputs"]:
            field["link"] = None
        for field in node["outputs"]:
            field["links"] = []
        nodes[node_id] = node
    # Replace both old UNET -> Attention and Attention -> Sigma connections.
    chain = [1, 39, 22, 40, 41, 42, 13]
    workflow["links"] = [wire for wire in workflow["links"] if not (
        wire[3] in chain and nodes[wire[3]]["inputs"][wire[4]]["name"] == "model"
    )]
    for origin, target in zip(chain, chain[1:]):
        link(origin, 0, target, "model", "MODEL")
    # Step count comes from Current Shot for both ordinary and Spectrum sampling.
    nodes[24]["widgets_values"][8] = 20
    nodes[15]["widgets_values"][1] = 20
    return hashlib.sha256(source.read_bytes()).hexdigest()


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
        schema = cls.INPUT_TYPES()
        for name, spec in {**schema["required"], **schema.get("optional", {})}.items():
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
    vc_node("VCDirectorBuild", 33, [-600, 180], dict(input_mode="scenario", emd=(ROOT / "assets/emd/minimal.scenario.emd").read_text(encoding="utf-8"), output_root="", image_facts_json="[]", model_name=default_model_name(), seed=1), "language")
    vc_node("VCDirectorLoad", 34, [80, 180], dict(project_directory="scenario_demo", runtime_root=runtime_path), "video")
    old = nodes[11]
    native = vc_node("VCDirectorRef2VA", 11, old["pos"], dict(prompt="", width=864, height=480, length=243, clip_index=1, runtime_root=runtime_path), "video")
    for l in workflow["links"]:
        if l[3] == 11:
            old_name = old["inputs"][l[4]]["name"]
            l[4] = next(i for i, field in enumerate(native["inputs"]) if field["name"] == old_name)
    # Store both modes in one graph; no live language->video dependency.
    for node in nodes.values():
        if node["id"] not in {32, 33}:
            node["properties"]["vcd_group"] = "video"
            node["mode"] = 2
    # The editor validates local JSON even with plan_json_input connected.
    # Empty shots opens repair-only mode and hides canvas/sampling settings.
    # At execution the connected Load supplies the actual compiled scenes.
    nodes[24]["widgets_values"][0] = json.dumps({"shots": [dict(
        id="load_preview", length=243, prompt="Load supplies the compiled scene at execution.",
        context_length=0, audio_context_length=0,
    )]}, ensure_ascii=False)
    nodes[24]["widgets_values"][1] = "vc_director_scenario_demo"
    nodes[24]["widgets_values"][2] = ""
    nodes[24]["widgets_values"][3:5] = [864, 480]
    # The Context Loop editor collapses unconnected native widgets/sockets.
    # Real links keep these sockets visible; use core nodes without MV dependencies.
    nodes[35] = dict(
        id=35, type="ResolutionSelector", title="Production Resolution",
        pos=[80, 850], size=[360, 190], flags={}, order=len(nodes), mode=2,
        inputs=[dict(name=name, type=kind, link=None, widget=dict(name=name))
                for name, kind in (("aspect_ratio", "COMBO"), ("megapixels", "FLOAT"),
                                   ("multiple", "INT"), ("preview", "RESOLUTION_PREVIEW"))],
        outputs=[dict(name=name, type="INT", links=[]) for name in ("width", "height")],
        properties={"Node name for S&R": "ResolutionSelector", "vcd_group": "video"},
        widgets_values=["16:9 (Widescreen)", 0.4, 32],
        widgets_values_named=dict(aspect_ratio="16:9 (Widescreen)", megapixels=0.4, multiple=32),
    )
    link(35, 0, 24, "width", "INT")
    link(35, 1, 24, "height", "INT")
    seed = vc_node("VCDirectorSeed32", 37, [80, 1170], dict(mode="fixed", seed=1), "video")
    seed["size"] = [300, 150]
    link(37, 0, 24, "base_seed", "INT")
    text = (ROOT / "assets/emd/minimal.scenario.emd").read_bytes()
    loader = vc_node("VCDirectorLoadTextFile", 38, [-600, 1150], dict(
        file_data_base64=base64.b64encode(text).decode("ascii"), basename="minimal.scenario.txt",
        browser_metadata_json=json.dumps(dict(size=len(text), type="text/plain")),
    ), "language")
    loader["size"] = [420, 180]
    link(38, 0, 33, "emd", "STRING")
    settings = vc_node("VCDirectorInferenceSettings", 43, [-600, 1450], {}, "language")
    settings["size"] = [540, 600]
    link(43, 0, 33, "inference_settings", "VC_INFERENCE_SETTINGS")
    nodes[30]["widgets_values"] = ["Visual continuity", "Generate audio"]
    # Pass completed segments through without waiting for review by default.
    nodes[28]["widgets_values"][0] = False  # MiniMaxH3ChainReview.enabled
    if "widgets_values_named" in nodes[28]:
        nodes[28]["widgets_values_named"]["enabled"] = False
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
    model_stack_hash = apply_video_model_stack(workflow, nodes, link)
    nodes[24]["pos"] = [80, 1450]
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
                          dict(title="言語生成 · Compiler · Save", bounding=[-650, 80, 600, 2050], color="#57689a", font_size=24, flags={}),
                          dict(title="動画生成 · Context Loop", bounding=[20, 80, 6720, 3200], color="#92704a", font_size=24, flags={})]
    workflow["last_node_id"] = max(nodes)
    workflow["last_link_id"] = max(l[0] for l in workflow["links"])
    workflow["extra"] = dict(workflow.get("extra", {}), vc_director=dict(schema="VCD_WORKFLOW_V1", base_workflow_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), runtime_contract="VCD_CL_GUIDE_24FPS_V1", video_model_stack_sha256=model_stack_hash))
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
