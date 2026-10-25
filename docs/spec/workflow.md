# 単一ワークフローと実行契約

状態: Draft / 0.3

## 1. ベースとグループ

Context Loop 0.7.3の標準`example_workflows/Ref2V Basic - MiniMax H3 0.6.json`を基に、共有素材、言語生成、動画生成を一つのWFに置く。素材が増えるためVC adapterがSceneごとの有効素材を選択する。初期実装は`workflows/VC Director.json`。

| グループ | 言語生成 | 動画生成 | 停止 |
|---|---|---|---|
| 共有素材 | 必要時ロード | 必要時ロード | 出力要求がなければ評価不要 |
| 言語生成・コンパイル・保存 | 有効 | Mute | Mute |
| JSON読込・Context Loop・連結 | Mute | 有効 | Mute |

両方Muteは正常な停止状態で、処理・保存・推論をしない。両方有効は同一実行でファイルを読み書きする競合になるため診断する。コンパイル専用という第三のグループモードは設けない。

言語生成グループ内の入力選択を`scenario` / `saved_scene`にする。後者は保存済みシーンEMDをCompilerへ渡し、Vision / Enhancer / Plannerを実行しない。グループ状態は言語生成有効のままとする。

グループの囲みだけでなく実ノードのMute / Bypassを同期する。副作用やtyped入出力のあるノードはMuteを使い、Bypassは型が一致するpass-throughだけに使う。通常のQueueで有効経路だけが動くことを検証する。

## 2. ファイル境界

言語生成はscenario.emd、scenes.emd、plan.json、execution.json、run_metadata.jsonを保存する。動画生成は保存済みJSONを読む。動画側からLLMへのlive接続は不要とする。

画像・音声・動画のローダーとレジストリは言語グループ外へ置く。言語グループをMuteしても動画経路の素材入力が生きるようにする。

## 3. 人物と参照番号

シナリオの`@hero`は人物識別子であり、素材aliasではない。内部IDはhero。Compilerは出演者のglobal定義順を基準にScene内の1〜4へ割り当て、すべての人物参照を`<Subject N>`へ変換する。発話span内の文字列は変換しない。

各区間で`hero → <Subject N> → 画像・動画・声質音声asset`の対応表を保存する。人物IDは全編で固定し、出演集合が変わって番号が変わっても対応表と素材で同一性を保持する。参照画像のPicture番号を人物番号として扱わない。

素材についてもCompilerがactive scheduleを解決し、導入済みContext Loopと同じ提示順でnative `<Picture N>` / `<Video N>` / `<Audio N>`を確定する。最終Planに素材aliasの解決を残さない。scheduleと番号表の一致を生成前に検証する。

## 4. 現行Ref2VAとの接続

実装時の登録Schema照合で、0.7.3の公開ノードから旧Scheduled系が廃止されていることを確認した。残存する内部alias処理を公開ノードと誤認しない。現行Tagged系はprompt内のtagから素材を有効化するため、VCが作成したnative promptの経路には挿入しない。

VC実行adapterは次を契約する。

1. schedule、素材種別、番号、active Scene、有効参照数をstrictに事前検証する。
2. Compilerが生成したnative参照promptとScene別の素材を、ComfyUI標準`MiniMaxH3ReferenceToVideo`へGraphBuilderで接続する。
3. promptを走査・置換するaliasノードを経由しない。`reference_policy=disabled`への依存も撤回する。
4. VC事前検証で保存revision・参照一覧・素材hash・runtime Contractの一致を必須とする。
5. conditioning直前のprompt全体とraw lengthをCompiler出力に照合する。台詞中の@記号も保持する。

既存プラグインへのmonkeypatchは行わない。登録SchemaとPlan正規化の照合は`tools/verify_context_loop.py`で行う。実Queue・生成品質とは別の検証とする。

動画モデルはMV-DirectorのHybrid構成を採用する。FL2VAをbase、Ref2VAをoverlayとし、block 25〜49のAdaLNを重ねる。native Ref2VA conditioningの接続とDiffusion Modelのbase選択は別の設定であり、Ref2VA単独モデルを必須としない。モデル経路はHybrid Loader → Turbo LoRA（Bypass）→ Comfy Kitchen Attention → SLA → Spectrum → FP16 accumulation → Sigma Shiftとする。切替時はモデルpatchを含む動画経路全体をMuteし、有効化時にはLoRAのBypassを維持する。設定・追加依存は[配布WF](../../workflows/README.md#動画モデルと高速化)を正本とする。

Sceneあたりの上限は画像9、動画3、独立音声3を基準とする。paired audioは独立Audio番号に含める。作品全体の登録素材数とは分ける。動画参照はrestart_each_scene、paired audioは明示有効化したときだけ使用する。音声参照と出力音声のコピーは結び付けない。

## 5. 音声と再開

Context Loopのgenerated_audioを使い、生成PCMを標準decode / Trim / Segment Save / Assembleへ渡す。Assembleはaudio_source=generated。copy / mix / 元音源差替えのVCノードは作らない。

各Sceneの発声・無音・BGM・環境音・効果音の指示をPlanへ必ず出力し、audio contextの明示値は[時間Contract](timing-and-splitting.md)へ従う。

Plan、execution、素材、参照番号表、Contract、モデル・生成条件をfingerprintに反映する。変更後の旧Runを無検証で再開しない。実配線・Mute切替・参照番号・生成音声をCPUと短区間GPUで検証する。
