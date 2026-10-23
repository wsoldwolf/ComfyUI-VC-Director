# VC Director workflow

[VC Director.json](VC%20Director.json)は一つのWFに共有素材、言語生成・コンパイル・保存、JSON読込・動画生成を持ちます。MV-Directorから引き継ぐのは配置とノード境界で、VCの単一WF方針は維持します。

1. `AssetRegistry`に素材を登録します。サンプルは`[]`でコンパイルできます。
2. `Build`の`scenario`へシナリオEMDとGemma4を指定するか、`saved_scene`へ既存Scene EMDを指定します。
3. 「言語生成へ切替」で保存し、`Load`に作品名または作品ディレクトリを指定します。
4. H3モデル・`runtime_root`を確認し、「動画生成へ切替」で保存JSONから生成します。

初期値は`assets/emd/minimal.scene.emd`の内容、`saved_scene`、`model_name=none`、作品名`minimal`です。言語経路のCPU確認に使えます。両方停止はMute、両方有効は競合エラーです。ComfyUI画面でのQueue・GPU生成は引き続き受入検証の対象です。

再生成:

```powershell
python tools/generate_workflow.py --context-loop-root PATH/TO/ComfyUI-MiniMaxH3-Contex-Loop
python tools/verify_context_loop.py --context-loop-root PATH/TO/ComfyUI-MiniMaxH3-Contex-Loop
```

生成元は`tools/workflow_templates/`に置いた標準Ref2V Basicのsnapshotです。元ファイルhashを配布JSONに記録し、runtime Contractと登録Schemaを別に照合します。
