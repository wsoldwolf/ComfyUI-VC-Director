import { app } from "../../../scripts/app.js";

const MAX_TEXT_BYTES = 16 * 1024 * 1024;

function widget(node, name) {
  return node.widgets?.find((item) => item.name === name);
}

function hideSerializedWidget(item) {
  if (!item) return;
  item.hidden = true;
  item.type = "hidden";
  item.computeSize = () => [0, -4];
  item.draw = () => {};
  for (const element of [item.element, item.inputEl]) {
    element?.style?.setProperty?.("display", "none", "important");
  }
}

function normalizeTextFileNodeSize(node) {
  queueMicrotask(() => {
    const width = Math.max(Number(node.size?.[0]) || 0, 320);
    const height = Number(node.size?.[1]) || 0;
    if (height < 110 || height > 260) node.setSize([width, 180]);
  });
}

function syncSelectedFileDisplay(node) {
  const update = () => {
    const display = widget(node, "selected_file");
    if (!display) return;
    const basename = String(widget(node, "basename")?.value ?? "").trim();
    display.value = basename || "No file selected";
    node.setDirtyCanvas(true, true);
  };
  update();
  queueMicrotask(update);
  setTimeout(update, 0);
}

function bytesToBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let index = 0; index < bytes.length; index += chunk) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunk));
  }
  return btoa(binary);
}

async function embedTextFile(node, file) {
  if (!file?.name?.toLowerCase().endsWith(".txt")) {
    throw new Error("VC Director Load Text File accepts .txt only.");
  }
  if (file.size > MAX_TEXT_BYTES) {
    throw new Error("The text file exceeds the 16 MiB limit.");
  }
  const bytes = new Uint8Array(await file.arrayBuffer());
  widget(node, "file_data_base64").value = bytesToBase64(bytes);
  widget(node, "basename").value = file.name;
  widget(node, "browser_metadata_json").value = JSON.stringify({
    lastModified: file.lastModified,
    size: file.size,
    type: file.type || "text/plain",
  });
  syncSelectedFileDisplay(node);
  node.setDirtyCanvas(true, true);
}

app.registerExtension({
  name: "VCDirector.Utilities",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name === "VCDirectorSeed32") {
      const original = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function () {
        const result = original?.apply(this, arguments);
        this.addWidget("button", "randomize once", null, () => {
          const data = new Uint32Array(1);
          crypto.getRandomValues(data);
          widget(this, "seed").value = (data[0] % 2147483647) + 1;
          widget(this, "mode").value = "fixed";
          this.setDirtyCanvas(true, true);
        });
        return result;
      };
    }

    if (nodeData.name === "VCDirectorLoadTextFile") {
      const original = nodeType.prototype.onNodeCreated;
      const originalConfigure = nodeType.prototype.onConfigure;
      nodeType.prototype.onNodeCreated = function () {
        const result = original?.apply(this, arguments);
        hideSerializedWidget(widget(this, "file_data_base64"));
        hideSerializedWidget(widget(this, "basename"));
        hideSerializedWidget(widget(this, "browser_metadata_json"));
        const display = this.addWidget("text", "selected_file", "No file selected", () => {}, { serialize: false });
        display.serialize = false;
        display.options ??= {};
        display.options.serialize = false;
        this.addWidget("button", "select .txt file", null, () => {
          const input = document.createElement("input");
          input.type = "file";
          input.accept = ".txt,text/plain";
          input.onchange = async () => {
            try { await embedTextFile(this, input.files?.[0]); }
            catch (error) { alert(error.message); }
          };
          input.click();
        });
        const originalDrop = this.onDragDrop;
        this.onDragDrop = async (event) => {
          const file = event?.dataTransfer?.files?.[0];
          if (!file) return originalDrop?.call(this, event) ?? false;
          try { await embedTextFile(this, file); }
          catch (error) { alert(error.message); }
          return true;
        };
        const originalOver = this.onDragOver;
        this.onDragOver = (event) => {
          if (event?.dataTransfer?.types?.includes("Files")) return true;
          return originalOver?.call(this, event) ?? false;
        };
        syncSelectedFileDisplay(this);
        normalizeTextFileNodeSize(this);
        return result;
      };
      nodeType.prototype.onConfigure = function () {
        const result = originalConfigure?.apply(this, arguments);
        hideSerializedWidget(widget(this, "file_data_base64"));
        hideSerializedWidget(widget(this, "basename"));
        hideSerializedWidget(widget(this, "browser_metadata_json"));
        syncSelectedFileDisplay(this);
        normalizeTextFileNodeSize(this);
        return result;
      };
    }
  },
});
