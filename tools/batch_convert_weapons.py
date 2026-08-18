#!/usr/bin/env python3
'Convert weapons and shields: one .txt table per weapon type.\n\nData/Weapons/ CSVs are named by type (sword1h.csv, bow.csv, ...)\ninstead of the old numeric ids (character_system.h:41-64), and have the\nSame structure as character equipment: MeshName and TextureName per\nseparate. Over 622 rows there are 385 meshes against 564 textures, so the\nmesh is reused with many textures and you have to keep them separate -- see\nthe long note in batch_convert_characters.py.\n\nUnlike equipment, weapons do NOT deform: they have no bones or weights.\nThey hang whole from a hand bone, which depends on the character and the\nweapon type (tools/weapon_bones.py).\n\nOutput:\n    data/weapons/<tipo>.txt index -> mesh + texture + alpha clipping\n    data/weapons/meshes/<nombre>.glb geometry, WITHOUT material\n    data/weapons/textures/<n>.png 1 per unique texture\n    data/weapons/catalog.txt available types and whether they are dual wield\n\nThe two tables are CSV (comma separated values, with header) and not JSON\nbecause they are flat -- one row per record, always the same columns --\njust like the original the source client CSVs you read from. See the note in\nscripts/data_table.gd.\n\nThe EXTENSION is .txt and not .csv on purpose: Godot imports the .csv as tables\nof TRANSLATION (first column = key, the rest = languages) and generates a\n.translation per column. Worse still, a file with an importer is left out\ndel export -- only the imported product is packaged -- and DataTable, which reads\nthe raw file with FileAccess, I wouldn\'t find anything. It can be avoided with a\n.import per table that says importer="keep", but there are 18 more files for\nmake Godot do nothing; .txt does not have an importer and none is needed.\n\nUsage:\n    python batch_convert_weapons.py [--data-root DIR]'

import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from convert_3do import parse_3do, write_glb  # noqa: E402
from data_table import write_table  # noqa: E402
from texture_utils import load_rgba_debled  # noqa: E402
from weapon_bones import DUAL_WIELD_TYPES  # noqa: E402

DEFAULT_ORIGINAL_DATA_ROOT = Path(os.environ.get("ASSET_SOURCE_ROOT", "data/source"))
PROJECT_DATA_ROOT = Path(__file__).parent.parent / "data"

## CSV names = type names, in the same order as the WeaponType enum
## from the original (character_system.h:17-38). The last two are shields, which
## They go to the other hand and are chosen separately.
WEAPON_TYPES = ("sword1h", "sword2h", "axe1h", "axe2h", "dualsword", "spear",
                "mace1h", "hammer2h", "dagger", "javelin",
                "staff", "bow", "crossbow", "claw")
SHIELD_TYPES = ("shieldlight", "shielddark")

## Labels for the panel. The dataset does not include them; they leave the comment of
## each value of the WeaponType enum.
TYPE_LABELS = {
    "sword1h": "Espada 1M", "sword2h": "Espada 2M", "axe1h": "Hacha 1M",
    "axe2h": "Hacha 2M", "dualsword": "Espadas dobles", "spear": "Lanza",
    "mace1h": "Maza 1M", "hammer2h": "Martillo 2M", "dagger": "Daga",
    "javelin": "Jabalina", "staff": "Baculo", "bow": "Arco",
    "crossbow": "Ballesta", "claw": "Garras",
    "shieldlight": "Escudo (Luz)", "shielddark": "Escudo (Oscuridad)",
}


## Godot has its own importer for .csv: it assumes it is a table of
## TRANSLATIONS (first column = key, the rest = languages) and generates a
## .translation per column. These tables are not that, and also a file with
## importer is OUTSIDE the export -- only the imported product is packaged
## and DataTable, which reads the raw .csv with FileAccess, would find nothing.
##

## importer="keep" is the way to tell Godot "this file is not imported,
## copy it as is."
KEEP_IMPORT = '[remap]\n\nimporter="keep"\n'


def read_csv(path: Path) -> list:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [row for row in csv.DictReader(fh) if row.get("MeshName")]


def convert_texture(src_dds: Path, dst_png: Path, alpha_cutout: bool = False) -> bool:
    'alpha_cutout exits the CSV and decides whether to dilate the color under the\n    transparent pixels. It only applies when the piece is cut by\n    alpha; in an opaque the alpha means something else and dilate replaces the\n    texture by radial veins. Measured on all 564 weapon textures in the\n    dataset: 71 were ruined, all in "Unknown(-1)" mode, which is\n    opaque. See texture_utils.load_rgba_debled.'
    if dst_png.exists():
        return True
    try:
        image = load_rgba_debled(src_dds, debleed=alpha_cutout)
    except Exception as exc:
        print('SKIP texture %s: %s' % (src_dds.name, exc))
        return False
    dst_png.parent.mkdir(parents=True, exist_ok=True)
    image.save(dst_png, format="PNG")
    return True


def convert_type(weapons_dir: Path, out_dir: Path, type_name: str,
                 done_meshes: dict, done_textures: dict) -> dict | None:
    rows = read_csv(weapons_dir / ("%s.csv" % type_name))
    if not rows:
        return None

    entries = []
    skipped = 0
    for row in rows:
        mesh_name = row["MeshName"].strip()
        src_3do = weapons_dir / "3do" / mesh_name
        if not src_3do.exists():
            # The CSV lists more records than meshes present (team
            # episodes that this dataset does not include).
            src_3do = weapons_dir / "3do" / mesh_name.lower()
        if not src_3do.exists():
            skipped += 1
            continue

        mesh_stem = src_3do.stem.lower()
        if mesh_stem not in done_meshes:
            try:
                model = parse_3do(src_3do)
            except ValueError as exc:
                print("  SKIP %s: %s" % (mesh_name, exc))
                skipped += 1
                continue
            write_glb(model, out_dir / "meshes" / ("%s.glb" % mesh_stem))
            # Only the name of the file: the folder is set by whoever reads the table
            # (CharacterRig.weapon_entries). Repeat "meshes/" in each of
            # the 622 rows is noise, and the folder is fixed per column.
            done_meshes[mesh_stem] = "%s.glb" % mesh_stem

        # The alpha mode is read BEFORE converting the texture: decide whether to
        # It also dilates if the mesh cuts.
        alpha_mode = (row.get("AlphaBlendingMode") or "").strip()
        alpha_cutout = alpha_mode in ("Alpha", "Visibility")

        texture_ref = ""
        texture_name = (row.get("TextureName") or "").strip()
        if texture_name:
            texture_stem = Path(texture_name).stem.lower()
            if texture_stem not in done_textures:
                src_dds = weapons_dir / "dds" / texture_name
                if not src_dds.exists():
                    src_dds = weapons_dir / "dds" / texture_name.lower()
                if src_dds.exists() and convert_texture(
                        src_dds, out_dir / "textures" / ("%s.png" % texture_stem),
                        alpha_cutout):
                    done_textures[texture_stem] = "%s.png" % texture_stem
                else:
                    done_textures[texture_stem] = ""
            texture_ref = done_textures[texture_stem]

        # Same rule as character equipment (character_system.cpp:594):
        # only "Alpha" and "Visibility" clip. Most weapons come with
        # "Unknown(-1)", which is opaque.
        entries.append({
            "index": int(row["RecordIndex"]),
            "mesh": done_meshes[mesh_stem],
            "texture": texture_ref,
            "alphaCutout": alpha_cutout,
        })

    if not entries:
        return None

    path = out_dir / ("%s.txt" % type_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_table(path, ("index", "mesh", "texture", "alphaCutout"), entries)

    print('%-12s %3d records (%d omitted), %d meshes, %d textures'
          % (type_name, len(entries), skipped,
             len({e["mesh"] for e in entries}), len({e["texture"] for e in entries})))
    return {
        "type": type_name,
        "label": TYPE_LABELS.get(type_name, type_name),
        "data": "res://data/weapons/%s.txt" % type_name,
        "dualWield": type_name in DUAL_WIELD_TYPES,
        "count": len(entries),
    }


def main() -> int:
    raw = sys.argv[1:]
    data_root = DEFAULT_ORIGINAL_DATA_ROOT
    if "--data-root" in raw:
        data_root = Path(raw[raw.index("--data-root") + 1])

    weapons_dir = data_root / "weapons"
    if not weapons_dir.is_dir():
        print("no existe %s" % weapons_dir, file=sys.stderr)
        return 1

    out_dir = PROJECT_DATA_ROOT / "weapons"
    print("Convirtiendo armas -> %s" % out_dir)

    # Shared between types: the same mesh or texture can appear in
    # several CSVs.
    done_meshes: dict = {}
    done_textures: dict = {}
    weapons, shields = [], []

    for type_name in WEAPON_TYPES:
        option = convert_type(weapons_dir, out_dir, type_name, done_meshes, done_textures)
        if option is not None:
            weapons.append(option)
    for type_name in SHIELD_TYPES:
        option = convert_type(weapons_dir, out_dir, type_name, done_meshes, done_textures)
        if option is not None:
            shields.append(option)

    # The two lists go in ONE table with a "kind" column that separates them: in
    # a flat table there is no way to nest two lists in one file, and two files to
    # This would be worse than a column.
    catalog_path = out_dir / "catalog.txt"
    rows = [dict(w, kind="weapon") for w in weapons] +            [dict(s, kind="shield") for s in shields]
    write_table(catalog_path, ("kind", "type", "label", "data", "dualWield", "count"), rows)
    print('OK: %d weapon types, %d shield, %d meshes, %d textures -> %s'
          % (len(weapons), len(shields), len(done_meshes),
             len([t for t in done_textures.values() if t]), catalog_path.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
