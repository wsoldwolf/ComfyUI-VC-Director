# 実装計画・受入条件

状態: Draft / 0.3

## 1. 基準

MV-DirectorのImage to Subject EMD、英訳・保護付きCompiler、artifact・protocol・GGUF runtime、cache、context予算、有限回復を再利用する。旧EMD文法そのものとの互換性は要求しない。移植元commitと変更一覧を実装時に記録し、LICENSEと著作権表示を保持する。

Context Loopのローカル調査版は0.7.3。標準Ref2V Basic WFをベースに、VCのScene別素材adapterと標準Ref2VA、言語・コンパイル経路を一つのWFへ統合する。旧Scheduled公開ノードは0.7.3で廃止済みのため使わない。MV側の二本WF構成は採用しない。

## 2. 実装順序

| 段階 | 内容 | 完了条件 |
|---|---|---|
| 1. 基盤固定 | 移植元・Context Loop・標準WFの版、依存、モデルfingerprintを記録 | 実装正本とruntime契約が固定される |
| 2. EMD / typed artifact | シナリオEMD、シーンEMD、Schema、renderer、原文位置 | 背景・出演者・台詞・具体的時刻を欠落なく保持できる |
| 3. Vision / Compiler移植 | 画像観察、保護付き翻訳、Scene別人物・背景、Plan出力 | 固定シーンEMDから有効な標準Planを得る |
| 4. 機械コンパイル | 時間Contract、context、人物・素材native番号、発話保護・結合、保存 | Planとexecutionが一致する |
| 5. Gemma4計画コア | Enhancer、構成、詳細化、時刻生成、15秒制約・有限回復 | 粗いシナリオからシーンEMDを生成する |
| 6. 単一WF | 共有素材、mute / bypass切替、ファイル読込、VC Scene Ref2VA、generated_audio | 両モードと両方Muteで契約どおり動く |
| 7. 実生成 | 60秒→3〜5分、Context Loop全編生成 | 状態・背景・出演・音響・採用区間を検証できる |

## 3. 受入条件

### 文法・コア

- 参照画像付き背景とテキストのみ背景をシナリオ段階で指定できる。
- 展開ごとの出演者・背景を生成した各Sceneへ保持する。
- Gemma4が具体的なScene / Shot / event時刻を書き、Pythonが検証する。
- 一展開から複数Sceneを生成し、固定出来事・台詞・順序・結末を保持する。
- 選定・創作は計画コア、機械処理はコンパイル層に分離される。
- 未定義参照、未知schema、型違い、時刻不正は位置付きで診断する。
- IDと順序を分離し、追加・削除・並べ替え後の再生成と作者固定を検証する。

### 時間・音響・参照

- 15秒超過、台詞を跨ぐ分割、格子伸長、context二重採用、音声sample境界を検証する。
- Sceneごとに出演者だけを定義し、背景なし・人物なしのSceneも生成できる。
- 人物@記号、native番号、schedule、各Sceneのactive参照上限、paired audioを検証する。
- 参照音声はconditioningにだけ使い、短い参照を作品尺不足として拒否しない。
- 音響未指定・全無音・発声なし物理音あり・指定台詞・BGM生成を区別する。
- 鉤括弧のJapanese変換、既存dタグの無変換、Python結合、台詞中の@記号保持を検証する。
- 説明文の読み上げ、無許可発声、追加BGM、発声あり→なしのaudio contextを検査する。

### WF・保存・再開

- 動画モードでLLMを実行せず、言語モードでH3を実行しない。
- 両グループMuteでは推論・保存をしない。言語モードsaved_scene入力でLLMなしの再コンパイルができる。
- 共有素材はどちらのモードでも利用できる。
- 型の異なるノードをBypassで直結しない。
- 保存済みJSONのみで動画経路を実行できる。
- Plan / executionの片方だけの更新、素材変更、古いhashを検出する。
- OOM・中断・不正protocolを空成功にせず、成功成果物だけを再利用する。

### 実推論・映像

意味の保持、自然な動作密度、人物・背景の切替、H3発声・音楽抑制は構文検証と分けて評価する。同じ素材と条件で複数seedを比較し、生成直後と最終音声を別に確認する。同期と同一性の完全保証は行わない。

## 4. 残る実装検証

- 0.7.3の標準Ref2VAとVCレジストリadapterの実Queue。登録Schemaと静的リンクはオフライン照合済み。
- 標準AssembleがH3生成音声を採用し、context / decoded overlap / match_tailが整合すること。
- MV-Directorの実モデルファイル・mmproj・backend対応と版の固定。
- システムプロンプトと新VC recordの実Gemma4回復率。
- シーンEMDの非発話引用、発話保護・結合、Compiler解決済み参照とruntime再置換無効化の実装。

これらは設計未決のまま責務を残す項目ではなく、上記契約を実装して検証する項目とする。
