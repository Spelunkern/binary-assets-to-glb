#!/usr/bin/env python3
"Global map effects assets.\n\nThe original format saves, per map, `effectFileName + effectId`; that ID is\na local index to the map's .eft library. For Godot we want the\nmaps call effects like assets: by stable name. Each asset generated is\nself-sufficient: brings the sequence, effects, textures and meshes that\nneeds, with its own local indices."

import json
import re
from copy import deepcopy
from pathlib import Path

PROJECT_DATA_ROOT = Path(__file__).parent.parent / "data"
EFFECT_ROOT = PROJECT_DATA_ROOT / "effects"
EFFECT_ASSET_ROOT = EFFECT_ROOT / "mapeffects"
EFFECT_ALIAS_PATH = EFFECT_ROOT / "catalogs" / "map_effect_aliases.json"


def sanitize_library_name(stem: str) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", stem.strip()).strip("_").lower()
    return name or "effect"


def effect_asset_name(library_stem: str, sequence_index: int) -> str:
    return f"{sanitize_library_name(library_stem)}_seq_{sequence_index:03d}"


def canonical_effect_asset_name(name: str) -> str:
    if not EFFECT_ALIAS_PATH.exists():
        return name
    try:
        payload = json.loads(EFFECT_ALIAS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return name
    aliases = payload.get("aliases", {})
    return str(aliases.get(name, name)) if isinstance(aliases, dict) else name


def build_assets_for_library(eft_json_path: Path) -> int:
    eft = json.loads(eft_json_path.read_text(encoding="utf-8"))
    library_stem = eft_json_path.stem
    EFFECT_ASSET_ROOT.mkdir(parents=True, exist_ok=True)

    written = 0
    for sequence_index, _sequence in enumerate(eft.get("sequences", [])):
        name = effect_asset_name(library_stem, sequence_index)
        if canonical_effect_asset_name(name) != name:
            continue
        asset = build_effect_asset(name, eft, sequence_index)
        path = EFFECT_ASSET_ROOT / f"{name}.json"
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        text = json.dumps(asset, ensure_ascii=False, indent="\t")
        if text != old:
            path.write_text(text, encoding="utf-8")
            written += 1
    return written


def build_all_assets() -> int:
    total = 0
    for path in sorted(EFFECT_ROOT.glob("*.json")):
        total += build_assets_for_library(path)
    return total


def build_effect_asset(name: str, eft: dict, sequence_index: int) -> dict:
    sequences = eft.get("sequences", [])
    if not (0 <= sequence_index < len(sequences)):
        raise IndexError(sequence_index)

    sequence = sequences[sequence_index]
    texture_names = []
    mesh_names = []
    effects = []
    records = []
    texture_map = {}
    mesh_map = {}
    effect_map = {}

    for record in sequence.get("records", []):
        effect_index = int(record.get("effectId", -1))
        if not (0 <= effect_index < len(eft.get("effects", []))):
            continue
        if effect_index not in effect_map:
            effect = deepcopy(eft["effects"][effect_index])
            remapped_textures = []
            for raw_texture_id in effect.get("textureIds", []):
                texture_id = int(raw_texture_id)
                if not (0 <= texture_id < len(eft.get("textureNames", []))):
                    continue
                if texture_id not in texture_map:
                    texture_map[texture_id] = len(texture_names)
                    texture_names.append(eft["textureNames"][texture_id])
                remapped_textures.append(texture_map[texture_id])
            effect["textureIds"] = remapped_textures

            mesh_index = int(effect.get("meshIndex", -1))
            if 0 <= mesh_index < len(eft.get("meshNames", [])):
                if mesh_index not in mesh_map:
                    mesh_map[mesh_index] = len(mesh_names)
                    mesh_names.append(eft["meshNames"][mesh_index])
                effect["meshIndex"] = mesh_map[mesh_index]
            else:
                effect["meshIndex"] = -1

            effect_map[effect_index] = len(effects)
            effects.append(effect)

        records.append({
            "effectId": effect_map[effect_index],
            "time": float(record.get("time", 0.0)),
        })

    return {
        "name": name,
        "displayName": sequence.get("name", ""),
        "format": eft.get("format", ""),
        "meshNames": mesh_names,
        "textureNames": texture_names,
        "effects": effects,
        "sequences": [{"name": name, "records": records}],
    }


def migrate_wld_dict(wld: dict) -> int:
    effect_file_name = wld.get("effectFileName", "")
    if not effect_file_name:
        return 0

    library_stem = Path(effect_file_name).stem
    migrated = 0
    for inst in wld.get("effectInstances", []):
        if "effectName" in inst:
            inst.pop("effectId", None)
            continue
        effect_id = inst.pop("effectId", None)
        if effect_id is None:
            continue
        name = effect_asset_name(library_stem, int(effect_id))
        inst["effectName"] = canonical_effect_asset_name(name)
        migrated += 1

    wld.pop("effectFileName", None)
    return migrated


def effect_asset_exists(effect_name: str) -> bool:
    canonical = canonical_effect_asset_name(effect_name)
    return (EFFECT_ASSET_ROOT / f"{canonical}.json").exists()
