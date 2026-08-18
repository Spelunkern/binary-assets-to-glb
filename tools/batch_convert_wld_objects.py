#!/usr/bin/env python3
'Phase 3: convert the decoration assets (Building/Shape/Tree/Grass/\nPrimaryVani/SecondaryVani/Dungeon/Ladder) than an already converted .wld reference.\n\n.vani reuses the same binary format as .smod (see src/world/vani_loader.h:\n"SmodModel load_vani(...)") -- converted with the same convert_smod.py.\n\nThe files live in dist/windows/data/entity/<carpeta>/ (not data/,\nwhich does not have them -- see README, "Available data"). Mapping section WLD ->\nfolder:\n    Building -> building Shape -> shape Tree -> tree\n    Grass -> grass Dungeon -> world/dungeon (.dg)\n    Ladder -> ladder (from entity/object in the original data)\n    PrimaryVani / SecondaryVani -> vani (same files, two slots)\n\nThe textures that each mesh references live in dist/windows/data/entity/texture/.\n\nOnly convert assets that the map actually uses (by assetIndex\nactually instantiated), not the entire folder.\n\nUsage:\n    python batch_convert_wld_objects.py <wld.json> [--data-root DIR]'

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import convert_dg  # noqa: E402
from convert_smod import convert as convert_smod  # noqa: E402
from convert_vani import convert as convert_vani  # noqa: E402

DEFAULT_DIST_DATA_ROOT = Path(os.environ.get("ASSET_DIST_ROOT", "data/source_dist"))
PROJECT_DATA_ROOT = Path(__file__).parent.parent / "data"

SECTION_FOLDERS = {
    "Building": "building",
    "Shape": "shape",
    "Tree": "tree",
    "Grass": "grass",
    "Ladder": "ladder",
    "PrimaryVani": "vani",
    "SecondaryVani": "vani",
    "Dungeon": "dungeon",
}

SOURCE_FOLDERS = {
    **SECTION_FOLDERS,
    "Ladder": "object",
}


def convert_section(dist_root: Path, section_name: str, asset_names: list) -> dict:
    folder = SECTION_FOLDERS.get(section_name)
    if folder is None:
        return {"ok": 0, "failed": 0}
    if section_name == "Dungeon":
        return convert_dungeon_assets(dist_root, asset_names)
    is_vani = folder == "vani"
    texture_dirs = [dist_root / "entity" / "texture"]

    stats = {"ok": 0, "failed": 0}
    source_folder = SOURCE_FOLDERS.get(section_name, folder)
    for asset_name in asset_names:
        src = dist_root / "entity" / source_folder / asset_name
        if not src.exists():
            print(f"  SKIP {section_name}/{asset_name}: no existe en dist/")
            stats["failed"] += 1
            continue

        # Lowercase: _object_mesh_path in terrain_builder.gd does to_lower()
        # on the asset name to resolve the path -- they must match
        # or fails with "case mismatch" on case-sensitive file systems.
        # entity/<folder>, not world/objects/<folder> -- same folder as
        # original (dist/windows/data/entity/<folder>/), only with .glb in
        # instead of .smod/.vani.
        dst_glb = PROJECT_DATA_ROOT / "entity" / folder / f"{Path(asset_name).stem.lower()}.glb"
        try:
            convert_fn = convert_vani if is_vani else convert_smod
            convert_fn(src, dst_glb, texture_dirs)
            stats["ok"] += 1
        except ValueError as exc:
            print(f"  SKIP {section_name}/{asset_name}: {exc}")
            stats["failed"] += 1

    return stats


def convert_dungeon_assets(dist_root: Path, asset_names: list) -> dict:
    stats = {"ok": 0, "failed": 0}
    src_dir = dist_root / "world" / "dungeon"
    dst_dir = PROJECT_DATA_ROOT / "entity" / "dungeon"
    texture_dirs = [dist_root / "entity" / "texture"]

    for asset_name in asset_names:
        src = src_dir / asset_name
        if not src.exists():
            match = next((c for c in src_dir.glob("*.dg")
                          if c.name.lower() == asset_name.lower()), None)
            src = match if match else src
        if not src.exists():
            print(f"  SKIP Dungeon/{asset_name}: no existe en dist/")
            stats["failed"] += 1
            continue

        stem = Path(asset_name).stem
        dst_glb = dst_dir / f"{stem.lower()}.glb"
        dst_glb.parent.mkdir(parents=True, exist_ok=True)
        lightmap_dir = src_dir / stem
        if not lightmap_dir.is_dir():
            lightmap_dir = src_dir / src.stem
        try:
            convert_dg.convert(src, dst_glb, texture_dirs,
                               lightmap_dir if lightmap_dir.is_dir() else None)
            stats["ok"] += 1
        except Exception as exc:  # noqa: BLE001 -- un .dg raro no debe frenar el lote
            print(f"  SKIP Dungeon/{asset_name}: {exc}")
            stats["failed"] += 1

    return stats


def main() -> int:
    args = sys.argv[1:]
    dist_root = DEFAULT_DIST_DATA_ROOT
    if "--data-root" in args:
        i = args.index("--data-root")
        dist_root = Path(args[i + 1])
        args = args[:i] + args[i + 2:]

    if len(args) != 1:
        print('Usage: batch_convert_wld_objects.py <wld.json> [--data-root DIR]', file=sys.stderr)
        return 1

    wld = json.loads(Path(args[0]).read_text(encoding="utf-8"))

    total_ok = 0
    total_failed = 0
    mani_asset_names_by_section = {}
    for inst in wld.get("maniInstances", []):
        section_name = inst.get("assetSection", "Building")
        asset_name = inst.get("assetName")
        if asset_name:
            mani_asset_names_by_section.setdefault(section_name, set()).add(asset_name)
    for section in wld.get("objectSections", []):
        name = section["name"]
        if name not in SECTION_FOLDERS:
            continue
        # Only assets actually placed by at least one instance.
        used_indices = {inst["assetIndex"] for inst in section["instances"]}
        used_names = [section["assets"][i] for i in used_indices
                      if 0 <= i < len(section["assets"])]
        mani_asset_names = mani_asset_names_by_section.get(name, set())
        if mani_asset_names:
            by_stem = {Path(asset).stem.lower(): asset for asset in section["assets"]}
            used_names.extend(by_stem.get(stem, stem) for stem in sorted(mani_asset_names))
        if not used_names:
            continue

        print(f"{name}: {len(used_names)} assets usados de {len(section['assets'])} totales "
              f"({len(section['instances'])} instancias)")
        stats = convert_section(dist_root, name, used_names)
        total_ok += stats["ok"]
        total_failed += stats["failed"]

    print(f'\nOK: {total_ok}converted assets,{total_failed}failed')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
