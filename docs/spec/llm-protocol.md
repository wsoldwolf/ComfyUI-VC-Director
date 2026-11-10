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
| outline | scenario、enhanced、accepted_outlines:SceneOutline[]、任意のbeat_windows | 今回構成する展開ID | SCENES、{scenes:SceneOutline[]} |
| detail | scenario、enhanced、outline:SceneOutline、previous_scenes:Scene[]、locked_scene:Scene/null、任意のrequired_dialogue_ids | 詳細化するScene ID、一件ずつ | DETAIL、完全なScene一件 |
| retime | document:SceneDocument、changed_scene_ids:string[] | 時刻を変更するScene ID | TIMING、TimingPatch一件 |

型の実体は[Schema](schemas/vc-artifacts.schema.json)の各request / responseと$defsで固定する。初回構成の可変シーン数は展開slotのSCENES配列で扱い、DETAIL段階に未完成Sceneを完全Sceneとして渡さない。

ENHANCEDのslot=1は作品全体を表す。生成用Schemaは入力の全展開を固定長のprefixItemsとして持ち、件数・ID・順序を固定する。project_id、scenario_sha256、作者のconstraintsと、各必須出来事のbeat_id・出典行範囲も入力から制約する。公開artifact Schemaは変更せず、応答の意味検証も維持する。

enhance version `vcd-story-v5-required-event-ids`では、必須出来事の生成IDを`req_`で始まる識別子へ制約し、作者の台詞IDを流用しないよう指示する。必須出来事のIDは生成側の管理情報であり、作者の台詞ID・本文・話者とは区別する。ENHANCED受理時、及び旧EnhancedStoryをPlannerへ渡した時点で、作者の人物・背景・展開・制約・台詞IDと生成済み必須出来事IDを予約する。衝突した生成IDだけを展開別の一意なIDへ割り当て直す。この時点ではSceneからの参照がまだ存在しないため、後のoutlineは確定済みIDだけを参照する。全出来事の本文・順序・出典行を保持し、既存の衝突しない生成IDは変更しない。割り当ては再適用しても変化せず、変更内容はtraceの`required_event_ids`へ記録する。作者ID同士の重複は引き続き拒否し、診断に実際の重複IDを示す。

DETAILの生成用Schemaは対象展開の音響許可を上限とする。禁止された音響カテゴリをnone・description=nullへ固定し、未使用の作者台詞がなければvocal=noneとする。許可された音声参照の部分集合とsound_event種別だけを生成でき、環境音・効果音が両方禁止ならsound_events=[]に固定する。Shot/event/sound_eventの時刻をScene相対0〜Scene長以内に制約し、プロンプトにも今回の絶対時刻・相対範囲を数値で示す。Shotの連続性や正の長さ、音響の整合は引き続き意味検証する。DETAILだけのprompt versionを更新でき、未変更段階の成功cacheは再利用する。

SceneOutlineは具体的な開始・終了時刻、出演者、背景、接続、依存、状態、必須出来事を持つ。DETAILは構成のID・境界・固定情報を保持し、演技・撮影・発話・音響を追加する。時刻変更が必要ならRETIMEへ回す。

展開には任意のdirection配列とtransition（auto／cut／continue）を持てる。outline生成Schemaは対象展開の出演・背景・接続を制約し、作品先頭のcutをprefixとして固定する。continueの直前Scene依存と共通state値も、outlineの意味検証内で確認する。展開方針をoutline／detailへ提示し、他展開の方針を適用しない。autoでも直前Sceneと出演者集合又は背景が異なる境界では、今回の先頭Sceneをcutへ制約する。後続Sceneのauto選択は保持する。outlineのprompt versionはvcd-story-v6-cast-boundary-cut。指定のない入力と既存Scene EMDの読込を維持する。

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

outline version `vcd-story-v8-global-beat-budget`では、固定duration_msを後続beatの前に使い切った場合、DETAILを始める前に全体の時間配分を一度だけ見直す。同じShot・同じ時間帯を複数beatが共有する場合、その時間を二重加算しない。補助LLMは全beatの順序・IDを固定した`SCENES<TAB>1<TAB>{"windows":[{"beat_id":"...","start_ms":0,"end_ms":9000},...]}`だけを返す。全枠が正の長さで連続し、総尺が作者の指定に一致することを検証する。台詞・出来事・人物・元シナリオは変更しない。最大二回までで、成功した配分だけをcacheへ保存し、生応答と失敗理由はoutline-budget診断へ保存する。

配分に成功した場合は無効なoutlineの時間配分を破棄し、全beatを先頭から構成し直す。任意payload `beat_windows`は上記windows配列であり、生成Schemaが各beatの開始・終了を固定し、15秒超の枠は複数Sceneへ分割する。通常のoutline検証も通し、必須出来事の重複・欠落、依存・状態・出演・背景の不整合を引き続き拒否する。総尺未指定又は後続beatへ時間が残る場合は、この補助呼出しを追加しない。

context不足時は任意の過去出力を縮小し、分割可能なtargetsを分ける。作者原文、固定条件、出演者・背景、必要な状態、発話、検証エラーを切り捨てない。単一targetが収まらない場合はContextBudgetErrorとして停止する。欠けたJSONの末尾をPythonが推測して成功にしない。

モデル・runtime・token予算、cache、OOM、中断、モデル解放は[計画コア](scene-expansion.md)と移植元基盤に従う。

Build / CLI planは`<output_root>/<project_id>/diagnostics/`へ段階別の読みやすいJSONログを保存する。system prompt、request、モデル識別、各attemptの生応答・解析結果・検証エラー・終了状態を保持する。再試行上限に達した場合は例外にもログの絶対パスを表示する。独立したEnhancer / TimelinePlannerノードはcacheルート内の`story/diagnostics/`を使う。

Build / CompileSave / CLIの翻訳も同じ作品のdiagnosticsへ`translation-*.json`を保存する。原文、保護値、system prompt、各試行のrequest・生応答・marker照合・意味検証エラーと終了状態を保持する。翻訳のprotocol・参照保護・発声検査を合わせて最大二回で回復し、失敗時は例外に診断ファイルの絶対パスを含める。独立したGemmaTranslatorは指定cache内のdiagnosticsを既定とする。

DETAILの生成Schemaはsubject_descriptionsとretentionをoutline.actor_idsの順序で固定長配列に制約し、各人物IDを一件ずつ必須にする。人物なしでは両配列を空に固定する。電話越し等の演出による人物定義の欠落を防ぐが、声だけの出演者を映像出演者と区別する機能ではない。detailのprompt versionはvcd-story-v8-exact-subject-coverage。

outlineのprompt versionはvcd-story-v7-remaining-duration。最終展開では確定済みoutlineの終了時刻から作品指定尺までの残時間と、1Scene最大15秒から求めた最低Scene数を提示し、生成Schemaの必須prefixに最低数を確保する。最後のend_ms一致は意味検証を維持し、差分msを修正要求へ返す。既存時刻の自動引き延ばしや新規台詞の補充は行わない。

Scene詳細の生成Shot・非台詞event・sound eventが、同一Scene内・以前のScene・予約済みScene IDと衝突した場合は、PlannerがScene IDを接頭辞とする一意IDへ割り当て直す。作者台詞IDは先に予約し、生成ShotやcameraがそのIDを使っていても台詞側は変更しない。本文・順序・時刻・件数・required_event_idsは保持する。

sound_events.trigger_event_idは、ID変更前に同じSceneのShot内eventへ結び付け、変更後も同じeventを参照させる。Shotやsound自身と同名でも、Shot内eventが一つなら参照先は確定する。二つ以上のShot内eventが同じ参照IDを持つ場合は、近い時刻・種類・同文の本文から推測せず、参照するsoundと衝突IDを示してLLMへ修正を返す。作者DIALOGUEの重複も拒否する。未参照cameraなどの生成ID重複は再生成せず解消でき、変更箇所をtraceのgenerated_idsへ記録する。キャッシュ応答も同じ経路を通す。ID割り当て後にShot被覆・台詞・音響・参照を全検証し、重複Shotの削除やPythonでの時間変更は行わない。これによりID重複で隠れていたSHOT_COVERAGEも、既存の一Shot回復制約へ正しく返せる。

detailのprompt versionはvcd-story-v9-visual-directions。生成Schemaはvisual_directionsを必須化する。人物・時刻・台詞の制約を維持しながら、作者directionの映像上の意図をvisual_directionsへ整理し、音声はDIALOGUE/audio/sound_eventsへ分離する。元directionは保存する。旧artifact Schemaではvisual_directionsは省略可として互換読込を維持する。

detailのprompt versionはvcd-story-v10-bounded-detail-repair。visual_directionsは生成時に最大8件へ統合する制約を付ける（作者原文や完成応答の切り捨てはしない）。SHOT_COVERAGE後の再試行では、生成Schemaが0〜Scene全尺の1Shotと同尺CAMERA一件を要求し、残りのeventはACTION又は作者DIALOGUEとする。通常生成は複数Shot・複数CAMERAを引き続き許可する。被覆エラーは期待終端・実終端・不足msを返す。

detailのprompt versionはvcd-story-v11-scene-roles。visible_actor_idsを必須生成し、Scene参加者の部分集合に制約する。実話者はDIALOGUEから導出する。非可視人物へのACTION/CAMERA・summary・映像方針のID参照は拒否し、正しい撮影対象での再生成を要求する。旧response schemaでは可視性fieldを省略可として読込互換を維持する。

detailのprompt versionはvcd-story-v12-field-repair。VISUAL_AUDIO_ROLEでScene全体の検証に失敗した場合、1Sceneにつき最大一回、visual_directionsだけのLLM修正を追加実行する。可視人物・作者演出・失敗した方針・検証理由を渡し、出力Schemaはvisual_directionsだけを許可する。他fieldを含む修正は拒否し、候補を元Sceneのコピーへ適用したうえで全体を再検証する。失敗時の通常再試行上限は維持する。修正request/raw/statusは元診断ログのvisual_direction_repairへ保存する。文字列の自動削除や検証の緩和は行わない。

detailのprompt versionはvcd-story-v13-preserve-field-repair。visual_directions部分修正はfield単独で検証してfield_acceptedとして保持する。後続のScene全体検証で別fieldが失敗しても破棄せず、同じScene・visible_actor_idsの再試行へ再適用する。可視人物が変わった応答へは流用しない。全体の検証は引き続き必須で、診断ログにはfield_acceptedとreused_fieldsを記録する。

現行detail versionは`vcd-story-v14-explicit-actor-references`。上記のfield修正保持を継承し、summary・映像方針・CAMERA・ACTION等の人物を明示`@actor_id`で生成する。登録済みの名前は検証前にIDへ結び付け、複数人物Sceneの映像文に曖昧な人物代名詞が残る場合は`AMBIGUOUS_ACTOR_REFERENCE`で既存のdetail再試行へ返す。人物の可視性・動作主体・所有者を原文と構造化IDから判断し直し、直前の名前への一律置換はしない。DIALOGUEは作者の本文と話者を変更しない。

現行detail versionは`vcd-story-v15-visual-field-feedback`。v14の人物参照規則を継承し、visual_directionsの部分修正には、許可された人物symbolと除外人物IDを明示する。音声役割に加え、このfield内の未知／非可視人物参照・曖昧な人物代名詞・同名人物の参照エラーも部分修正の対象にする。部分修正のセッションは1Sceneにつき一度で、意味検証に失敗したときだけ、失敗した応答と具体的な検証理由を渡して同じfieldをもう一度修正する（最大二回）。Schemaやrecord形式の違反、他fieldの変更は受理しない。原文や完成応答の語句をPythonで削除せず、LLMが可視人物だけの構図へ書き直す。

部分修正を受理した後は、Scene全体を必ず再検証する。別fieldが失敗しても、同じ可視人物集合での全体再試行では修正済みvisual_directionsを保持する。部分修正の診断は`visual_direction_repair.attempts`にrequest・raw・検証結果を記録し、可視性・作者台詞・時刻・shots・sound_eventsを部分修正で変更しない。

現行detail versionは`vcd-story-v16-prose-field-repair`。人物参照の部分修正をsummary、CAMERA/ACTION本文、人物特徴、音響説明のscalar fieldへも適用する。`AMBIGUOUS_ACTOR_REFERENCE`、`AMBIGUOUS_ACTOR_NAME`、`UNKNOWN_ACTOR`が生成済みの説明文で発生した場合、対象の本文、作者の展開・方針、可視人物、eventの固定話者／主体を渡し、`DETAIL<TAB>1<TAB>{"text":"..."}`だけを生成する。返却Schemaはtext一件以外を許可せず、DIALOGUEは修正対象一覧に含めない。許可されたID、発声・音楽条件を再検証し、検証失敗時は元の本文と失敗応答を保持して同じfieldを最大二回まで修正する。代名詞の一律置換や語句削除で人物を決めない。

一つの説明文を修正した後もScene全体を再検証し、次の説明文・映像方針に失敗箇所があればそのfieldを修正する。これによりsummaryを直すためにShot構造や台詞時刻を生成し直さない。受理済みscalar fieldは参加者・可視人物・event ID/kind/ownerが同じ全体再試行で保持し、構造の異なる応答へは流用しない。同じfield・構造につき修正セッションは一回、全体再試行は従来どおり三回とする。診断ログは`prose_repairs`に対象、request、raw、検証結果を記録する。部分修正後も全体検証を省略しない。

翻訳version `vcd-prose-v5-bound-field-owner`では`field_context`に構造化ownerと、参照先が一人に確定する場合のpronoun_ownerを渡す。これはimmutable_spansとは別の文脈であり、参照出現を追加する指示ではない。英語の代名詞はこの一人に限って許可し、曖昧な場合の修正理由は既存markerの再利用又は名詞句・動詞の接続を要求する。原文のmarker一覧が空なら、再試行でもmarkerを創作させない。二回制限とmarker出現数の厳密な照合は維持する。

現行detail versionは`vcd-story-v17-persistent-shot-coverage`。一度SHOT_COVERAGEが発生したSceneは、受理又は再試行上限まで「一つのShotが0〜Scene全尺を覆い、一件のCAMERAが同じ全尺を覆う」という生成制約を保持する。次の失敗が発声・人物参照など別fieldでも、制約を解除して重複Shotへ戻さない。通常の初回生成では従来どおり複数Shotを許可し、制約を別のScene・別runへ持ち越さない。

各再試行のrequest.errorsは現在のエラーを先頭に置き、必要なら保持中のSHOT_COVERAGE制約を追記する。生成SchemaとLLMに渡す修正情報で同じ制約を使い、診断ログのactive_constraintsに`single_shot_full_scene`を記録する。語句やeventをPythonで削除・再時刻化せず、作者の動作、カメラ移動、DIALOGUEを保ってSceneを再生成する。EFFECT/AMBIENCEは非発声の物理音であり、人物の発声を効果音へ迂回させない。

SHOT_COVERAGE前までの検証を通過したvisible_actor_idsも同じSceneの回復中は保持する。生成SchemaはACTION/CAMERAのactor_idをその可視人物又はenvironmentに制限し、画面外話者を視覚ACTIONへ移して別のエラーへ変えることを防ぐ。作者DIALOGUEのactor_id・text・languageは元の定数のままなので、画面外人物の台詞は削除しない。この制約は`fixed_visible_cast`として記録する。

現行detail versionは`vcd-story-v18-referenced-actor-identity`。v17の時間・可視性の回復制約を継承し、画面外の実話者のsubject_descriptionsを匿名の相手役だけへ縮退させない。元人物に明示された女性／男性・年齢層・同一性属性等を簡潔に保持し、衣服や背景の列挙、継続的発声の指示、新しい台詞を追加しない。未知の性別・声質は補わない。内部Schemaの参加者全員のdescriptionという要件は維持し、Compiler v9が実際の参照に基づいて必要な人物だけを出力する。

現行detail versionは`vcd-story-v19-scoped-context`。ネイティブ生成Schemaを利用する推論では、入力のscenario.beatsとenhanced.beatsを対象Sceneのbeatだけへ絞る。対象展開の台詞・原文・方針・必須出来事、全人物／背景定義、全体の方針／制約／音響許可、確定outline、必要なprevious_scenesは保持する。全体のScene構成は既にoutline段階で承認済みであり、この射影でoutlineや作者原文を変更しない。cacheキー・意味検証・診断のrequestは完全な入力を使い、実際のモデル入力はattempt.inference_requestへ別途保存する。

ネイティブSchemaを使う各段階では、system内の長いJSON Schemaをfield名・型・enum・nullableの略記へ置き換える。出力を制約するGBNFの生成Schema、固定台詞・時刻・人物の値、生成後の検証は維持する。ネイティブSchemaを持たないbackendには完全なSchemaと入力を渡す。

推論のperformanceにはモデル読み込み、入力のtoken計数／予算検査、completion、最初の非空テキストまでの待ち時間、stream全体の秒数、入出力token数を記録する。first_text_secondsは純粋なprefill時間ではなく、入力処理・最初の出力までの待ちを含む。token数はsystem＋request本文の計数で、chat templateの上乗せは含まない。部分修正と翻訳にもstream計測を保存する。既存の診断にはこれらの秒数がなく、出力EMDの長さだけから速度低下の原因を確定しない。

現行detail versionは`vcd-story-v20-required-dialogue`。Plannerは各beatの最後のSceneへ、同beatの前Sceneで未使用の作者台詞をrequired_dialogue_idsとして渡す。途中Sceneへ全台詞を強制せず、既に使った台詞も再配置しない。IDは作者の未使用台詞だけの部分集合として検証し、systemにも必須のID・原文・話者・言語を明示する。公開Scene artifactは変更せず、旧requestはこの任意fieldを省略できる。

小物タグ追加後のoutlineは`vcd-story-v9-prop-tags`、detailは`vcd-story-v21-prop-tags`。outlineはBeatのprop_idsをコピーし、detailはその中から実際に映る小物をvisible_prop_idsへ選別する。人物と小物を@IDで区別して参照し、共通のprop定義を毎Scene生成し直さない。小物の画像内位置は参照選択の情報で、Sceneの配置は演技とstateへ記述する。必須台詞の生成制約・人物の可視性・音響制約を維持する。

現行detail versionは`vcd-story-v22-visible-text`。画面・看板・紙面に印字する作者の文字を、通常の演技説明や発声から分離して`<visible>予想を更新しました</visible>`と記録する。これはVC-Director内部の表記であり、H3固有の制御タグではない。原文と言語を保持し、Compilerは翻訳のimmutable spanへ渡した後、H3向けには通常の文字表示の引用へ変換する。印字された@ユーザー名・人称代名詞・発声に関する単語は、人物参照や発声指示として扱わない。未閉鎖・空・入れ子のタグは拒否し、通常の説明文にある発声指示やDIALOGUEの不変条件は引き続き検証する。

visual_directionsのSTRAY_DIALOGUE／VISIBLE_TEXTも既存の部分修正の対象とする。部分修正へ元のauthored_beatを渡し、実際の表示文字と発話を作者原文から区別させる。表示文字の省略・翻訳や、台詞を画面文字へ移すことを禁止し、出力はvisual_directionsだけへ制約する。最大二回のfield修正後にScene全体を再検証し、別fieldが失敗しても同じ可視人物・小物であれば修正済み方針を保持する。時刻・Shot・DIALOGUE・小物参照・音響をこの部分修正で変更しない。

DIALOGUE_COVERAGEで必須台詞の欠落を検出した場合、同じ最大三試行の範囲で「一ShotがScene全尺を覆い、一件のCAMERAと各必須DIALOGUEを一度ずつ含む」生成制約へ切り替える。既存のSHOT_COVERAGE回復に必須台詞がある場合も同じ出力制約を使う。DIALOGUEのid・dialogue_id・text・actor_id・languageを作者の定数に固定し、時刻と演技はLLMがScene内へ配置する。Pythonによる台詞の挿入・原文の書換え・時刻の捏造は行わない。CAMERA・ACTION・台詞の本文や順序・時刻は引き続き全体検証する。

生成Schemaのeventsはcontains／minContains=1／maxContains=1で各必須台詞の存在回数を検証する。導入済みllama.cppがcontainsを強制しないため、ローカルGBNF converterは内部annotation `x-vcd-event-sequence`を処理する。先頭CAMERAの後に、作者順の必須DIALOGUEを必ず生成し、その前後・間にはACTIONを任意に置ける。必須台詞を再び任意のtail候補へ入れず、省略・重複をdecoderでも防ぐ。配列の先頭へ全台詞をまとめる必要はない。event時刻は配列位置とは独立にLLMが指定する。

回復開始後に別の検証エラーが起きても、必須台詞と全尺一Shotの制約を解除しない。active_constraintsのrequired_dialogue_eventsとsingle_shot_full_scene、request.errorsへ保持中の条件を記録する。台詞欠落の診断には欠落IDを具体的に示す。作者原文の保存、成功cache再検証、話者・言語・本文の不変条件と全台詞一回ずつの最終検証は維持する。


## 人物・小物の区分と翻訳スロット

翻訳version `vcd-prose-v6-typed-occurrence-slots`はScene内のSubject番号ごとにactor / propをCompilerから受け取る。代名詞の候補は、本文に明示された人物と構造上の人物ownerのみから決め、小物を人物候補に数えない。人物が複数残る場合や候補なしの場合は、they / their等の人物参照を自動で誰かに割り当てず、既存の二回以内の翻訳・修正で曖昧性を解消する。Subjectの区分・owner・本文が異なる成功cacheは共有しない。

保護対象がある自由描写は、フィールド全体を文脈ごと一度に翻訳し、`TRANSLATION<TAB>1<TAB>{"prefix":"...","slots":[{"slot":1,"position":1,"after":"..."},...]}`という一行レコードを返す。元の出現ごとに一つのslotを割り当て、native grammarでslot IDの個数・ID・項目順を固定する。positionは英語での読み順であり、日本語の参照順を固定しない。1〜slot数の置換になっているかPythonで検証してから、元のマーカーを各slotに一度だけ結合する。同じSubjectが原文に二度あれば、別slotを二つ保持する。通常の英語chunkへの保護マーカー・raw参照・Unicode escapeによる混入もnative grammarとPython検証で拒否する。保護対象なしの場合は従来どおり一行の平文だが、存在しないマーカーを出力できないgrammarを使う。

slot応答のJSONを旧方式のマーカー付き英文と混同しない。修正要求には前回の応答、必要なslot ID、具体的な失敗理由を渡し、JSON内にマーカーがないことを欠落と数えない。作者の動作や台詞を削除したり、マーカーをPythonで推測補充・重複削除して修復したりしない。日本語エラーには代名詞、マーカーの必要数／出力数、slotの読み順違反、元の技術情報と応答ログの場所を残す。

生成制約の通常文字は、同じ状態へ戻る文字を一つの否定文字クラスにまとめる。文字ごとのリテラル選択肢を列挙すると、llama.cppが保持する候補stackが増え、GPUがCPUの全語彙検査を待つ。Gemma 31Bの262,144語彙・vocab_onlyのCPU単体検査では、旧slot grammarの約3.09秒／回から約0.10秒／回、平文は約2.71秒／回から約0.064秒／回になった。これは候補検査だけの測定で、コンパイル全体の倍率ではない。マーカー混入・Unicode escape・小文字混在・slot重複の拒否と、正常な参照順変更の受理をnative samplerで再確認する。許容する出力言語とpromptは同一なので、翻訳versionと成功cacheは維持する。

### 補強JSONの未完了と展開単位への回復

通常のenhanceは従来どおり作品全体を一応答で返す。モデル応答がJSON末尾で途切れた、文字列が閉じられていない、又はnativeの`finish_reason=length`が記録された場合、複数展開の補強では同じ大きな要求を繰り返す前に展開単位へ切り替える。JSON途中の構文不良は未完了と区別し、通常の最大三回で回復できなければ展開単位への回復を試す。作者の制約・ID・出典の意味検証は省略しない。

enhance requestの任意`payload.enhance_beat_id`は出力対象を一展開へ限定する。原文scenario・全体方針・人物・背景・画像分析・固定条件は全て文脈として保持し、native Schemaは出力beatsをその一件へ固定する。他の展開の出来事を混ぜない。各応答を検証して作者順へ統合し、必須出来事IDの重複は参照生成前に既存の割り当て処理で解消する。元の台詞・固定条件・出典行を変更しない。作品全体のintentは最初の分割応答から保持し、後続応答に同じ全体文脈を提示する。

必須出来事の出典は、入力EMD全体の一始まり行番号を使う。分割後の対象展開の先頭を1へ振り直さない。生成Schemaは開始行の定数と、その開始行〜対象展開終端の終了行範囲を組み合わせ、範囲外・開始終了の逆転を生成時から拒否する。細かな正しい出典範囲は維持し、誤った出典をPythonでクランプしたり展開全体へ貼り直したりしない。意味検証の失敗時は対象展開・出来事ID・許容行・実際の行・展開ID／文書種別の違いを日本語で示す。

導入済みSchemaConverterが無視していた単純な整数のminimum／maximumは、ローカルconverterで桁prefixのコンパクトなGBNFへ変換する。各整数値を全列挙せず、負数・0・桁境界にも対応する。const・enum等の他の制約を数値範囲で上書きしない。公開schemaの型と最終検証は維持し、生成時にも範囲を拘束する。

分割した正常応答はStageRunnerの成功キャッシュへ保存する。一件でも正常完了した場合だけ、同じ入力・モデル・promptで次回も分割処理へ入るstrategy hintを保存する。hintは完成したEnhancedStoryではない。中断後は、正常完了した展開を再利用し、未完了の展開から生成する。無効・再生成のキャッシュ設定にも従う。全件が揃う前に補強全体の成功artifactを作らず、閉じ括弧やカンマの推測追加、原文短縮、台詞削除でJSONを修復しない。

native streamの終了理由とmax_tokensを診断へ記録する。JSONエラーには、LLM応答内の行・列・文字位置と原因を日本語で残し、入力EMDの行番号とは区別する。一展開分でも出力上限に達した場合は同じ上限での反復を止め、contextの空き範囲で出力上限を増やすか展開を分けるよう案内する。完成した妥当なJSONは終了理由がlengthでも受理する。

### 生成文章の発声違反の部分修正

detailの`VOCAL_CUE`は検証を解除せず、該当する生成済み文章だけのLLM修正へ送る。summary・subject_descriptions・ACTION/CAMERAのtext・非発声sound_eventsのtext・生成音響descriptionを対象とする。visual_directionsは既存の配列フィールド修正で処理する。作者のDIALOGUE本文と許可済みNONVERBALイベント、作者の発声指示・音響許可そのものは修正対象にしない。

scalar修正のtaskは`repair_prose_vocal_cues`。元の文章・作者の展開・可視人物・固定済み動作とともに、`fixed_dialogue_events`（話者・本文・言語・時刻・Shot）と`fixed_audio_policy`を渡す。会話相手などの役割名は必ずしも新しい発声指示ではないため、作者の文脈から正しい人物参照と画面上の関係へ書き直す。Pythonで特定語を一律削除したり、発声語を許可リストへ入れたりしない。DIALOGUEを削除・改変せず、視覚的な動作・対象・順序も維持する。既存の`<visible>`の表示文字は個数・内容を変更できない。

出力Schemaは`text`だけを許可し、他fieldの追加を拒否する。最大二回の部分修正で具体的な検証理由と前の応答を返し、候補は同じ発声・人物参照検査を通してから元Sceneのコピーへ適用する。全Sceneを再検証して成功した結果だけをキャッシュする。通常の全体再試行は既存の三回上限を維持し、修正文が不正ならCompilerへ通さない。

修正文の保持条件にはSceneの時刻・構造・動作本文・台詞本文／話者／時刻・音響許可も含める。対象の文章そのものだけは文脈署名から除外するため、他条件が同じ全体再試行へ正しい修正文を再適用できる。固定文脈が変わった候補には流用しない。診断の`prose_repairs.repair_kind`に`vocal_cue`を記録し、進捗には「発声表現の文章修正」を表示する。

### 翻訳の人物名・ID混入からの回復

`TRANSLATION_ACTOR_NAME`は、英訳中の保護されていない登録名・裸の人物IDを検出語・人物ID・文字位置付きで返す。複数の語が混入していれば全て列挙し、既存の保護スロットを一度ずつ維持したまま修正するよう求める。人物IDが一般英単語と一致する場合も検証を解除せず、意味を保つ同義語・構文への変更を求める。表示文字の`<visible>...</visible>`と復元済みSubjectはこの名前照合から除外する。

現行翻訳versionは`vcd-prose-v7-proactive-reference-grammar`。登録済みASCII名・裸のIDを避けるGBNFを初回から適用し、禁止候補を`field_context.forbidden_person_aliases`へ記録する。`man`等の一般英単語とIDが一致する場合も、意味・年齢・性別・同一性を保持する別の名詞句を初回から使うよう指示する。各fieldの一回目で同じ失敗を再学習しない。

複数人物の明示参照検査を行うfieldで、元の参照と構造上のownerから一人の人物へ代名詞を結び付けられない場合は、検査と同じ英語代名詞集合を初回のGBNFでも拒否する。禁止語は`field_context.forbidden_person_pronouns`へ記録する。参照先が一人の人物に確定していれば`their`等を禁止しない。小物は人物候補に数えず、markerなしのfieldにSubjectやslotを補充しない。owner説明は一人の定義fieldにも提示する。fieldごとにこの条件を計算し、別fieldのowner判断を流用しない。

単語境界を区別し、`they`を含まない`theory`や、部分文字列が一致するだけの別単語まで一律に禁止しない。JSONの引用符・バックスラッシュ・スラッシュのエスケープも、復号される文字に対して同じ境界を検査する。非ASCII名・Unicode case変種も含め、最後のPython検証は引き続き必須とする。既存の二回の上限、人物・小物の区分、スロットの個数・読み順・本文・台詞の不変条件は維持する。警告自体は隠さない。

文字ごとの遷移は同一状態へ進む文字クラスにまとめ、grammarを条件とslot数でキャッシュする。通常のsampling処理は変更しない。翻訳キャッシュのキーには新versionと生成条件・owner許可も含め、条件の異なる翻訳を共有しない。保存済みrevisionの再現は維持する。Compilerが翻訳エラーを返すときはSceneと対象IDを付加し、入力の人物／展開の見出しへ対応付ける。

### Outlineの必須イベント割り当て

Outlineの先頭Sceneは、直前までの確定終了時刻から始まる15秒以内の区間へ生成時から制約する。`OUTLINE_TIMING`が出た場合、確定したbeat window・最後の展開の作品終了時刻、又は後続展開の時間を奪わないdraft終端を使い、同じ開始・終了を15秒以内の連続区間へ再分割する。再試行のnative SchemaはScene数・各開始／終了を定数に固定する。例: 35000〜55000msは35000〜50000msと50000〜55000msに分ける。出来事・台詞を削除して15秒へ短縮しない。別のエラーを直す場合も修正済み時間制約を保持し、非native応答も同じ区間と全体検証を通す。日本語エラーには必要時刻・実際の時刻・長さ・15000ms上限を残す。

明示`continue`の先頭Schemaは直前SceneのIDをdepends_onへ固定し、現在も定義されている人物・小物・環境のterminal_stateをinitial_stateの必須prefixへ置く。新しいoutlineの検証では、引き継ぐ状態を省略する応答も拒否する。既存revisionの省略許容は保持する。detailには直前の最後のCAMERAと継続開始状態を明示し、動きを最初から再演せず、終了構図から今回の動作へ進むよう求める。

outlineの生成Schemaは、現在の展開の`required_event_ids`をEnhancedStoryの定義済みIDだけに制限する。必須イベントがない場合は空配列に固定し、確定した時間枠が一Sceneだけの場合は全IDをそのSceneへ固定する。

`OUTLINE_REQUIRED`では、必要件数・出力件数と、未割り当て・重複・未定義のIDを日本語で具体的に示す。一Sceneなら配置先が一意なので、そのSceneの参照配列を定義済みの全IDで確定する。複数Sceneなら、必須IDごとに既存SceneのIDだけを選択する部分修正を実行する。各必須IDは定数と固定長tupleで一度だけ出力させ、既に一意に配置されている参照は元Sceneに固定する。欠落の配置先は原文・必須イベント・既存Sceneの文脈からLLMが選び、Pythonで先頭Scene等へ推測配置しない。

変更できるのは`required_event_ids`だけで、イベント本文・作者の台詞・Scene数・時刻・人物・背景・状態は変更しない。部分修正は一展開につき最大一セッション・二応答とし、通常の全体再試行は三回の上限を維持する。修正後に全体検証を必ず行い、成功した応答だけをキャッシュする。診断ログの`required_assignment_repair`へ修正前後・部分応答・失敗理由を記録する。既存の成功キャッシュも全体検証して再利用するため、outlineのprompt versionは維持する。

### DETAILの出力上限と発声時刻の部分修正

DETAILのJSON解析失敗と生成終了理由`length`が両方確認された場合、残りの全体再試行へ`compact_detail`制約を追加する。人物・出来事・台詞・音響許可を維持し、繰り返す説明や不要なイベントを増やさず完全なJSONを返す。作者指定のカットが必要な場合は保持する。出力を文字列で切り詰めたり、JSONの末尾を推測で補ったり、台詞を削って収めたりしない。`max_tokens`自体は利用者設定を維持する。

生成する自由文のfieldは、初回から原則1024文字、`compact_detail`では512文字の`maxLength`をnative grammarへ反映する。作者指定の台詞やoutlineの定数には適用せず、長い`<visible>`本文がある場合は原文と周囲の説明を収められる上限へ広げる。生成済みの文をPythonで切り落とさない。生成IDにもScene IDの長さに応じた上限を付ける。長さ制約はScene全体のトークン数を保証するものではなく、無制限に長い単一fieldの生成を防ぐ。

bounded stringは上流converterの長いoptional列へ展開せず、GBNFの`{min,max}`へ直接変換する。JSONの引用符・バックスラッシュ・Unicode escapeを維持し、未エスケープの制御文字は許可しない。元からあるconst・enum・独自patternの意味を上書きしない。

JSONが完成した後の`NONVERBAL_OVERLAP`はトークン不足とは区別し、既存非言語発声の時刻だけを部分修正する。台詞の本文・話者・言語・時刻、非言語発声のID・種類・人物・件数、Shot・音響許可を固定する。詳細は[非言語発声仕様](nonverbal-voice.md)を参照。`vocal_timing_repair`へ部分応答と失敗理由を記録し、全体検証が成功してからキャッシュへ保存する。最終的な日本語エラーにも衝突した人物・イベント・実際の時間区間を残す。

同一Scene内で作者の台詞IDが複数回生成された場合は、`dialogue_placement_repair`を一セッション・一応答だけ実行できる。原文のID・本文・話者・言語に一致する既存の配置候補を提示し、LLMが各IDにつき一候補を選ぶ。固定長tuple・ID定数・配置pathのenumで新しいID・配置・台詞の書き換えを禁止する。選択されたイベントの時刻と本文は元のまま保持し、その同じIDの余剰コピーだけを取り除く。別IDの台詞、ACTION・CAMERA・NONVERBAL・効果音、Shotや音響許可は保持する。Shotの時間枠・24fps格子・固定効果音のtriggerを満たさない候補は選べない。

本文・話者の変更、以前のSceneとの重複、未定義の台詞を、この配置選択で隠さない。最後の全体生成後でも部分修正は実行できるが、全体検証を再実行してから成功キャッシュへ保存する。最終エラーは入力の台詞ID変更を促す一般文ではなく、生成Sceneと実際に重複したIDを日本語で示す。

### 音の参照先の部分修正

`SOUND_TRIGGER`は同じSceneのShot内に参照先がない場合と、音の開始が参照イベントの区間外である場合を区別し、音ID・参照ID・実際の区間を日本語で示す。出典の`required_event_ids`、Shot・人物・別Scene・別の音のIDは`trigger_event_id`として使えない。継続環境音だけは参照を`null`にできる。保存済みSceneの検証にも同じ説明を使い、自動修正はscenarioのdetail生成中だけ実行する。

detailでは一Sceneにつき一セッション・最大二応答の`sound_trigger_repair`を実行できる。同じSceneの全不正参照を固定長tupleへまとめ、各音IDを定数とし、参照先を実在するShot内イベントの定数候補へ制約する。LLMは原文と確定済みイベントから音の原因を選ぶ。候補が音の元の開始時刻を含むなら元の開始・終了を固定し、含まない候補を選ぶ場合だけ、音の長さを保ってScene内で移動する。環境音の`null`選択でも時刻を維持する。本文・種類・件数・音響許可・全Shotイベントと台詞は変更しない。

原因に一致するイベントがない場合、LLMは`unresolved=true`を返して修正不能を明示できる。時間が重なるだけのイベントへ機械的に結び付けたり、効果音を環境音へ変えたり、音や動作を追加・削除したりしない。Pythonで音の長さ・24fps格子・参照先と時間枠を再検証し、さらにScene全体の検証が成功した場合だけキャッシュする。最終試行後でも部分修正は使え、診断には候補Schema・部分応答・具体的な失敗理由を残す。

### 全語彙samplingの候補削減

`core/inference/fast_grammar.py`はstock samplerのgrammar直前へ、結果を変更しないことを確認できる候補削減を挿入する。先行するpenalty等を適用済みの候補をscratch領域へコピーし、上位候補だけをnative grammarで検査する。元の全候補配列は確認が終わるまで変更しない。後続が既知のtop-k→typical→top-p→min-p→temperature→dist又はgreedyの場合だけ適用し、Mirostat・未対応API・未知の後続処理には適用しない。

有効候補がK以上あり、捨てる候補が境界へ到達できず同点もない場合は、同じtop-k集合が確定する。K未満の場合は、未知の候補の最大logitと残り個数から確率質量の上界を求め、その質量があってもtop-pの切れ目とmin-pの生存候補が変わらない場合だけ採用する。数値境界には余裕を設け、確定できなければ元の全語彙検査へ戻す。新しい乱数を消費せず、native grammar・top-k以降のsamplerとaccept/resetは維持する。slotや人物検証を緩めない。

RTX 5090 / Gemma 31B Q4_K_S / n_ctx=8192の最小シナリオ構成では、同じseed・同じJSON（82 token）のウォーム比較で3.98秒から2.06〜2.09秒になった。これはその入力の実測であり、作品全体の倍率ではない。通常・slot・同点・greedy・確率が集中する場合のnative比較、未知の尾部候補を変化させる単体試験でも最終候補とseed付き選択の一致を確認する。診断にはsampling/eval時間、生成速度、候補削減回数と全語彙へ戻った回数を推定できるprobe/hit件数を残す。
