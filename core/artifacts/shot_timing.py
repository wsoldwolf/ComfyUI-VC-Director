"""Validate the temporal Shot scaffold before checking its event contents."""
from ..errors import require


def validate_shot_coverage(scene):
    duration = scene["end_ms"] - scene["start_ms"]
    endpoint = 0
    for shot in scene["shots"]:
        require(shot["start_ms"] == endpoint and endpoint < shot["end_ms"] <= duration,
                "SHOT_COVERAGE", shot["id"],
                f"生成Shot {shot['id']} の区間 {shot['start_ms']}..{shot['end_ms']}ms が不正です。"
                f"必要な開始は直前Shotの終了 {endpoint}ms、Sceneの終端は {duration}msです。"
                + ("前のShotがScene終端まで使っており、後続Shotの時間が残っていません。" if endpoint >= duration else "")
                + "Shotを削除せず、全Shotへ正の長さを割り当て、0msからScene終端まで空白・重複なく再配分してください。")
        endpoint = shot["end_ms"]
    require(endpoint == duration, "SHOT_COVERAGE", scene["id"],
            f"生成ShotがScene全体を覆っていません。必要な区間: 0..{duration}ms、"
            f"最後のShotの終了: {endpoint}ms、差: {duration - endpoint}ms。"
            "Sceneの尺・出来事・台詞を保持し、Shotの区間を連続させてください。")
