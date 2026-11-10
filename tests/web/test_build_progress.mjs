import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../web/js/build_progress.js", import.meta.url), "utf8");

function setup() {
    let extension;
    const listeners = new Map();
    const nodes = new Map();
    const context = vm.createContext({
        app: {
            registerExtension(value) { extension = value; },
            graph: { getNodeById(id) { return nodes.get(String(id)); } },
        },
        api: { addEventListener(name, callback) { listeners.set(name, callback); } },
        document: { createElement(tag) { return { tag, style: {}, children: [], append(...items) { this.children.push(...items); } }; } },
    });
    vm.runInContext(source.replace(/^import .*;\r?$/gm, ""), context);
    const create = (id, comfyClass = "VCDirectorBuild") => {
        const node = { id, comfyClass, widgets: [], setDirtyCanvas() {},
            addDOMWidget(name, type, element, options) {
                const widget = { name, type, element, options }; this.widgets.push(widget); return widget;
            },
        };
        nodes.set(String(id), node);
        extension.nodeCreated(node);
        return node;
    };
    const send = (node, overrides = {}) => listeners.get("vc_director.build_progress")({ detail: {
        node_id: String(node.id), run_id: "run_a", sequence: 1, value: 480, total: 1000,
        state: "running", message: "Scene詳細化 2/3 · scene_b · 生成中", ...overrides,
    } });
    return { create, send };
}

test("Build status is node-specific, read-only and excluded from workflow values", () => {
    const { create, send } = setup();
    const first = create(1), second = create(2), other = create(3, "VCDirectorLoad");
    assert.equal(other.widgets.length, 0);
    const widget = first.widgets[0];
    assert.equal(widget.serialize, false);
    assert.equal(widget.options.serialize, false);
    assert.equal(widget.computeSize()[1], widget.options.getMinHeight());
    send(first);
    const [label, bar] = widget.element.children;
    assert.equal(bar.value, 480);
    assert.match(label.textContent, /2\/3.*48%/);
    assert.equal(second.widgets[0].element.children[1].value, 0);
});

test("failure keeps partial progress, completion reaches 100 and a new run resets it", () => {
    const { create, send } = setup();
    const node = create(42);
    const [label, bar] = node.widgets[0].element.children;
    send(node);
    send(node, { sequence: 2, state: "error", message: "中断またはエラー" });
    assert.equal(bar.value, 480);
    assert.match(label.textContent, /中断またはエラー/);
    assert.equal(bar.style.accentColor, "#e87878");
    send(node, { sequence: 1, value: 0 }); // Outdated message in the same run.
    assert.equal(bar.value, 480);
    send(node, { sequence: 3, value: 1000, state: "complete", message: "完了" });
    assert.equal(label.textContent, "完了 · 100%");
    send(node, { run_id: "run_b", sequence: 1, value: 0, message: "入力確認" });
    assert.equal(bar.value, 0);
    assert.equal(bar.style.accentColor, "#64a4ff");
});
