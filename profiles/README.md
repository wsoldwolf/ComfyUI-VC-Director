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

Scene構成の速度はGPUのSM使用率・消費電力に加え、診断ログの`decode_tokens_per_second`、`sampling_seconds`、`evaluation_seconds`で確認できます。`sampling_seconds`は構文検査・候補選択と、必要なGPU logits受信待ちを含むsample呼び出しの時間です。`evaluation_seconds`はpromptと生成tokenのeval呼び出しの時間です。CPU側の一つの処理が律速すると、CPU全体の使用率が低くてもGPUが待つ場合があります。`n_batch`は主にprompt評価の設定で、単一Sceneの逐次生成をその数だけ同時実行する設定ではありません。

標準llama.cppのtop-k samplingには候補検査の高速経路を自動適用します。上位候補から、従来の全語彙検査と同じtop-k又はtop-p/min-pの生存候補になることを確認できる場合だけ、後段の検査対象を絞ります。確定できない場合・同点・不明なsampling構成では全語彙検査へ戻り、native grammarと元のsamplerは必ず実行します。新しい設定操作やcache削除は不要です。効果はモデルと生成内容で変わるため、使用率100%を前提にせず実行時間で比較してください。反映にはComfyUI再起動が必要です。
