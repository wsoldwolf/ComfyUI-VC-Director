"""Exact coverage of source requirements across an outline's Scenes."""
from collections import Counter

from ..errors import require


def beat_requirements(payload, beat_id):
    return [event for beat in payload["enhanced"]["beats"] if beat["id"] == beat_id
            for event in beat["required_events"]]


def validate_required_coverage(scenes, expected, beat_id):
    """Keep every requirement exactly once; never infer or discard source events."""
    require(len(expected) == len(set(expected)), "DUPLICATE_ID", beat_id,
            "必須イベントの定義IDが重複しています。Sceneへの割り当て前にIDを一意にしてください。")
    counts = Counter(identifier for scene in scenes for identifier in scene["required_event_ids"])
    missing = [identifier for identifier in expected if not counts[identifier]]
    duplicate = [f"{identifier}（{count}回）" for identifier, count in counts.items() if count > 1]
    allowed = set(expected)
    unexpected = [identifier for identifier in counts if identifier not in allowed]
    reasons = []
    for label, values in (("未割り当て", missing), ("重複", duplicate), ("未定義ID", unexpected)):
        if values:
            reasons.append(label + ": " + ", ".join(values))
    require(not reasons, "OUTLINE_REQUIRED", beat_id,
            f"必須イベントのScene割り当てが不正です（必要{len(expected)}件／出力{sum(counts.values())}件）。"
            + "；".join(reasons) + "。各IDを一度ずつ配置し、本文・台詞を削除しないでください。")
