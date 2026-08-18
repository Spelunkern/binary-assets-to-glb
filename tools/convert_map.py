#!/usr/bin/env python3
'Orchestrate the complete conversion of A Research map (FLD of world\nopen or dungeon DUN) for the Godot map selector\n(MapSwitcherPanel/Main.load_map).\n\nCombines in a single step what was previously carried out separately by hand\n(convert_wld.py, copy_texture.py for texture, batch_convert_wld_objects.py,\nconvert_svmap.py, batch_convert_creatures.py, convert_field_lightmap.py,\nconvert_dg.py, prepare_wld_effects.py):\n\n  - FLD: terrain + terrain textures + decoration objects +\n    NPCs/monsters + lightmap/field weights (if the dataset has them) +\n    particle effects.\n  - DUN: real geometry of the dungeon (.dg, with its baked lightmap and\n    real textures) + decoration objects + NPCs/monsters + effects.\n    No terrain/water/lightmap "field" (no heightmap). Various maps\n    DUN share the same .dg (same reused floor) -- it becomes a\n    only once per file name.\n\nUsage:\n    python convert_map.py <mapId> [<mapId> ...] [--skip-existing]\n    python convert_map.py --all [--skip-existing]'

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import batch_convert_creatures
import creature_catalog  # noqa: E402
import batch_convert_wld_objects  # noqa: E402
import convert_dg  # noqa: E402
import convert_field_lightmap  # noqa: E402
import convert_mani  # noqa: E402
import convert_svmap  # noqa: E402
import convert_wld  # noqa: E402
import effect_asset_catalog  # noqa: E402
import prepare_wld_effects  # noqa: E402
from texture_utils import load_rgba_debled  # noqa: E402

ORIGINAL_DIST_ROOT = Path(os.environ.get("ASSET_DIST_ROOT", "data/source_dist"))
ORIGINAL_DATA_ROOT = Path(os.environ.get("ASSET_SOURCE_ROOT", "data/source"))
FIELD_ROOT = ORIGINAL_DIST_ROOT / "world" / "field"
PROJECT_ROOT = Path(__file__).parent.parent
PROJECT_DATA_ROOT = PROJECT_ROOT / "data"
MONSTER_CATALOG_TABLE = PROJECT_DATA_ROOT / "monster" / "catalog.txt"
NPC_CATALOG_TABLE = PROJECT_DATA_ROOT / "npc" / "catalog.txt"


def convert_terrain_textures(terrain_layers: list) -> None:
    for layer in terrain_layers:
        stem = Path(layer["textureFileName"]).stem.lower()
        dst = PROJECT_DATA_ROOT / "entity" / "terrain" / f"{stem}.png"
        if dst.exists():
            continue
        src = ORIGINAL_DIST_ROOT / "entity" / "terrain" / f"{stem}.dds"
        if not src.exists():
            print(f'SKIP terrain texture not found:{src}')
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Terrain layers are data textures, not alpha-cutout sprites.
        load_rgba_debled(src, debleed=False).save(dst, format="PNG")
        print(f'OK terrain texture:{stem}.png')


def convert_dungeon_mesh(dg_file_name: str, skip_existing: bool = False) -> None:
    stem = Path(dg_file_name).stem
    dst_glb = PROJECT_DATA_ROOT / "world" / "dungeon" / f"{stem.lower()}.glb"
    # The jump obeys --skip-existing like the rest of the script. Before it was
    # unconditional, and that made it impossible to regenerate a dungeon: any
    # converter fix (collision mesh, lightmap) was left
    # out because the old .glb was already on disk, without saying a word.
    if skip_existing and dst_glb.exists():
        print(f"  SKIP dg {dg_file_name} (ya existe)")
        return

    dungeon_root = ORIGINAL_DIST_ROOT / "world" / "dungeon"
    src = dungeon_root / dg_file_name
    if not src.exists():
        # The name in the .wld may differ in capitalization from the actual file.
        match = next((c for c in dungeon_root.glob("*.dg") if c.name.lower() == dg_file_name.lower()), None)
        src = match if match else src
    if not src.exists():
        print(f"  SKIP .dg no encontrado: {dg_file_name}")
        return

    lightmap_dir = dungeon_root / stem
    texture_dirs = [ORIGINAL_DIST_ROOT / "entity" / "texture"]
    try:
        stats = convert_dg.convert(src, dst_glb, texture_dirs,
                                    lightmap_dir if lightmap_dir.is_dir() else None)
        print(f"  OK dg -> {dst_glb} ({stats['meshCount']} meshes, {stats['texturedMeshCount']}/{stats['meshCount']}with texture,{stats['lightmappedMeshCount']}/{stats['meshCount']}with lightmap)")
    except Exception as exc:  # noqa: BLE001 -- un .dg corrupto/raro no debe frenar el resto del lote
        print(f"  SKIP dg {dg_file_name}: {exc}")


def convert_map(map_id: int, skip_existing: bool = False) -> bool:
    wld_src = ORIGINAL_DIST_ROOT / "world" / f"{map_id}.wld"
    if not wld_src.exists():
        print(f'map{map_id}: no existe {wld_src}, salteado')
        return False

    magic = wld_src.read_bytes()[:3]
    is_dungeon = magic == b"DUN"

    print(f"=== Map{map_id} ({('DUN' if is_dungeon else 'FLD')}) ===")
    wld_json = PROJECT_DATA_ROOT / "world" / f"{map_id}.wld.json"
    if not (skip_existing and wld_json.exists()):
        result = convert_wld.parse_wld(wld_src)
        wld_json.parent.mkdir(parents=True, exist_ok=True)
        wld_json.write_text(json.dumps(result), encoding="utf-8")
        print(f"  OK wld -> {wld_json}")
    wld = json.loads(wld_json.read_text(encoding="utf-8"))

    if is_dungeon:
        dg_name = wld.get("dungeonDgFileName", "")
        if dg_name:
            print("  -- geometria de mazmorra (.dg) --")
            convert_dungeon_mesh(dg_name, skip_existing)
    else:
        print('-- terrain textures --')
        convert_terrain_textures(wld["terrainLayers"])

    print('-- decorative objects --')
    # MANIs are separate instances from normal objects, so their
    # assetName must also enter the set of assets to be converted per section.
    mani_asset_names_by_section = {}
    for inst in wld.get("maniInstances", []):
        section_name = inst.get("assetSection", "Building")
        asset_name = inst.get("assetName")
        if asset_name:
            mani_asset_names_by_section.setdefault(section_name, set()).add(asset_name)

    total_ok, total_failed = 0, 0
    for section in wld.get("objectSections", []):
        name = section["name"]
        if name not in batch_convert_wld_objects.SECTION_FOLDERS:
            continue
        used_indices = {inst["assetIndex"] for inst in section["instances"]}
        used_names = [section["assets"][i] for i in used_indices
                      if 0 <= i < len(section["assets"])]
        mani_asset_names = mani_asset_names_by_section.get(name, set())
        if mani_asset_names:
            by_stem = {Path(asset).stem.lower(): asset for asset in section["assets"]}
            used_names.extend(by_stem.get(stem, stem) for stem in sorted(mani_asset_names))
        if not used_names:
            continue
        stats = batch_convert_wld_objects.convert_section(ORIGINAL_DIST_ROOT, name, used_names)
        total_ok += stats["ok"]
        total_failed += stats["failed"]
    print(f'OK objects:{total_ok}converted,{total_failed}failed')

    if wld.get("maniInstances"):
        print('-- mani (object rotation) --')
        used_mani_names = {inst["maniName"] for inst in wld["maniInstances"]
                           if inst.get("maniName")}
        mani_dir = ORIGINAL_DIST_ROOT / "entity" / "mani"
        ok_mani = 0
        for mani_name in sorted(used_mani_names):
            dst_json = PROJECT_DATA_ROOT / "entity" / "mani" / f"{mani_name}.json"
            if dst_json.exists():
                ok_mani += 1
                continue
            src = mani_dir / f"{mani_name}.mani"
            if not src.exists():
                match = next((c for c in mani_dir.glob("*.mani")
                              if c.stem.lower() == mani_name.lower()), None)
                src = match if match else src
            if not src.exists():
                print(f"  SKIP mani no encontrado: {mani_name}")
                continue
            result = convert_mani.parse_mani(src)
            dst_json.parent.mkdir(parents=True, exist_ok=True)
            dst_json.write_text(json.dumps(result), encoding="utf-8")
            ok_mani += 1
        print(f'  OK mani: {ok_mani}/{len(used_mani_names)}converted')

    svmap_src = ORIGINAL_DIST_ROOT / "world" / f"{map_id}.svmap"
    svmap_json = PROJECT_DATA_ROOT / "world" / f"{map_id}.svmap.json"
    if svmap_src.exists():
        print('-- svmap (NPCs/monsters) --')
        if not (skip_existing and svmap_json.exists()):
            svmap_result = convert_svmap.parse_svmap(svmap_src)
            svmap_json.write_text(json.dumps(svmap_result), encoding="utf-8")
            print(f"  OK svmap -> {svmap_json}")

        if MONSTER_CATALOG_TABLE.exists() and NPC_CATALOG_TABLE.exists():
            svmap_data = json.loads(svmap_json.read_text(encoding="utf-8"))
            monster_catalog = creature_catalog.load_monsters(PROJECT_DATA_ROOT)
            npc_catalog = creature_catalog.load_npcs(PROJECT_DATA_ROOT)

            mob_ids = {str(m["mobId"]) for area in svmap_data["monsterAreas"] for m in area["mobs"]}
            monster_models = batch_convert_creatures.models_for(monster_catalog, mob_ids)
            npc_keys = {f"{g['npcType']}:{g['npcId']}" for g in svmap_data["npcGroups"]}
            npc_models = batch_convert_creatures.models_for(npc_catalog, npc_keys)

            monster_stats = batch_convert_creatures.convert_models(
                ORIGINAL_DATA_ROOT, "monster", monster_models, monster_catalog)
            npc_stats = batch_convert_creatures.convert_models(
                ORIGINAL_DATA_ROOT, "npc", npc_models, npc_catalog)
            print(f"OK creatures:{monster_stats['models']} modelos de monstruo, {npc_stats['models']} modelos de NPC")
        else:
            print('SKIP monster/NPC catalogs not found, creatures are not converted')
    else:
        print(f"  SKIP no existe {svmap_src}")

    if not is_dungeon:
        field_dir = FIELD_ROOT / str(map_id)
        if field_dir.is_dir():
            print('-- lightmap/field weights --')
            out_dir = PROJECT_DATA_ROOT / "world" / "field" / str(map_id)
            manifest = convert_field_lightmap.convert(str(map_id), wld["mapSize"], out_dir, FIELD_ROOT)
            print(f"  OK field: hasData={manifest['hasData']}")

    if wld.get("effectFileName") and wld.get("effectInstances"):
        print('-- particle effects --')
        try:
            eft_json_path = prepare_wld_effects.ensure_eft_json(wld["effectFileName"], ORIGINAL_DATA_ROOT)
            eft = json.loads(eft_json_path.read_text(encoding="utf-8"))
            stats = prepare_wld_effects.prepare_library_assets(eft, ORIGINAL_DATA_ROOT)
            written_assets = effect_asset_catalog.build_assets_for_library(eft_json_path)
            migrated = effect_asset_catalog.migrate_wld_dict(wld)
            wld_json.write_text(json.dumps(wld, ensure_ascii=False, indent="\t"), encoding="utf-8")
            print(f"OK effects:{len(eft['effects'])}effects,{stats['textures_ok']}textures ({stats['textures_missing']}missing),{stats['meshes_ok']}.3DE meshes ({stats['meshes_missing']}missing),{written_assets} assets globales, {migrated}refs by name")
        except FileNotFoundError as exc:
            print(f'SKIP effects:{exc}')

    return True


def all_map_ids() -> list:
    world_dir = ORIGINAL_DIST_ROOT / "world"
    ids = []
    for f in world_dir.glob("*.wld"):
        if f.stem.isdigit() and f.read_bytes()[:3] in (b"FLD", b"DUN"):
            ids.append(int(f.stem))
    return sorted(ids)


def main() -> int:
    args = sys.argv[1:]
    skip_existing = "--skip-existing" in args
    args = [a for a in args if a != "--skip-existing"]

    if not args:
        print(__doc__, file=sys.stderr)
        return 1

    if args[0] == "--all":
        map_ids = all_map_ids()
    else:
        map_ids = [int(a) for a in args]

    print(f'Maps to convert:{map_ids}')
    ok_count = 0
    failed_ids = []
    for map_id in map_ids:
        try:
            if convert_map(map_id, skip_existing):
                ok_count += 1
        except Exception as exc:  # noqa: BLE001 -- un mapa roto/bloqueado no debe frenar el resto del lote
            print(f'FAILURE map{map_id}: {exc}')
            failed_ids.append(map_id)
    print(f'\n=== {ok_count}/{len(map_ids)}converted maps ===')
    if failed_ids:
        print(f'Failed maps (retry with --skip-existing):{failed_ids}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
