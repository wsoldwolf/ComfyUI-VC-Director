# 初期実装・検証状況

2026-10-24 / version 0.1.0

## 実装した経路

| 範囲 | 実装 |
|---|---|
| EMD | シナリオ／Scene parser、位置付き文法エラー、Scene renderer、出典の保持 |
| 型・整合 | 公開JSON Schemaでの検証、ID、出演者、背景、台詞、依存、状態、時間包含、必須出来事 |
| 音響 | なしを既定とする許可、全無音／発声禁止／指定台詞、BGM抑制、参照用途の照合 |
| 発話 | 「」からJapanese dタグへPython変換、既存dタグ無変換、翻訳対象から除外、@記号の保持 |
| 時間 | 24fps・17k+5・Guide22、明示audio context、raw/output event境界と格子余剰の記録 |
| Compiler | global人物定義順のSubject割当、Scene別素材番号とschedule、六セクションのPlan |
| 推論 | MVのlazy llama.cpp、事前予算、保護付き全field翻訳、成功cache、中断・失敗時の解放 |
| 計画コア | Enhancer、展開別outline、Scene別detail、数値のみのretime API、Schemaと最大3回の有限回復 |
| 画像 | MVの画像観察・hint・可視性処理・GGUF/mmproj探索・成功cache、VC人物／背景断片へのadapter |
| 保存 | 不変revision、一時保存とatomic pointer、hash照合、翻訳traceを使う決定的な再コンパイル |
| ComfyUI | 9ノード、共有ファイル素材、native Ref2VAへの動的配線、単一WF、Mute切替ボタン |

## 検証したこと

- Python unittest 43件で、公開JSON例の正式なDraft 2020-12検証、EMD往復、台詞保護、音響・参照・時刻、成功cache、改変検出、移植ファイルと契約の整合を検証。
- 最小Scene EMDからCLIでPlan / executionを保存し、保存revisionを再読込・再コンパイルしてpreflightに成功。
- 導入済みContext Loopのsource hashがContractと一致。
- 単一WFの28ノード・56リンクを導入済み登録Schemaと照合。型・slot・リンク整合と初期Mute状態を確認。
- Context Loopの実Plan正規化関数にCompiler出力を渡し、prompt・raw frame・明示audio context・generated_audioを確認。
- 画像観察fixtureから人物／背景断片への変換、曖昧な特徴の除外、IMAGE通過を検証。合成参照動画を12→24fpsへ変換し、音声のopt-inもCPUで確認。
- オフラインのwheelビルドに成功。
- MV形式への配置変更後も移動前のPlan / execution fixtureと一致し、配布WFもbyte単位で不変。公開9ノードの入力・出力・実行名を移動前snapshotと照合。
- ComfyUI相当のpackage importで、グローバル`nodes`との衝突やtorch / llama_cppの先行ロードがないことを確認。隔離したwheelだけからprofile / promptを読み、CLI compile / preflightに成功。
- トップの`vc_director/`と`vendor/`を廃止。CLIは`tools/cli.py`、再利用コードは`core/`、再利用promptは`prompts/`へ統合。移植元のhash・統合先・ライセンスは`mv-director-sources.json`に保持。

配置の詳細は[ディレクトリ構成](directory-layout.md)を参照してください。

テストで使うLLM応答は固定fixtureです。実Gemma4の成功率や、実H3の発声抑制を証明するものではありません。

## 実装中に修正した仕様

0.7.3では旧Scheduled公開ノードが廃止されていました。内部helperの存在と公開ノードの登録を分けて確認し、VCがScene別素材を検証してComfyUI標準`MiniMaxH3ReferenceToVideo`へ直接配線する方式へ訂正しました。alias処理と`reference_policy=disabled`を経由せず、発話中の@記号を保持します。既存プラグインの改変はありません。

## 次の受入検証

1. 実ComfyUIでWFを読み込み、切替ボタン、両方Mute、両方有効の拒否、通常Queueの動作を確認する。
2. 実Gemma4で1展開→複数Scene、構成と詳細化の回復、作者固定と状態保持を測定する。
3. 実Visionで人物／背景・複数view・hint conflictを確認する。
4. H3の短区間生成で全無音、指定台詞、物理音のみ、声質参照、続きのaudio contextを比較する。
5. 60秒から3〜5分へ拡大し、人物・背景・音響・context trimと生成音声の連結を確認する。

初期UIはモデル名・seedを中心に露出し、詳細なGPU・context設定はPython APIの`LlamaRuntimeConfig`で指定します。既存Sceneの部分詳細化・ロック付き編集UI、過去Sceneの依存閉包を使う自動再生成、失敗した推論traceの独立保存は今後の拡張です。retime APIは既存本文を保持し、指定Sceneの数値だけを変更します。必須contextが収まらない場合は入力を切り捨てず停止します。
