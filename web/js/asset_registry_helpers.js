// Pure helpers shared by the upload UI and its regression tests.
export const IMAGE_ACCEPT = ".png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff,.avif";
const IMAGE_EXTENSION = /\.(png|jpe?g|webp|gif|bmp|tiff?|avif)$/i;
const ASSET_ID = /^[a-z][a-z0-9_]*$/;

export function isImageFile(file) {
    return !!file && IMAGE_EXTENSION.test(file.name);
}

export function readAssets(value) {
    let entries;
    try { entries = JSON.parse(value || "[]"); }
    catch { throw new Error("assets_json のJSONを修正してから画像を追加してください。"); }
    if (!Array.isArray(entries)) throw new Error("assets_json は配列で指定してください。");
    const ids = new Set();
    for (const entry of entries) {
        if (!entry || typeof entry !== "object" || typeof entry.id !== "string" || !ASSET_ID.test(entry.id)) {
            throw new Error("素材IDは英小文字で始まる英小文字・数字・_で指定してください。");
        }
        if (ids.has(entry.id)) throw new Error(`素材IDが重複しています: ${entry.id}`);
        ids.add(entry.id);
    }
    return entries;
}

export function nextImageId(filename, entries) {
    let stem = filename.replace(/\.[^.]+$/, "").normalize("NFKC").toLowerCase()
        .replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
    if (!stem) stem = "image";
    if (!/^[a-z]/.test(stem)) stem = `image_${stem}`;
    const base = stem.endsWith("_image") || stem === "image" ? stem : `${stem}_image`;
    const ids = new Set(entries.map(entry => entry.id));
    let id = base;
    for (let index = 2; ids.has(id); index++) id = `${base}_${index}`;
    return id;
}

export function uploadedImage(entries, response, filename) {
    if (response.type !== "input" || typeof response.name !== "string" ||
        !response.name || /[/\\]/.test(response.name)) {
        throw new Error("画像アップロードの応答が不正です。");
    }
    const subfolder = String(response.subfolder || "").replace(/\\/g, "/");
    if (subfolder.startsWith("/") || subfolder.split("/").includes("..")) {
        throw new Error("画像アップロードの保存先が不正です。");
    }
    const locator = [subfolder.replace(/\/$/, ""), response.name].filter(Boolean).join("/") + " [input]";
    const existing = entries.find(entry => entry.kind === "image" && entry.locator === locator);
    return existing || { id: nextImageId(filename, entries), kind: "image", locator };
}

export function inputImageView(entry) {
    if (entry.kind !== "image" || typeof entry.locator !== "string" || !entry.locator.endsWith(" [input]")) return null;
    const relative = entry.locator.slice(0, -8).replace(/\\/g, "/");
    const parts = relative.split("/");
    if (parts.includes("..") || relative.startsWith("/") || relative.includes(":")) return null;
    return { filename: parts.pop(), subfolder: parts.join("/"), type: "input" };
}

export function renameAsset(entries, previousId, nextId) {
    if (!ASSET_ID.test(nextId)) throw new Error("素材IDは英小文字で始まる英小文字・数字・_で指定してください。");
    if (entries.some(entry => entry.id !== previousId && entry.id === nextId)) {
        throw new Error(`素材IDが重複しています: ${nextId}`);
    }
    return entries.map(entry => entry.id === previousId ? { ...entry, id: nextId } : entry);
}
