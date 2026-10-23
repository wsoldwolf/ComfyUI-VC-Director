# 公開ノードと配置

ノードIDとsocketを維持し、MV-Directorと同じ`nodes/node_*/node.py`単位に分割しています。登録は`nodes/__init__.py`、共通処理は`nodes/common/`です。

| ノードID | 実装ディレクトリ | 用途 |
|---|---|---|
| `VCDirectorAssetRegistry` | `node_asset_registry` | 共有素材のID・path・hash |
| `VCDirectorEnhancer` | `node_direction_enhancer` | シナリオの意図・必須出来事 |
| `VCDirectorTimelinePlanner` | `node_timeline_planner` | Scene構成・詳細化 |
| `VCDirectorCompileSave` | `node_emd_compiler` | Scene EMDの検証・翻訳・保存 |
| `VCDirectorBuild` | `node_story_builder` | scenario / saved_sceneの統合入口 |
| `VCDirectorLoad` | `node_plan_load` | 保存revisionの検証・読込 |
| `VCDirectorRef2VA` | `node_ref2va` | Scene別素材を標準Ref2VAへ接続 |
| `VCDirectorReferenceVideo` | `node_reference_video` | 参照動画を24fpsへdecode |
| `VCDirectorImageToSubjectEMD` | `node_image_to_subject_emd` | 観察結果から人物／背景EMD断片 |

配布WFの操作は[workflows](../../workflows/README.md)、文法・音響・時間は[仕様](../spec/README.md)を参照してください。
