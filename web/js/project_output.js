import { app } from "../../../scripts/app.js";

const LEGACY_RUN = "vc_director_scenario_demo";
const PLANS = new Set(["MiniMaxH3ChainPlanModern", "MiniMaxH3ChainPlan"]);
let supportsProjectName = false;

function projectSlot(node) {
    return node.outputs?.findIndex(output => output.name === "project_name") ?? -1;
}

function connectedLoad(plan, inputName) {
    const input = plan.inputs?.find(input => input.name === inputName);
    const link = input?.link != null ? app.graph.links[input.link] : null;
    const source = link && app.graph.getNodeById(link.origin_id);
    return source?.comfyClass === "VCDirectorLoad" ? { node: source, link } : null;
}

app.registerExtension({
    name: "VC.Director.ProjectOutput",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "VCDirectorLoad") return;
        supportsProjectName = nodeData.output_name?.includes("project_name") ?? false;
        const original = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const result = original?.apply(this, arguments);
            const name = message?.project_name?.[0];
            if (typeof name !== "string" || !name) return result;
            // Keep Context Loop's Output button and displayed Run name in sync
            // with the actual linked value, without invoking project rename actions.
            for (const plan of app.graph?._nodes ?? []) {
                if (!PLANS.has(plan.comfyClass)) continue;
                const connected = connectedLoad(plan, "run_name");
                if (connected?.node !== this || connected.link.origin_slot !== projectSlot(this)) continue;
                const widget = plan.widgets?.find(widget => widget.name === "run_name");
                if (widget) widget.value = name;
                plan.setDirtyCanvas?.(true, true);
            }
            return result;
        };
    },
    afterConfigureGraph() {
        if (!supportsProjectName) return; // Backend must be restarted first.
        for (const plan of app.graph?._nodes ?? []) {
            if (!PLANS.has(plan.comfyClass)) continue;
            const widget = plan.widgets?.find(widget => widget.name === "run_name");
            const runInput = plan.inputs?.find(input => input.name === "run_name");
            if (widget?.value !== LEGACY_RUN || runInput?.link != null) continue;
            if (plan.inputs?.some(input => input.name === "project_assets" && input.link != null)) continue;
            const source = connectedLoad(plan, "plan_json_input");
            if (!source || source.link.origin_slot !== 0) continue;
            const convert = globalThis.comfyAPI?.widgetInputs?.convertToInput;
            if (!runInput && typeof convert !== "function") continue;
            let slot = projectSlot(source.node);
            if (slot < 0) {
                // Older serialized Load nodes contain only the original three outputs.
                source.node.addOutput("project_name", "STRING");
                slot = projectSlot(source.node);
            }
            if (!runInput) convert(plan, widget, ["STRING", {}]);
            // Current ComfyUI keeps widgets and sockets together; its legacy
            // convertToInput is a no-op, so restore a missing serialized socket.
            if (!plan.inputs.some(input => input.name === "run_name")) {
                plan.addInput("run_name", "STRING", { widget: { name: "run_name" } });
            }
            const input = plan.inputs.findIndex(input => input.name === "run_name");
            if (input >= 0) source.node.connect(slot, plan, input);
        }
    },
});
