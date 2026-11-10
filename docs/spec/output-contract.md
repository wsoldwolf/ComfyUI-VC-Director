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

metadataの`prompt_policy_version`はpromptの生成規則を記録する。新規コンパイルは`vcd-audio-v10`で、v9のScene別人物選別を継承し、人物・小物のSubjectを区別して翻訳・代名詞を検証する。翻訳traceと成功cacheにはSubjectのactor / prop区分も含める。旧revisionは記録されたv1〜v9で再コンパイル照合し、記録なしはv1として読む。新規則の適用には別revisionを保存する。[音響仕様](audio-control.md)を参照。

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

新規コンパイルのpolicyは`vcd-audio-v11-continuity`。continueでは映像コンテキスト22フレームとGuideを維持し、前Scene終端の構図・人物位置・姿勢・保持物・照明・カメラ軸・進行中の動作から繋ぐ視覚的な指示をdetailed_descriptionへ加える。音声や人物を追加しない。cutにはこの継続指示を加えない。保存済みv10以前は旧policyの同じpromptで再現する。これらはモデルへの条件であり、実映像の連続性は生成結果で確認する必要がある。

promptはsubject_definitions、summary、retention_analysis、detailed_description、overall_soundscape、non_diegetic_musicの順。出演者と当該背景だけを出力し、可変設定を共通prefixへ混ぜない。背景画像なしはテキストだけで表現する。

H3のSubjectはScene内1〜4。人物@記号は発話の外でのみ変換する。素材native番号はCompilerとscheduleで確定し、runtimeの文字列alias置換に依存しない。発話は[音響仕様](audio-control.md)の保護・結合規則へ従う。

v7ではsummary・映像方針・CAMERA・ACTIONも人物を`@actor_id`で記述する。登録名・IDと完全一致する人物参照は翻訳前にIDへ結び付け、同名の複数人物は明示IDを要求する。複数人物が参加するSceneの映像文に残った彼女・彼・she・her・they等の人物代名詞は推測で置換せず、detailの修正要求へ返す。映像文は画面内人物だけを参照できる。CompilerではIDを確定した`<Subject N>`を既存の出現単位markerで保護して翻訳し、翻訳が登録人物名や裸のID、曖昧な人物代名詞を再導入した場合は既存の二回制限内で修正する。外見定義内の所有者が明白な代名詞はこの映像文の制限とは分ける。検査は既知の表記と代名詞を対象とし、未知の別名や任意の意味的な取り違えを完全に検出するものではない。

作者のDIALOGUE本文はこの表記変換・翻訳へ渡さない。話者ID・台詞・言語・時刻を保持する。旧Sceneの曖昧な人物参照を直す場合は`scenario`からBuildを再実行する。保存PlanのLoadだけでは人物表記を更新しない。

detail生成時に曖昧な人物参照が残った場合は、対象説明文だけをLLMで修正する。summary・CAMERA/ACTIONなどの人物を作者の展開、visible_actor_ids、構造化ownerから解決し、台詞や時間軸を再生成しない。映像方針の部分修正と併用し、修正後にScene全体を検証する。saved_sceneのCompilerは推測による代名詞置換を行わないため、曖昧な旧Sceneはscenarioから再生成する。

v8ではACTION/CAMERAのactor_idと人物特徴のownerを、そのSceneの`<Subject N>`へ解決して翻訳contextへ渡す。本文に明示されたSubjectと構造化ownerの和集合が一人だけなら、英訳のtheir/they/them/her等がその一人を指す表現を許可する。本文が別の人物も参照する場合や、ownerも明示Subjectもない場合は代名詞を一律に許可せず、既存marker又は曖昧でない名詞句での修正を要求する。日本語の人物代名詞を推測して置換する処理ではない。

ownerのcontextは原文の参照出現ではないため、保護markerを追加しない。翻訳は原文のmarker数を厳密に保持し、markerなしの原文に架空のSubjectやmarkerを作る修正は禁止する。翻訳cacheとmetadata.translation_traceは原文・owner_subject・explicit_peopleの組を区別し、同じ本文でも異なる人物の翻訳を混在させない。保存revisionの再コンパイルも同じcontextで照合する。台詞・話者・時刻は変更しない。

v9の人物選別は翻訳前に行う。Sceneのsummary・映像方針・非DIALOGUE event本文・音響説明の`@actor_id`参照、eventのactor_id、明示visible_actor_idsを使用する。DIALOGUE本文に含まれる人物名や`@`文字列は作者の発話として保持し、別人物の出演指定に解釈しない。全体の人物一覧、Scene.actor_ids、subject_descriptions、retentionは定義・参加者の宣言であり、それだけで最終人物定義や参照素材を有効化しない。

参照された画面外人物についてもScene.subject_descriptionsの本文を英訳して保持し、匿名speakerへ置き換えない。元の女性／男性等の同一性情報を残し、off-screenの制約を併記する。画面外人物の画像・動作参照は有効化せず、声質参照はそのSceneの実際のDIALOGUE話者だけに付ける。非発話の人物参照にはoff-screen personを使い、発声を許可するspeaker指定を追加しない。人物番号は採用後に全体の人物宣言順で確定する。

step数はProduction Plan / Generation Profile側で設定する。

## 4. executionと再現範囲

型は[データ契約](data-contract.md)、時間は[Contract](timing-and-splitting.md)に従う。各segmentはPlanのshots IDと一致する。rawと採用範囲、eventごとのraw / output時刻、人物・参照番号を保存する。audio_operationsは持たない。

segmentの`audio_output=silence`は全音響なしの作者意図を記録する。現在のWFはこの値で生成済みPCMを無音へ置き換えず、H3の生成音声を保存する。音楽禁止や無音を守ったかは実出力の検証が必要であり、manifestの方針値だけを成功判定に使わない。

素材実体は同一WFの共有レジストリから取得し、hashと接続を照合する。短い参照音声を作品尺と比較して拒否しない。対応していない参照形式は型・runtime条件の不一致として診断する。

シーンEMD、素材レジストリ、同じ翻訳結果・Compiler版・Contractから同じJSONを再生成できる設計とする。生成音声や映像の決定性までは保証しない。シーンEMD単独の編集情報とmetadataの実行条件を分ける。

## 5. 再実行

保存済みシーンEMDの再コンパイルは言語生成グループ内のsaved_scene入力で行う。LLMを再実行しない。時刻変更が必要なら別途Gemma4のRETIME段階を使う。

両グループMuteは何もしない。旧EMDとの互換や自動移行は要求しない。旧仕様のaudio_operations / source音声採用を含むJSONは未知keyとして拒否し、新しいSchemaで再生成する。

v6では人物定義と参照素材をScene.visible_actor_idsと実際のDIALOGUEの話者から選別する。参加者一覧だけから全人物の外見や音声を渡さない。詳細は[音響仕様](audio-control.md)を参照。
