#!/usr/bin/env python3
'Phase 2: Convert the monster/NPC models that a map actually uses.\n\nCrosses an already converted .svmap (tools/convert_svmap.py) with the tables of\nmonster/NPC (tools/convert_monster_catalog.py / convert_npc_catalog.py)\nto know which modelIndex is needed, and run convert_character.py (plus the\ncopy+fix of your .dds texture) on each visual part of those models. alone\nconverts what the map actually references -- the entire dataset\nIt has thousands of monsters/NPCs, most of them do not appear on any map\navailable.\n\nOutput: data/monster/models/<modelIndex>/part_<objectIndex>.glb (+\n.skeleton.json) and the same under data/npc/models/.\n\nUsage:\n    python batch_convert_creatures.py <svmap.json> [--data-root DIR]\n\nThe catalogs are no longer passed by argument: they always come from data/monster and\ndata/npc of the project itself.'

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from convert_character import (  # noqa: E402
    parse_3dc,
    parse_cloak_3dc,
    parse_headerless_skinned_3dc,
    parse_textured_header_3dc,
    write_glb,
)
import creature_catalog  # noqa: E402

DEFAULT_ORIGINAL_DATA_ROOT = Path(os.environ.get("ASSET_SOURCE_ROOT", "data/source"))
PROJECT_DATA_ROOT = Path(__file__).parent.parent / "data"


def convert_part(original_data_root: Path, category: str, model_index: str,
                  object_index: int, mesh_name: str, texture_name: str) -> bool:
    src_3dc = original_data_root / category / "3dc" / mesh_name
    if not src_3dc.exists():
        print(f"  SKIP part {object_index}: no existe {src_3dc}")
        return False

    dst_glb = PROJECT_DATA_ROOT / category / "models" / model_index / f"part_{object_index}.glb"
    src_dds = original_data_root / category / "dds" / texture_name
    tex_path = src_dds if src_dds.exists() else None

    try:
        try:
            model = parse_3dc(src_3dc)
        except ValueError:
            try:
                model = parse_textured_header_3dc(src_3dc)
            except ValueError:
                try:
                    model = parse_headerless_skinned_3dc(src_3dc)
                except ValueError:
                    model = parse_cloak_3dc(src_3dc)
    except ValueError as exc:
        print(f"  SKIP part {object_index} ({mesh_name}): {exc}")
        return False

    write_glb(model, dst_glb, tex_path, write_skeleton_json=bool(model["bones"]))
    return True


def convert_models(original_data_root: Path, category: str, model_indices: set, catalog: dict) -> dict:
    stats = {"models": 0, "parts_ok": 0, "parts_failed": 0}
    for model_index in sorted(model_indices, key=int):
        parts = catalog["visualRows"].get(model_index)
        if not parts:
            continue
        print(f"{category} model {model_index}: {len(parts)} partes")
        any_ok = False
        for part in parts:
            ok = convert_part(original_data_root, category, model_index,
                               part["objectIndex"], part["meshName"], part["textureName"])
            any_ok = any_ok or ok
            stats["parts_ok" if ok else "parts_failed"] += 1
        if any_ok:
            stats["models"] += 1
    return stats


## modelIndex of the entries whose key appears on the map. The keys that
## unknown catalog are ignored, just as the original does.
def models_for(catalog: dict, keys: set) -> set:
    models = set()
    for key in keys:
        row = catalog["lookup"].get(key)
        if row is not None:
            models.add(catalog["catalog"][row]["modelIndex"])
    return models


def main() -> int:
    args = sys.argv[1:]
    original_data_root = DEFAULT_ORIGINAL_DATA_ROOT
    if "--data-root" in args:
        i = args.index("--data-root")
        original_data_root = Path(args[i + 1])
        args = args[:i] + args[i + 2:]

    if len(args) != 1:
        print('Usage: batch_convert_creatures.py <svmap.json> [--data-root DIR]',
              file=sys.stderr)
        return 1

    svmap = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    monster_catalog = creature_catalog.load_monsters(PROJECT_DATA_ROOT)
    npc_catalog = creature_catalog.load_npcs(PROJECT_DATA_ROOT)

    mob_ids = {str(m["mobId"]) for area in svmap["monsterAreas"] for m in area["mobs"]}
    monster_models = models_for(monster_catalog, mob_ids)
    npc_keys = {f"{g['npcType']}:{g['npcId']}" for g in svmap["npcGroups"]}
    npc_models = models_for(npc_catalog, npc_keys)

    print(f'Monster models to convert:{len(monster_models)}')
    monster_stats = convert_models(original_data_root, "monster", monster_models, monster_catalog)
    print(f'NPC models to convert:{len(npc_models)}')
    npc_stats = convert_models(original_data_root, "npc", npc_models, npc_catalog)

    print(f"OK monsters:{monster_stats['models']} modelos, {monster_stats['parts_ok']} partes OK, {monster_stats['parts_failed']}failed parts")
    print(f"OK NPCs: {npc_stats['models']} modelos, {npc_stats['parts_ok']} partes OK, {npc_stats['parts_failed']}failed parts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
