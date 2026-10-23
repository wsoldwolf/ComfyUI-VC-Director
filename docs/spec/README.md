# VC-Director 初期仕様

策定日: 2026-10-24（Asia/Tokyo）  
状態: 初期仕様 / Draft  
仕様版: 0.3

ComfyUI-VC-Directorは、日本語のシナリオEMD、参照画像・参照音声・任意の参照動画を入力し、Gemma4が具体的な時刻を持つシーンEMDを生成する。PythonコンパイラがこれをContext Loop / MiniMax H3 Ref2VA用JSONと実行用データへ変換する。

本書は設計基準であり、実映像品質を示すものではない。0.3は、H3による全音声生成、人物@記号、発話のPython結合、Context Loop時間Contract、段階別payload、両グループMute時の停止を反映する。初期実装を開始し、現行runtimeに合わせて旧Scheduledノードへの依存を標準Ref2VA直結へ訂正した。実装・検証範囲は[実装状況](../implementation/status.md)を参照。

## 文書一覧

| 文書 | 内容 |
|---|---|
| [全体仕様](overview.md) | 責務と全体フロー |
| [シナリオEMD](simple-emd.md) | 作者が背景・出演者・物語を記述する入力形式 |
| [Gemma4計画コア](scene-expansion.md) | Enhancer、シーン生成、具体化 |
| [シーンEMD](scene-emd.md) | H3の六セクションに近い、時刻付き中間形式 |
| [データ契約](data-contract.md) | ID、必須項目、型、コア間Schema |
| [段階別payload](llm-protocol.md) | 構成・詳細化・時刻修正、slotと依存 |
| [時間・分割仕様](timing-and-splitting.md) | LLM時刻、15秒制約、格子、分割 |
| [単一ワークフロー](workflow.md) | Ref2VA標準WF、グループ切替、素材と音声の実行 |
| [Image to Subject EMD](image-to-subject-emd.md) | 画像観察と断片の再利用 |
| [出力契約](output-contract.md) | 標準Plan JSON、execution JSON、再開 |
| [発声・音響制御](audio-control.md) | 発声許可、効果音、音声参照、発話のPython結合 |
| [実装計画](implementation-plan.md) | 移植・実装段階と受入条件 |

## 設計原則

- シナリオで使用する背景と出演人物を決める。背景参照画像がない情景も指定できる。
- シナリオの一つの展開を複数シーンへ展開でき、利用者にシーン分割を要求しない。
- Gemma4がシーン・演技・撮影・音響・具体的時刻を生成し、Pythonが構文・参照・時刻を検証し機械変換する。
- シナリオEMDとシーンEMDを保存・編集可能な中間形式とする。
- cl-japanese2json / MV-Directorの旧EMD文法との互換性は要求しない。既存coreの有用な部品とノウハウを再利用する。
- 単一WFで言語生成とContext Loop動画生成を切り替える。標準実行はファイル経由とし、動画生成でLLMを再実行しない。
- 発声・BGMを無断追加しない。許可した環境音・効果音の具体化はGemma4計画コアが行う。
- 入力音声は参照、出力音声はH3生成とする。copy / mixのVC独自操作は設けない。
- 人物@記号とnative参照、Context Loop時間Contract、発話の翻訳保護はPythonコンパイル層が確定する。
- 両グループMuteは何もしない正常な停止状態とする。
