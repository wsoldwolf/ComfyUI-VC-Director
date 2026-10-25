import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
const source = await readFile(new URL("../../web/js/utilities.js", import.meta.url), "utf8");
function create(name, widgets) {
    let extension;
    const context = vm.createContext({
        app: { registerExtension(value) { extension = value; } },
        queueMicrotask, setTimeout, Uint8Array, Uint32Array,
        btoa: value => Buffer.from(value, "binary").toString("base64"),
        crypto: { getRandomValues(array) { array[0] = 123; } },
        alert: message => { throw Error(message); },
    });
    vm.runInContext(source.replace(/^import .*;\r?$/gm, ""), context);
    class Node {
        constructor() { this.widgets = widgets; this.size = [420, 180]; }
        addWidget(type, name, value, callback, options) {
            const item = { type, name, value, callback, options }; this.widgets.push(item); return item;
        }
        setDirtyCanvas() {}
        setSize(size) { this.size = size; }
    }
    extension.beforeRegisterNodeDef(Node, { name });
    const node = new Node(); node.onNodeCreated(); return node;
}
test("randomize once stores a positive seed and switches to fixed", () => {
    const node = create("VCDirectorSeed32", [{ name: "seed", value: -1 }, { name: "mode", value: "random" }]);
    node.widgets.find(w => w.name === "randomize once").callback();
    assert.equal(node.widgets[0].value, 124);
    assert.equal(node.widgets[1].value, "fixed");
});
test("text drop embeds content and restores selected filename", async () => {
    const node = create("VCDirectorLoadTextFile", [
        { name: "file_data_base64", value: "" }, { name: "basename", value: "lyrics.txt" },
        { name: "browser_metadata_json", value: "{}" },
    ]);
    await node.onDragDrop({ dataTransfer: { files: [{ name: "scene.txt", size: 5,
        lastModified: 1, type: "text/plain", arrayBuffer: async () => Buffer.from("hello") }] } });
    assert.equal(node.widgets[0].value, "aGVsbG8=");
    node.onConfigure();
    assert.equal(node.widgets.find(w => w.name === "selected_file").value, "scene.txt");
    assert.equal(node.widgets[0].hidden, true);
    assert.equal(node.widgets.find(w => w.name === "selected_file").serialize, false);
});
