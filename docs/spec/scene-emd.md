# シーンEMD仕様

状態: Draft / 0.3  
識別子: VCD_SCENE_EMD_V1

新規のTimelinePlanner生成では各ShotにCAMERAを必須とし、Scene相対時刻でShot全体を隙間・重複なく覆う。固定カメラも明記する。ACTION・DIALOGUEとの重なりは許可する。作者の方針・固定条件・展開にある撮影指定を優先し、対象・初期構図・移動方向又は固定・速度・終端構図を記述する。指定がなければ場面の動作に合う撮影を選び、「静かな映画」を一律固定と解釈しない。

これは新規計画の契約であり、公開Scene Schema・Compilerはカメラのない旧EMDも受理する。Compilerはカメラを創作せず、既存CAMERAの翻訳・人物割当・Context Loop時刻変換を行う。変更を使うには言語生成から新revisionを保存する。完全なカメラ計画を持つSceneのretimeは、その時間被覆も維持する。

## 1. 文書構成

形式、作品、人物・背景、展開定義、固定条件、出典表、一個以上のSceneの順に置く。人物・背景・展開定義は元シナリオなしでも再コンパイルに必要な内容を保持する。素材実体は同一WFレジストリで解決する。

人物・背景・固定条件は[シナリオEMD](simple-emd.md)と同じfield。作品にはid、尺、方針、元シナリオhash、音響既定を持つ。元シナリオhashは手書きScene文書で出典がない場合に限りなしを許可する。

展開定義は`# 展開定義 ID`。出演・背景・原文・台詞・解決済み音響を保持し、`* 必須出来事 ID: 本文`を追加できる。必須出来事はEnhancerが作者原文から抽出したものとし、原文対応を出典表で保持する。

## 2. Scene部分の例

```markdown
# Scene sc_return_a 00:00.000 --> 00:10.000
* 展開: return
* 出演: @hero
* 背景: station
* 接続: cut
* 依存: なし
* 固定: なし
* 開始状態 @hero.position: 駅出口
* 終了状態 @hero.position: 駅前
* 必須出来事: req_exit

## subject_definitions
* @hero: 赤いコートを着た少女。hero_imageを人物定義の参照に使う。

## summary
* 少女が駅を出て町を見渡す。

## retention_analysis
* @hero: fully_preserved

## detailed_description
### Shot sh_exit 00:00.000 --> 00:10.000
* ACTION ev_walk 00:00.000 --> 00:05.000 | @hero | 駅出口から石畳へゆっくり歩く。
* CAMERA ev_camera 00:00.000 --> 00:10.000 | @hero | 少女越しに町を見せる。
* DIALOGUE dlg_home 00:05.000 --> 00:07.000 | @hero | 「ただいま。」

## overall_soundscape
* 発声: 指定台詞のみ
* 環境音: なし
* 効果音: シーンに合わせて補完
* 音声参照: なし
* EFFECT fx_step 00:00.000 --> 00:05.000 | ev_walk | 石畳を踏む小さな足音。

## non_diegetic_music
* BGM: なし
```

Scene時刻は作品絶対、Shot / eventはScene相対。予定時刻の実現frameへの対応は[Context Loop Contract](timing-and-splitting.md)に従う。

## 3. 発話と記号

DIALOGUEの本文は「」または既存dタグ。鉤括弧内をPythonで`<d>[Japanese]...</d>`へ変換し、dタグ入力は一切翻訳しない。言語と本文の表記を保存する。DIALOGUE IDは元台詞IDと一致させる。

発話field以外の未escapeの「」・dタグは誤配置として診断する。非発話引用には『』またはescapeを使う。発話span内の@heroや@recipientは原文の一部であり、置換しない。

人物記号は@heroなどのsymbol。Compilerは発話の外だけをSceneのSubject番号へ変換する。素材native番号もCompilerが確定し、最終promptへ出力する。

## 4. 依存・状態・検証

- Scene / Shot / event IDは文書内で一意、人物IDと順序は別に管理する。
- 依存は先行Scene IDのカンマ区切りまたはなし。未来参照・循環を禁止する。continueは直前Sceneへの依存を必須とする。
- 接続はcut / continue。先頭cut。背景・出演集合が変わる境界はV1ではcut。
- 固定はstructure / timing / contentの部分集合またはなし。LLMが解除しない。
- 状態は`* 開始状態 @人物.key: 値` / `* 終了状態 @人物.key: 値`。environment.keyも許可する。空集合はなし。
- 必須出来事は担当ID一覧。一つの出来事を複数Sceneへ重複配置しない。
- 六セクションは例の順で必須。人物定義と保持分析は出演者だけを含む。人物なしではそれぞれ`* なし`。
- eventは`種類 ID 開始 --> 終了 | 対象 | 本文`。ACTION / CAMERAは@人物またはenvironment、DIALOGUEは@話者。
- EFFECT / AMBIENCEの対象は契機event ID。契機のないAMBIENCEは`なし`でnullを表す。
- ShotはSceneを覆い、eventはShot内に収める。同時進行は許可する。音響eventはScene内に収める。
- 15秒超過・不正時間はGemma4に修正を要求し、Compilerが自然文を短縮しない。

## 5. 出典表と相互変換

```markdown
# 出典
* return: scenario:40-44
* dlg_home: scenario:43-43
* req_exit: scenario:44-44
* end_walking: scenario:52-53
```

sourceを持つBeat / Dialogue / Constraint / RequiredEventをIDで対応付ける。scenarioとsceneは文書種別、行位置は1始まり・終端行を含む。元シナリオの行位置は保存された値を使い、生成文書の位置から再推定しない。手書きScene由来はsceneを使う。

EMDとtyped artifactは意味を変えずに相互変換する。物語・台詞・状態・出典はシーンEMDへ、Contractと格子補正・素材実体・モデル・翻訳traceは実行metadataへ保存する。
