# コア間データ契約

状態: Draft / 0.3

## 1. 共通規則

機械可読定義は[vc-artifacts.schema.json](schemas/vc-artifacts.schema.json)。未知schema・未知key・型違い・必須field欠落を拒否する。integerにboolを受理しない。IDは`[a-z][a-z0-9_]*`、hashは小文字SHA-256の64桁。人物のEMD表記@heroに対して内部IDはhero。

時刻は整数ms、終端を含まない区間。JSONはUTF-8、NaN禁止、非ASCII非escape。配列順を保持する。保存表示は2-space indent・末尾LF。

## 2. artifact

| schema | 必須field |
|---|---|
| VCD_SCENARIO_V1 | schema、project_id、source_sha256、duration_ms、direction、actors、backgrounds、beats、constraints、audio |
| VCD_ENHANCED_STORY_V1 | schema、project_id、scenario_sha256、intent、beats、constraints |
| VCD_SCENE_DOCUMENT_V1 | schema、project_id、scenario_sha256、duration_ms、direction、actors、backgrounds、beats、constraints、required_events、audio、scenes |
| VCD_EXECUTION_V1 | schema、project_id、scene_emd_sha256、plan_sha256、runtime_contract、assets、segments、reference_schedule、output_audio |
| VCD_LLM_REQUEST_V1 / VCD_LLM_RESPONSE_V1 | [段階別protocol](llm-protocol.md)のとおり |

source_sha256とscene_emd_sha256はLF正規化したEMDのUTF-8 hash。Plan hashは保存JSON bytesから算出する。execution自身のhashはrun_metadataへ保存し自己参照しない。scenario_sha256は元シナリオのない手書きScene文書だけnullを許す。

## 3. 共通record

各fieldは必須。任意情報はnullまたは空配列で表す。

| record | fieldと型 |
|---|---|
| SourceSpan | document:scenario/scene、first_line:int>=1、last_line:int>=first_line |
| Actor | id、name、description:string、image_asset_id、video_asset_id、voice_asset_id:string/null |
| Background | id、description:string、image_asset_id:string/null |
| Constraint | id、text:string、source:SourceSpan |
| AudioReference | asset_id:string、purpose:music/ambience/effects |
| AudioPolicy | vocal:none/dialogue、bgm:none/generate、bgm_description:string/null、ambience:none/explicit/expand、ambience_description:string/null、effects:none/explicit/expand、effects_description:string/null、reference_audio:AudioReference[] |
| Dialogue | id、actor_id:string、language:string/null、text:string、source:SourceSpan |
| Beat | id、text:string、actor_ids:string[]、background_id:string、source:SourceSpan、dialogues:Dialogue[]、audio:AudioPolicy |
| RequiredEvent | id、beat_id、text:string、source:SourceSpan |
| EnhancedBeat | id、intent:string、required_events:RequiredEvent[] |
| State | owner_id:string、key:ID形式string、value:string |

Dialogue.textは原表記「...」または既存dタグ全体を保持する。languageは鉤括弧ならJapanese、dタグのLanguage指定があればその値、指定なしならnull。発話内容を英訳しない。Event.textにも同じ原表記を持ち、対応する台詞との一致を検証する。

親の音響許可は上限とする。Sceneはその範囲で許可を狭められる。発話が配置されないSceneはvocal=noneとしてよく、許可を理由に同じ台詞を反復させない。音声参照は対応する音響カテゴリが有効なSceneだけで有効化する。

## 4. Scene / Outline / event

SceneOutlineはid、beat_id、start_ms、end_ms、actor_ids、background_id、transition、depends_on、locked、required_event_ids、initial_state、terminal_stateを持つ。

Sceneはこれらにsubject_descriptions、summary、retention、audio、shots、sound_eventsを追加する。

| field | 条件 |
|---|---|
| start_ms / end_ms | 作品絶対ms、end>start、計画尺15秒以内 |
| actor_ids / background_id | 元Beatに一致 |
| transition | cut / continue、先頭cut |
| depends_on | 先行Scene ID配列、未来参照・循環なし。continueは直前Sceneを含む |
| locked | structure / timing / contentの部分集合 |
| required_event_ids | 文書のRequiredEventを参照。一つの出来事を一Sceneへ担当配置 |
| subject_descriptions | {actor_id,text}[]、出演者と一対一 |
| retention | {actor_id,mode:fully_preserved/partially_preserved}[]、出演者と一対一 |
| initial_state / terminal_state | State[]、owner_idは人物IDまたはenvironment |

Shotはid、start_ms、end_ms、eventsを持つ。時間はScene相対。Eventはid、kind:action/camera/dialogue、start_ms、end_ms、actor_id、text、dialogue_id:string/null、language:string/null。dialogueは話者・台詞ID・言語を対応付ける。action / cameraのdialogue_idとlanguageはnull。

SoundEventはid、kind:effect/ambience、start_ms、end_ms、trigger_event_id:string/null、text。effectは契機eventを必須とし、ambienceはnullを許す。コピー元波形ではなく、H3へ生成させる音の内容を示す。

分割はGemma4がSceneとして確定する。旧split_points_msと、Compilerによる不完全な情報からの自動Scene生成はV1から外す。

## 5. 実行record

Assetはid、kind:image/audio/video、sha256、locator、sample_rate:int/null、sample_count:int/null、fps:number/null、frame_count:int/null、paired_audio_enabled:boolを持つ。素材aliasは使わない。レジストリ順を保存する。

ReferenceBindingはasset_id、role:identity/background/motion/voice/music/ambience/effects/video_audio、token:native Picture/Video/Audio参照を持つ。

Segmentはid、scene_id、index、planned_start_ms、planned_end_ms、start_frame、end_frame、raw_length、context_frames、audio_context_frames、local_subjects、reference_bindings、event_timing、audio、audio_outputを持つ。

local_subjectsは{actor_id,subject_number:1〜4}[]。event_timingは{event_id,planned_start_ms,planned_end_ms,raw_start_frame,raw_end_frame,output_start_frame,output_end_frame}[]。原相対時刻とraw / 出力frameの両方を保存する。audio_outputはgenerated / silenceの生成指示分類であり、waveformのcopy操作ではない。

ReferenceScheduleはasset_id、segment_ids:string[]、roleを持つ。各segment内のReferenceBindingと同じ有効集合・番号順になることを検証する。

runtime_contractはcontract_id、context_loop_version、context_loop_commit、source_fingerprint、fps、output_sample_rate、compiler_version。実行のoutput_audioはgeneratedに固定する。audio_operations、source採用、mixは持たない。

## 6. 横断検証と例

参照・ID一意性、時刻包含、Shot被覆、出演者・背景・許可の一致、発話の保護・対応数、依存の非循環、native番号とschedule、ContractとPlan length、hashをPythonで検証する。意味・演技・波形品質は別評価とする。

EMDとartifactは意味を変えずに相互変換する。出典位置はシーンEMDの出典表へ保存する。実行frame補正はEMDの原時刻に書き戻さない。

[JSON例](examples/artifacts/scenario.json)と各段階のrequest / response例をexamples/artifactsへ置く。素材locator・hash・compiler_versionは説明用であり、そのままGPU実行できる入力ではない。
