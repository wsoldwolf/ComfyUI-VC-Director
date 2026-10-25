# H3専用NegPiPと独立音声接続の検証

2026-10-25。H3専用NegPiPの導入と音声接続の独立制御を実装した。実H3で負条件の適用と音声Guideを確認したが、今回のNegPiP条件ではBGM除去を達成していない。既定は従来のprompt方式を保つ。

## 導入と生成条件

- 外部依存: [hako-mikan/comfyui-minimax-h3-negpip](https://github.com/hako-mikan/comfyui-minimax-h3-negpip)、commit `f725718b4c597fab92cdb1fe981dfe3544a75665`、AGPL-3.0。[ファイルhash](negpip-source.json)。`C:\Software\ComfyUI\custom_nodes\comfyui-minimax-h3-negpip`へ導入。本体をVCへ同梱せず、pip packageの追加・更新も行っていない。
- 元動画: `C:\Software\ComfyUI\output\h3_chains\vc_director_scenario_demo\final\t2v_normal_2026-10-25_005.mp4`。埋込prompt／WF／Plan／manifestと、元revision `8eb8c4068012903b1a464db4ad2d2d182eb2d8221fad5ac86cfe209605e82bf8`を照合して固定。
- 元動画の先頭無音指定Sceneと末尾指定台詞Sceneを単独区間にして比較。各組でseed、参照画像とhash、解像度、モデル、sampler、元promptを固定し、BだけCompiler所有の負条件を追加。LLMの再生成なし。入力音声・動画参照なし。
- 864×480、24fps、20steps、res_multistep、shift video12／audio3。FL2VA int8 convrotをbase、Ref2VA int8 convrotのAdaLN25〜49をoverlay。Qwen3VL32B NVFP4、Comfy Kitchen、SLA、Spectrum、FP16 accumulation、Turbo LoRA bypassを維持。
- 負条件は`(background music, instrumental soundtrack:-1.0@a0-)`。音声streamの全時間へ適用。`apply_positive_weights=false`、`protect_text_rows=true`。value_strengthを1と3で比較。

## 同一seed比較

Musicは[AST AudioSet分類器](https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593)による先頭窓のsigmoid応答。無音指定は先頭10秒と末尾10秒、指定台詞は全6.583秒を入力し、元音量とRMS −20dB正規化の両方を測定した。表は元音量の先頭窓。分類値はBGM量や除去率ではなく、音量低下だけで改善とは判断しない。聴取・ASRによる台詞一致や声質評価は実施していない。

| 指定 | seed | frames／秒 | 条件 | RMS dBFS | Music |
|---|---|---|---|---|---|
| 全無音 | 1377979519882038844 | 294／12.25 | 従来prompt | −15.77 | 0.630 |
| 全無音 | 同上 | 同上 | NegPiP 1 | −16.36 | 0.534 |
| 全無音 | 同上 | 同上 | NegPiP 3 | −17.52 | 0.613 |
| 指定台詞のみ | 16434205766500901458 | 158／6.583 | 従来prompt | −30.03 | 0.826 |
| 指定台詞のみ | 同上 | 同上 | NegPiP 1 | −29.05 | 0.784 |
| 指定台詞のみ | 同上 | 同上 | NegPiP 3 | −28.75 | 0.701 |

無音指定の末尾窓はMusic 0.704→0.560→0.791で、強度増加による単調な改善はない。音量を揃えてもMusic応答が残る。指定台詞SceneではSpeech応答も0.757→0.632→0.641と変わり、台詞品質を無影響と扱わない。

NegPiPログでは負条件5token、追加5token、時間制限5token、全50blockへのhook適用を確認した。attention massは128個の**映像query**に対する診断値であり、音声への効きや抑制成功の尺度とはしない。Spectrum offline replayは従来同様`numerical backend policy transition`で中止し、有効な初回パスを採用。生成成功とreplay成功を区別する。

成果物はリポジトリの`output/negpip/`へ保存（git対象外）。`jobs.json`が条件と各API request／revisionを対応付け、`audio-analysis.json`が両窓・正規化後・全分類値を保持する。生成履歴は`<silent|dialogue>_<prompt|negpip|negpip3>-result.json`、試聴用PCMは同名`.wav`。比較run名のprefixは`vc_negpip_20261025_203803_`、動画は`C:\Software\ComfyUI\output\h3_chains\<run名>\final\<run名>.mp4`。

## 映像cutと独立した音声Guide

元動画の門前と帰宅の2Sceneを使用し、両方の映像をcut、音声だけcontinueとした。NegPiP強度1、元と同じseed・素材・描写を使用。入力は`output/negpip/audio-carry.json`、検証結果は`audio-carry-result.json`。

| Scene | 映像context | 音声context | raw／出力frames | 実PCM rate／samples |
|---|---|---|---|---|
| scene_at_the_gate | 0 | 0 | 158／158 | 32000／210667 |
| scene_home | 0 | 22 | 158／158 | 32000／210667 |

ログ上、2区間目は映像cond blockなし・trim0、音声22frameを37 latent steps（0.925秒）として受け取った。音声格子の境界はframe −0.200で、新Scene開始前に終わる。316frames・13.1667秒の映像と生成音声を標準Assembleで連結した。音声の継続を条件付けできることを確認し、聴感上の完全な連続性やBGM除去の成功は主張しない。

run名は`vc_negpip_20261025_203803_audio_only_carry`。この検証revisionは修正前のoutput_sample_rate宣言48000Hzを保持しているが、実PCMは32000Hzだった。修正後の新規明示音声接続revisionでは追加Contractのnative rate32000Hzを記録し、既存revisionは保存宣言値を渡して決定的に再コンパイルする。

元作品のactive revisionは変更していない。無音化・音声分離・元音源への差替えは行っていない。

## 自動検証

Python unittest120件、ブラウザ11件。Contractと37ノード／68リンク、Context Loop実Plan正規化関数の映像cut＋音声22を検証した。未指定EMDの旧接続動作、Scene EMD往復、保存再読込、音響prompt v1／v2の再現、LLMの制御値固定、音響許可を狭める境界の拒否、強度変更時のfingerprintを含む。

## cl-japanese2jsonとの切り分け

ユーザーの旧版での抑制成功報告を受け、導入済みcl-japanese2jsonの`node_japanese_to_json/compiler/jsongen.py`を確認した。無音時は`Complete silence.`、台詞のみはexact shot-synchronized dialogueと他の音がない旨、音楽欄は`N/A`だった。VCのpolicy v2は同じ欄へmusic／soundtrack／score／melody等の禁止語を長く列挙していた。

今回、モデル経路を変えず音響2欄だけ導入済み旧実装の文面へ差し替え、同じ2seedで追加比較した。VC adapterと同じnative Ref2VA＋同じLoadImageをAPI graphへ明示接続し、実験promptを渡した。NegPiPなし、音響欄以外のprompt・参照hash・steps・seed・解像度を維持した。旧実装の全機能や成功WFを再現した試験ではない。

| 指定 | VC v2 Music | 旧文面 Music | 旧文面 Music（音量正規化） | RMS dBFS（v2→旧文面） | Speech（v2→旧文面） |
|---|---|---|---|---|---|
| 全無音 | 0.6297 | 0.0163 | 0.0323 | −15.77→−43.44 | 0.0014→0.0024 |
| 指定台詞のみ | 0.8263 | 0.0026 | 0.0024 | −30.03→−29.89 | 0.7573→0.7628 |

無音指定の末尾窓Musicも0.7041→0.0108（正規化0.0185）へ低下した。台詞区間はほぼ同じ音量・Speech応答でMusicが低下しており、全体を小音量化した結果ではない。無音区間には弱い物理音・雑音の分類応答が残り、完全無音とは扱わない。正確な台詞・声質・聴感は未検証。

今回の2例では、VC v2の長い音響禁止文がBGM抑制を弱めた有力な要因である。音響2欄を同時変更したため、どの単語・どちらの欄が主因か、一般のSceneへどこまで再現するかは未確定。禁止語が音楽概念を誘発した可能性は仮説であり、内部機序を証明していない。

この結果から新規コンパイルを簡潔なpolicy v3へ変更した。保存済みv1／v2は元policyで照合するため、既存Planの動画生成だけでは文面は変わらない。Scene EMDの再コンパイルでv3の新revisionを保存する。追加試験のrequest／履歴／PCMは`<silent|dialogue>_clwording-*`、動画run名は上記prefixに`silent_clwording`／`dialogue_clwording`を付ける。API prompt IDは`5d7677a7-97b4-4aa3-a74d-a69bfe4f6ea0`／`b396d538-33d0-40b4-9aef-2d02e1321f44`。

### 提供された成功動画の実設定

ユーザー提供の`E:\OutputCollection\MiniMaxH3\junk\h3_chains\ref2v_demo_20260822_buttle\final\t2v_normal_2026-08-27_001.mp4`（24秒・3Scene）の埋込API graph／Plan／manifestを確認した。解析資料は`output/negpip/cl-success-*.json`へ保存。

| 項目 | 成功動画の実設定 | 今回のVC比較 |
|---|---|---|
| モデル | FL2VA int8 convrot単体 | FL2VA＋Ref2VA AdaLN25〜49 Hybrid |
| Turbo LoRA | 有効、strength1 | bypass |
| sampler／steps | beta scheduler、8steps | res_multistep、20steps |
| 音声参照 | 声質用2本、人物へ明示割当 | なし |
| 映像／音声context | 全Scene 0／0 | 単独比較は0／0 |
| 音楽欄 | `None.` | v2は長い禁止文、比較文面／v3は`N/A` |
| 音響欄 | 台詞と飛翔音など、生成する具体音を短く指定 | v2は長い禁止列挙、比較文面／v3は簡潔な制約 |
| Text Encoder・shift・解像度選択 | Qwen3VL32B NVFP4、12／3、16:9・0.4MP | 同じ選択 |

成功動画にはSageAttention patchとComfy Kitchenがあり、SLA／Spectrum／NegPiPはない。API graphにcl-japanese2jsonの実行ノードは含まれず、生成時点の最終promptを証拠とする。この動画は音楽欄が`None.`であり、追加比較の`N/A`と同一ではない。短い音響文面という共通点を確認したが、Turbo LoRA・参照音声・内容の寄与をこの動画だけから分離しない。今回のHybrid・Turboなしの比較でも改善したため、旧モデル経路への全面復帰を改善の必要条件とはしない。

### 再生成用の別WF

`output/negpip/VC Director audio v3.json`に元作品の素材・モデル設定を残したWFを用意した。別の保存先`output/negpip/projects/audio_v3/scenario_demo/`をLoadし、全4Sceneをv3で再コンパイル済み。映像contextは0／22／0／0、音声contextは0／22／22／22。NegPiPは選択式のまま、このWFのEMDでは有効にしていない。ComfyUIを再起動・ブラウザを再読み込みして、このWFを読み込めば動画生成できる。新規の全4Scene動画生成は未実施で、元作品のactive revisionは保持する。言語経路の入力も同じScene EMDへ揃え、saved_sceneで再コンパイルできるようにしている。
