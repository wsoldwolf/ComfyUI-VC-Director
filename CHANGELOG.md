# 更新履歴

## 0.1.0（開発中）

- 新規TimelinePlannerの各Shotに明示的なCAMERA計画を追加。固定・追従・パン等を作者の指定と動作から選択し、文法制約・時間被覆・重複・有限回復で検証。旧EMDのコンパイル互換を維持し、完全なカメラ計画はretime後も被覆を保つ。

- 配布WFのレビューゲートを既定で無効化し、生成後の承認待ちを省略。
- 画像付きシナリオで人物の特徴・背景の情景の省略を許可。Vision／image_factsを計画用定義とScene EMDへ反映し、人物の観察特徴を各SceneのSubject描写にも保持。補完不能時はVision設定を案内し、完成Scene artifactの非空制約は維持。
- Production Planの幅・高さ入力を標準Resolution Selectorへ統合。既定は16:9・0.4MP・32ピクセル単位（864×480）。
- `VC · Gemma Settings`を追加。計画・翻訳・Visionのcontextと出力上限、GPU層数・バッチ・sampling・KV cacheをUIから指定し、各ノードの任意入力と配布WFへ接続。既定値と未接続WFの動作を維持。
- MV-DirectorのFL2VA/Ref2VA Hybrid、Comfy Kitchen、SLA、Spectrum、FP16 accumulationとNVFP4 Text Encoder設定を単一WFへ移植。20ステップ・Turbo LoRAバイパスを維持し、グループ切替でバイパスが解除される問題を修正。
- 音響prompt policy v2で音楽欄のN/Aを明示的な音楽禁止に変更。全無音・台詞のみの区間を具体化し、policy v1の保存済みrevision読込も維持。
- 日本語の「言葉を口にする」等を要約・ACTIONの発声検査へ追加。翻訳の発声検査をwhole-fieldの二回の修正枠へ組み込み、原文・生応答・修正理由の診断ログを追加。作者台詞をDIALOGUEへ保持。
- DETAILの生成時に、展開で禁止された音響カテゴリ・音声参照・sound_eventを制約。Scene相対時刻の上限と具体的な修正指示を追加し、音響許可の拡張と長さ0のShotによる再試行失敗を改善。
- 複数展開のEnhancer応答で先頭の展開だけが返る問題を修正。生成時に全展開の件数・ID・順序と出典範囲を固定し、欠落・順序不一致の診断に期待値と実値を表示。
- Gemma4の計画応答を段階別SchemaのGBNFで制約。DETAILでoutlineと作者の台詞を固定し、段階別prompt・具体的な修正指示・生応答の診断ログを追加。JSON終端後の余分な出力を明示的に拒否。
- シナリオ／シーンEMDとBuildモードの不一致を推論前に診断。素材なしの最小シナリオ例と、Buildから保存・再読込までの回帰テストを追加。
- GGUFのモデル指定を検出済みモデルのプルダウンへ変更。シナリオモードの`none`をモデル選択の案内付きで拒否し、同名モデルの検索ルートも解決。
- AssetRegistryに画像の複数D&D・選択、プレビュー、素材ID編集、登録解除を追加。ComfyUI標準アップロードを使い、既存WFにも適用。
- 保存JSONとノードのPlan JSON出力を2スペースのインデント・改行付きへ変更。既存revisionの読込とhash検証を維持。
- シナリオ／Scene EMD、Gemma4計画コア、音響・発話制御、Planコンパイル、revision保存、9ノードと単一WFの初期実装。
- MV-Directorに合わせ、`core/`、`nodes/node_*/`、`prompts/`、`profiles/`、`assets/`、`workflows/`、`web/js/`へ再配置。
- ノードIDを維持。CLI入口を`tools/cli.py`へ集約し、再利用コードを`core/`へ統合。トップの`vc_director/`・`vendor/`を廃止し、移植元とライセンスの記録を保持。
