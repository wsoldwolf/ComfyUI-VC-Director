# Context Loop時間契約

状態: Draft / 0.3  
Contract: `VCD_CL_GUIDE_24FPS_V1`

## 1. 基準

H3 / Context Loopの実処理長を正本とする。Gemma4は15秒以内を目標に具体的なScene / Shot / event時刻を書く。Compilerが契約に合わせて実現時刻を確定し、LLM時刻を勝手に原文へ書き戻さない。

ローカル確認対象はContext Loop 0.7.3、HEAD `3c2af7308b9f709f9e10795332181f41597757ee`。`chain_nodes.py`のframe格子、映像context、明示的なScene audio_context_lengthを契約する。保存済み[contract JSON](contracts/context-loop-timing.json)に確認対象と関連ファイルhashを記録する。標準Ref2VAの実装であるComfyUIの`comfy_extras/nodes_minimax_h3.py`も対象とし、パスはContext Loopディレクトリからの相対とする。source_fingerprintはsource_filesをkey順・空白なし・UTF-8 JSONへ変換したSHA-256。runtime更新時は互換試験を行い、版だけで自動的に同一契約と見なさない。

| 項目 | V1 |
|---|---|
| FPS | 24 |
| raw length | 5〜3592、17k+5 |
| 継続方式 / anchor | guide / head |
| 映像context | cut=0、continue=22frame |
| 音声経路 | generated_audio、最終採用はgenerated |
| 音声context | autoは従来の非発声・同方針・同参照の映像継続だけ22。明示cutは0、明示continueは映像と独立して22。作品先頭は0 |
| trim | raw先頭の映像context区間を除去し、標準Trimのmatch_tailを有効にする |

音声context値は各Plan shots要素へ必ず明記する。省略時のPlan既定値0は「映像contextに追従」し得るため、省略と明示0を混同しない。音声参照自体はcontextや最終音声のコピーではない。

明示的な音声cut／continueには追加契約`VCD_CL_AUDIO_GUIDE_V1`を用いる。映像cut・音声continueでは、直前22frame相当の音声Guideを新Sceneの開始時刻までに配置する。映像prefix・追加trim・event offsetは0。映像continue・音声cutでは従来の映像prefix22だけを除去する。追加のruntime source hashを[Contract JSON](contracts/context-loop-timing.json)の`independent_audio`へ分離し、旧revisionのsource fingerprintを維持する。音響許可を狭める境界の扱いは[音声接続](audio-control.md)を参照。

## 2. rawと実現尺

```text
N = ceil(LLM計画尺ms * 24 / 1000)
C = cutなら0、continueなら22
R = N+C以上で最小の17k+5
delivered_frames = R-C
```

各区間のdelivered_framesを累積して実現タイムラインを作る。0〜16frameはNに対する格子差であり、ms尺からの切上げ差を含まない。要求作品尺は目標値とし、最終尺はContext Loop契約の結果を優先して差分表示する。

参照音声の長さと実現尺を一致させない。短い参照をループ・コピーして最終トラックを埋める処理は行わない。V1の作品尺指定は時刻だけとし、参照音声尺からの自動取得は外す。

## 3. event時刻の変換

シーンEMDのeventはScene相対ms。共通境界関数を使う。

```text
Q(t_ms) = floor(t_ms * 24 / 1000 + 0.5)
raw_start_frame = C + Q(event.start_ms)
raw_end_frame   = C + Q(event.end_ms)
output_start_frame = segment.start_frame + Q(event.start_ms)
output_end_frame   = segment.start_frame + Q(event.end_ms)
```

rawに含まれるcontext後から新規演技・発話を始める。同じ時刻は必ず同じ境界へ写す。frame化でeventが0frameになったら縮約せず診断する。H3向け時刻はraw frameをms表示へ変換してpromptに書き、同じeventの出力時間もexecutionへ保存する。

格子で増えた末尾はSceneの終端状態を維持し、台詞や単発音を反復しない。これはH3への指示であり、生成結果で検証する。

## 4. 音声sample

素材音声は参照conditioningとしてロードする。最終PCMはH3 audio VAEと標準decode / Trim / Segment Saveが生成する。再生用元素材のsource範囲、copy、mixの操作は設けない。

出力sample境界は累積frame位置から`floor(frame * output_sample_rate / 24 + 0.5)`で算出する。output_sample_rateは実audio VAE / decodeから取得し、contractへ記録する。LLMがsample数を生成しない。生成音声と映像の最終sample数はmatch_tailと累積境界で整合させる。

追加契約で確認したH3 audio VAEのnative rateは32000Hz。新しく明示音声接続を使うrevisionはこの値を記録する。従来revisionの48000Hzという宣言値は再現読込時に保持し、既存hashを変更しない。実生成のsample数・rateはSegmentの出力を正本として検証する。

## 5. 超過・分割

15秒超過、時刻逆転、欠落・重複、包含違反は計画コアへ返す。固定内容を保ってGemma4がScene分割と後続時刻を再生成する。修正要求は[段階別protocol](llm-protocol.md)の上限内とする。

V1のCompilerは単なるsplit_pointsだけを根拠に新Sceneを創作しない。Pythonによる機械分割は、時刻・対象event・必須出来事・境界状態がすべて明記された分割計画を受け取れる将来拡張とする。現在は安全な分割案もLLMがシーンEMDへ確定する。

シーンEMDのShotは一H3生成内のシーケンス変化であり、必ずハードカットにはならない。明確なカットは別Sceneとcut指定で表す。Context LoopでPlan全体を順次生成する。
