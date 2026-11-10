"""Validate authored input and referenced files before any model is constructed."""
import json
from pathlib import Path

from ..errors import VCError
from ..artifacts.schema import validate
from ..artifacts.storage import verify_assets
from ..artifacts.validation import validate_story
from ..artifacts.props import tag_ids
from ..artifacts.visible_text import outside_visible_text
from .diagnostics import SourceIndex, japanese

NAMESPACES = {"source": "展開・固定条件・台詞・必須出来事", "entity": "人物・小物",
              "timeline": "Scene・Shot・イベント"}


def read_image_facts(text):
    try:
        facts = json.loads(text)
    except json.JSONDecodeError as error:
        line = text.split("\n")[error.lineno - 1].rstrip("\r")
        raise VCError("IMAGE_FACT", "image_facts_json",
                      f"画像分析結果のJSONが不正です。{error.lineno}行・{error.colno}列を確認してください。\n  {error.lineno}行 | {line}") from error
    if not isinstance(facts, list):
        raise VCError("IMAGE_FACT", "image_facts_json", "画像分析結果はJSON配列で指定してください。未指定の場合は[]です。")
    return facts


def preflight(document, text, registry=None, *, image_facts=(), vision=False):
    index = SourceIndex(text)
    issues = []

    def add(code, path, message, lines=None):
        lines = tuple(sorted(set(index.locate(path, document) if lines is None else lines)))
        issue = (code, path, message, lines)
        if issue not in issues:
            issues.append(issue)

    for namespace, declarations in index.declarations.items():
        for identifier, lines in declarations.items():
            if len(lines) > 1:
                add("DUPLICATE_ID", identifier,
                    f"ID「{identifier}」が重複しています（{NAMESPACES.get(namespace, namespace)}）。異なるIDに変更し、参照も修正してください。", lines)

    actors = {actor["id"] for actor in document["actors"]}
    props = {prop["id"] for prop in document.get("props", [])}
    backgrounds = {bg["id"] for bg in document["backgrounds"]}

    prose_fields = [("作品", document["direction"])]
    prose_fields += [(item["id"], [item["text"]]) for item in document["constraints"]]
    prose_fields += [(item["id"], [item["description"]])
                     for key in ("actors", "props", "backgrounds") for item in document.get(key, [])]
    for path, values in prose_fields:
        missing = tag_ids([outside_visible_text(value) for value in values]) - actors - props
        if missing:
            add("UNKNOWN_ENTITY", path, "未定義の人物・小物タグがあります: " + ", ".join("@" + value for value in sorted(missing))
                + "。人物・小物定義のIDを確認してください。")

    def unknown(values, defined, code, path, label, field=None):
        missing = sorted(set(values) - defined)
        if missing:
            add(code, path, f"{label}が未定義です: {', '.join('@' + value if label != '背景' else value for value in missing)}。定義セクションを追加するか、IDを修正してください。",
                index.locate(path + "/" + field, document) if field else None)

    for beat in document["beats"]:
        path = beat["id"]
        unknown(beat["actor_ids"], actors, "UNKNOWN_ACTOR", path, "人物", "actor_ids")
        unknown([beat["background_id"]], backgrounds, "UNKNOWN_BACKGROUND", path, "背景", "background_id")
        unknown(beat.get("prop_ids", []), props, "UNKNOWN_PROP", path, "小物", "prop_ids")
        for dialogue in beat["dialogues"]:
            unknown([dialogue["actor_id"]], actors, "UNKNOWN_ACTOR", dialogue["id"], "人物")
            if dialogue["actor_id"] in actors and dialogue["actor_id"] not in beat["actor_ids"]:
                add("DIALOGUE_PERMISSION", dialogue["id"], f"台詞の話者@{dialogue['actor_id']}が、この展開の出演に含まれていません。出演欄に追加してください。")
        # Speech and visible printed text are literal data, not entity references.
        tags = tag_ids([outside_visible_text(value) for value in [beat["text"], *beat.get("direction", [])]])
        for identifier in sorted(tags - actors - props):
            tag_lines = [n for block in index.blocks if block["start"] in index.ids[path]
                         for n in range(block["start"] + 1, block["end"] + 1)
                         if identifier in tag_ids([outside_visible_text(index.lines[n - 1])])
                         and not index.lines[n - 1].startswith("* 台詞 ")]
            add("UNKNOWN_ENTITY", path, f"タグ@{identifier}の人物・小物定義がありません。「# 人物 @{identifier}」または「# 小物 @{identifier}」を定義してください。", tag_lines)
        missing_props = (tags & props) - set(beat.get("prop_ids", []))
        if missing_props:
            add("UNKNOWN_PROP", path, "本文で使用する小物を、この展開の小物欄にも含めてください: " + ", ".join("@" + p for p in sorted(missing_props)))

    try:
        validate_story(document)
    except VCError as error:
        d = error.diagnostic
        # Detailed duplicate/reference findings above already identify every occurrence.
        if not any(code == d.code for code, _, _, _ in issues):
            add(d.code, d.path, japanese(d.code, d.message) + ("\n技術情報: " + d.message if japanese(d.code, d.message) != d.message else ""))

    manual = set()
    for position, fact in enumerate(image_facts):
        try:
            validate(fact, "image_fact")
        except VCError:
            add("IMAGE_FACT", f"image_facts_json/{position}", f"画像分析結果の{position + 1}件目の形式が不正です。entity_kind・entity_id・textを確認してください。", [])
            continue
        kind, identifier = fact["entity_kind"], fact["entity_id"]
        if identifier not in (actors if kind == "actor" else backgrounds):
            add("IMAGE_FACT", f"image_facts_json/{position}", f"画像分析結果のID「{identifier}」がEMDで定義されていません。", [])
        elif not fact["text"].strip():
            add("IMAGE_FACT_EMPTY", identifier, "手入力の画像分析結果が空です。特徴・情景を記述してください。")
        else:
            manual.add((kind, identifier))
    for kind, values in (("actor", document["actors"]), ("background", document["backgrounds"])):
        for entity in values:
            if not entity["description"].strip() and not (vision and entity.get("image_asset_id")) and (kind, entity["id"]) not in manual:
                add("VISION_DESCRIPTION", entity["id"], "特徴・情景が空です。記述を追加するか、Visionを有効にするか、image_facts_jsonで補完してください。")

    if registry is not None:
        assets = {}
        for asset in registry.get("assets", []):
            identifier = asset.get("id")
            if identifier in assets:
                add("DUPLICATE_ID", str(identifier), f"AssetRegistryの素材ID「{identifier}」が重複しています。")
            assets[identifier] = asset
        references = {}
        for identifier, kind, line in index.asset_refs:
            references.setdefault(identifier, []).append((kind, line))
        for identifier, uses in references.items():
            lines = [line for _, line in uses]
            if identifier not in assets:
                add("MISSING_ASSET", identifier, f"素材ID「{identifier}」がAssetRegistryに登録されていません。素材を登録するか、EMDのIDを修正してください。参照を使用しない場合は「なし」を指定します。", lines)
                continue
            asset = assets[identifier]
            file_path = asset.get("locator", "未指定")
            if isinstance(file_path, str) and not Path(file_path).is_absolute():
                file_path = str(Path(registry.get("root", ".")) / file_path)
            if any(asset.get("kind") != kind for kind, _ in uses):
                add("ASSET_KIND", identifier, f"素材ID「{identifier}」の種類が一致しません。登録の種類: {asset.get('kind')}、必要な種類: {', '.join(sorted({kind for kind, _ in uses}))}。", lines)
            try:
                verify_assets([asset], registry.get("root", "."))
            except VCError as error:
                add(error.diagnostic.code, identifier, japanese(error.diagnostic.code, error.diagnostic.message)
                    + f"\n素材ID: {identifier} / ファイル: {file_path}", lines)
            except OSError as error:
                add("ASSET_READ", identifier, f"参照素材を読み取れません。素材ID: {identifier} / ファイル: {file_path}\n{error}", lines)

    if issues:
        entries = []
        for code, path, message, lines in issues:
            excerpt = index.excerpt(lines) or "  EMDの行番号はありません（入力設定・AssetRegistryを確認してください）。"
            entries.append(f"[{code}] {path}: {message}\n{excerpt}")
        first = issues[0]
        error = VCError(first[0], first[1], f"入力の事前検査で{len(issues)}件の問題が見つかりました。Vision・LLMは実行していません。\n\n" + "\n\n".join(entries), first[3][0] if first[3] else None)
        error.emd_context = True
        error.issues = issues
        raise error
    return document
