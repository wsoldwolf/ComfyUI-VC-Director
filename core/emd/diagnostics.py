"""Japanese user-facing diagnostics against the actual input, not source-table spans."""
import json
import re
from collections import defaultdict

from ..errors import VCError
from ..utilities import normalize

MESSAGES = {
    "JSON": "LLM応答のJSON構文が不正です。入力EMDの構文エラーではありません。",
    "JSON_INCOMPLETE": "LLMのJSON応答が途中で終了した可能性があります。出力トークン上限と生成終了理由を確認してください。",
    "ENHANCED_EVENT_SOURCE": "補強した出来事の展開ID・文書種別・出典行が、対象の展開と一致していません。",
    "GENERATION_TRUNCATED": "出力トークン上限に達し、LLMのJSON応答が途中で終了しました。",
    "EMD_HEADER": "見出しの書式または配置が正しくありません。先頭は「# 形式」、人物定義は「# 人物 @ID」です。",
    "EMD_VERSION": "schemaにはVCD_SCENARIO_EMD_V1またはVCD_SCENE_EMD_V1を指定してください。",
    "EMD_FIELD": "項目を「* 項目名: 内容」の書式で記述してください。",
    "UNKNOWN_FIELD": "使用できない項目名です。項目名と記述するセクションを確認してください。",
    "DUPLICATE_FIELD": "同じ項目が複数回記述されています。台詞はそれぞれ異なるIDを指定してください。",
    "DUPLICATE_ID": "IDが重複しています。それぞれ異なるIDを指定し、参照先も合わせて修正してください。",
    "DUPLICATE": "同じセクションが重複しています。",
    "MISSING_FIELD": "必須項目がありません。対象セクションに追加してください。",
    "EMD_REQUIRED": "作品・展開など、必要なセクションが不足しています。",
    "EMD_ORDER": "セクションの順序が正しくありません。作品・人物・背景の定義を展開やSceneより前に記述してください。",
    "ID": "IDの書式が正しくありません。英小文字で始まる英小文字・数字・アンダースコアを使用し、人物・小物には@を付けてください。",
    "TIME": "時刻はMM:SS.mmmの書式で指定してください。",
    "SCENE_HEADER": "Sceneの見出しを「# Scene ID 開始時刻 --> 終了時刻」の書式で指定してください。",
    "SCENE_SECTION": "Sceneに必要なセクションが不足しているか、順序が違っています。",
    "SHOT": "Shotの見出しまたはイベントの配置が正しくありません。",
    "EVENT": "イベントの種類・ID・時刻・対象人物・本文の書式を確認してください。",
    "SOUND_EVENT": "効果音・環境音イベントのID・時刻・参照先・本文の書式を確認してください。",
    "SOUND_TRIGGER": "音の参照先が同じSceneのShot内に存在しないか、音の開始時刻が参照先の区間外です。",
    "SOUND_TRIGGER_REPAIR": "音の参照修正で固定ID・件数・ローカル参照先・時刻の制約に違反しました。",
    "SOUND_TIME": "音の区間はSceneの先頭を0とする相対時刻で、Scene全体の時間内に収めてください。",
    "SUMMARY": "summaryには要約を一つの箇条書きで記述してください。",
    "DIALOGUE": "台詞は「* 台詞 ID: @人物ID | 「台詞」」の書式で記述してください。空の台詞や入れ子の括弧は使用できません。",
    "DIALOGUE_PERMISSION": "台詞の話者を展開の出演に含め、発声設定で台詞を許可してください。",
    "DIALOGUE_LANGUAGE": "台詞の言語指定と記述内容が一致していません。",
    "UNKNOWN_ACTOR": "人物が未定義、または使用できる出演人物に含まれていません。「# 人物 @ID」と出演欄を確認してください。",
    "UNKNOWN_ENTITY": "未定義の人物・小物タグがあります。定義と、この展開で使用する小物を確認してください。",
    "UNKNOWN_PROP": "小物IDが定義されていません。「# 小物 @ID」を確認してください。",
    "UNKNOWN_BACKGROUND": "背景IDが定義されていません。「# 背景 ID」を確認してください。",
    "MISSING_ASSET": "参照素材が未登録、またはファイルが存在しません。AssetRegistryとファイルの場所を確認してください。",
    "ASSET_KIND": "参照素材の種類が項目と一致しません。画像・動画・音声を確認してください。",
    "ASSET_HASH": "参照素材が登録後に変更されています。AssetRegistryを再実行して登録し直してください。",
    "ASSET_READ": "参照素材を読み取れません。ファイルの権限や利用状態を確認してください。",
    "SCHEMA": "項目の値または書式が規定と一致しません。",
    "STRAY_DIALOGUE": "本文・映像方針に台詞の括弧や<d>タグがあります。発話は台詞欄に記述し、画面上の文字は<visible>文字</visible>で指定してください。",
    "VISIBLE_TEXT": "画面上の文字は、改行や入れ子のない<visible>文字</visible>で指定してください。",
    "AUDIO_POLICY": "音響設定の値が正しくありません。発声・BGM・環境音・効果音の指定を確認してください。",
    "AUDIO_CONFLICT": "「音響: 無音」と音を有効にする設定が競合しています。",
    "AUDIO_REFERENCE": "音声参照にはmusic / ambience / effectsを指定してください。",
    "VOCAL_PATTERN": "発声パターンIDが未定義、重複、または書式違反です。",
    "NONVERBAL_OVERLAP": "同じ人物の台詞と非言語発声、または非言語発声同士の時刻が重複しています。",
    "NONVERBAL_TIMING_PROTOCOL": "発声時刻の部分修正で固定ID・件数・時間枠の制約に違反しました。",
    "VISUAL_AUDIO_ROLE": "映像方針に声や発話の役割が混入しています。画面内人物と台詞を使って、映像と発声を分けて指定してください。",
    "VOCAL_CUE": "映像の本文に発声を誘導する記述があります。発話は台詞、非言語発声は専用設定へ分けてください。",
    "AMBIGUOUS_ACTOR_REFERENCE": "人物を指す代名詞が曖昧です。正しい@人物IDで指定してください。",
    "TRANSLATION_ACTOR_NAME": "英訳に人物名または裸の人物IDが混入しています。元の保護スロットで人物を保持してください。",
    "DIALOGUE_COVERAGE": "指定された台詞が欠落または重複しています。台詞ID・話者・本文を維持して一度ずつ配置してください。",
    "DIALOGUE_REPAIR": "重複した台詞の配置選択に失敗しました。作者の台詞ID・本文・話者を保持した有効な配置が必要です。",
    "SHOT_COVERAGE": "Shotの時間に重複や空白があります。Scene全体を連続して覆うように配置してください。",
    "SHOT_TIMING_REPAIR": "Shotと映像イベントの時間配分の部分修正に失敗しました。固定ID・件数・音声時刻と具体的な失敗理由を確認してください。",
    "SHOT_TIMING_UNSATISFIABLE": "固定音声と既存Shotを保持した時間配分を確定できません。Shot・音声の区間とSceneの尺を確認してください。",
    "SCENE_COVERAGE": "Sceneの時間に重複や空白があります。先頭の0秒から連続して配置してください。",
    "OUTLINE_TIMING": "Sceneの時間配置が不正です。連続する15秒以内のSceneに分割してください。",
    "OUTLINE_DURATION": "Scene構成の合計尺が指定尺と一致していません。",
    "OUTLINE_DEPENDENCY": "生成SceneのIDが重複しているか、依存先が自己・未定義・後続のSceneを参照しています。先行SceneのIDだけを指定してください。",
    "OUTLINE_DEPENDENCY_REPAIR": "Sceneの依存関係の部分修正に失敗しました。先行Sceneの候補と具体的な失敗理由を確認してください。",
    "OUTLINE_REQUIRED": "必須イベントのScene割り当てに欠落・重複・未定義IDがあります。各IDを一度ずつ配置してください。",
    "OUTLINE_TRANSITION": "継続Sceneの依存先・出演人物・背景が直前のSceneと一致していません。",
    "STATE_CONTINUITY": "継続Sceneの開始状態が不足しているか、直前Sceneの終了状態と一致していません。",
    "SUBJECT_COVERAGE": "人物定義または保持設定に、出演人物の不足・重複があります。",
    "IMAGE_FACT": "画像分析結果の形式または人物・背景IDを確認してください。",
    "IMAGE_FACT_EMPTY": "画像分析結果の特徴・情景が空です。",
    "VISION_DESCRIPTION": "人物の特徴または背景の情景を記述するか、Vision・image_factsで補完してください。",
}


def japanese(code, message):
    # Keep already-authored Japanese explanations, never send UI translations to LLM repairs.
    if re.match(r"[ぁ-んァ-ヶ一-龥]", message):
        return message
    return MESSAGES.get(code.upper(), "記述または設定に問題があります。対象IDと該当箇所を確認してください。")


class SourceIndex:
    def __init__(self, text):
        self.lines = normalize(text).split("\n")
        self.ids, self.fields, self.values, self.blocks = defaultdict(list), defaultdict(list), defaultdict(list), []
        self.declarations = defaultdict(lambda: defaultdict(list))
        self.asset_refs = []
        current = None
        for number, text in enumerate(self.lines, 1):
            heading = re.fullmatch(r"(#{1,3}) (.+)", text)
            if heading:
                if current is not None:
                    current["end"] = number - 1
                current = dict(title=heading[2], start=number, end=len(self.lines), fields=defaultdict(list))
                self.blocks.append(current)
                match = re.match(r"(人物 @|小物 @|背景 |展開 |展開定義 |固定条件 |発声パターン |Scene |Shot )([a-z][a-z0-9_]*)\b", heading[2])
                if match:
                    kind, identifier = match.groups()
                    namespace = ("entity" if kind in {"人物 @", "小物 @"} else
                                 "source" if kind in {"展開 ", "展開定義 ", "固定条件 "} else
                                 "timeline" if kind in {"Scene ", "Shot "} else kind.strip())
                    self.declarations[namespace][identifier].append(number)
                    self.ids[identifier].append(number)
            field = re.fullmatch(r"\* ([^:]+): (.*)", text)
            if field:
                key, value = field.groups()
                self.fields[key].append(number)
                self.values[value].append(number)
                if current is not None:
                    current["fields"][key].append(number)
                declared = re.fullmatch(r"(?:台詞|必須出来事) ([a-z][a-z0-9_]*)", key)
                if declared:
                    self.declarations["source"][declared[1]].append(number)
                    self.ids[declared[1]].append(number)
                kind = {"画像": "image", "動画": "video", "声質音声": "audio"}.get(key)
                if key.startswith("音声参照 "):
                    kind = "audio"
                if kind:
                    self.asset_refs.extend((identifier.strip(), kind, number) for identifier in value.split(",")
                                           if identifier.strip() and identifier.strip() != "なし")
            event = re.match(r"\* (?:ACTION|CAMERA|DIALOGUE|NONVERBAL|AMBIENCE|EFFECT) ([a-z][a-z0-9_]*) ", text)
            if event:
                self.declarations["timeline"][event[1]].append(number)
                self.ids[event[1]].append(number)

    def locate(self, path, document=None):
        parts = path.strip("/").split("/")
        labels = {"actors": "人物 @", "props": "小物 @", "backgrounds": "背景 ", "beats": "展開", "scenes": "Scene "}
        if parts[0] in labels and len(parts) > 1 and parts[1].isdigit():
            values = document.get(parts[0], []) if document is not None else []
            if int(parts[1]) < len(values):
                parts[0] = values[int(parts[1])]["id"]
            else:
                matching = [block for block in self.blocks if block["title"].startswith(labels[parts[0]])]
                if int(parts[1]) < len(matching):
                    return [matching[int(parts[1])]["start"]]
        if parts[0] in self.ids:
            starts = self.ids[parts[0]]
            field = {"description": "特徴", "image_asset_id": "画像", "video_asset_id": "動画", "voice_asset_id": "声質音声",
                     "actor_ids": "出演", "background_id": "背景", "prop_ids": "小物", "text": "原文"}.get(parts[-1])
            selected = [n for b in self.blocks if b["start"] in starts for n in b["fields"].get(field, [])] if field else []
            return selected or starts
        for block in self.blocks:
            if block["title"] == parts[0]:
                return block["fields"].get(parts[-1], []) or [block["start"]]
        if path in self.fields:
            return self.fields[path]
        return []

    def excerpt(self, numbers):
        return "\n".join(f"  {n}行 | {self.lines[n - 1]}" for n in sorted(set(numbers)) if 1 <= n <= len(self.lines))


def translation_failure_reason(reason):
    """Retain the concrete validator failure when wrapping a non-JSON error."""
    details = re.sub(r"^whole-field translation invalid after [0-9]+ attempts: ", "", reason)
    pronoun = re.search(r"Unbound person pronoun ['\"]([^'\"]+)['\"]", details)
    counts = re.findall(r"(__MVD_KEEP_[0-9]+__): expected=([0-9]+), got=([0-9]+)", details)
    if pronoun:
        message = f"代名詞「{pronoun[1]}」の参照先人物を特定できません。人物・小物の区分と、元の人物参照を確認してください。"
    elif counts:
        message = "保護マーカーの個数が一致しません。" + " / ".join(f"{marker}: 必要数{expected}、出力数{actual}" for marker, expected, actual in counts)
    elif "occurrence-slot order mismatch" in details:
        message = "保護スロットの読み順が重複または欠落しています。positionには1からスロット数までを一度ずつ指定する必要があります。"
    elif "occurrence-slot coverage mismatch" in details or "occurrence-slot duplicate key" in details:
        message = "保護スロットの追加・重複・欠落、またはIDの変更を検出しました。元の出現ごとに一度ずつ保持する必要があります。"
    elif "occurrence-slot text contains" in details or "unprotected reference or marker" in details:
        message = "通常の英訳テキストに、保護マーカー・未保護の参照・使用できない制御文字が混入しています。"
    elif "protocol mismatch" in details:
        message = "翻訳応答の書式が規定と一致しません。レコード形式・JSON・行数を確認してください。"
    elif "TRANSLATION_ACTOR_NAME " in details:
        message = "英訳に人物名または裸の人物IDが混入しました。下記の検出語・人物ID・英訳内の位置を確認してください。"
    else:
        message = "翻訳結果が整合性の検証を通過しませんでした。"
    return message + "\n技術情報: " + details


def contextual_error(error, text, document=None):
    if getattr(error, "emd_context", False):
        return error
    diagnostic = error.diagnostic
    index = SourceIndex(text)
    lines = [diagnostic.line] if diagnostic.line else index.locate(diagnostic.path, document)
    if diagnostic.code in {"ID", "TIME"} and not diagnostic.line:
        lines = index.values.get(diagnostic.path, []) or lines
    if diagnostic.code == "EMD_HEADER" and not lines:
        lines = [next((n for n, line in enumerate(index.lines, 1) if line.strip()), 1)]
    if diagnostic.code == "DUPLICATE_FIELD" and diagnostic.line:
        lines = next((block["fields"].get(diagnostic.path, lines) for block in index.blocks
                      if block["start"] <= diagnostic.line <= block["end"]), lines)
    if not lines and diagnostic.path.isdigit():
        lines = [int(diagnostic.path)]
    message = japanese(diagnostic.code, diagnostic.message)
    if diagnostic.code in {"LLM_RETRIES_EXHAUSTED", "TRANSLATION_RETRIES_EXHAUSTED"}:
        message = "言語生成の修正回数を超えたため停止しました。"
        try:
            errors = json.loads(diagnostic.message.split("; 応答ログ:", 1)[0])
        except (ValueError, TypeError):
            errors = []
        if isinstance(errors, list) and errors:
            message += "\n" + "\n".join(f"[{item['code']}] {item.get('path', '')}: {japanese(item['code'], item.get('message', ''))}" for item in errors)
        else:
            reason = diagnostic.message.split("; 応答ログ:", 1)[0]
            message += "\n" + translation_failure_reason(reason)
        if "; 応答ログ:" in diagnostic.message:
            message += "\n応答ログ:" + diagnostic.message.split("; 応答ログ:", 1)[1]
    if message != diagnostic.message and diagnostic.code not in {"LLM_RETRIES_EXHAUSTED", "TRANSLATION_RETRIES_EXHAUSTED"}:
        message += f"\n技術情報: {diagnostic.message}"
    excerpt = index.excerpt(lines)
    message += "\n" + (excerpt if excerpt else "入力EMDの該当行は特定できません（生成後の内容・設定も確認してください）。")
    result = VCError(diagnostic.code, diagnostic.path, message, lines[0] if lines else None)
    result.emd_context = True
    return result


def parse_checked(text):
    from .parser import parse
    try:
        return parse(text)
    except VCError as error:
        raise contextual_error(error, text) from error
