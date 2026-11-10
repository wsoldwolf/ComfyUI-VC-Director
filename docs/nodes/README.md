# 公開ノードと配置

ノードIDとsocketを維持し、MV-Directorと同じ`nodes/node_*/node.py`単位に分割しています。登録は`nodes/__init__.py`、共通処理は`nodes/common/`です。

| ノードID | 実装ディレクトリ | 用途 |
|---|---|---|
| `VCDirectorAssetRegistry` | `node_asset_registry` | 共有素材のID・path・hash |
| `VCDirectorEnhancer` | `node_direction_enhancer` | シナリオの意図・必須出来事 |
| `VCDirectorTimelinePlanner` | `node_timeline_planner` | Scene構成・詳細化 |
| `VCDirectorCompileSave` | `node_emd_compiler` | Scene EMDの検証・翻訳・保存 |
| `VCDirectorBuild` | `node_story_builder` | scenario / saved_sceneの統合入口 |
| `VCDirectorLoad` | `node_plan_load` | 保存revisionの検証・読込 |
| `VCDirectorRef2VA` | `node_ref2va` | Scene別素材を標準Ref2VAへ接続 |
| `VCDirectorReferenceVideo` | `node_reference_video` | 参照動画を24fpsへdecode |
| `VCDirectorImageToSubjectEMD` | `node_image_to_subject_emd` | 観察結果から人物／背景EMD断片 |
| `VCDirectorSeed32` | `node_seed32` | 1〜2147483647のseed、fixed／random・randomize once |
| `VCDirectorLoadTextFile` | `node_load_text_file` | UTF-8 .txtをブラウザで選択・D&DしSTRING出力 |
| `VCDirectorInferenceSettings` | `node_inference_settings` | 計画・翻訳・Visionのcontext／出力上限、GPU・sampling設定 |
| `VCDirectorAudioControl` | `node_audio_control` | Planに応じたH3専用NegPiPのMODEL／CLIP patch |

配布WFの操作は[workflows](../../workflows/README.md)、文法・音響・時間は[仕様](../spec/README.md)を参照してください。

`VC · Build`の`cache_mode`で、中断後の再開と再生成を選択できます。ComfyUIを再起動し、ブラウザを再読み込みすると既存のBuildにも選択欄が表示されます。

`* 接続: continue`は、直前Sceneと同じ出演者・背景を引き継ぐ指定です。新規生成では直前の終了状態とカメラ終端からの継続を検査・指示し、新規コンパイルではGuide用の映像コンテキスト22フレームと視覚的な継続指示をPlanへ渡します。更新を反映する場合はBuildを実行し、新しいrevisionをLoadしてから動画生成してください。既存のrevisionは以前のpromptを保持します。

Scene生成時にsummary等の文章へ発声表現が混入した場合は、その文章だけをLLMで視覚描写へ修正します。台詞・話者・時刻・許可済みの非言語発声は固定し、Scene全体を最初から作り直す前に最大二回の部分修正を行います。進捗には「発声表現の文章修正」を表示します。修正後も同じ検査を行い、Compilerの最終検査を緩めません。

補強結果のJSONが途中で終了した場合、複数展開のシナリオでは全体を一度に返す処理から展開単位へ切り替えます。原文全体を文脈として保持し、成功済みの展開はキャッシュして再開に利用します。未完了JSONの推測補修は行いません。一展開分でも出力上限へ達する場合は、推論設定の`planning_max_tokens`をcontextの空き範囲で増やすか、入力の展開を分けてください。エラーのJSON行・列は入力EMDではなくLLM応答内の位置です。

分割補強の必須出来事にも、入力EMD全体での出典行番号を使います。対象展開の行範囲と開始行≦終了行を生成時から制約し、違反時は許容値と実際の値を日本語で表示します。別展開の出典や、分割後に1から数え直した行番号を、自動で正しい出典へ付け直して受理することはしません。

| 設定 | 動作 |
|---|---|
| 再利用（既定） | 正常完了した画像分析・シナリオ補強・Scene構成・Scene詳細・翻訳・コンパイルを再利用し、未完了の箇所から再開 |
| 再生成 | 保存済みキャッシュを読まず全段階を計算し直し、正常完了した結果で更新 |
| 無効 | 保存済みキャッシュを読まず計算し、キャッシュへの書き込みも行わない |

キャッシュは`output_root/<作品id>/.build_cache/`へ永続保存します。ComfyUI再起動後も、同じ設定でキューを実行すると再開できます。EMDの署名・seed・モデル内容・推論設定・素材Registry・画像分析結果・実装とpromptの署名を使い、変更のある入力では結果を混用しません。失敗・中断した応答は保存しません。Scene詳細化はScene単位、翻訳はフィールド単位の成功結果も使うため、Scene途中の中断でも、それ以前の正常完了分を再利用します。コンパイルが完了して保存だけ失敗した場合は、コンパイル結果を再利用して保存します。

キュー実行のたびに、キャッシュより先に入力と参照素材を事前検査します。キャッシュ自体も署名・形式・対応する入力を検証し、不整合があれば再生成します。使用した段階はプログレス表示とログに「完了済みキャッシュを使用」と表示し、保存metadataの`run_details.build_cache`へ記録します。「再生成」「無効」を続けて選んだ場合も、ComfyUIのノード出力キャッシュで省略されず毎回実行します。「無効」は既存キャッシュを削除しません。新方式の永続キャッシュは更新後の正常完了分から蓄積します。

`VC · Load`の`project_name`出力は、読み込んだ保存EMDの作品idです。Production Planの`run_name`へ接続すると、動画・中間生成物の保存先が`output/h3_chains/<作品id>/`になります。先頭3出力の順序は維持し、4番目に追加しています。旧配布WFの固定Run nameはブラウザでのWF再読み込み時にプロジェクト名接続へ移行し、Load実行時に表示名も同期します。独自のRun名や他の入力接続は自動変更しません。

`VC · Audio Control`はSigma ShiftのMODEL、Text EncoderのCLIP、Loadのcompiledを受け取り、MODELをGuiderとScheduler、CLIPをRef2VA、generation_fingerprintをProduction Planへ渡します。EMDに`BGM抑制: negpip`がある場合だけ外部NegPiPを有効化します。`value_strength`は既定1、`measure_attention_mass`は診断用で既定falseです。音声接続はEMDの`音声接続: auto/cut/continue`からCompilerが決定し、この強度widgetとは独立します。

`VC · 32-bit Seed`と`VC · Load Text File`はMV-Directorから移植した独立ノードです。seedの`-1`は初回の乱数生成、`random`は実行ごとの乱数生成、`randomize once`はブラウザで乱数を設定してfixedへ切り替えます。テキストはUTF-8（BOM可）の`.txt`、最大16 MiBです。内容はWFへ埋め込まれるため、保存後の実行には元ファイルのパスは不要です。追加ノードを使う前にComfyUIを再起動し、ブラウザを再読み込みしてください。

`VC · AssetRegistry`は画像の複数D&D・ファイル選択に対応します。プレビューと編集可能な素材IDを表示し、`assets_json`へ追記します。EMDの`画像`欄へそのIDを指定してください。登録解除はファイルを削除しません。アップロード画像のlocatorはComfyUI標準の` [input]`注釈を付け、Queue時にinput内の絶対パスへ解決します。その他の素材は既存の`asset_root`を基準に扱います。

小物は`# 小物 @notebook`で定義し、`名前`・`特徴`・`画像`・任意の`寸法`を記述します。展開本文では`@mahoが@notebookを開く`と参照できます。本文／展開方針にある登録済み小物タグから使用候補を取得し、`* 小物: @notebook, @pencil`でも明示できます。人物と小物のIDは重複できません。小物は話者にならず、共通の特徴・寸法はCompilerが各Sceneへ展開します。

一枚の小物一覧画像を共有する場合、複数の小物定義に同じ画像asset IDを指定し、`* 画像内位置: 左上の赤い手帳`のように対象を区別します。これは参照画像内の物体選択の説明で、動画内の配置指示とは区別されます。H3へ渡す画像は重複排除して一枚、Subjectは小物ごとに別番号です。画面内に選ばれた小物だけを渡し、人物・背景を含めた画像上限9枚は維持します。座標クロップや領域マスクでの固定は行いません。[小物一覧画像用のシナリオ例](../../assets/emd/prop-sheet.scenario.emd)の`prop_sheet`をAssetRegistryへ登録して使用してください。特徴・寸法・位置は例なので実際の画像に合わせて記述します。小物の自動Vision分析はこの追加機能の対象外です。

`Build`・`Enhancer`・`TimelinePlanner`・`CompileSave`の`model_name`は検出したGGUFのプルダウンです。シナリオ生成にはモデルが必須です。`Build`・`CompileSave`だけがモデルを使わない英語Scene EMDのコンパイル用に`none`を提供します。候補に同じ相対名のモデルが複数ある場合は、検索ルートを付けたIDで区別します。

Buildの`vision_model_name`でVision GGUF＋mmprojのペアを選ぶと、`scenario`実行時にシナリオの人物・背景の`画像`欄に紐づくAssetRegistryの画像を自動分析し、Enhancerへimage factsを渡します。人物はsubject_only、背景はscene_onlyで分析します。`image_facts_json`は`[]`のままで利用でき、同じ人物・背景の手入力があれば手入力を優先します。結果は保存revisionのrun_metadata.jsonのrun_details.image_factsに記録します。画像・モデル・分析設定が同じ場合は既存Visionキャッシュを使います。画像内の文字は指示として扱いません。

観察結果はTimelinePlannerにも渡し、Buildの`scene_emd`出力と保存先`scenes.emd`の`# 人物 @ID`→`* 特徴:`、`# 背景 ID`→`* 情景:`へ記録します。人物の観察特徴は各Sceneの`## subject_definitions`にも入り、最終PlanのSubject描写へコンパイルされます。入力の`scenario.emd`は原文のままです。画像を指定しVisionで補完する場合は、入力シナリオの特徴／情景を省略できます。画像なしの記述と完成したScene EMDでは必須です。

EnhancerとTimelinePlannerを独立して使い、手入力`image_facts_json`で補完する場合は、同じ値を両ノードへ渡してください。TimelinePlannerの任意入力にも`image_facts_json`を追加しています。

`disabled`が初期値です。`saved_scene`ではVisionを実行しません。Vision処理は言語モデルのロード前に完了・解放します。新しい項目を表示するにはComfyUIを再起動し、ブラウザを再読み込みしてください。

`VC · Gemma Settings`の出力はBuild、Enhancer、TimelinePlanner、CompileSave、ImageToSubjectEMDの任意入力`inference_settings`へ接続します。同じ設定を共有でき、ノードごとに別のSettingsをつなぐこともできます。Buildでは計画・翻訳・自動Visionへ段階別の設定を渡します。未接続なら以前と同じprofile値です。[項目と調整例](../../profiles/README.md)を参照してください。

`VC · Build`はComfyUI標準のプログレスバーとノード内の状態表示を更新します。入力確認→画像分析（有効な場合）→シナリオ補強→Scene構成→Scene詳細化→翻訳・コンパイル→保存の工程、処理対象のID・件数、モデル読込・再試行・キャッシュ使用を表示します。LLMのストリーム出力中も更新します。割合は工程の配分・完了件数・生成出力に基づく目安で、残り時間の予測ではありません。再試行時も後退せず、保存とBuildの正常終了後に100%になります。エラー・中断時は途中の値を維持します。`saved_scene`は計画工程を省きます。表示を反映するにはComfyUIを再起動し、ブラウザを再読み込みしてください。

BuildはVision・LLMの作成前にEMDと参照素材を事前検査します。人物・小物・背景の未定義ID、台詞を含むIDの重複、話者と出演の不一致、素材IDの未登録・種類違い、参照ファイルの消失・変更、手入力の画像分析結果を確認します。「画像: なし」は画像不要の指定です。構文を解釈できる入力では、見つかった問題をまとめて日本語で表示し、該当するEMDの行番号と記述を添えます。重複IDは両方の定義行を表示します。行番号はBuildへ実際に入力されたEMDの先頭を1行目とし、Scene EMDの出典表に記録された元シナリオの行番号とは区別します。構文エラーでは最初の問題で停止し、必須項目が無い場合はそのセクションの見出し行を表示します。

事前検査に失敗するとモデル生成とrevision保存を行いません。Enhancer・TimelinePlannerの独立ノードでもEMDを先に検査し、CompileSaveでは参照素材も検査します。参照ファイルはEMDで指定された素材をIDごとに一度だけ読み、登録時のハッシュと照合します。未使用の登録素材のファイルはBuildの検査対象に含めません。検査後に変更されたファイルや、LLMが後から生成した不正なID・欠落・時間配置は事前に確定できないため、各生成段階と保存データ読込時の検証も維持します。Buildの生成エラーも主要な原因を日本語で説明し、対象IDと応答ログの場所を残します。入力行を特定できない生成後のエラーには、推測の行番号を付けません。詳細はComfyUIのエラー表示・コンソールで確認してください。

GPUで言語GGUFを新規ロードする前に、同じComfyUIプロセスが現在のCUDA deviceへ保持するモデルを標準のmodel_managementで退避し、Torchの空きcacheを解放します。動画モデルがVRAMへ残った状態でLLMを追加ロードする競合を減らすためです。必要な動画モデルは後続の通常ノードで再ロードされます。CPU設定・CLI実行・同じロード済みGGUFへの連続呼び出しではこの退避を繰り返しません。別プロセスのモデルは操作しません。生成中の既存プロセスには変更を適用せず、ComfyUI再起動後から有効です。

発声条件の選別（audio policy v6）はBuildのscenarioモードで言語生成を再実行して適用します。Sceneの`画面内人物`をLLMが生成し、実際に話す人物はDIALOGUEから決定します。画面外かつ発話なしの人物は最終promptと参照素材から除外し、無発声人物の声質・参照動画の付属音声も渡しません。ComfyUI再起動後、言語生成→保存→Load→動画生成の順で実行してください。


新規Build / CompileSaveは人物と小物のSubjectを区分して翻訳する。小物の参照で人物の代名詞が曖昧と誤判定されることを防ぎ、複数人物間の曖昧な代名詞検査は維持する。保護対象は出現ごとの専用slotで受け取り、通常文へのマーカー追加・slot IDの重複をnative grammarで制限し、Pythonが元の参照を一度ずつ戻す。slotの読み順の重複・欠落は検証して再試行する。翻訳cacheは新版に分かれるため手動削除は不要。保存済みrevisionは旧policyで読み込める。ComfyUI再起動後にBuild又はCompileSaveを実行して新しいrevisionを保存し、そのrevisionをLoadして使用する。日本語の停止メッセージには具体的な翻訳失敗理由も表示する。

翻訳v7では、裸の人物ID・登録名と、参照先を確定できない人物代名詞を初回から生成制約へ渡します。`man`等が一般名詞と人物IDの両方になる場合は意味を保つ別表現を使い、fieldごとの初回警告と再翻訳を減らします。一人の人物に結び付いた`their`等は許可し、小物だけを参照している場合には人物の代名詞を許可しません。保護した表示文字や台詞はそのまま保持します。警告表示や最終検査を無効化する設定ではありません。
