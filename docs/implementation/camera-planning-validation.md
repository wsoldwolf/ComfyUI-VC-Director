# カメラ計画の検証記録

2026-10-25。実装範囲は[計画](camera-planning-plan.md)に対応する。

## 実装と自動検証

TimelinePlanner DETAILが各Shotに固定又は移動のCAMERAを作成する。先頭eventの生成文法と`core/planner/camera.py`の意味検証を併用する。欠落・区間の空白・重複・Shot外を既存の最大3試行へ返す。成功cacheも同じ検証を通る。内容補完のためのLLM呼出しやCompilerの創作は追加していない。

導入済みllama.cppのJSON Schema変換は、`prefixItems`と辞書型`items`の併用時に先頭制約を落とす。`core/protocols/grammar.py`のアダプターは、先頭部分と後続eventの反復をGBNFへ変換する。変換時の参照解決が公開Schemaを書き換えないようdeep copyする。静的文法検査に加え、以下の実推論でもこの文法を使用した。

Python unittest 105件とブラウザ側11件が通過。Context Loop source hash・36ノード／65リンク・Plan正規化を照合した。新規テストは固定・追従・パン・人物なし・複数Shot、開始／途中／終端の空白、重複、0長、Shot外、有限回復、cache再検証、retimeの被覆保持、旧EMD、台詞・音響保持、Subject割当、continue時のGuide22補正を含む。共有Scene Schema・CompilerのCAMERA任意性は維持する。

## 実Gemma4

元の作品revisionは`329dd60978182d32437a7f8fae7eb46b93d29bc4e1997766cb9777987493fb6d`。墓地→鉄門→居間の30秒シナリオと既存Vision観察を使用した。Enhancerとoutlineはそのrevisionの過去の実応答を再検証して再利用し、DETAILは今回の文法・プロンプトで実推論した。Visionを再推論した試験ではない。

モデルは`gemma-4-31b-it-heretic-ara.Q4_K_S.gguf`、SHA256 `2fa55d46083775b3b308b41b0c255f1d44466fd9f9df8308f7545b369494e858`。plannerはn_ctx=24576、max_tokens=4096、seed=42、temperature=0.2、top_p=0.9、repetition_penalty=1.05、GPU全層、n_batch=512、flash attention、KV q8_0。翻訳のn_ctxは16384。

| ケース | 実応答 |
|---|---|
| 墓地10秒 | 中景から歩行を追従し、停止後は表情を捉える構図 |
| 鉄門10秒 | 小道へ追従し、振り返りに合わせて緩やかに寄る |
| 居間10秒 | 入室から着座・月を見る横顔へ構図を移す |
| 固定を明示した別ケース | 「固定カメラ。中景の位置・画角を保持する」 |
| 右追従を明示した別ケース | 横から右へゆっくり追従し、終端に上半身と進行先の家 |

全5件のDETAILは初回で検証に成功した。各Shotに一件の0〜10000ms CAMERAがある。作者の台詞`「ただいま。」`、BGM／環境音／効果音の禁止を保持し、実翻訳→Scene EMD→Plan→保存・再読込まで確認した。固定／追従の別ケースは構造を既存応答から再利用してDETAILを評価したもので、Enhancerからの全段階再生成ではない。

成果物はworkspaceの`output/camera-planning/`。`scenes.emd`、`document.json`、`real-result.json`、`planning-trace.json`、`static.emd`、`follow.emd`、段階別diagnosticsを保持する。完成30秒Planのrevisionは`compiled/scenario_demo/revisions/d7001a683a90d95830c2f743e564eb79c48d8d64bf205bb31c5e7d7d65c84a01`。ユーザー作品のactive revisionは変更していない。

## H3の条件を揃えた比較

Aはユーザーが提示した既存映像のclip revision `5a6da231e90d4990ada2ff644ed5355c`。その実行アーカイブから設定・seed・元プロンプトを取得した。Bは元のScene本文を保持し、今回Gemmaが生成した先頭SceneのCAMERAだけを追加した別revision。LLMで再生成した他の本文は比較へ混ぜていない。

両方864×480、24fps、243frames、10.125秒、20steps、seed=`1377979519882038844`。FL2VA base＋Ref2VA AdaLN25〜49、Comfy Kitchen、SLA、Spectrum、FP16 accumulation、res_multistep、shift12/3、NVFP4 Text Encoder、参照素材hashを保持した。Bの実行アーカイブでCAMERA以外のプロンプトの完全一致、seed・解像度・raw frame・stepsの一致、モデル・高速化・参照ノードのAPI入力の一致をassertした。出力名・Plan・保存projectのノードだけを比較用に変更した。

追加した行:

```text
[00:00.000 --> 00:10.000] CAMERA <Subject 1>: Medium shot. Gently follow the walking protagonist, and after stopping, maintain a composition that captures their expression from the front.
```

Bはprompt ID `215bc663-1462-43a1-81f3-85ed65a54a8e`、clip revision `b8afb692395b4a9c9c25fe3552212407`。全実行99.93秒で成功し、連結まで完了した。

```text
C:/Software/ComfyUI/output/h3_chains/vc_director_camera_compare_20261025/final/vc_director_camera_compare_2026-10-25.mp4
```

比較画像は`output/camera-planning/comparison.jpg`、数値は`motion.json`、条件は`comparison.json`、実行履歴・ログは`h3-history.json`と`comfy-logs.json`。実行アーカイブは同runの`recovery_archives/b8afb692395b4a9c9c25fe3552212407/`。

目視でAは背景がほぼ固定、Bには構図の寄りと前景の墓石の相対移動が見られた。背景中心部の人物を除外した特徴点追跡でも、Aの先頭→終端はscale約0.99999、変位約0.07／0.04px、inlier1293/1660だった。Bでは前景の視差と奥の背景の動きが異なり、単一アフィン推定の支持は114/946まで下がるため、一つの拡大率をカメラ移動量として採用しない。人物の拡大だけを根拠にはしていない。

Bの終端は後方寄りの構図となり、「停止後は正面を保持」という指示を最後まで守ったとはいえない。計画生成・転送の成功と、モデルによる撮影指示の遵守は区別する。Spectrum offline replayは`numerical backend policy transition`で中止され、有効な初回パスが採用された。これはAの既知の状態と同じで、今回replay互換性を改善したものではない。

今回の比較は16:9でもCAMERA追加により構図変化が出ることを確認する試験。4:3との条件付き比較、複数seedでの成功率、全30秒の動画再生成、音声品質の再評価は実施していない。16:9を元の静止化の単独原因とする結論にはしていない。

利用時は新しいPythonコードを読み込んだComfyUIで言語生成から新revisionを作り、その後動画生成へ切り替える。旧Planを動画生成し直すだけではCAMERAは追加されない。
