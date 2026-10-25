# 実装資料

| 文書 | 内容 |
|---|---|
| [ディレクトリと依存方向](directory-layout.md) | MV-Directorから引き継いだ構成、各責務、CLIとの対応 |
| [実装・検証状況](status.md) | 初期実装、CPU・静的検証、実生成の残項目 |
| [カメラ計画の実装計画](camera-planning-plan.md) | CAMERAの生成必須化、互換性、条件を揃えた実生成検証（実装済み） |
| [カメラ計画の検証記録](camera-planning-validation.md) | 実Gemma4の固定／追従、H3の16:9比較とモデル遵守の限界 |
| [MV移植元の記録](mv-director-sources.json) | 元ファイルと統合先のhash、使用・除外の対応 |
| [動画WF移植の記録](mv-video-workflow-sources.json) | Hybrid・高速化・モデル設定の移植元とsnapshot hash |

規範仕様は[docs/spec](../spec/README.md)、操作は[ノード一覧](../nodes/README.md)と[WF手順](../../workflows/README.md)を参照してください。
