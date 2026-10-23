# 更新履歴

## 0.1.0（開発中）

- シナリオ／Scene EMD、Gemma4計画コア、音響・発話制御、Planコンパイル、revision保存、9ノードと単一WFの初期実装。
- MV-Directorに合わせ、`core/`、`nodes/node_*/`、`prompts/`、`profiles/`、`assets/`、`workflows/`、`web/js/`へ再配置。
- ノードIDを維持。CLI入口を`tools/cli.py`へ集約し、再利用コードを`core/`へ統合。トップの`vc_director/`・`vendor/`を廃止し、移植元とライセンスの記録を保持。
