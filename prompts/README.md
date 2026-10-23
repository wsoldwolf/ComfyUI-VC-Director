# Promptの正本

`story_system_prompt.txt`はEnhancer / Outline / Detail / Retime共通のVC計画promptです。`core/protocols/llm.py`がstage、record名、到達可能なJSON Schemaを結合します。意味・台詞・時刻の生成責務は変更しません。

MV-Directorからの翻訳・Vision promptもこのディレクトリへ統合しています。`core/compiler/whole_field.py`と`core/vision/image_to_subject.py`が参照します。内容と移植元の記録は[THIRD_PARTY.md](../THIRD_PARTY.md)と`docs/implementation/mv-director-sources.json`に保持し、同じpromptを二重管理しません。
