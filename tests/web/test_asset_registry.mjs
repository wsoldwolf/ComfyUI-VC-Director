import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const helperSource = await readFile(new URL("../../web/js/asset_registry_helpers.js", import.meta.url), "utf8");
const helpers = await import(`data:text/javascript;base64,${Buffer.from(helperSource).toString("base64")}`);
const source = await readFile(new URL("../../web/js/asset_registry.js", import.meta.url), "utf8");
const flush = () => new Promise(setImmediate);

class Element {
    constructor(tagName) { this.tagName = tagName; this.style = {}; this.children = []; this.events = {}; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(name, callback) { this.events[name] = callback; }
    click() { this.events.click?.(); }
}

function uploader(initial = [], fetchApi = async () => ({ ok: true, json: async () => ({ name: "hero.png", subfolder: "vc_director", type: "input" }) })) {
    let extension, panel;
    const assets = { name: "assets_json", value: typeof initial === "string" ? initial : JSON.stringify(initial) };
    const root = { name: "asset_root", value: "D:/reference_materials" };
    const node = {
        comfyClass: "VCDirectorAssetRegistry", widgets: [assets, root],
        addDOMWidget(name, type, element, options) { panel = element; const widget = { name, type, options }; this.widgets.push(widget); return widget; },
        setDirtyCanvas() {},
    };
    const context = vm.createContext({
        ...helpers, FormData, URLSearchParams,
        app: { registerExtension(value) { extension = value; } },
        api: { fetchApi, apiURL: path => path },
        document: { createElement: tag => new Element(tag) },
    });
    vm.runInContext(source.replace(/^import .*;\r?$/gm, ""), context);
    extension.nodeCreated(node);
    const drop = files => node.onDragDrop({ dataTransfer: { files } });
    return { node, assets, root, panel, drop, status: () => panel.children[2].textContent, rows: () => panel.children[3].children };
}

test("IDs handle duplicate names, Japanese names, and leading digits", () => {
    assert.equal(helpers.nextImageId("Hero.png", []), "hero_image");
    assert.equal(helpers.nextImageId("hero.png", [{ id: "hero_image" }]), "hero_image_2");
    assert.equal(helpers.nextImageId("少女.png", [{ id: "image" }]), "image_2");
    assert.equal(helpers.nextImageId("01_hero.webp", []), "image_01_hero_image");
    assert.equal(helpers.nextImageId("hero_image.jpg", []), "hero_image");
});

test("upload results use the returned filename and preserve duplicate registrations", () => {
    const response = { name: "hero (1).png", subfolder: "vc_director", type: "input" };
    const entry = helpers.uploadedImage([], response, "hero.png");
    assert.equal(entry.locator, "vc_director/hero (1).png [input]");
    assert.equal(helpers.uploadedImage([entry], response, "hero.png"), entry);
    assert.throws(() => helpers.uploadedImage([], { ...response, type: "output" }, "hero.png"));
    assert.throws(() => helpers.uploadedImage([], { ...response, subfolder: "../escape" }, "hero.png"));
});

test("invalid registry or conflicting IDs cannot replace existing assets", () => {
    assert.throws(() => helpers.readAssets("broken"));
    assert.throws(() => helpers.readAssets("{}"));
    assert.throws(() => helpers.readAssets('[{"kind":"image","locator":"hero.png"}]'));
    assert.throws(() => helpers.readAssets('[{"id":123}]'));
    assert.throws(() => helpers.readAssets('[{"id":"hero"},{"id":"hero"}]'));
    const entries = [{ id: "hero_image" }, { id: "station_image" }];
    assert.throws(() => helpers.renameAsset(entries, "hero_image", "station_image"));
    assert.throws(() => helpers.renameAsset(entries, "hero_image", "日本語"));
    assert.equal(helpers.renameAsset(entries, "hero_image", "friend_image")[0].id, "friend_image");
    assert.equal(entries[0].id, "hero_image");
});

test("multiple dropped images upload without overwriting and retain other materials", async () => {
    const requests = [];
    const voice = { id: "voice", kind: "audio", locator: "voice.wav", sha256: "a".repeat(64) };
    const ui = uploader([voice], async (url, options) => {
        requests.push({ url, ...options });
        return { ok: true, json: async () => ({ name: `hero${requests.length}.png`, subfolder: "vc_director", type: "input" }) };
    });
    assert.equal(ui.drop([new File(["image1"], "hero.png"), new File(["image2"], "hero.png")]), true);
    await flush();
    const entries = JSON.parse(ui.assets.value);
    assert.deepEqual(entries[0], voice);
    assert.deepEqual(entries.slice(1).map(item => item.id), ["hero_image", "hero_image_2"]);
    assert.equal(entries[1].locator, "vc_director/hero1.png [input]");
    assert.equal(ui.root.value, "D:/reference_materials");
    assert.equal(ui.rows().length, 2);
    assert.equal(ui.node.widgets.at(-1).serialize, false);
    assert.equal(ui.node.widgets.at(-1).options.serialize, false);
    for (const request of requests) {
        assert.equal(request.url, "/upload/image");
        assert.equal(request.body.get("type"), "input");
        assert.equal(request.body.get("subfolder"), "vc_director");
        assert.equal(request.body.get("overwrite"), "false");
    }
});

test("upload failure leaves existing material unchanged and reports the error", async () => {
    const existing = { id: "hero_image", kind: "image", locator: "hero.png" };
    const ui = uploader([existing], async () => ({ ok: false, status: 413 }));
    ui.drop([new File(["image"], "friend.png")]);
    await flush();
    assert.deepEqual(JSON.parse(ui.assets.value), [existing]);
    assert.match(ui.status(), /413/);
    assert.equal(ui.panel.children[1].disabled, false);
});

test("invalid JSON blocks uploads, and registration recovers after correction", async () => {
    let requests = 0;
    const ui = uploader("broken", async () => { requests++; return { ok: true, json: async () => ({ name: "hero.png", subfolder: "vc_director", type: "input" }) }; });
    ui.drop([new File(["image"], "hero.png")]);
    await flush();
    assert.equal(requests, 0);
    ui.assets.value = "[]";
    ui.drop([new File(["image"], "hero.png")]);
    await flush();
    assert.equal(requests, 1);
    assert.equal(JSON.parse(ui.assets.value)[0].id, "hero_image");
});

test("workflow restoration, ID edits, and removal update the serialized registry", () => {
    const ui = uploader([{ id: "hero_image", kind: "image", locator: "vc_director/hero.png [input]" }]);
    ui.assets.value = '[{"id":"station_image","kind":"image","locator":"vc_director/station.png [input]"}]';
    ui.node.onConfigure();
    let row = ui.rows()[0];
    assert.match(row.children[0].src, /station.png/);
    const id = row.children[1].children[0];
    id.value = "town_image";
    id.events.change();
    assert.equal(JSON.parse(ui.assets.value)[0].id, "town_image");
    row = ui.rows()[0];
    assert.match(row.children[1].children[1].textContent, /town_image/);
    row.children[2].click();
    assert.deepEqual(JSON.parse(ui.assets.value), []);
});

test("edits and another drop during upload are preserved", async () => {
    let release, calls = 0;
    const wait = new Promise(resolve => { release = resolve; });
    const ui = uploader([], async () => {
        if (++calls === 1) await wait;
        return { ok: true, json: async () => ({ name: `image${calls}.png`, subfolder: "vc_director", type: "input" }) };
    });
    ui.drop([new File(["image"], "hero.png")]);
    await flush();
    ui.assets.value = '[{"id":"voice","kind":"audio","locator":"voice.wav"}]';
    ui.drop([new File(["image"], "station.png")]);
    release();
    await flush();
    assert.deepEqual(JSON.parse(ui.assets.value).map(item => item.id), ["voice", "hero_image", "station_image"]);
});
