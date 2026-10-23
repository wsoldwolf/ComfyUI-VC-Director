# 設計図

| 図 | 形式 |
|---|---|
| VC-Director 全体ブロック図（仕様 Draft 0.3） | [SVG](vc-director-overview.svg) / [PNG](vc-director-overview.png) |

言語生成、保存ファイル境界、動画生成を一つのワークフローとして示す。PNGは2200×1530px、SVGはテキストを編集できるベクター形式。

生成元は`tools/generate_architecture_diagram.mjs`。リポジトリルートから次を実行する。

```sh
node tools/generate_architecture_diagram.mjs
```

Node.jsとsharpを使用する。sharpがローカルで解決できない場合は、sharpを含むnode_modulesディレクトリを環境変数`VCD_NODE_MODULES`へ指定する。日本語の表示・PNG生成にはMeiryo、Yu Gothic、Noto Sans CJK JPのいずれかのフォントを使用する。
