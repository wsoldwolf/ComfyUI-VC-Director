# Gemma4段階別payload・依存契約

状態: Draft / 0.3

## 1. transport

MV-Directorの行protocol・slot対応・有限回復を使う。protocol識別子は`VCD_LLM_LINES_V1`。モデル応答は一物理行一record。

```text
RECORD_TYPE<TAB>SLOT<TAB>JSON_PAYLOAD
```

JSONは改行をescapeした一行。CRLF / CRをLFへ正規化し、既知fenceとtransport wrapperだけを除去する。自由文・発話の内容を正規化で修正しない。未知record、未知slot、型違い、競合重複を拒否する。

GemmaBackendは段階別payloadのJSON SchemaからGBNFを生成し、record種別・slot=1・JSONオブジェクト一件へ出力を制約する。DETAILの生成Schemaには承認済みoutlineと作者の台詞ID・話者・原文・言語を固定値として与える。これは生成時の制約であり、公開Schemaと生成後の意味検証を置き換えない。要約欄への台詞混入も拒否する。

受信側ではJSONの外側にある完全なjson/text fenceと、JSONの整形改行も扱える。JSON終端後の任意の文字列や二件目のrecordを切り捨てて採用しない。閉じ括弧の追加や本文の書換えで不正応答を修復しない。

## 2. request共通

保存・cache上のrequestはschema=VCD_LLM_REQUEST_V1、request_id、stage、targets、retry、errors、payloadを必須とする。targetsは{slot:int>=1,target_id:string}[]。slotは呼出し内だけの番号、target_idは作品・展開・Sceneの安定ID。slotを人物やシーンの名前として使わない。

retryはinitial / repair / isolated。errorsは{target_id,path,code,message}[]で、初回は空。request_idは応答cacheと対応付けるためのIDで、モデルへ新しい物語IDの創作を要求するものではない。

## 3. 段階別payload

| stage | 入力payload | target | 応答recordとJSON payload |
|---|---|---|---|
| enhance | scenario:Scenario、image_facts:{entity_kind:actor/background,entity_id,text}[] | 作品ID、slot=1 | ENHANCED、EnhancedStory一件 |
| outline | scenario、enhanced、accepted_outlines:SceneOutline[] | 今回構成する展開ID | SCENES、{scenes:SceneOutline[]} |
| detail | scenario、enhanced、outline:SceneOutline、previous_scenes:Scene[]、locked_scene:Scene/null | 詳細化するScene ID、一件ずつ | DETAIL、完全なScene一件 |
| retime | document:SceneDocument、changed_scene_ids:string[] | 時刻を変更するScene ID | TIMING、TimingPatch一件 |

型の実体は[Schema](schemas/vc-artifacts.schema.json)の各request / responseと$defsで固定する。初回構成の可変シーン数は展開slotのSCENES配列で扱い、DETAIL段階に未完成Sceneを完全Sceneとして渡さない。

ENHANCEDのslot=1は作品全体を表す。生成用Schemaは入力の全展開を固定長のprefixItemsとして持ち、件数・ID・順序を固定する。project_id、scenario_sha256、作者のconstraintsと、各必須出来事のbeat_id・出典行範囲も入力から制約する。公開artifact Schemaは変更せず、応答の意味検証も維持する。

DETAILの生成用Schemaは対象展開の音響許可を上限とする。禁止された音響カテゴリをnone・description=nullへ固定し、未使用の作者台詞がなければvocal=noneとする。許可された音声参照の部分集合とsound_event種別だけを生成でき、環境音・効果音が両方禁止ならsound_events=[]に固定する。Shot/event/sound_eventの時刻をScene相対0〜Scene長以内に制約し、プロンプトにも今回の絶対時刻・相対範囲を数値で示す。Shotの連続性や正の長さ、音響の整合は引き続き意味検証する。DETAILだけのprompt versionを更新でき、未変更段階の成功cacheは再利用する。

SceneOutlineは具体的な開始・終了時刻、出演者、背景、接続、依存、状態、必須出来事を持つ。DETAILは構成のID・境界・固定情報を保持し、演技・撮影・発話・音響を追加する。時刻変更が必要ならRETIMEへ回す。

展開には任意のdirection配列とtransition（auto／cut／continue）を持てる。outline生成Schemaは対象展開の出演・背景・接続を制約し、作品先頭のcutをprefixとして固定する。continueの直前Scene依存と共通state値も、outlineの意味検証内で確認する。展開方針をoutline／detailへ提示し、他展開の方針を適用しない。outlineのprompt versionはvcd-story-v5-beat-transition。指定のない入力と既存Scene EMDの読込を維持する。

DETAILは各Shotのevents先頭をCAMERAへ制約する。生成SchemaのprefixItemsに先頭camera、itemsに残りのeventを指定し、導入済みllama.cppがprefixを無視する問題をローカルの変換アダプターで補う。実応答・成功cacheのどちらもPlannerの意味検証を通し、CAMERA_MISSING／CAMERA_COVERAGE／CAMERA_OVERLAP／CAMERA_TIMEを既存の最大3試行で回復する。必要な区間を診断へ数値で示し、Pythonでカメラ本文を補わない。DETAILのprompt versionはvcd-story-v7-explicit-camera。Enhancer／outlineのversionは維持する。

TimingPatchはscene_id、start_ms、end_ms、shots、sound_events。shotsは{id,start_ms,end_ms,events:[{id,start_ms,end_ms}]}[]、sound_eventsは{id,start_ms,end_ms}[]。既存IDの集合を保持し、台詞や演出本文を再生成するfieldを持たない。

## 4. response保存形

行protocolを解析した結果はschema=VCD_LLM_RESPONSE_V1、request_id、stage、recordsを持つ。recordsは{record_type,slot,payload}[]。requestとのstage / target対応と、各payloadのSchemaを検証する。モデル自身にこの外側wrapperの出力を要求しない。

各段階の[request例](examples/artifacts/outline-request.json)と[response例](examples/artifacts/outline-response.json)を保存する。

## 5. 依存と不変条件

- depends_onは先行Sceneだけを参照し、循環・未来参照を拒否する。
- continueは直前Sceneを含み、その終端状態を開始状態へ引き継ぐ。
- cutでも所持品・出来事を引き継ぐ場合は該当先行Sceneを明示する。
- 再生成は変更Sceneと依存する後続Sceneへ限定し、無関係な成功recordを保持する。
- lockedのstructure / timing / contentを変更しない。固定情報が衝突したら解除せず診断する。
- 発話は元の台詞ID・原表記・話者を保持し、追加発声の許可を作らない。

## 6. 回復と予算

初回、対象段階の不正・欠落slot再要求一回、残存slotの単独再要求一回まで。15秒超過や分割もこの上限内で修正する。新しいSceneが必要な修正はoutline→detailへ戻し、scene_id対応を保存する。

context不足時は任意の過去出力を縮小し、分割可能なtargetsを分ける。作者原文、固定条件、出演者・背景、必要な状態、発話、検証エラーを切り捨てない。単一targetが収まらない場合はContextBudgetErrorとして停止する。欠けたJSONの末尾をPythonが推測して成功にしない。

モデル・runtime・token予算、cache、OOM、中断、モデル解放は[計画コア](scene-expansion.md)と移植元基盤に従う。

Build / CLI planは`<output_root>/<project_id>/diagnostics/`へ段階別の読みやすいJSONログを保存する。system prompt、request、モデル識別、各attemptの生応答・解析結果・検証エラー・終了状態を保持する。再試行上限に達した場合は例外にもログの絶対パスを表示する。独立したEnhancer / TimelinePlannerノードはcacheルート内の`story/diagnostics/`を使う。

Build / CompileSave / CLIの翻訳も同じ作品のdiagnosticsへ`translation-*.json`を保存する。原文、保護値、system prompt、各試行のrequest・生応答・marker照合・意味検証エラーと終了状態を保持する。翻訳のprotocol・参照保護・発声検査を合わせて最大二回で回復し、失敗時は例外に診断ファイルの絶対パスを含める。独立したGemmaTranslatorは指定cache内のdiagnosticsを既定とする。
