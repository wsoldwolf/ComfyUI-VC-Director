# 推論プロファイル

[runtime/gemma4.json](runtime/gemma4.json)はMV-Directorの配布設定から継承した初期値です。ノードとCLIが`core/inference/profile_loader.py`経由で読み込みます。

| 用途 | n_ctx | max_tokens |
|---|---:|---:|
| Enhancer | 24576 | 4096 |
| Planner | 24576 | 4096 |
| Compiler英訳 | 16384 | 4096 |
| Vision | 16384 | 1024 |

モデル名、seed、temperature、KV cache、オフロード等を同じファイルに保持します。呼出し側が明示した設定を優先し、実行設定は従来どおりhash／cache条件へ反映します。統合`Build`の計画処理はPlanner設定を使います。Style / Motion / Camera profileの選択機能はまだ実装していません。

UIでは`VC · Gemma Settings`から上書きできます。配布WFは`Build.inference_settings`へ接続済みです。未接続の既存WFは上記のprofile値を使います。計画用はEnhancer・Planner共通、翻訳用はCompiler、画像認識用はVisionへ適用します。

| UI項目 | 対象・意味 |
|---|---|
| `planning_n_ctx` / `planning_max_tokens` | シナリオの演出・Scene構成・詳細化 |
| `compiler_n_ctx` / `compiler_max_tokens` | 描写の英訳 |
| `vision_n_ctx` / `vision_max_tokens` | 参照画像の認識 |
| `gpu_layers` / `n_batch` | 計画・翻訳のGPU層数とprompt処理バッチ |
| `vision_gpu_layers` / `vision_n_batch` | Vision本体のGPU層数とバッチ。mmprojの配置は変更しない |
| `temperature` / `top_p` / `repetition_penalty` | 全段階のsampling設定 |
| `flash_attn` / `kv_cache_type` / `op_offload` | 全段階のllama.cpp実行設定 |

`gpu_layers`はllama.cppの`n_gpu_layers`へ渡します。`-1`は全層GPU、`0`はCPU、正の値はGPUへ載せる層数です。`n_ctx=0`はモデル既定のcontextを使います。通常は明示値を使い、出力上限だけでcontextを埋めないようにしてください。入力と出力が収まらない場合は既存の予算検査で停止します。

例として、計画を32768 contextへ拡張しつつ翻訳を16384、Visionを8192に保てます。メモリ不足時は該当段階の`n_ctx`・GPU層数・`n_batch`を調整します。推論条件の変更は成功キャッシュのキーへ反映し、計画・翻訳の実行設定と自動Visionの設定は保存revisionの`run_metadata.json`へ記録します。テキストのseedは従来どおり各ノードで設定します。自動Visionはprofileのseed、独立ImageToSubjectEMDはそのノードのseedを使います。

`keep_model_loaded`は公開していません。現行ノードは各段階の終了時にモデルを解放するためです。retry回数・必須台詞保護・Schema制約はこの設定ノードでは変更しません。
