"""Observe scenario-bound registry images and build enhancer facts."""
import copy
from ..artifacts.assets import path_for
from ..artifacts.schema import validate
from ..artifacts.storage import verify_assets
from ..errors import require


def apply_image_facts(scenario, facts=()):
    """Materialize observations in a derived scenario, preserving authored source."""
    result = copy.deepcopy(scenario)
    entities = {(kind, entity["id"]): entity
                for kind, key in (("actor", "actors"), ("background", "backgrounds"))
                for entity in result[key]}
    grouped = {}
    for fact in facts:
        validate(fact, "image_fact")
        key = fact["entity_kind"], fact["entity_id"]
        require(key in entities, "IMAGE_FACT", fact["entity_id"], "Unknown entity")
        text = fact["text"].strip()
        require(bool(text), "IMAGE_FACT_EMPTY", fact["entity_id"], "Vision returned no visible facts")
        grouped.setdefault(key, [])
        if text not in grouped[key]:
            grouped[key].append(text)
    for key, entity in entities.items():
        authored = entity["description"]
        if not authored.strip():
            authored = ""
        observations = [text for text in grouped.get(key, []) if text not in authored]
        # Keep explicit author descriptions first; observations supplement them.
        entity["description"] = "\n".join(([authored] if authored else []) + observations)
        field = "特徴" if key[0] == "actor" else "情景"
        require(bool(entity["description"]), "VISION_DESCRIPTION", key[1] + "/" + field,
                f"{field}を補完する画像観察がありません。Buildのvision_model_nameを有効にするか、image_facts_jsonまたはEMDの{field}を記述してください。")
    return result


def apply_scene_image_facts(scene, scenario, facts=()):
    """Keep observed appearance in each active Subject, beyond LLM summaries."""
    observed = {f["entity_id"] for f in facts if f["entity_kind"] == "actor"}
    actors = {a["id"]: a for a in scenario["actors"]}
    result = copy.deepcopy(scene)
    for description in result["subject_descriptions"]:
        actor_id = description["actor_id"]
        if actor_id in observed:
            appearance, local = actors[actor_id]["description"], description["text"]
            if appearance not in local:
                description["text"] = appearance if local in appearance else appearance + "\n" + local
    return result


def load_image(path):
    import numpy as np
    import torch
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        pixels = np.array(ImageOps.exif_transpose(source).convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(pixels).unsqueeze(0)


def scenario_image_facts(scenario, registry, pair, cache_root, manual=(), *, observer=None, image_loader=None, config=None):
    from .image_to_subject import image_to_entity
    observer = observer or image_to_entity
    image_loader = image_loader or load_image
    assets = {asset["id"]: asset for asset in registry["assets"]}
    entities = [(kind, entity) for kind, key in (("actor", "actors"), ("background", "backgrounds"))
                for entity in scenario[key]]
    known = {(kind, entity["id"]) for kind, entity in entities}
    facts = list(manual)
    for fact in facts:
        validate(fact, "image_fact")
        require((fact["entity_kind"], fact["entity_id"]) in known, "IMAGE_FACT", fact["entity_id"], "Unknown entity")
    manual_keys = {(fact["entity_kind"], fact["entity_id"]) for fact in facts}
    for kind, entity in entities:
        asset_id = entity.get("image_asset_id")
        # Explicit manual descriptions take precedence for that entity.
        if not asset_id or (kind, entity["id"]) in manual_keys:
            continue
        require(asset_id in assets, "IMAGE_FACT_ASSET", asset_id, "Scenario image is missing from registry")
        asset = assets[asset_id]
        require(asset["kind"] == "image", "IMAGE_FACT_ASSET", asset_id, "Expected an image asset")
        verify_assets([asset], registry["root"])
        fragment, _, _, _ = observer(
            image_loader(path_for(asset, registry["root"])), pair,
            entity_id=entity["id"], asset_id=asset_id, name=entity.get("name", entity["id"]),
            analysis_profile="subject_only" if kind == "actor" else "scene_only",
            hint_mode="observe_only", cache_root=cache_root,
            **({"config": config} if config is not None else {}),
        )
        prefix = "* 特徴: " if kind == "actor" else "* 情景: "
        text = " ".join(line[len(prefix):] for line in fragment.splitlines() if line.startswith(prefix))
        require(bool(text.strip()), "IMAGE_FACT_EMPTY", entity["id"], "Vision returned no visible facts")
        fact = dict(entity_kind=kind, entity_id=entity["id"], text=text)
        validate(fact, "image_fact")
        facts.append(fact)
    return facts
