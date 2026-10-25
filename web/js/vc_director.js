import { app } from "../../../scripts/app.js";

// Mute side-effect nodes; bypass is unsafe for typed carriers and file I/O.
app.registerExtension({
    name: "VC.Director.Groups",
    nodeCreated(node) {
        if (!["VCDirectorBuild", "VCDirectorLoad"].includes(node.comfyClass)) return;
        for (const [label, enabled] of [["言語生成へ切替", "language"], ["動画生成へ切替", "video"], ["両方停止", null]]) {
            node.addWidget("button", label, null, () => {
                for (const item of app.graph._nodes) {
                    const group = item.properties?.vcd_group;
                    if (group) item.mode = group === enabled ? (item.properties?.vcd_active_mode ?? 0) : 2;
                }
                app.graph.setDirtyCanvas(true, true);
            }, { serialize: false });
        }
    },
});
