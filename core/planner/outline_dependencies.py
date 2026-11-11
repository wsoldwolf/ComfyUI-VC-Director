"""Validate ordered Scene identities separately from source-owned event IDs."""
from collections import Counter

from ..errors import require


def validate_outline_dependencies(scene, accepted):
    known = [previous["id"] for previous in accepted]
    duplicate = scene["id"] in known
    repeated = [id for id, count in Counter(scene["depends_on"]).items() if count > 1]
    invalid = [id for id in scene["depends_on"] if id not in known]
    require(not duplicate and not repeated and not invalid, "OUTLINE_DEPENDENCY", scene["id"],
            f"生成Scene {scene['id']} のID・依存先が不正です。"
            + (f"Scene IDが確定済みSceneと重複: {scene['id']}。" if duplicate else "")
            + (f"同じ依存先の重複: {repeated}。" if repeated else "")
            + (f"未確定・自己参照・未定義の依存先: {invalid}。" if invalid else "")
            + f"参照可能なのは先行Sceneのみ: {known}。作品先頭のdepends_onは空配列です。"
              "作者の出来事IDや台詞IDを変更せず、生成SceneのIDと参照関係を修正してください。")
