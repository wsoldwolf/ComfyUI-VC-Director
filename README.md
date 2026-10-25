# ComfyUI-VC-Director

日本語の粗いシナリオから、Gemma4が時刻付きシーンEMDを生成し、PythonがMiniMax H3 / Context Loop用Plan JSONへコンパイルします。参照画像・参照音声・任意の参照動画を扱います。入力音声は条件付け専用で、最終音声はH3が生成します。

初期実装版 `0.1.0`。CPUコアと導入済みContext Loop 0.7.3の静的接続に加え、最小シナリオを実Gemma4 31Bで計画・翻訳してJSON保存・再読込する経路を検証しています。長尺での成功率、Vision・H3の生成品質、ComfyUI画面上でのQueue検証は引き続き受入項目です。範囲と残項目は[実装状況](docs/implementation/status.md)に記録しています。

プロジェクト配置はMV-Directorに合わせ、処理本体を`core/`、ComfyUI wrapperを`nodes/node_*/`、promptと既定設定を`prompts/`・`profiles/`へ分けています。[ディレクトリ構成](docs/implementation/directory-layout.md)と[文書索引](docs/README.md)を参照してください。

## 導入

ComfyUIの`custom_nodes`からこのディレクトリを参照できるよう配置し、ComfyUIが使うPythonで依存を導入して再起動します。

```powershell
python -m pip install -r requirements.txt
```

Gemma4推論には、そのモデルとGPUに対応した`llama-cpp-python`が必要です。VisionにはMTMD対応ビルド、対応GGUFとmmprojが必要です。MV-Directorの推論・画像観察・翻訳基盤を同梱しています。ComfyUIとContext Loop本体は同梱しません。

## 単一ワークフロー

[VC Director.json](workflows/VC%20Director.json)を読み込みます。標準Ref2V Basicのsampler / Context Loop / decode / trim / save / assemble経路を基にしています。

1. 共有`AssetRegistry`へ画像をD&Dするか、「画像を選択して追加」で登録します。初期サンプルは素材なしでコンパイルできます。
2. `Build`の`input_mode`を`scenario`にし、[素材なしの最小シナリオ例](assets/emd/minimal.scenario.emd)または[参照素材を使うシナリオ例](assets/emd/homecoming.scenario.emd)を入力して`model_name`のプルダウンからGGUFを選択します。既存シーンEMDを使う場合は`saved_scene`にします。
3. 「言語生成へ切替」で言語グループを有効にしてQueueし、シーンEMDとJSONを保存します。
4. `Load`の作品ディレクトリ、Context Loopの`runtime_root`、H3モデル・生成設定を確認します。「動画生成へ切替」で保存JSONから全Sceneを生成します。

動画モデルはMV-Directorと同じHybrid Loaderを使います。`base_model`はFL2VA、`overlay_model`はRef2VAで、`block_range_adaln`の25〜49を重ねます。Comfy Kitchen → SLA → Spectrum → FP16 accumulation → Sigma Shiftへ接続し、Turbo LoRAはバイパス、20ステップが既定です。モデル名は実際の登録名から選び直してください。[必要ノード・設定と使い方](workflows/README.md#動画モデルと高速化)を参照してください。

初期WFは`scenario`と[最小シナリオ例](assets/emd/minimal.scenario.emd)、既定のGemma4モデルです。実行前に`model_name`でインストール済みGGUFを確認してください。既存の英語Scene EMDをLLMなしでコンパイルする場合は`saved_scene`と`model_name=none`を選びます。

シナリオモードへ変更した場合は、`model_name`も`none`から実モデルへ変更してください。候補は`ComfyUI/models/LLM/GGUF/`と登録済みの`LLM`・`gguf`・`llm`フォルダから検出し、`mmproj`を除外します。新規ノードでは検出できた既定Gemma4を優先します。既存WFのモデル選択は保持します。モデルを追加した場合はComfyUIのモデル一覧を更新するか、再起動してください。

モードを切り替えてもEMD本文は変わりません。配布WFの本文は`VCD_SCENARIO_EMD_V1`のシナリオEMDです。既存Sceneを使う場合は本文を`VCD_SCENE_EMD_V1`へ置き換え、`saved_scene`を選びます。最小シナリオ例とLoadの初期作品IDは`scenario_demo`です。

両グループの「両方停止」はMuteにします。両方有効でQueueした場合はファイル競合を診断します。型を持つノードと保存ノードはBypassで切り替えません。

Gemma4の推論条件は配布WFの`VC · Gemma Settings`で変更できます。計画・翻訳・Visionのcontextと最大出力、GPU層数、バッチ、temperature、KV cache等を設定します。既定値は従来と同じです。詳細は[推論設定](profiles/README.md)を参照してください。

画像をD&Dするとプレビューと素材IDを表示し、`assets_json`へ自動登録します。`hero.png`なら`hero_image`を割り当て、同じIDがある場合は連番を付けます。日本語だけのファイル名には`image`などのIDを割り当てます。素材IDはプレビュー横で編集できます。EMDの人物・背景に`* 画像: hero_image`のように記述してください。EMDは自動変更しません。

画像はComfyUIのinput内の`vc_director/`へアップロードします。`asset_root`が別のフォルダでもアップロード画像を利用できます。「解除」は登録だけを解除し、ファイルは残します。画像追加・ID変更後は言語生成をQueueし、中間JSONを更新してから動画生成へ切り替えます。導入後はComfyUIを再起動し、ブラウザを再読み込みしてください。既存WFにも操作欄が追加されます。

音声・動画や既存ファイルは、引き続き`assets_json`に登録できます。入力例（`asset_root`からの相対パス、または絶対パス）:

```json
[
  {"id":"hero_image", "kind":"image", "locator":"hero.png"},
  {"id":"hero_voice", "kind":"audio", "locator":"voice.wav"},
  {"id":"station_image", "kind":"image", "locator":"station.png"}
]
```

hashを省略した場合、レジストリがファイルから計算します。動画は`kind=video`、`paired_audio_enabled`の既定はfalseです。動画参照は各Sceneで冒頭から再使用し、24fpsへ変換して最初の最大15秒を使います。素材は動画経路で有効になった時に読み込みます。

`ImageToSubjectEMD`は元のIMAGEを通過させ、安定IDを持つ人物／背景のVC断片を返します。その断片をシナリオへ追加します。画像がない背景は作者の情景記述だけで指定できます。

## CLI

```powershell
python tools/cli.py validate assets/emd/homecoming.scenario.emd
python tools/cli.py compile assets/emd/minimal.scene.emd --output output/vc_director
python tools/cli.py plan assets/emd/homecoming.scenario.emd --model PATH/TO/model.gguf --assets assets.json --asset-root PATH/TO/MEDIA --cache output/cache
python tools/cli.py preflight output/vc_director/minimal --asset-root . --runtime-root PATH/TO/ComfyUI-MiniMaxH3-Contex-Loop
```

保存先は`<output>/<project_id>/revisions/<hash>/`。作品直下の`run_metadata.json`が有効revisionを指します。`scenes.emd`を編集する場合はコピーして再コンパイルし、新revisionを作成します。動画経路はファイルhash、素材hash、保存翻訳による再コンパイル、runtime Contractを照合します。

保存JSONとノードの`plan_json`出力は、UTF-8・2スペースのインデント・改行付きで出力します。以前の1行JSONも読み込めます。保存済みJSONの直接整形はファイルhashを変えるため、再保存して新revisionを作成してください。

Gemma4の計画応答は段階別Schemaの文法で制約します。計画時のrequest・生応答・再試行の検証エラーは`<output>/<project_id>/diagnostics/`に保存し、失敗時の例外にもログの場所を表示します。作品のrevisionは全段階の検証成功後に更新します。

ソースcheckoutのCLI入口は`tools/cli.py`です。wheelをインストールした環境では`vc-director`または`python -m vc_director`も使えます。トップにCLI専用の`vc_director/`フォルダは置きません。

## 検証

```powershell
python -m unittest discover -s tests -v
node --test tests/web/*.mjs
python tools/verify_context_loop.py --context-loop-root PATH/TO/ComfyUI-MiniMaxH3-Contex-Loop
```

Node.jsのテストは画像登録UIの複数アップロード、ID競合、失敗時の保持、WF再読込を検証します。最後のコマンドは導入済み版の登録Schema、WFリンク、標準Planの正規化を調べます。モデルをロードしたり動画をサンプリングしたりしません。WF再生成は`tools/generate_workflow.py`で行います。

[仕様](docs/spec/README.md) · [全体ブロック図](docs/assets/vc-director-overview.svg) · [移植元とライセンス](THIRD_PARTY.md)

ソースコードはGPL-3.0-onlyです。再利用したMV-Directorコードの著作権表示とライセンスを保持します。
