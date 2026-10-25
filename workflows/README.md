# VC Director workflow

[VC Director.json](VC%20Director.json)は一つのWFに共有素材、言語生成・コンパイル・保存、JSON読込・動画生成を持ちます。MV-Directorから引き継ぐのは配置とノード境界で、VCの単一WF方針は維持します。

1. `AssetRegistry`へ画像をD&Dするか「画像を選択して追加」で登録し、表示された素材IDをEMDの人物・背景の`画像`欄へ指定します。音声・動画は`assets_json`へ登録します。サンプルは`[]`でコンパイルできます。
2. `Build`の`scenario`へシナリオEMDを入力し、`model_name`のプルダウンからGemma4のGGUFを選択します。`none`は`saved_scene`へ英語Scene EMDを入力してコンパイルする場合に使います。
3. 「言語生成へ切替」で保存し、`Load`に作品名または作品ディレクトリを指定します。
4. H3モデル・`runtime_root`を確認し、「動画生成へ切替」で保存JSONから生成します。

初期値は`assets/emd/minimal.scenario.emd`の内容、`scenario`、既定のGemma4モデル、作品名`scenario_demo`です。実行前に`model_name`でインストール済みGGUFを確認してください。両方停止はMute、両方有効は競合エラーです。ComfyUI画面でのQueue・GPU生成は引き続き受入検証の対象です。

レビューゲート（`Review Candidates`）は既定で`enabled=false`です。生成後の承認待ちを挟まず、次のScene・連結へ進みます。レビューする場合はこのノードの`enabled`を有効にしてください。切替ボタンで動画生成側を有効にしても、この設定は維持します。

`scenario`へ切り替えた場合は本文も[最小シナリオ例](../assets/emd/minimal.scenario.emd)などの`VCD_SCENARIO_EMD_V1`へ置き換えます。モード変更だけでは本文は変換しません。最小シナリオ例は素材なし・作品名`scenario_demo`なので、動画生成時の`Load.project_directory`にも`scenario_demo`を指定してください。

画像はinputの`vc_director/`へ保存します。プレビュー横で素材IDを編集でき、「解除」は登録だけを外します。画像追加・ID変更後は言語経路を再実行してください。画像登録UIの導入後はComfyUIを再起動し、ブラウザを再読み込みします。WFの差し替えは不要です。

Production Plan の `width` / `height` は左側の `Production Resolution`（ComfyUI標準Resolution Selector）から設定します。既定は`16:9 (Widescreen)`・`0.4 MP`・`multiple=32`で、32ピクセル単位への丸め後は864×480です。`width`出力をPlanの`width`、`height`出力をPlanの`height`へ接続しています。Context Loopが未接続の設定ソケットを隠すため、配布WFでは最初から接続しています。`base_seed`は`VC · 32-bit Seed`で設定し、初期値はfixed・1です。これは動画生成用のseedで、Buildの言語生成用seedとは別です。

`VC · Load Text File`はBuildの`emd`へ接続済みです。初期状態では最小シナリオ例が埋め込まれています。`select .txt file`またはD&Dで入力を置き換えます。既存のScene EMDを読み込む場合はBuildの`input_mode`を`saved_scene`へ変更してください。

`VC · Gemma Settings`はBuildの`inference_settings`へ接続済みです。計画・翻訳・Visionそれぞれの`n_ctx`と`max_tokens`、GPU層数・バッチ・sampling・KV cacheをUIで調整できます。既定は従来と同じ値です。[設定の意味と調整例](../profiles/README.md)を参照してください。既存WFにはノードを追加してBuildの任意入力へ接続します。追加ノードとsocketを認識させるにはComfyUIを再起動し、ブラウザも再読み込みしてください。

参照画像の特徴を自動で使う場合は、シナリオの人物・背景の`画像`欄に登録済み素材IDを指定し、Buildの`vision_model_name`でVision GGUF＋mmprojのペアを選びます。`image_facts_json`は`[]`で構いません。`scenario`時のみ分析し、初期値の`disabled`では自動分析しません。

Visionで補完する人物の`特徴`・背景の`情景`は、入力シナリオで省略できます。生成後のBuild出力と`scenes.emd`には、人物の`* 特徴:`・背景の`* 情景:`として保存されます。人物の特徴は各Sceneの`subject_definitions`にも反映します。入力の`scenario.emd`は原文を保持します。手書きの特徴・情景は残し、観察事実を補足します。

再生成:

解像度は`Production Resolution`、ステップ数はProduction Planの`Generation defaults`で指定します。接続されたLoadのシーンを実行時に使用します。旧WFで`The plan needs at least one scene in shots.`が出る場合は、修復欄へ`{"shots":[{"id":"load_preview","length":243,"prompt":"Load supplies the compiled scene at execution.","context_length":0,"audio_context_length":0}]}`を貼り付け、`Apply repaired JSON`を押すと設定欄が表示されます。

```powershell
python tools/generate_workflow.py --context-loop-root PATH/TO/ComfyUI-MiniMaxH3-Contex-Loop
python tools/verify_context_loop.py --context-loop-root PATH/TO/ComfyUI-MiniMaxH3-Contex-Loop
```

生成元は`tools/workflow_templates/`に置いた標準Ref2V Basicのsnapshotです。元ファイルhashを配布JSONに記録し、runtime Contractと登録Schemaを別に照合します。

## 動画モデルと高速化

新しいカメラ計画を使う場合は、ComfyUIを再起動してから「言語生成へ切替」で新revisionを作成し、その後「動画生成へ切替」で生成します。既存Planの動画生成だけではCAMERAは追加されません。出力先の`scenes.emd`のCAMERA行と`plan.json`の`detailed_description`で、対象・方向・時刻を確認できます。固定カメラも明記されます。作者が固定・追従等を指定するときは、シナリオの`* 方針:`や展開本文へ記述してください。

MV-Directorの`02_video_generator.json`から、モデル読み込みと高速化の設定・接続を移植しています。更新した[VC Director.json](VC%20Director.json)を読み込み、ブラウザを再読み込みしてから切り替えます。既存の開いたWFへ新しいノードは自動追加されません。既存の素材登録やEMDを残す場合は、先にそのWFを保存してから移してください。

```text
Hybrid Loader (FL2VA + Ref2VA AdaLN 25..49)
  → Turbo LoRA (Bypass)
  → Comfy Kitchen Attention → Block Sparse Attention (SLA)
  → Spectrum → FP16 accumulation → Sigma Shift
  → BasicGuider / BasicScheduler → Sampler
```

| 設定 | 配布値 |
|---|---|
| Hybrid base / overlay | FL2VA int8 convrot / Ref2VA int8 convrot |
| Overlay | `block_range_adaln`, 25〜49（両端含む）、final AdaLN=false |
| Text Encoder | Qwen3VL 32B MiniMax H3 NVFP4 AWQ |
| Attention | `comfy kitchen attention` |
| SLA | keep=10%、start=0.2、end=1.0、min_tokens=12288、extra_tokens=256、sink=`exact_kv_and_rows` |
| Spectrum | enabled=true、blend=0.5、history / archive=`system_ram`、offline replay=true、audio blend=0 |
| FP16 accumulation | true |
| Sampler / steps / shift | `res_multistep` / 20 / video=12・audio=3 |

Hybridは一つのモデルの重みを選択的に合成する方式です。FL2VAをベースに、指定範囲のAdaLNだけRef2VAから取得します。二段サンプリングではありません。VCのScene別参照選択、native conditioning、Context Loopの24fps・格子・generated audio・trim・連結は引き続き使用します。配布WFの解像度は16:9・0.4MPです。

追加依存は[Hybrid Loader](https://github.com/scottmudge/ComfyUI_MinimaxH3HybridLoader)、[Spectrum MiniMax H3](https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3)、[KJNodes](https://github.com/kijai/ComfyUI-KJNodes)です。Comfy KitchenとBlockSparseAttentionはComfyUI本体側です。この環境では全ノードの登録を確認済みです。他の環境では各ノードと対応モデルを導入し、モデル選択を確認してください。

「動画生成へ切替」はTurbo LoRAのBypassを維持します。「言語生成へ切替」「両方停止」では高速化ノードもMuteします。20ステップはProduction PlanからCurrent Shot経由でSchedulerへ渡ります。高速化は近似計算を含み、画質・速度は生成条件で変わります。BGM抑制の保証にはなりません。

移植元とsnapshot hashは[移植記録](../docs/implementation/mv-video-workflow-sources.json)に保存しています。配布WFの再生成にMV-Director本体のインストールは不要です。導入済みノード・モデルの選択まで照合する場合は、ローカル`/object_info`のJSONを保存して`verify_context_loop.py --object-info FILE`へ渡します。

2026-10-25の短区間検証では、896×672・243 frames・20ステップを159.9秒で生成し、映像／生成音声とも10.125秒で連結まで完了しました。Spectrumは`numerical backend policy transition`でoffline replayを中止し、有効な初回パスを出力しています。生成成功をreplay成功とは扱いません。MV設定を維持しており、この環境でのreplay互換性と長尺での速度・画質比較は未確認です。
