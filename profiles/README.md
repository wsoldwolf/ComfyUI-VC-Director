# 推論プロファイル

[runtime/gemma4.json](runtime/gemma4.json)はMV-Directorの配布設定から継承した初期値です。ノードとCLIが`core/inference/profile_loader.py`経由で読み込みます。

| 用途 | n_ctx | max_tokens |
|---|---:|---:|
| Enhancer | 24576 | 4096 |
| Planner | 24576 | 4096 |
| Compiler英訳 | 16384 | 4096 |
| Vision | 16384 | 1024 |

モデル名、seed、temperature、KV cache、オフロード等を同じファイルに保持します。呼出し側が明示した設定を優先し、実行設定は従来どおりhash／cache条件へ反映します。統合`Build`の計画処理はPlanner設定を使います。Style / Motion / Camera profileの選択機能はまだ実装していません。
