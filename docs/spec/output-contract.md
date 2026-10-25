# 出力・コンパイル契約

状態: Draft / 0.3

## 1. 成果物

| ファイル | 役割 |
|---|---|
| scenario.emd | 作者のシナリオ |
| scenes.emd | Gemma4が生成・時刻を記述したシーン。編集正本 |
| plan.json | Context Loop標準Plan。独自root keyを追加しない |
| execution.json | Contract、素材、人物・参照番号、時刻対応、生成音声の制御記録 |
| run_metadata.json | EMD・JSON・素材・設定hash、モデル、プロンプト、翻訳trace、診断 |

保存先は`output/vc_director/<project_id>/`。Planとexecutionを一組として一時ファイルへ書き、両方のhashが確定してからmetadataの有効revisionを更新する。片方だけの新旧混在は読込時に拒否する。

初期実装は`revisions/<revision_hash>/`に上記ファイルをまとめて保存し、作品直下の`run_metadata.json`を有効revisionへのatomic pointerとする。revision内のPlan / executionを直接上書きしない。読込時にファイルhashと翻訳traceによる再コンパイルを照合する。

JSONファイルとノードの`plan_json`出力はUTF-8、日本語非エスケープ、2スペースのインデント、末尾改行付きで出力する。Plan / execution間の参照hashは従来どおりキーをソートした空白なしのcanonical JSONを対象とし、metadataのfilesは保存ファイルの実バイトを対象とする。以前の1行JSONも読込可能とする。

metadataの`prompt_policy_version`は音響指示の生成規則を記録する。新規コンパイルは`vcd-audio-v3`でBGMなしの音響文面を簡潔化する。同一seed比較で長い禁止文より抑制が改善したため、全無音はComplete silence、音楽欄はN/Aとする。記録のない旧revisionは`vcd-audio-v1`、明示v2は従来の長い禁止文で再コンパイル照合し、保存済みPlanの再現性を維持する。新しい規則で再生成する場合はScene EMDを再コンパイルし、別revisionを保存する。[比較記録](../implementation/negpip-audio-validation.md)を参照。

## 2. Compiler

1. シーンEMDを解析し、出演者・背景・依存・許可・時刻を検証する。
2. 発話を退避し、人物`@hero`と参照素材をnative tokenへ解決する。
3. 自由描写だけを英訳し、Pythonで発話を結合する。
4. Context Loop Contractでraw / delivered / event時刻を確定する。
5. schedule、人物番号、参照番号、音響指示を記録する。
6. Plan / execution / metadataを保存する。

人物・背景・動作・音響を創作しない。入力音声は参照だけに使い、copy・mix・素材音声による最終尺の充足検査を行わない。

## 3. Plan

rootのshots配列に生成Sceneを置く。各要素はid、length、prompt、context_length、audio_context_lengthを持つ。continueはcontinuation_mode=guideを明記する。音声経路はgenerated_audioとし、音声のsource lockを有効化しない。

promptはsubject_definitions、summary、retention_analysis、detailed_description、overall_soundscape、non_diegetic_musicの順。出演者と当該背景だけを出力し、可変設定を共通prefixへ混ぜない。背景画像なしはテキストだけで表現する。

H3のSubjectはScene内1〜4。人物@記号は発話の外でのみ変換する。素材native番号はCompilerとscheduleで確定し、runtimeの文字列alias置換に依存しない。発話は[音響仕様](audio-control.md)の保護・結合規則へ従う。

step数はProduction Plan / Generation Profile側で設定する。

## 4. executionと再現範囲

型は[データ契約](data-contract.md)、時間は[Contract](timing-and-splitting.md)に従う。各segmentはPlanのshots IDと一致する。rawと採用範囲、eventごとのraw / output時刻、人物・参照番号を保存する。audio_operationsは持たない。

segmentの`audio_output=silence`は全音響なしの作者意図を記録する。現在のWFはこの値で生成済みPCMを無音へ置き換えず、H3の生成音声を保存する。音楽禁止や無音を守ったかは実出力の検証が必要であり、manifestの方針値だけを成功判定に使わない。

素材実体は同一WFの共有レジストリから取得し、hashと接続を照合する。短い参照音声を作品尺と比較して拒否しない。対応していない参照形式は型・runtime条件の不一致として診断する。

シーンEMD、素材レジストリ、同じ翻訳結果・Compiler版・Contractから同じJSONを再生成できる設計とする。生成音声や映像の決定性までは保証しない。シーンEMD単独の編集情報とmetadataの実行条件を分ける。

## 5. 再実行

保存済みシーンEMDの再コンパイルは言語生成グループ内のsaved_scene入力で行う。LLMを再実行しない。時刻変更が必要なら別途Gemma4のRETIME段階を使う。

両グループMuteは何もしない。旧EMDとの互換や自動移行は要求しない。旧仕様のaudio_operations / source音声採用を含むJSONは未知keyとして拒否し、新しいSchemaで再生成する。
