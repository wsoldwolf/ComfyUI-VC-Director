import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("group switch preserves Turbo bypass across stop and language modes", async () => {
    let extension;
    const nodes = [
        { properties: { vcd_group: "video", vcd_active_mode: 4 }, mode: 2 },
        { properties: { vcd_group: "video", vcd_active_mode: 0 }, mode: 2 },
        { properties: { vcd_group: "video" }, mode: 2 },
        { properties: { vcd_group: "language" }, mode: 0 },
        { properties: {}, mode: 0 },
    ];
    globalThis.__vcdGroupTestApp = {
        registerExtension(value) { extension = value; },
        graph: { _nodes: nodes, setDirtyCanvas() {} },
    };
    try {
        const source = (await readFile(new URL("../../web/js/vc_director.js", import.meta.url), "utf8"))
            .replace('import { app } from "../../../scripts/app.js";', 'const app = globalThis.__vcdGroupTestApp;');
        await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
        const buttons = {};
        extension.nodeCreated({ comfyClass: "VCDirectorLoad", addWidget(type, name, value, action) { buttons[name] = action; } });
        buttons["動画生成へ切替"]();
        assert.deepEqual(nodes.map(n => n.mode), [4, 0, 0, 2, 0]);
        buttons["両方停止"]();
        assert.deepEqual(nodes.map(n => n.mode), [2, 2, 2, 2, 0]);
        buttons["言語生成へ切替"]();
        assert.deepEqual(nodes.map(n => n.mode), [2, 2, 2, 0, 0]);
        buttons["動画生成へ切替"]();
        assert.deepEqual(nodes.map(n => n.mode), [4, 0, 0, 2, 0]);
    } finally {
        delete globalThis.__vcdGroupTestApp;
    }
});
