# ComfyUI-VC-Director

日本語の粗いシナリオから、Gemma4が時刻付きシーンEMDを生成し、PythonがMiniMax H3 / Context Loop用Plan JSONへコンパイルします。参照画像・参照音声・任意の参照動画を扱います。入力音声は条件付け専用で、最終音声はH3が生成します。

初期実装版 `0.1.0`。CPUコアと導入済みContext Loop 0.7.3の静的接続を検証しています。実Gemma4・Vision・H3による生成品質と、ComfyUI画面上でのQueue検証はこれからです。範囲と残項目は[実装状況](docs/implementation/status.md)に記録しています。

プロジェクト配置はMV-Directorに合わせ、処理本体を`core/`、ComfyUI wrapperを`nodes/node_*/`、promptと既定設定を`prompts/`・`profiles/`へ分けています。[ディレクトリ構成](docs/implementation/directory-layout.md)と[文書索引](docs/README.md)を参照してください。

## 導入

ComfyUIの`custom_nodes`からこのディレクトリを参照できるよう配置し、ComfyUIが使うPythonで依存を導入して再起動します。

```powershell
python -m pip install -r requirements.txt
```

Gemma4推論には、そのモデルとGPUに対応した`llama-cpp-python`が必要です。VisionにはMTMD対応ビルド、対応GGUFとmmprojが必要です。MV-Directorの推論・画像観察・翻訳基盤を同梱しています。ComfyUIとContext Loop本体は同梱しません。

## 単一ワークフロー

[VC Director.json](workflows/VC%20Director.json)を読み込みます。標準Ref2V Basicのsampler / Context Loop / decode / trim / save / assemble経路を基にしています。

1. 共有`AssetRegistry`に素材IDとファイルを登録します。初期サンプルは素材なしでコンパイルできます。
2. `Build`の`input_mode`を`scenario`にし、[シナリオ例](assets/emd/homecoming.scenario.emd)とモデル名を入力します。既存シーンEMDを使う場合は`saved_scene`にします。
3. 「言語生成へ切替」で言語グループを有効にしてQueueし、シーンEMDとJSONを保存します。
4. `Load`の作品ディレクトリ、Context Loopの`runtime_root`、H3モデル・生成設定を確認します。「動画生成へ切替」で保存JSONから全Sceneを生成します。

初期WFは`saved_scene`と[最小シーン例](assets/emd/minimal.scene.emd)、`model_name=none`です。描写は英語、台詞は日本語のままで、LLMなしのコンパイル確認に使えます。日本語描写の英訳にはモデルが必要です。再コンパイル時は、同じモデル・設定・プロンプトに対する成功翻訳cacheを再利用します。

両グループの「両方停止」はMuteにします。両方有効でQueueした場合はファイル競合を診断します。型を持つノードと保存ノードはBypassで切り替えません。

共有素材の入力例（`asset_root`からの相対パス、または絶対パス）:

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

ソースcheckoutのCLI入口は`tools/cli.py`です。wheelをインストールした環境では`vc-director`または`python -m vc_director`も使えます。トップにCLI専用の`vc_director/`フォルダは置きません。

## 検証

```powershell
python -m unittest discover -s tests -v
python tools/verify_context_loop.py --context-loop-root PATH/TO/ComfyUI-MiniMaxH3-Contex-Loop
```

2つ目は導入済み版の登録Schema、WFリンク、標準Planの正規化を調べます。モデルをロードしたり動画をサンプリングしたりしません。WF再生成は`tools/generate_workflow.py`で行います。

[仕様](docs/spec/README.md) · [全体ブロック図](docs/assets/vc-director-overview.svg) · [移植元とライセンス](THIRD_PARTY.md)

ソースコードはGPL-3.0-onlyです。再利用したMV-Directorコードの著作権表示とライセンスを保持します。
