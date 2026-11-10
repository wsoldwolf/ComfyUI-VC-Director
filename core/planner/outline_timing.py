"""Recover outline timing by repartitioning a bounded beat, never truncating it."""
from ..errors import require


def recovery_intervals(payload, result, beat_id):
    accepted = payload["accepted_outlines"]
    start = accepted[-1]["end_ms"] if accepted else 0
    beats = payload["scenario"]["beats"]
    index = next(i for i, beat in enumerate(beats) if beat["id"] == beat_id)
    total = payload["scenario"]["duration_ms"]
    window = next((w for w in payload.get("beat_windows", []) if w["beat_id"] == beat_id), None)
    if window:
        end = window["end_ms"]
    elif index == len(beats) - 1 and total is not None:
        end = total
    else:
        scenes = result.get("scenes", [])
        if not scenes:
            return None
        end = scenes[-1]["end_ms"]
        # A draft may supply a usable beat endpoint, but cannot take time from
        # later beats. Leave an invalid global allocation to the budget planner.
        if total is not None and end > total - (len(beats) - index - 1):
            return None
    if type(end) is not int or end <= start:
        return None
    return [[cursor, min(cursor + 15000, end)] for cursor in range(start, end, 15000)]


def validate_intervals(result, intervals, beat_id):
    actual = [[scene["start_ms"], scene["end_ms"]] for scene in result["scenes"]]
    require(actual == intervals, "OUTLINE_TIMING", beat_id,
            f"時間修正済みのScene区間を維持してください。必要: {intervals}ms、実際: {actual}ms。"
            "出来事・台詞を削除せず、各Sceneを15000ms以内に分割してください。")
