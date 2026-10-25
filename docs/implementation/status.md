# 初期実装・検証状況

2026-10-25 / version 0.1.0

## 実装した経路

| 範囲 | 実装 |
|---|---|
| EMD | シナリオ／Scene parser、位置付き文法エラー、Scene renderer、出典の保持 |
| 型・整合 | 公開JSON Schemaでの検証、ID、出演者、背景、台詞、依存、状態、時間包含、必須出来事 |
| 音響 | なしを既定とする許可、全無音／発声禁止／指定台詞、BGM抑制、参照用途の照合 |
| 発話 | 「」からJapanese dタグへPython変換、既存dタグ無変換、翻訳対象から除外、@記号の保持 |
| 時間 | 24fps・17k+5・Guide22、明示audio context、raw/output event境界と格子余剰の記録 |
| Compiler | global人物定義順のSubject割当、Scene別素材番号とschedule、六セクションのPlan |
| 推論 | MVのlazy llama.cpp、事前予算、保護付き全field翻訳、成功cache、中断・失敗時の解放 |
| 計画コア | Enhancer、展開別outline、Scene別detail、数値のみのretime API、SchemaのGBNF制約と最大3回の有限回復、段階別診断ログ |
| 画像 | MVの画像観察・hint・可視性処理・GGUF/mmproj探索・成功cache、VC人物／背景断片へのadapter |
| 保存 | 不変revision、一時保存とatomic pointer、hash照合、翻訳traceを使う決定的な再コンパイル |
| ComfyUI | 9ノード、共有ファイル素材、native Ref2VAへの動的配線、単一WF、Mute切替ボタン |

## 検証したこと

- カメラ計画追加後はPython 105件、ブラウザ側11件、Context LoopのContract・36ノード／65リンク検証が通過。新規DETAILのCAMERA先頭制約、各Shotの時間被覆、欠落・空白・重複からの有限回復、成功cacheの再検証、retime、旧EMD互換、Subject割当、continueのGuide22補正を確認。
- 実Gemma4 31Bで既存30秒3SceneのDETAILを再生成し、全ShotにCAMERAが初回成功。固定／右追従の指定ケースも初回成功し、作者台詞と音響禁止を保持してPlan保存・再読込まで完了。Enhancer／outlineは過去の実応答を再検証して再利用。H3では864×480・20steps・同一seed／素材／モデル設定でCAMERA行だけを追加し、243frames・10.125秒を99.93秒で生成。構図の寄りと前景の視差を確認したが、最後まで正面を保つ指示は未達。Spectrum replayは従来と同じbackend policy transitionで中止され、初回パスを採用。[検証記録](camera-planning-validation.md)に条件と出力先を記載。

- Visionの反映修正後はPython 96件が通過。画像付きシナリオで特徴／情景の省略を許可し、未補完時はLLM呼出し前に案内することを確認。固定観察結果を使い、特徴なしのBuild入力→全計画requestへの定義反映→人物定義／各Scene Subject→Plan JSON→保存・再読込まで検証。手書き記述・原文hash・台詞・音響許可を保持し、完成Scene Schemaの非空制約も確認。この修正のテストではVisionモデルの再推論は行っていない。
- 解像度入力を標準Resolution Selectorへ統合後は36ノード・65リンクで静的照合に成功。レイアウト関連7件のテストが通過し、導入済み標準ノードのexecuteでも16:9・0.4MP・multiple=32から864×480を返すことを確認。動画の再生成は行っていない。
- Gemma Settings追加後はPython 92件が通過。段階別context／出力上限、Vision専用GPU層数・バッチ、ノードseedの優先、未接続時の旧profile保持、モデルロード前の不正値拒否を確認。Buildの計画・翻訳と自動Visionへの伝達、runtime変更によるcache identity変更を検証。配布WFは37ノード・65リンクで静的照合に成功。この設定UIの検証で実GGUFの再推論は行っていない。
- Python unittest 81件で、公開JSON例の正式なDraft 2020-12検証、EMD往復、台詞保護、音響・参照・時刻、成功cache、改変検出、移植ファイルと契約の整合、応答解析・文法転送・診断ログ・失敗回復を検証。複数展開の件数・ID・順序・出典、音響許可・参照の部分集合・Scene相対時刻の生成制約、欠落・長さ0のShotへの修正要求、日本語の発声説明、保護markerを維持する翻訳の意味検証・二回の修正・失敗診断、音楽禁止policy v2と旧revision読込を含む。
- 実Gemma4 31B Q4_K_SとRTX 5090で、素材なしの`minimal.scenario.emd`をEnhancer→outline→detail→英訳→JSON保存→再読込まで実行。10秒・一Scene、243 raw frames、作者の「ただいま。」がJapanese dタグとして保持されることを確認。修正中の後段再実行では前段の成功cacheを再利用。
- 先頭展開だけを返して失敗した実シナリオ（30秒・return/journey/home）を、修正後のEnhancerへ再入力。同じGemma4 31B Q4_K_Sで初回応答が成功し、3展開の順序と各3件の必須出来事・出典を検証。ComfyUIの常駐モデルと共存するため、この単独検証はGPU offload 40層・context 12288で実行。後続のoutline/detail・H3生成はこの検証に含めない。
- DETAILで音響許可を拡張して失敗した同じ30秒シナリオを、実Gemma4 31B Q4_K_S・失敗時と同じseedと標準runtimeで再検証。既存の実Enhancer/outline成功応答を再検証して再利用し、全3SceneのDETAILは新規推論で初回成功。実英訳→Scene EMD/JSON保存→素材hash確認付き再読込まで完了し、全SceneのBGM・環境音・効果音なし、音響eventなし、作者の台詞一件の保持を確認。H3生成はこの検証に含めない。
- 別seed（556250985）で通過していた日本語要約の「帰宅の言葉を口にし」が、英訳でsays wordsとなりVOCAL_CUEに停止することを同じ実GGUFで再現。日本語検査とDETAIL promptを修正後、同じseed・標準runtimeで3Sceneを再生成し、英訳→JSON保存→再読込まで成功。前段の実Enhancer/outline応答だけを再利用し、DETAILと翻訳は新規推論。要約から発声説明がなくなり、作者台詞一件と音響禁止を保持。翻訳の失敗時も原文・生応答・修正理由を診断へ保存する。
- 実H3の30.375秒・3Sceneを比較生成。ユーザー報告の元動画では禁止指示が全Sceneへ渡っていたがBGMが発生。元WFのFL2VAをRef2VAへ変更し、音楽禁止policy v2で同じ3seed・896×672・20 stepsを使用して別runへ保存・連結した。後にFL2VAは品質を目的としたユーザーの選択だったと確認し、配布WFはFL2VAをbaseとするMV式Hybridへ訂正した。無音指定の先頭2Sceneに音声が残り、生成指示だけによる無音保証・BGM解消の確認には至っていない。音声の後処理は採用せず、出力とPCM測定値を比較用に保持。モデル変更による効果と音響文面の効果は分離して評価する必要がある。
- 最小Scene EMDからCLIでPlan / executionを保存し、保存revisionを再読込・再コンパイルしてpreflightに成功。
- 導入済みContext Loopのsource hashがContractと一致。
- MV-Directorのモデル経路を移植後、単一WFの36ノード・64リンクを登録Schemaと照合。Hybrid・高速化・NVFP4 Text Encoderのwidget値を導入済みComfyUIの`/object_info`とも照合。Python 88件、ブラウザ側11件のテストを通過し、モデル経路の接続・MV snapshotとの設定一致・切替時のTurbo LoRAバイパス維持を確認。
- 移植したHybrid経路で既存作品の先頭1Sceneを実生成。FL2VA base・Ref2VA AdaLN 25〜49・896×672・20ステップ・同じseedと参照画像で、243 framesと生成音声を10.125秒のMP4へ保存・連結した。所要159.9秒。中間フレームも確認。Spectrumのoffline replayは`numerical backend policy transition`で中止され、有効な初回パスが採用された。移植元の設定は維持し、replayの互換性は残項目とする。単一区間の実行確認であり、長尺の安定性・速度・画質改善の比較試験ではない。検証生成物は`output/hybrid-port/`、ComfyUIのrun名は`vc_director_hybrid_port_smoke_20261025`。
- Context Loopの実Plan正規化関数にCompiler出力を渡し、prompt・raw frame・明示audio context・generated_audioを確認。
- 画像観察fixtureから人物／背景断片への変換、曖昧な特徴の除外、IMAGE通過を検証。合成参照動画を12→24fpsへ変換し、音声のopt-inもCPUで確認。
- オフラインのwheelビルドに成功。
- MV形式への配置変更後も移動前のPlan / execution fixtureと一致し、配布WFもbyte単位で不変。公開9ノードの入力・出力・実行名を移動前snapshotと照合。
- ComfyUI相当のpackage importで、グローバル`nodes`との衝突やtorch / llama_cppの先行ロードがないことを確認。隔離したwheelだけからprofile / promptを読み、CLI compile / preflightに成功。
- トップの`vc_director/`と`vendor/`を廃止。CLIは`tools/cli.py`、再利用コードは`core/`、再利用promptは`prompts/`へ統合。移植元のhash・統合先・ライセンスは`mv-director-sources.json`に保持。

配置の詳細は[ディレクトリ構成](directory-layout.md)を参照してください。

自動回帰テストのLLM応答は固定fixtureで、別途実GGUFの最小ケースも実行しています。多数のシナリオでの成功率や、実H3の発声抑制はまだ測定していません。

## 実装中に修正した仕様

0.7.3では旧Scheduled公開ノードが廃止されていました。内部helperの存在と公開ノードの登録を分けて確認し、VCがScene別素材を検証してComfyUI標準`MiniMaxH3ReferenceToVideo`へ直接配線する方式へ訂正しました。alias処理と`reference_policy=disabled`を経由せず、発話中の@記号を保持します。既存プラグインの改変はありません。

## 次の受入検証

1. 実ComfyUIでWFを読み込み、切替ボタン、両方Mute、両方有効の拒否、通常Queueの動作を確認する。
2. 実Gemma4で1展開→複数Scene、構成と詳細化の回復、作者固定と状態保持を測定する。
3. 実Visionで人物／背景・複数view・hint conflictを確認する。
4. H3の短区間生成で全無音、指定台詞、物理音のみ、声質参照、続きのaudio contextを比較する。
5. 60秒から3〜5分へ拡大し、人物・背景・音響・context trimと生成音声の連結を確認する。

Gemma推論のGPU・context設定は`VC · Gemma Settings`からUIで指定できます。計画・翻訳・Visionの上限を分け、Build等の任意入力へ渡します。未接続の旧WFは従来のprofile設定を使用します。既存Sceneの部分詳細化・ロック付き編集UI、過去Sceneの依存閉包を使う自動再生成は今後の拡張です。失敗を含む推論traceは作品の`diagnostics/`へ独立保存します。retime APIは既存本文を保持し、指定Sceneの数値だけを変更します。必須contextが収まらない場合は入力を切り捨てず停止します。
