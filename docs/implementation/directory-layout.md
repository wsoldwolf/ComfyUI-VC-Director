# ディレクトリ構成

MV-Directorの`core/`・`nodes/node_*/node.py`・`prompts/`・`profiles/`・`assets/`・`workflows/`・`web/js/`の配置方針を引き継ぎます。VC固有のシナリオ方式と単一ワークフローを保持します。

```text
ComfyUI-VC-Director/
├─ __init__.py                ComfyUIの登録入口
├─ core/                      ComfyUIノードから独立した処理
│  ├─ artifacts/              Schema、整合検証、素材registry、revision保存
│  │  └─ schemas/             配布用JSON Schema
│  ├─ audio/                  発声・音響許可と台詞保護
│  ├─ compiler/               決定的なRef2VA Plan変換
│  ├─ direction/              Story Enhancer
│  ├─ emd/                    parserとrenderer
│  ├─ h3_contract/            フレーム変換とruntime照合
│  │  └─ contracts/           導入済みruntimeの固定契約
│  ├─ inference/              Gemma backend、保護付き英訳、profile読込
│  ├─ planner/                Scene構成・詳細化・retime
│  ├─ protocols/              段階別payload、行応答、有限再試行
│  ├─ utilities/              canonical JSON・hash・時刻表記
│  └─ vision/                 画像観察adapterと参照動画decode
├─ nodes/
│  ├─ common/                 モデル探索、cache先、グループ実行guard
│  └─ node_*/                 一つの公開ノードにつき__init__.pyとnode.py
├─ prompts/                   VC計画用system prompt
├─ profiles/runtime/          MVのノウハウを継承したGemma4既定値
├─ assets/emd/                シナリオ・Scene EMDサンプル
├─ workflows/                 言語／動画切替の単一WF
├─ tests/fixtures/            移動前のPlan・executionの回帰基準
├─ tools/                     図・WF生成、runtime検証
│  ├─ cli.py                 ソースcheckout用CLI
│  └─ workflow_templates/     固定版の標準Ref2V Basicテンプレート
├─ web/js/                    ComfyUI拡張
└─ docs/                      仕様、実装、ノード、図版、移植元記録
```

## 境界

- `nodes/`が`core/`を呼び出します。`core/`は`nodes/`をimportしません。
- シナリオの解釈は`core/direction`と`core/planner`、段階別通信は`core/protocols`、機械変換は`core/compiler`です。
- ノードのモデル探索・ComfyUIのpath参照は`nodes/common`、重い推論・動画decodeは明示実行時に行います。
- `prompts/story_system_prompt.txt`にschemaを付加してLLM要求を構成します。移植元の翻訳・観察promptも`prompts/`を正本として使います。
- `profiles/runtime/gemma4.json`は推論の配布既定値です。Style / Motion / Camera profileの演出選択機能を追加したという意味ではありません。
- 再利用コードは`core/`へ統合し、独立した`vendor/`は置きません。元snapshot53レコードのうち使用する40レコードの配置・hashと、未使用13レコードの除外を`mv-director-sources.json`へ記録しています。
- `.gitattributes`でPython・prompt・JSONのLFを固定し、WFテンプレートは元bytesを維持します。

## 読み込み

ComfyUIはルート`__init__.py`から相対importで`nodes/`を読み込みます。ComfyUI本体の同名`nodes` moduleに依存せず、ノードID・socket・配布WFを維持します。既存の`custom_nodes`ジャンクションはプロジェクトルートを指すため、この配置をそのまま読み込みます。

ソースcheckoutでは`python tools/cli.py`を使います。ローカルtoolは`tools/_bootstrap.py`でルートの一つのpackageを読み込みます。CLI専用`vc_director/`とnamespace検索パスの追加は廃止しました。

wheelでは`setup.py`がルート`__init__.py`と同じ実装を`vc_director` packageへ配置するため、インストール後は`vc-director`または`python -m vc_director`を使えます。ComfyUIとCLIが同じ`core/`・`nodes/`を利用し、実装コードを二重にコピーしません。

## 配布・検証

SchemaとContractの配布先は`core/artifacts/schemas`と`core/h3_contract/contracts`です。`docs/spec`の正本との一致をテストします。新しいファイル配置のimport、公開ノードsurface、移動前後のPlan・execution一致、wheel単体のCLIを検証します。実ComfyUIのQueueやGPU生成品質とは区別します。
