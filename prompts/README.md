# Promptの正本

新規DETAILでは各Shotの明示的なCAMERAを生成します。固定・移動とも作者の指定を優先し、時間被覆をPlannerが検証します。生成Schemaは先頭CAMERAと後続eventを制約し、失敗は既存の有限回復へ返します。カメラを追加するための専用LLM呼出しはありません。

`story_system_prompt.txt`はVC計画promptの共通部分です。`story_enhance_prompt.txt`、`story_outline_prompt.txt`、`story_detail_prompt.txt`、`story_retime_prompt.txt`が段階別の指示を持ち、他段階の出力を混ぜないようにします。`core/protocols/llm.py`がstage、record名、段階別指示、到達可能なJSON Schemaを結合します。意味・演出・時刻の生成責務はLLMに保持し、作者の台詞と承認済みoutlineを生成時のSchemaでも固定します。

MV-Directorからの翻訳・Vision promptもこのディレクトリへ統合しています。`core/compiler/whole_field.py`と`core/vision/image_to_subject.py`が参照します。内容と移植元の記録は[THIRD_PARTY.md](../THIRD_PARTY.md)と`docs/implementation/mv-director-sources.json`に保持し、同じpromptを二重管理しません。
