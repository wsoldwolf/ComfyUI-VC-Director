# Image to Subject EMD再利用仕様

状態: Draft / 0.3

## 1. 方針

MV-DirectorのImage to Subject EMDを取り込み、人物・物品・背景画像の可視事実を編集可能な断片へ変換する。core/vision、観察prompt、protocol、hint処理、GGUF + mmproj探索、lifecycle、cache、ノード、必要なWeb UIとテストを再利用する。

全体の旧EMD文法との互換性は要求しない。既存の観察結果・断片をVCの人物・背景recordへ構造変換するadapterを置き、本文を意味的に書き換えない。観察は物語や発声を創作しない。

## 2. 入出力

入力のimage、model_name、general / subject_only / scene_only、subject_hint、additional_instruction、observe_only / assist / lock_identity、warn / strictを維持する。解析解像度、GPU配置、モデル解放と成功cacheも移植する。

出力はemd_fragment、reference_bindings、元image、observations_jsonを基準とする。人物と背景を別authorityとして扱い、背景断片をSubjectとして数えない。観測JSONは診断用であり、計画の正本にはしない。

## 3. 作者記述との統合

lock_identityではhintを優先して保持し、追加の可視特徴だけを後置する。曖昧な特徴を断定しない。同一人物の複数viewは同一性資料として統合し、参照構図・背景・ポーズをそのままシーン指示へ持ち込まない。

人物・背景をシナリオの安定IDへ対応付ける。背景画像なしの場合はVisionを呼ばず、作者の情景記述だけを使う。明示された時刻・照明は観測した画像照明より優先する。

## 4. 共有素材とScene別Ref2VA

VC標準WFでは共有素材IDと人物@記号を使い、Compilerがscheduleとnative参照番号を確定する。VC adapterから標準Ref2VAへ接続し、文字列alias再置換は使わない。旧auto_h3の直接接続検出を必須にしない。

旧manual / none / auto_h3の有用な検証は移植可能だが、Scene別素材を固定Picture入力と誤認しない。元IMAGEのpass-throughと解析画像hashを保持し、素材実体のhashは独立した共有レジストリで管理する。言語生成グループをMuteしても共有素材を使えるよう、素材レジストリを独立させる。

## 5. 検証

Buildの自動Visionは画像を人物／背景IDへ対応付け、観察事実をEnhancerだけでなくTimelinePlannerへも渡す。計画時のdescriptionへ決定的に合成し、生成したScene EMDの人物`特徴`・背景`情景`へ保存する。人物の観察特徴は各Sceneの`subject_definitions`にも反映する。原文シナリオは保存時にも書き換えない。手書き文章は保持して観察を補足し、同一人物／背景の手入力image_factsは自動観察より優先する。

画像を指定したシナリオでは特徴／情景の省略を許可するが、計画前に観察結果で補完することを必須とする。補完不能時はLLMへ不完全な定義を渡さず、Vision設定または文章入力を案内する。完成したScene artifactのdescriptionは引き続き非空とする。

同じ観察recordから同等の人物・背景断片を得ること、profile・hint・複数view・画像保持・cacheを検証する。VCのID変換とschedule対応を別に試験する。観察の引用は非発話記法へ構造化し、人物のDIALOGUEを観察から新設しない。発声・BGMの許可を増やさない。

実Vision品質と機械処理の互換性は分けて評価する。モデル・mmproj・プロンプト変更で依存する結果を無効化する。
