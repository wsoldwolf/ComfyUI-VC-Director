import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";
import { IMAGE_ACCEPT, isImageFile, readAssets, uploadedImage, inputImageView, renameAsset } from "./asset_registry_helpers.js";

function installUploader(node) {
    const assets = node.widgets?.find(widget => widget.name === "assets_json");
    if (!assets || node._vcdImageUploader) return;
    node._vcdImageUploader = true;
    const panel = document.createElement("div");
    panel.style.cssText = "height:100%;overflow:auto;box-sizing:border-box;padding:8px;background:var(--comfy-input-bg,#222);color:var(--input-text,#ddd);font:13px sans-serif;";
    const drop = document.createElement("div");
    drop.textContent = "ここにリファレンス画像をD&D（複数可）";
    drop.style.cssText = "border:1px dashed #888;border-radius:6px;padding:12px;text-align:center;";
    const choose = document.createElement("button");
    choose.type = "button";
    choose.textContent = "画像を選択して追加";
    choose.style.cssText = "margin:8px 0;padding:5px 10px;cursor:pointer;";
    const status = document.createElement("div");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.style.cssText = "white-space:pre-wrap;margin-bottom:8px;";
    const list = document.createElement("div");
    panel.append(drop, choose, status, list);
    const widget = node.addDOMWidget("vcd_images", "vcd_images", panel, { serialize: false });
    widget.serialize = false;
    widget.computeSize = () => [320, 240];
    widget.options ??= {};
    widget.options.serialize = false;
    widget.options.getMinHeight = () => 240;

    const message = (text, error = false) => {
        status.textContent = text;
        status.style.color = error ? "#ff9e9e" : "inherit";
    };
    const write = entries => {
        assets.value = JSON.stringify(entries, null, 2);
        assets.callback?.(assets.value);
        render();
        node.setDirtyCanvas(true, true);
    };
    function render() {
        list.replaceChildren();
        let entries;
        try { entries = readAssets(assets.value); }
        catch (error) { message(error.message, true); return; }
        for (const entry of entries.filter(item => item.kind === "image")) {
            const row = document.createElement("div");
            row.style.cssText = "display:flex;gap:8px;align-items:center;margin:6px 0;";
            const view = inputImageView(entry);
            if (view) {
                const image = document.createElement("img");
                image.src = api.apiURL(`/view?${new URLSearchParams(view)}`);
                image.alt = entry.id;
                image.style.cssText = "width:56px;height:56px;object-fit:contain;background:#111;";
                row.append(image);
            }
            const detail = document.createElement("div");
            detail.style.cssText = "flex:1;min-width:0;";
            const id = document.createElement("input");
            id.type = "text";
            id.value = entry.id;
            id.setAttribute("aria-label", "画像の素材ID");
            id.style.cssText = "width:100%;box-sizing:border-box;";
            id.addEventListener("change", () => {
                try {
                    write(renameAsset(readAssets(assets.value), entry.id, id.value.trim()));
                    message("素材IDを変更しました。EMDの画像IDも合わせて変更してください。");
                } catch (error) { id.value = entry.id; message(error.message, true); }
            });
            const hint = document.createElement("div");
            hint.textContent = `EMD: * 画像: ${entry.id}`;
            hint.title = entry.locator;
            hint.style.cssText = "font-size:12px;overflow-wrap:anywhere;margin-top:3px;";
            detail.append(id, hint);
            const remove = document.createElement("button");
            remove.type = "button";
            remove.textContent = "解除";
            remove.addEventListener("click", () => {
                try {
                    write(readAssets(assets.value).filter(item => item.id !== entry.id));
                    message("画像の登録を解除しました。保存ファイルは残ります。");
                } catch (error) { message(error.message, true); }
            });
            row.append(detail, remove);
            list.append(row);
        }
    }
    let pending = Promise.resolve();
    function enqueue(files) {
        // Snapshot FileList now; browsers may clear DataTransfer after the drop handler.
        const selected = Array.from(files || []);
        if (!selected.length) return pending;
        pending = pending.then(async () => {
            readAssets(assets.value); // Do not upload until existing registration is valid.
            choose.disabled = true;
            const registered = [], errors = [];
            try {
                for (const file of selected) {
                    if (!isImageFile(file)) { errors.push(`${file.name}: 対応する画像ファイルを選択してください。`); continue; }
                    message(`${file.name} を登録中…`);
                    try {
                        const body = new FormData();
                        body.append("image", file, file.name);
                        body.append("type", "input");
                        body.append("subfolder", "vc_director");
                        body.append("overwrite", "false");
                        const response = await api.fetchApi("/upload/image", { method: "POST", body });
                        if (!response.ok) throw new Error(`アップロード失敗（HTTP ${response.status}）`);
                        const uploaded = await response.json();
                        // Preserve JSON edits made while waiting for the upload.
                        const entries = readAssets(assets.value);
                        const entry = uploadedImage(entries, uploaded, file.name);
                        if (!entries.includes(entry)) write([...entries, entry]);
                        registered.push(entry.id);
                    } catch (error) { errors.push(`${file.name}: ${error.message}`); }
                }
                message([registered.length ? `登録済み: ${registered.join(", ")}` : "", ...errors].filter(Boolean).join("\n"), errors.length > 0);
            } finally { choose.disabled = false; }
        }).catch(error => message(error.message, true));
        return pending;
    }
    choose.addEventListener("click", () => {
        const picker = document.createElement("input");
        picker.type = "file";
        picker.accept = IMAGE_ACCEPT;
        picker.multiple = true;
        picker.addEventListener("change", () => enqueue(picker.files));
        picker.click();
    });
    // Handle both the DOM widget and the surrounding node canvas.
    panel.addEventListener("dragover", event => { event.preventDefault(); event.stopPropagation(); });
    panel.addEventListener("drop", event => {
        event.preventDefault();
        event.stopPropagation();
        enqueue(event.dataTransfer?.files);
    });
    const previousDrop = node.onDragDrop;
    node.onDragDrop = function (event) {
        if (event?.dataTransfer?.files?.length) { enqueue(event.dataTransfer.files); return true; }
        return previousDrop?.call(this, event) ?? false;
    };
    const previousOver = node.onDragOver;
    node.onDragOver = function (event) {
        if (Array.from(event?.dataTransfer?.types || []).includes("Files")) return true;
        return previousOver?.call(this, event) ?? false;
    };
    const previousCallback = assets.callback;
    assets.callback = function () { const result = previousCallback?.apply(this, arguments); render(); return result; };
    const previousConfigure = node.onConfigure;
    node.onConfigure = function () { const result = previousConfigure?.apply(this, arguments); render(); return result; };
    message("画像を追加し、表示された素材IDをEMDの画像欄へ指定してください。");
    render();
}

app.registerExtension({
    name: "VC.Director.ImageRegistry",
    nodeCreated(node) {
        if (node.comfyClass === "VCDirectorAssetRegistry") installUploader(node);
    },
});
