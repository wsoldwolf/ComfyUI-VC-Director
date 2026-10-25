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

配布WFの操作は[workflows](../../workflows/README.md)、文法・音響・時間は[仕様](../spec/README.md)を参照してください。

`VC · 32-bit Seed`と`VC · Load Text File`はMV-Directorから移植した独立ノードです。seedの`-1`は初回の乱数生成、`random`は実行ごとの乱数生成、`randomize once`はブラウザで乱数を設定してfixedへ切り替えます。テキストはUTF-8（BOM可）の`.txt`、最大16 MiBです。内容はWFへ埋め込まれるため、保存後の実行には元ファイルのパスは不要です。追加ノードを使う前にComfyUIを再起動し、ブラウザを再読み込みしてください。

`VC · AssetRegistry`は画像の複数D&D・ファイル選択に対応します。プレビューと編集可能な素材IDを表示し、`assets_json`へ追記します。EMDの`画像`欄へそのIDを指定してください。登録解除はファイルを削除しません。アップロード画像のlocatorはComfyUI標準の` [input]`注釈を付け、Queue時にinput内の絶対パスへ解決します。その他の素材は既存の`asset_root`を基準に扱います。

`Build`・`Enhancer`・`TimelinePlanner`・`CompileSave`の`model_name`は検出したGGUFのプルダウンです。シナリオ生成にはモデルが必須です。`Build`・`CompileSave`だけがモデルを使わない英語Scene EMDのコンパイル用に`none`を提供します。候補に同じ相対名のモデルが複数ある場合は、検索ルートを付けたIDで区別します。

Buildの`vision_model_name`でVision GGUF＋mmprojのペアを選ぶと、`scenario`実行時にシナリオの人物・背景の`画像`欄に紐づくAssetRegistryの画像を自動分析し、Enhancerへimage factsを渡します。人物はsubject_only、背景はscene_onlyで分析します。`image_facts_json`は`[]`のままで利用でき、同じ人物・背景の手入力があれば手入力を優先します。結果は保存revisionのrun_metadata.jsonのrun_details.image_factsに記録します。画像・モデル・分析設定が同じ場合は既存Visionキャッシュを使います。画像内の文字は指示として扱いません。

観察結果はTimelinePlannerにも渡し、Buildの`scene_emd`出力と保存先`scenes.emd`の`# 人物 @ID`→`* 特徴:`、`# 背景 ID`→`* 情景:`へ記録します。人物の観察特徴は各Sceneの`## subject_definitions`にも入り、最終PlanのSubject描写へコンパイルされます。入力の`scenario.emd`は原文のままです。画像を指定しVisionで補完する場合は、入力シナリオの特徴／情景を省略できます。画像なしの記述と完成したScene EMDでは必須です。

EnhancerとTimelinePlannerを独立して使い、手入力`image_facts_json`で補完する場合は、同じ値を両ノードへ渡してください。TimelinePlannerの任意入力にも`image_facts_json`を追加しています。

`disabled`が初期値です。`saved_scene`ではVisionを実行しません。Vision処理は言語モデルのロード前に完了・解放します。新しい項目を表示するにはComfyUIを再起動し、ブラウザを再読み込みしてください。

`VC · Gemma Settings`の出力はBuild、Enhancer、TimelinePlanner、CompileSave、ImageToSubjectEMDの任意入力`inference_settings`へ接続します。同じ設定を共有でき、ノードごとに別のSettingsをつなぐこともできます。Buildでは計画・翻訳・自動Visionへ段階別の設定を渡します。未接続なら以前と同じprofile値です。[項目と調整例](../../profiles/README.md)を参照してください。
