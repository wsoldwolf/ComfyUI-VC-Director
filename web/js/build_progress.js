import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

const panels = new WeakMap();

app.registerExtension({
    name: "VC.Director.BuildProgress",
    nodeCreated(node) {
        if (node.comfyClass !== "VCDirectorBuild") return;
        const panel = document.createElement("div");
        panel.style.cssText = "padding:6px 10px;box-sizing:border-box;font:12px sans-serif;color:var(--input-text,#ddd);";
        const label = document.createElement("div");
        label.textContent = "VC Build · 待機中";
        label.style.cssText = "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:5px;";
        const bar = document.createElement("progress");
        bar.max = 1000;
        bar.value = 0;
        bar.style.cssText = "width:100%;height:10px;accent-color:#64a4ff;";
        panel.append(label, bar);
        const widget = node.addDOMWidget("vcd_build_progress", "vcd_build_progress", panel, { serialize: false });
        widget.serialize = false;
        widget.options ??= {};
        widget.options.serialize = false;
        widget.options.getMinHeight = () => 52;
        widget.computeSize = () => [300, 52];
        panels.set(node, { label, bar, sequence: 0, runId: null });
    },
});

api.addEventListener("vc_director.build_progress", ({ detail }) => {
    const node = app.graph?.getNodeById(detail.node_id);
    const panel = node && panels.get(node);
    if (!panel || !Number.isFinite(detail.value) || !Number.isFinite(detail.total) || detail.total <= 0) return;
    if (panel.runId === detail.run_id && detail.sequence <= panel.sequence) return;
    panel.runId = detail.run_id;
    panel.sequence = detail.sequence;
    panel.bar.max = detail.total;
    panel.bar.value = Math.max(0, Math.min(detail.total, detail.value));
    const percent = Math.floor(panel.bar.value / detail.total * 100);
    panel.label.textContent = `${detail.message} · ${percent}%`;
    panel.label.title = panel.label.textContent;
    panel.bar.title = "工程と生成出力に基づく進捗（残り時間の予測ではありません）";
    panel.bar.style.accentColor = detail.state === "error" ? "#e87878" : "#64a4ff";
    node.setDirtyCanvas?.(true, false);
});
