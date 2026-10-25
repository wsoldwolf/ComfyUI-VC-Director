# 再利用コード

任意のBGM抑制機能は外部カスタムノード[comfyui-minimax-h3-negpip](https://github.com/hako-mikan/comfyui-minimax-h3-negpip)へ接続します。検証版はcommit `f725718b4c597fab92cdb1fe981dfe3544a75665`、ライセンスはAGPL-3.0です。本体コードは同梱せず、`nodes/node_audio_control/`をadapterとします。[依存のhash記録](docs/implementation/negpip-source.json)と[実生成検証](docs/implementation/negpip-audio-validation.md)を参照してください。

動画WFのHybrid Loader・高速化・Text Encoder設定はMV-Directorの`workflows/02_video_generator.json`から抽出しています。GPL-3.0-only、Copyright © 2026 wsoldwolf。元HEAD・元WFと抽出snapshotのhashは[移植記録](docs/implementation/mv-video-workflow-sources.json)に保持し、`tools/workflow_templates/mv_video_model_stack.json`から再生成します。Hybrid Loader・Spectrum・KJNodes本体は同梱せず、既存の登録ノードへ接続します。

32-bit Seed・Load Text Fileと対応するブラウザUI／テキストdecode処理もMV-Directorから移植しています。VCのノードID・カテゴリへ変更し、UIから未使用のCombo処理を除外しています。移植元HEAD・ファイルhash・統合先hashは[追加移植記録](docs/implementation/mv-utilities-sources.json)へ保存しています（GPL-3.0-only、Copyright © 2026 wsoldwolf）。

ComfyUI-MV-Directorの推論、翻訳、画像観察と必要な補助型を`core/`へ、翻訳・観察promptを`prompts/`へ統合しています。

- 元リポジトリ: ComfyUI-MV-Director（同一プロジェクト群）
- 参照元HEAD: `f01685c0e99223118cc91bd3346c4d2eef802fb8`（`v0.3.0` ブランチ）
- ライセンス: GPL-3.0-only（リポジトリの `LICENSE`）
- 著作権: Copyright © 2026 wsoldwolf
- 取得形態: ローカルworking treeのsnapshot
- 記録: [移植元・配置対応・hash](docs/implementation/mv-director-sources.json)

元snapshotの53レコードそれぞれに元ファイル名とSHA-256を保持しています。使用する40レコードは統合先と現在のhashを記録し、未使用の旧Compiler・旧EMD parser等13レコードは`not_required`として実装対象から除外しました。

推論・翻訳・観察の処理を再利用し、package公開面とimportをVC構成へ統合しています。元の画像観察orchestratorはVC adapterとの名前衝突を避けて`core/vision/observation.py`へ配置しました。VCの文法、物語計画、音響許可、時間・参照対応は引き続きVC側の実装です。旧EMDの実行互換を追加したものではありません。

以前の独立保管用`vendor/`は削除し、統合先を正本とします。`.gitattributes`でPython・prompt・JSONのLFを固定し、統合先hashを検証します。

`workflows/VC Director.json` はContext Loopの標準
`Ref2V Basic - MiniMax H3 0.6.json`から生成しています。
元版はcommit `3c2af7308b9f709f9e10795332181f41597757ee`、
著作権はCopyright (C) 2026 NikoDemon80、ライセンスはGPL-3.0です。
元ファイルhashはworkflow内 `extra.vc_director.base_workflow_sha256` に保存します。
元テンプレートを`tools/workflow_templates/`に保存し、生成処理はそのsnapshotを使います。
ComfyUI本体・Context Loopの実装コードは取り込みません。
