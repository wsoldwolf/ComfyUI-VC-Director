import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../web/js/project_output.js", import.meta.url), "utf8");
function setup({ run = "vc_director_scenario_demo", converted = false, supports = true } = {}) {
    let extension;
    const graph = { _nodes: [], links: {}, getNodeById(id) { return this._nodes.find(n => n.id === id); } };
    let conversionCalls = 0;
    const context = vm.createContext({
        app: { graph, registerExtension(value) { extension = value; } },
        comfyAPI: { widgetInputs: { convertToInput(node) {
            conversionCalls++;
            if (converted) node.addInput("run_name", "STRING", { widget: { name: "run_name" } });
        } } },
    });
    vm.runInContext(source.replace(/^import .*;\r?$/gm, ""), context);
    class Load {
        constructor(id) {
            this.id = id; this.comfyClass = "VCDirectorLoad";
            this.outputs = ["plan_json", "compiled", "generation_fingerprint"].map(name => ({ name, type: "STRING" }));
        }
        addOutput(name, type) { this.outputs.push({ name, type }); }
        connect(slot, plan, input) {
            graph.links[2] = { origin_id: this.id, origin_slot: slot, target_id: plan.id, target_slot: input };
            plan.inputs[input].link = 2;
        }
    }
    extension.beforeRegisterNodeDef(Load, { name: "VCDirectorLoad", output_name: supports ? ["project_name"] : [] });
    const load = new Load(1);
    const plan = { id: 2, comfyClass: "MiniMaxH3ChainPlanModern", inputs: [{ name: "plan_json_input", link: 1 }],
        widgets: [{ name: "run_name", value: run }], addInput(name, type, options) { this.inputs.push({ name, type, ...options, link: null }); },
        setDirtyCanvas() {},
    };
    graph._nodes.push(load, plan);
    graph.links[1] = { origin_id: 1, origin_slot: 0, target_id: 2, target_slot: 0 };
    return { graph, load, plan, extension, conversionCalls: () => conversionCalls };
}

test("old demo workflow gains project_name wiring on both old and current Comfy widget APIs", () => {
    for (const converted of [false, true]) {
        const { plan, load, graph, extension } = setup({ converted });
        extension.afterConfigureGraph();
        assert.equal(load.outputs.length, 4);
        assert.equal(load.outputs[3].name, "project_name");
        const input = plan.inputs.find(input => input.name === "run_name");
        assert.equal(graph.links[input.link].origin_slot, 3);
        assert.equal(graph.links[input.link].origin_id, load.id);
        load.onExecuted({ project_name: ["station_demo_0001"] });
        assert.equal(plan.widgets[0].value, "station_demo_0001");
        extension.afterConfigureGraph();
        assert.equal(load.outputs.length, 4);
        assert.equal(plan.inputs.length, 2);
    }
});

test("custom names, existing run links, asset ownership and old backend are preserved", () => {
    for (const kind of ["custom", "linked", "assets", "old_backend"]) {
        const options = kind === "custom" ? { run: "chosen_run" } : kind === "old_backend" ? { supports: false } : {};
        const { plan, load, extension } = setup(options);
        if (kind === "linked") plan.inputs.push({ name: "run_name", link: 99 });
        if (kind === "assets") plan.inputs.push({ name: "project_assets", link: 98 });
        extension.afterConfigureGraph();
        assert.equal(load.outputs.length, 3);
        assert.ok(plan.inputs.every(input => input.link !== 2));
        const before = plan.widgets[0].value;
        load.onExecuted({ project_name: ["other_project"] });
        assert.equal(plan.widgets[0].value, before);
    }
});

test("execution sync updates only the Load that supplies the linked project name", () => {
    const { load, graph, plan, extension } = setup();
    extension.afterConfigureGraph();
    const other = { id: 3, comfyClass: "MiniMaxH3ChainPlanModern", inputs: [], widgets: [{ name: "run_name", value: "other" }] };
    graph._nodes.push(other);
    load.onExecuted({ project_name: ["second_project"] });
    assert.equal(plan.widgets[0].value, "second_project");
    assert.equal(other.widgets[0].value, "other");
});
