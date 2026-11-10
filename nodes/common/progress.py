"""Map real Build work to ComfyUI's node bar and a read-only status widget."""
import sys
from time import monotonic
from uuid import uuid4

TOTAL = 1000
LABELS = dict(prepare="入力確認", vision="画像分析", enhance="シナリオ補強", outline="Scene構成",
              detail="Scene詳細化", compile="翻訳・コンパイル", save="保存")


class BuildProgress:
    def __init__(self, mode, vision=False, *, node_id=None, bar=None, send=None):
        phases = [("prepare", 2)]
        if mode == "scenario":
            if vision:
                phases.append(("vision", 10))
            phases.extend((("enhance", 10), ("outline", 20), ("detail", 40)))
        phases.extend((("compile", 26 if mode == "scenario" else 94), ("save", 2 if mode == "scenario" else 4)))
        weight = sum(w for _, w in phases)
        self.ranges, cursor = {}, 0
        for stage, amount in phases:
            self.ranges[stage] = (cursor / weight * TOTAL, (cursor + amount) / weight * TOTAL)
            cursor += amount
        self.node_id = str(node_id) if node_id is not None else None
        self.bar, self.send = bar, send
        self.value, self.stage, self.index, self.units = 0, "prepare", 0, 1
        self.floor, self.chunks, self.active = 0, 0, False
        self.label, self.status, self.state = LABELS["prepare"], "", "running"
        self.loading = False
        self.run_id, self.sequence, self.last_sent = uuid4().hex, 0, 0.0
        self._publish(force=True)

    def _publish(self, *, force=False):
        if self.state == "running":
            start, end = self.ranges[self.stage]
            start = max(start, self.floor)
            # Work-based activity inside the current unit, never a clock timer
            # or a prediction of when the model will stop generating.
            fraction = min(0.9, self.chunks / (self.chunks + 256)) if self.active else 0
            position = (self.index + fraction) / max(1, self.units)
            self.value = max(self.value, min(TOTAL - 1, int(start + (end - start) * position)))
        now = monotonic()
        if not force and now - self.last_sent < 0.2:
            return
        self.last_sent = now
        # A cancellation hook may raise here. Do not replace the original error
        # with a second progress-hook exception while reporting failure.
        if self.bar is not None and self.state != "error":
            self.bar.update_absolute(self.value, TOTAL)
        self.sequence += 1
        status = "モデル読込中" if self.loading else self.status
        message = " · ".join(part for part in (self.label, status) if part)
        if self.send is not None:
            self.send(dict(node_id=self.node_id, run_id=self.run_id, sequence=self.sequence,
                           value=self.value, total=TOTAL, stage=self.stage, state=self.state, message=message))

    def __call__(self, event, **data):
        if event == "activity":
            if self.active:
                self.chunks += 1
                self._publish()
            return
        if event == "model":
            self.loading = data["loading"]
        elif event == "status":
            self.status = data["label"]
        elif event == "restart":
            self.floor = self.value
            self.index, self.chunks, self.active = 0, 0, False
            self.status = data.get("label", "再構成中")
        elif event in {"stage", "unit", "done", "stage_done"}:
            stage = data["stage"]
            if stage not in self.ranges:
                return
            if stage != self.stage:
                self.floor = self.ranges[stage][0]
            self.stage = stage
            self.units = max(1, data.get("total", 1))
            self.index = data.get("index", 0)
            self.chunks, self.active, self.status = 0, event == "unit", ""
            if event == "done":
                self.index += 1
            elif event == "stage_done":
                self.index = self.units
            count = f" {min(self.index + (1 if self.active else 0), self.units)}/{self.units}" if self.units > 1 else ""
            self.label = LABELS[stage] + count
            if data.get("label"):
                self.label += " · " + data["label"]
        elif event == "finish":
            previous = self.value, self.label, self.status, self.state, self.loading
            self.value, self.label, self.status, self.state = TOTAL, "完了", "", "complete"
            self.loading = False
            try:
                self._publish(force=True)
            except BaseException:
                # ComfyUI checks cancellation inside its final progress hook too.
                self.value, self.label, self.status, self.state, self.loading = previous
                raise
            return
        elif event == "failed":
            self.label, self.status, self.state = "中断またはエラー", "", "error"
            self.loading = False
        else:
            return
        self._publish(force=True)


def create_build_progress(mode, vision, node_id):
    try:
        from comfy.utils import ProgressBar
    except ImportError:
        bar = None
    else:
        bar = ProgressBar(TOTAL, node_id=str(node_id) if node_id is not None else None)
    module = sys.modules.get("server")
    server = getattr(getattr(module, "PromptServer", None), "instance", None)
    send = None
    if server is not None:
        client_id = server.client_id
        send = lambda payload: server.send_sync("vc_director.build_progress", payload, client_id)
    return BuildProgress(mode, vision, node_id=node_id, bar=bar, send=send)
