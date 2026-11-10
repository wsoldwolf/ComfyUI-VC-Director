# 発声・音響制御仕様

状態: Draft / 0.3

## 1. 前提

入力音声はリファレンスであり、最終音声はH3が生成する。元音源のコピー、曲尺への追従、音声素材の後付けmix、source_track / source_vocal方式はV1の対象から外す。参照音声が動画より短くても、それだけでは素材不足としない。

参照は声質・音楽・音の特徴を条件付ける資料であり、語句、タイミング、最終波形の完全一致を保証しない。生成内容はシーンEMDの許可とシーケンスを正本とする。

## 2. 音響許可

息遣い・反応声の自動計画と再利用パターンは[非言語発声](nonverbal-voice.md)を参照。指定台詞は保持し、許可された声を独立したNONVERBAL eventとして配置する。

| 項目 | 値 | 未指定時 |
|---|---|---|
| 発声 | なし / 指定台詞のみ / 非言語発声のみ / 指定台詞と非言語発声 | なし |
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

無声区間は発声許可をScene全体へ拡張せず、DIALOGUEと明示的に許可されたNONVERBAL event以外の発声を禁止する。人物の笑い声・溜め息・叫びなども無指定で追加しない。参照音声を「そのまま読み上げる」「追加の話者」と解釈させない。

新規コンパイルは音響prompt policy v6を使用する。具体的な音イベント・音のdescription・音響参照に基づく無音判定と、時間指定外の無音文面はv4を引き継ぐ。音を補完する許可だけで具体音がなければComplete silenceとする。継続的な環境音やBGMがあれば全無音にしない。

発声と映像の演出分離は、コンパイラの語句削除ではなく、LLMのScene詳細生成で行う。detailはvisual_directions（映像方針）の文字列配列を必須出力する。原文から画風・構図・演技・人物の可視性を保持し、声の内容・話者・発声時刻をDIALOGUE、許可された具体音をaudio/sound_eventsへ分離する。電話だけを特別扱いせず、独白・放送・遠方・画面外等にも同じ指示を適用する。生成した映像方針への発声要求は既存のVOCAL_CUE検証で修正要求へ返す。LLMによる意味分離が常に成功する保証はない。

作者の作品・展開directionは入力と保存Scene EMD内に保持する。v5の最終promptにはScene.visual_directionsを使い、作者directionを再添付しない。Scene EMDではSceneヘッダーの`* 映像方針:`として保存する（複数行可、空配列は`なし`）。旧Scene EMDにこのfieldがない場合は互換処理として原文を保持し、metadata.diagnosticsのLEGACY_VISUAL_DIRECTIONSで再生成が必要な旨を記録する。旧文書をLLMなしで意味分離したとは扱わない。

保存済みv1〜v4のrevisionは記録されたpolicyで再現する。v4の限定的な語句削除はその再現専用で、新規の既定v5では使用しない。適用にはscenarioモードで言語生成を再実行する必要があり、以前のv4比較WFで動画生成するだけではこの変更は反映されない。無音化や音声フィルターは行っていない。

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

## Sceneごとの人物条件（v6）

`actor_ids`は元展開の参加者、`visible_actor_ids`はそのSceneで画面にいる人物。実際の話者はそのSceneのDIALOGUEから導出し、LLMへ話者一覧を推測させない。detail生成ではvisible_actor_idsを必須とし、参加者以外の追加や重複を拒否する。ACTION/CAMERA・summary・映像方針で参照する人物は画面内人物に限定する。画面外の声はDIALOGUEで指定できる。

v6では画面内人物と実際の話者の和集合だけを最終subject_definitions/local_subjectsへ割り当てる。画面内人物には画像・動作参照を付け、発話する人物にだけ声質参照を付ける。画面外話者の定義はoff-screen speakerと必要な声質参照だけとし、外見・画像・保持分析を渡さない。どちらでもない人物は定義も参照も渡さない。人物番号とPicture/Audio番号は選別後に再構成し、台詞本文・話者ID・時刻を保持する。

無発声人物の参照動画は映像条件だけとして使い、付属音声をbindingから除外し、runtimeでもpaired_audio=Falseでdecodeする。明示された環境音・効果音の参照は別用途として保持する。Vision特徴の補完はvisible_actor_idsのあるSceneでは観察した視覚情報だけを追加し、作者の声・役割の原文を再注入しない。旧fieldなしSceneは互換のため全参加者が画面内にいる扱いとし、LEGACY_SCENE_VISIBILITY診断を記録する。

既存v1〜v5の再現読込は元policyを使用する。この設計を適用するにはscenarioから言語生成を再実行し、新しいScene EMD/Planを保存する。動画のみの再実行やseed変更では保存済み条件は変わらない。発声抑制率の実生成比較は未実施。

visible_actor_idsのある新Sceneのvisual_directionsに声・発話などの音声役割が残った場合はVISUAL_AUDIO_ROLEで修正要求へ返す。これは語句削除ではなく映像専用fieldの検証であり、台詞・音声条件を構造化fieldへ整理するようLLMへ要求する。未知の意味表現を完全に判定するものではない。

## 参照された人物の説明（v9）

画面外話者を匿名の`An off-screen speaker.`だけへ置き換えず、Scene内で実際に参照された人物のsubject_descriptionsを保持する。DIALOGUEの話者IDも参照として数える。元データに女性／男性等の情報があれば、そのまま翻訳へ渡し、性別や声質をPythonで推定・固定しない。detail生成は画面外話者の同一性属性を短く保持し、衣服等の不要な外見や新しい発声指示を追加しない。

出演者宣言・定義欄にいるだけの人物は翻訳も条件付けもしない。画面外人物には画像・動画の視覚参照を渡さず、実際のDIALOGUEがあるSceneにだけ声質参照を付ける。人物の本文中の参照は発声許可ではないため、無発話のSceneへ声質参照や新しい台詞を追加しない。v1〜v8の保存revisionは元policyで再現する。説明文で女性を指定しても生成音声の声質やScene間の声の同一性を保証するものではなく、必要な声質参照と実生成での確認は別途必要。
