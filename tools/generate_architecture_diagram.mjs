/** Generate the editable SVG and PNG architecture diagram from spec Draft 0.3.
 * Requires sharp, installed locally or resolved through VCD_NODE_MODULES.
 * Run: node tools/generate_architecture_diagram.mjs
 */
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
let sharp;
try {
  sharp = require('sharp');
} catch {
  if (!process.env.VCD_NODE_MODULES) {
    throw new Error('Install sharp or set VCD_NODE_MODULES to a node_modules directory containing sharp.');
  }
  sharp = require(path.join(process.env.VCD_NODE_MODULES, 'sharp'));
}

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.join(root, 'docs', 'assets');
const W = 2200;
const H = 1530;
const c = {
  ink: '#182b43', muted: '#53677e', border: '#cbd5e1', white: '#ffffff',
  purple: '#7552c8', purpleFill: '#f3effc', blue: '#2563a7', blueFill: '#edf5ff',
  teal: '#137d79', tealFill: '#eaf7f5', amber: '#926619', amberFill: '#fff8e7',
  red: '#b34e61', canvas: '#f7f9fc', line: '#60748c',
};
const parts = [];
const esc = (s) => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;').replaceAll('"', '&quot;');
function rect(x, y, w, h, fill, stroke = c.border, radius = 16, extra = '') {
  parts.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${radius}" fill="${fill}" stroke="${stroke}" stroke-width="1.6" ${extra}/>`);
}
function text(x, y, value, size = 20, fill = c.ink, weight = 400, extra = '') {
  parts.push(`<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" font-weight="${weight}" ${extra}>${esc(value)}</text>`);
}
function arrow(d, color = c.line, dashed = false, marker = 'arrow') {
  parts.push(`<path d="${d}" fill="none" stroke="${color}" stroke-width="2.7" stroke-linejoin="round" stroke-linecap="round" ${dashed ? 'stroke-dasharray="9 7"' : ''} marker-end="url(#${marker})"/>`);
}
function card(x, y, w, h, { title, tag, rows, accent = c.blue, fill = c.white, titleSize = 24 }) {
  rect(x, y, w, h, fill, c.border);
  parts.push(`<path d="M ${x + 16} ${y} H ${x + w - 16}" stroke="${accent}" stroke-width="4" stroke-linecap="round"/>`);
  text(x + 23, y + 34, tag, 15, accent, 700, 'letter-spacing="1.2"');
  text(x + 23, y + 73, title, titleSize, c.ink, 700);
  rows.forEach((row, i) => text(x + 23, y + 113 + 30 * i, row, 20, c.muted));
}

parts.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-labelledby="diagram-title diagram-description">`);
parts.push('<title id="diagram-title">ComfyUI-VC-Director 全体ブロック図 — Draft 0.3</title>');
parts.push('<desc id="diagram-description">同じワークフローで言語生成と動画生成を切り替える構成。共有素材とシナリオからGemma4が時刻付きシーンEMDを生成し、Pythonが発話保護、人物参照、時間契約を処理してJSONを保存する。動画モードではJSONを読み、VC Scene Ref2VAとContext Loopで映像および音声を生成する。</desc>');
parts.push(`<defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${c.line}"/></marker>
  <marker id="teal-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${c.teal}"/></marker>
  <marker id="red-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${c.red}"/></marker>
</defs>`);
parts.push('<g font-family="Meiryo, Yu Gothic, Noto Sans CJK JP, sans-serif">');
rect(0, 0, W, H, c.canvas, 'none', 0);
text(64, 68, 'ComfyUI-VC-Director', 38, c.ink, 700);
text(64, 108, 'シナリオから長尺動画へ — 単一ワークフローの全体構成', 25, c.muted);
text(2136, 66, 'SPEC DRAFT 0.3', 18, c.blue, 700, 'text-anchor="end" letter-spacing="1.5"');
text(2136, 101, '2026-10-24 · 初期実装の設計図', 18, c.muted, 400, 'text-anchor="end"');

// Shared inputs, independent of the muted language group.
card(96, 166, 530, 160, {
  tag: 'SHARED INPUTS', title: '共有素材・レジストリ', accent: c.teal, fill: c.tealFill,
  rows: ['画像 ／ 参照音声 ／ 参照動画（任意）', '人物・背景・声質を素材IDで管理'],
});
card(674, 166, 740, 160, {
  tag: 'AUTHOR INPUT', title: 'シナリオEMD', accent: c.amber, fill: c.amberFill,
  rows: ['粗い物語・背景・出演者 @hero・台詞・固定条件', '背景は参照画像あり／情景テキストのみを選択'],
});
card(1462, 166, 650, 160, {
  tag: 'PYTHON CONTRACT', title: 'Context Loop時間契約', accent: c.blue, fill: c.blueFill,
  rows: ['24 fps ／ raw 17k+5 ／ Guide context 22 frame', 'raw・採用区間・event時刻・音声contextを統一'],
});

// Group frames.
rect(64, 398, 2080, 398, c.white, '#d9ceee', 20);
text(96, 441, '01  言語生成・コンパイル', 27, c.purple, 700);
text(96, 765, '言語グループ ON ／ 動画グループ Mute', 20, c.purple);
rect(64, 1060, 2080, 326, c.white, '#beded9', 20);
text(96, 1103, '02  動画生成', 27, c.teal, 700);
text(2112, 1103, '言語グループ Mute ／ 動画グループ ON', 20, c.teal, 400, 'text-anchor="end"');

// Input routes and shared-reference bypass route.
arrow('M 256 326 V 348 H 76 V 535 H 96');
arrow('M 1044 326 V 365 H 629 V 476');
arrow('M 1975 326 V 476');
arrow('M 626 249 H 650 V 372 H 1860 V 476', c.teal, true, 'teal-arrow');
arrow('M 96 246 H 40 V 1350 H 629 V 1320', c.teal, true, 'teal-arrow');

const Y = 476;
const BH = 230;
card(96, Y, 320, BH, {
  tag: 'VISION · REUSED', title: 'Image to Subject EMD', titleSize: 21, accent: c.purple, fill: c.purpleFill,
  rows: ['画像 → 人物／背景断片', '作者の特徴を優先保持', '元画像・素材IDを維持'],
});
card(464, Y, 330, BH, {
  tag: 'GEMMA4 · CORE', title: 'Story Enhancer', accent: c.purple, fill: c.purpleFill,
  rows: ['意図・必須出来事の整理', '背景・出演者・制約を固定', '観察断片を統合'],
});
card(842, Y, 400, BH, {
  tag: 'GEMMA4 · CORE', title: 'Story Timeline Planner', titleSize: 23, accent: c.purple, fill: c.purpleFill,
  rows: ['構成 → 詳細化 → 具体的時刻', '演技・撮影・許可音響を展開', '各Sceneは15秒以内を目標'],
});
card(1290, Y, 330, BH, {
  tag: 'EDITABLE INTERMEDIATE', title: 'シーンEMD', accent: c.amber, fill: c.amberFill,
  rows: ['時刻付きH3シーケンス', '状態・出典・依存も保存', '確認・編集・固定が可能'],
});
card(1668, Y, 444, BH, {
  tag: 'PYTHON · COMPILE', title: 'EMDコンパイラ', accent: c.blue, fill: c.blueFill,
  rows: ['発話退避 → 描写だけ英訳 → 結合', '@hero → <Subject N>・素材解決', '時間契約・音響指示・参照schedule'],
});
arrow('M 416 582 H 464');
arrow('M 794 582 H 842');
arrow('M 1242 582 H 1290');
arrow('M 1620 582 H 1668');
arrow('M 1668 681 H 1644 V 746 H 1042 V 706', c.red, true, 'red-arrow');
text(1370, 777, '構造・時刻エラーは対象段階だけを有限回修正', 18, c.red, 400, 'text-anchor="middle"');
arrow('M 1890 706 V 842');

// Persistent files are the only language/video handoff.
rect(96, 842, 2016, 150, c.amberFill, '#dfc995', 18);
text(121, 879, '保存ファイル境界', 22, c.amber, 700);
text(121, 915, 'scenario.emd / scenes.emd', 23, c.ink, 700);
text(121, 952, '作者入力と編集可能な中間形式', 19, c.muted);
parts.push('<path d="M 692 864 V 972 M 1434 864 V 972" stroke="#dfc995" stroke-width="1.5"/>');
text(728, 879, 'VIDEO INPUT', 16, c.amber, 700, 'letter-spacing="1"');
text(728, 915, 'plan.json  +  execution.json', 25, c.ink, 700);
text(728, 952, 'H3 prompt・時刻対応・人物／参照番号・音響制御', 19, c.muted);
text(1468, 879, 'RUN METADATA', 16, c.amber, 700, 'letter-spacing="1"');
text(1468, 915, 'run_metadata.json', 24, c.ink, 700);
text(1468, 952, 'hash・モデル・Contract・翻訳trace', 19, c.muted);
arrow('M 1050 992 V 1010 H 76 V 1205 H 96');
text(700, 1040, '動画生成は保存済みJSONを読込。LLMへのlive接続は不要。', 21, c.muted);

card(96, 1140, 320, 180, {
  tag: 'LOAD & VALIDATE', title: 'JSON読込・照合', accent: c.teal, fill: c.tealFill,
  rows: ['素材・hash・Contract確認', '人物・native番号を照合'],
});
card(464, 1140, 330, 180, {
  tag: 'REFERENCE CONDITIONING', title: 'VC Scene Ref2VA', titleSize: 23, accent: c.teal, fill: c.tealFill,
  rows: ['Sceneごとの素材を選択', '発話の再置換を防止'],
});
card(842, 1140, 400, 180, {
  tag: 'H3 · CONTEXT LOOP', title: '全Sceneを順次生成', accent: c.teal, fill: c.tealFill,
  rows: ['映像 ＋ 音声をH3が生成', 'cut／continue・生成context'],
});
card(1290, 1140, 330, 180, {
  tag: 'STANDARD PIPELINE', title: 'Trim・保存・連結', accent: c.teal, fill: c.tealFill,
  rows: ['decode → Segment Save', 'match_tail → Assemble'],
});
card(1668, 1140, 444, 180, {
  tag: 'OUTPUT', title: '長尺動画 ＋ H3生成音声', accent: c.teal, fill: c.tealFill,
  rows: ['generated_audioを採用', '生成Run・checkpointを保存'],
});
arrow('M 416 1230 H 464');
arrow('M 794 1230 H 842');
arrow('M 1242 1230 H 1290');
arrow('M 1620 1230 H 1668');
text(100, 1344, '共有素材の実データは動画側でも利用', 18, c.teal);

// Operational invariants and legend.
text(96, 1433, '切替', 20, c.ink, 700);
text(166, 1433, '言語ON → JSON保存  ／  動画ON → 保存JSONで生成  ／  両方Mute → 何もしない', 21, c.ink);
text(96, 1474, '入力音声は参照専用。発声／無音は明示指示し、音声コピー・後付けmixは行わない。', 21, c.muted);
parts.push(`<path d="M 1710 1430 H 1765" stroke="${c.line}" stroke-width="2.7" marker-end="url(#arrow)"/>`);
text(1780, 1437, '処理・成果物', 18, c.muted);
parts.push(`<path d="M 1950 1430 H 2005" stroke="${c.teal}" stroke-width="2.7" stroke-dasharray="9 7" marker-end="url(#teal-arrow)"/>`);
text(2020, 1437, '共有素材', 18, c.muted);
parts.push('</g></svg>');

const svg = parts.join('\n') + '\n';
await mkdir(out, { recursive: true });
await writeFile(path.join(out, 'vc-director-overview.svg'), svg, 'utf8');
await sharp(Buffer.from(svg), { density: 144 }).resize(W, H).png().toFile(path.join(out, 'vc-director-overview.png'));
process.stdout.write(`Generated docs/assets/vc-director-overview.svg and .png (${W} x ${H}).\n`);
