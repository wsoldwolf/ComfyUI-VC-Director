# 発声・音響制御仕様

状態: Draft / 0.3

## 1. 前提

入力音声はリファレンスであり、最終音声はH3が生成する。元音源のコピー、曲尺への追従、音声素材の後付けmix、source_track / source_vocal方式はV1の対象から外す。参照音声が動画より短くても、それだけでは素材不足としない。

参照は声質・音楽・音の特徴を条件付ける資料であり、語句、タイミング、最終波形の完全一致を保証しない。生成内容はシーンEMDの許可とシーケンスを正本とする。

## 2. 音響許可

| 項目 | 値 | 未指定時 |
|---|---|---|
| 発声 | なし / 指定台詞のみ | なし |
| BGM | なし / 生成:具体的内容 | なし |
| 環境音 | なし / 具体的内容 / シーンに合わせて補完 | なし |
| 効果音 | なし / 具体的内容 / シーンに合わせて補完 | なし |
| 音声参照 | 素材IDと用途music / ambience / effects | 空配列 |
| 音声接続 | auto / cut / continue | auto（旧動作） |
| BGM抑制 | prompt / negpip | prompt |

人物の声質参照は人物定義の声質音声で指定する。声質音声を接続しても発声を許可しない。発声なしの人物の声質参照は、そのSceneで有効化しない。音楽参照もBGMなしのSceneへ条件付けしない。

作品→展開→生成Sceneへ方針を明記する。`音響: 無音`は発声・BGM・環境音・効果音をすべてなしにし、当該Sceneの音声参照を空にする。無音指定と発話や音響eventが同居したら停止する。

発声なしでも環境音・効果音を許可できる。この場合は人物発声禁止を出力し、全音響無音とは記述しない。全項目なしの場合にだけComplete silenceを出す。

## 3. 発話記法と翻訳保護

`「日本語の台詞」`を`<d>[Japanese]日本語の台詞</d>`へPythonで機械変換する。既にある`<d>...</d>`および`<d>[Language]...</d>`は表記・内容をそのまま保持し、一切翻訳しない。空、未閉鎖、入れ子、不整合な発話記法はエラーとする。

DIALOGUE recordの本文を発話として扱う。人物はrecordの話者`@hero`から確定する。シーンEMDのそれ以外のprompt fieldに未escapeの鉤括弧・dタグがあれば発話fieldへの移動を診断し、翻訳へ流さない。非発話の引用は`『』`またはescapeした`\「`と`\」`を使う。シナリオの自由文にある引用は、人物を明記した台詞recordと区別する。

処理順は次に固定する。

1. 発話spanを解析して退避し、原表記と話者・位置を記録する。
2. 発話の外にある人物記号を解決し、自由描写だけを英訳する。
3. 鉤括弧の発話をdタグへ変換する。入力がdタグなら無変換とする。
4. Pythonが発話と英訳済み本文を結合する。
5. 発話本文の一致・出現数・話者・適用区間を検証する。

英訳LLMへ発話本文を送らず、共通文・summary・人物・背景説明へ複製しない。台詞中の`@hero`や`@recipient`も語句として保存する。参照・人物記号の置換は発話の外だけで行う。

summary・ACTIONの「言葉を口にする」「話す」「声を出す」なども発声説明として詳細化段階で検査し、対象fieldだけの修正をGemma4へ要求する。作者台詞のDIALOGUEは削除・改変しない。翻訳が発声を追加した場合は、移植したwhole-field translatorの既存二回の試行枠内で意味検証と修正要求を行う。Pythonが発声句を切り落として翻訳成功にしない。検証に通った翻訳だけをcacheへ保存する。

## 4. H3への明示指示

許可からCompilerが固定制約を生成する。Gemma4が許可された動作・音響を具体化する責務は変わらない。

| 方針 | 出力条件 |
|---|---|
| 全音響なし | `overall_soundscape: Complete silence.` |
| 発声なし・物理音あり | 許可音だけを列挙し、speech / narration / singing / humming / character vocalizationを禁止 |
| 指定台詞のみ | 指定話者が指定語句を指定区間だけ発声。追加、言い換え、反復、説明文の読み上げを禁止 |
| BGMなし | `non_diegetic_music: N/A`。音楽禁止語の長い列挙を付加しない |
| BGM生成 | 指定音楽だけを生成。歌詞・歌唱が未許可なら器楽に限定 |

無声区間は発声許可をScene全体へ拡張せず、発話event以外の発声を禁止する。人物の笑い声・溜め息・叫びなども初期V1では無指定で追加しない。参照音声を「そのまま読み上げる」「追加の話者」と解釈させない。

新規生成は音響prompt policy v3を使用する。BGMなしではcl-japanese2jsonと同じ簡潔な音響文面を用い、台詞のみならexact shot-synchronized dialogueと他の音がない旨を記す。音声参照がある場合だけ参照の用途制約を追記する。無音指定はComplete silence、音楽欄はN/Aとする。同一seed比較で、長い禁止文を出すv2よりMusic分類応答が大きく低下したためである。BGM生成時は従来の許可・器楽制約を保つ。旧policy v1／v2は保存revisionの再現読込用に維持する。

発声キューの検査は日本語と英訳後に実施し、既定strictとする。warnは明示選択の診断用。構造エラー、許可不一致、話者不明をwarnで緩和しない。否定文・保護した発話・Compiler所有制約を肯定的発声要求と混同しない。

## 5. 実行と受入

音声経路はContext Loopのgenerated_audioを使い、標準Assembleで生成音声を採用する。copy / mix操作をexecutionへ保存しない。音声contextとtrimは[Context Loop時間契約](timing-and-splitting.md)に従う。

発声禁止・無音指示が主な生成制御であり、後段の音源差替えで代用しない。H3が指示に従うかは実音声で確認する。残存する発声・BGMは生成失敗として記録し、再生成対象とする。プロンプトだけによる完全無音や完全な音素同期は保証しない。

## 6. 映像と独立した音声接続

作品と展開で`* 音声接続:`を指定し、展開の値を優先する。生成Sceneは作者の指定を保持し、LLMが変更しない。Scene EMDではSceneの`overall_soundscape`に確定方針を保存する。

| 値 | 動作 |
|---|---|
| auto又は省略 | 従来どおり、映像continue・前後とも発声なし・音響方針と有効参照が一致する場合だけ音声Guide22 |
| cut | 映像continueでも音声Guide0 |
| continue | 映像cutでも直前の生成音声をGuide22として使用。作品先頭だけ0 |

これは生成の条件付けであり、前の波形をコピー・ループする指定ではない。映像cutで音声だけ続ける場合、音声Guideは新Scene開始より前に置き、映像context・追加trim・event時刻補正を増やさない。背景や出演者の変更も許可する。直前で許可していた音を新Sceneで禁止する境界では、持ち越しを防ぐため`AUDIO_CONTINUITY_PERMISSION`で停止し、`音声接続: cut`を案内する。音声継続は聴感上の完全な連続性やBGM抑制を保証しない。

## 7. H3専用NegPiP

`BGM: なし`と`BGM抑制: negpip`を併記すると、Compilerが音声だけに適用する負条件`(background music, instrumental soundtrack:-1.0@a0-)`を付加する。作者の台詞は変更しない。BGM生成とnegpipの併用は拒否する。省略時は従来の禁止promptのみ。

配布WFの`VC · Audio Control`は保存Planを読み、[H3専用NegPiP](https://github.com/hako-mikan/comfyui-minimax-h3-negpip)でMODELとCLIPの両方をpatchする。強度は同ノードの`value_strength`で設定（既定1、0は比較用）。未導入・CLIP未接続を明示エラーにし、強度と依存コードhashを生成fingerprintへ含める。要求のないPlanでは元のMODEL・CLIPをそのまま返す。

これは実験的な追加抑制である。[同一seed比較](../implementation/negpip-audio-validation.md)では強度1・3でもMusic判定が残り、除去成功とは扱っていない。音声の後処理による無音化・分離は実施しない。

## 8. 分析履歴

cl-japanese2jsonの`jsongen.py`と関連テストから、発声許可照合、未保護発声キュー検査、音響省略時の明示禁止を再利用する。MV-DirectorのCompilerから発話保護と固定音響directiveを再利用する。元音源コピーの設計は今回の参照音声主体の構成へ持ち込まない。実H3の比較結果は[検証記録](../implementation/negpip-audio-validation.md)を参照。
