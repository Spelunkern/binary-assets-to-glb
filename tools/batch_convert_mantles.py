#!/usr/bin/env python3
"Convert the source client's mantles.\n\nA layer is TWO different meshes, and it is important not to confuse them\n(character_system.cpp:1327-1515):\n\n  - the BODY of cloth, {prefijo}_mentle_l.3dc: .3dc format WITHOUT bones\n    (load_cloak_3dc), 5 columns x 13-14 rows. He is not encouraged by him\n    skeleton: simulated as cloth, see scripts/cloth_sim.gd.\n  - the SHOULDER, {prefijo}_mentle{NNN}_l.3dc: normal .3dc mesh WITH bones,\n    attached to the skeleton of the body, which is animated like any other part.\n    There are 8 designs (000-007) and the one that matches is (cloak_index - 1) % 8.\n\nThe design of the layer is given by the TEXTURE, not the mesh: mantle_<raza>.csv maps\ncloak_index -> .dds, and all layers of a race share the same two\ntights. The shoulder uses the same texture as the body.\n\nOutput:\n    data/mantles/<raza>.txt index -> texture + design\n                                                 shoulder, one row per design\n                                                 by prefix: meshes\n    data/mantles/meshes/<prefijo>_body.glb fabric, no bones or material\n    data/mantles/meshes/<prefijo>_shoulder<NNN>.glb shoulder with Skeleton3D\n    data/mantles/textures/<nombre>.png\n\nUsage:\n    python batch_convert_mantles.py [--data-root DIR]"

import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from data_table import write_table  # noqa: E402
from convert_character import parse_3dc, parse_cloak_3dc, resolve_skeleton, write_glb  # noqa: E402
from texture_utils import load_rgba_debled  # noqa: E402

DEFAULT_ORIGINAL_DATA_ROOT = Path(os.environ.get("ASSET_SOURCE_ROOT", "data/source"))
PROJECT_DATA_ROOT = Path(__file__).parent.parent / "data"

## Race abbreviation used in CSV and texture names
## (character_system.cpp:1334-1342).
RACE_ABBREV = {"human": "hu", "deatheater": "de", "elf": "el", "vile": "vi"}

RACE_PREFIXES = {
    "human": ("humf", "humm", "huwf", "huwm"),
    "elf": ("elmm", "elmr", "elwm", "elwr"),
    "vile": ("vimm", "vimr", "viwm", "viwr"),
    "deatheater": ("demf", "demr", "dewf", "dewr"),
}

## Number of shoulder designs per race (character_system.cpp:1477).
SHOULDER_DESIGNS = 8

## Fabric grid columns. It is not configurable: .3dc comes authored
## with that width and the simulation assumes it (kClothCols, character_system.h:616).
CLOTH_COLS = 5
CALIBRATED_PREFIXES = {"humf", "huwf"}
SHOULDER_OFFSETS = {"humm": [0.0, 0.011, -0.013]}


def write_cloth_profile(model: dict, dst: Path, prefix: str) -> None:
    'Retains the original .3dc grid for the ClothSim runtime.\n\n    The GLB visual is imported with vertices potentially reordered by\n    Godot. This compact copy preserves exact row 0 and connectivity\n    that the original game used.'
    dst.parent.mkdir(parents=True, exist_ok=True)
    calibrated = prefix in CALIBRATED_PREFIXES
    profile = {
        "version": 1,
        "columns": CLOTH_COLS,
        "positions": model["positions"],
        "normals": model["normals"],
        "uvs": model["uvs"],
        "indices": [index for face in model["faces"] for index in face],
        "pinSeam": 0.0 if calibrated else 1.0,
        "colliderEnabled": calibrated,
    }
    if prefix in SHOULDER_OFFSETS:
        profile["shoulderOffset"] = SHOULDER_OFFSETS[prefix]
    dst.write_text(json.dumps(profile, separators=(",", ":")), encoding="utf-8")


def convert_texture(src_dds: Path, dst_png: Path) -> bool:
    if dst_png.exists():
        return True
    try:
        image = load_rgba_debled(src_dds, debleed=True)
    except Exception as exc:
        print('SKIP texture %s: %s' % (src_dds.name, exc))
        return False
    dst_png.parent.mkdir(parents=True, exist_ok=True)
    image.save(dst_png, format="PNG")
    return True


def convert_body(mantles_dir: Path, out_dir: Path, prefix: str) -> int | None:
    'Cloth body. The _l is the normal variant; If it is missing it falls to the _hl,\n    which is a longer version (character_system.cpp:1377, :1466-1468).'
    for suffix in ("_l", "_hl"):
        src = mantles_dir / "3dc" / ("%s_mentle%s.3dc" % (prefix, suffix))
        if not src.exists():
            continue
        try:
            model = parse_cloak_3dc(src)
        except ValueError as exc:
            print("  SKIP %s: %s" % (src.name, exc))
            continue

        count = len(model["positions"])
        if count < CLOTH_COLS * 2 or count % CLOTH_COLS != 0:
            print('SKIP %s: %d vertices do not form a grid of %d columns'
                  % (src.name, count, CLOTH_COLS))
            continue

        dst = out_dir / "meshes" / ("%s_body.glb" % prefix)
        write_glb(model, dst, texture_path=None, skeleton=None)

        # The vertex order of the .3dc IS the grid (row 0 = the first
        # CLOTH_COLS). It is exported separately because Godot can reorder it when
        # import the GLB; ClothSim consumes this exact profile.
        write_cloth_profile(model, out_dir / "profiles" / ("%s.cloth.json" % prefix), prefix)
        return count
    return None


def convert_shoulders(mantles_dir: Path, out_dir: Path, race_dir: Path, prefix: str) -> list:
    'Shoulders: normal .3dc mesh, with bones, attached to the skeleton of the\n    body. It is converted in the same way as a piece of equipment so that\n    Skeleton3D himself animated it.'
    out = []
    for design in range(SHOULDER_DESIGNS):
        src = mantles_dir / "3dc" / ("%s_mentle%03d_l.3dc" % (prefix, design))
        if not src.exists():
            out.append("")
            continue
        try:
            model = parse_3dc(src)
        except ValueError as exc:
            print("  SKIP %s: %s" % (src.name, exc))
            out.append("")
            continue

        # The canonical skeleton comes from the RACE folder, not from mantles:
        # The shoulder is attached to the skeleton of the body.
        skeleton, _ = resolve_skeleton(race_dir / "3dc" / ("%s_x.3dc" % prefix), race_dir)
        dst = out_dir / "meshes" / ("%s_shoulder%03d.glb" % (prefix, design))
        write_glb(model, dst, texture_path=None, skeleton=skeleton)
        out.append("meshes/%s_shoulder%03d.glb" % (prefix, design))
    return out


def convert_race(data_root: Path, out_dir: Path, race: str) -> dict | None:
    mantles_dir = data_root / "mantles"
    race_dir = data_root / "character" / race
    abbrev = RACE_ABBREV[race]

    csv_path = mantles_dir / ("mantle_%s.csv" % abbrev)
    if not csv_path.exists():
        print("  no existe %s" % csv_path)
        return None

    entries = []
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            raw_index = (row.get("cloak_index") or "").strip()
            texture_name = (row.get("dds") or "").strip()
            if not raw_index or not texture_name:
                continue
            index = int(raw_index)
            src_dds = mantles_dir / "dds" / texture_name
            if not src_dds.exists():
                src_dds = mantles_dir / "dds" / texture_name.lower()
            texture_ref = ""
            stem = Path(texture_name).stem.lower()
            if src_dds.exists() and convert_texture(
                    src_dds, out_dir / "textures" / ("%s.png" % stem)):
                # Only the file name: the folder is added by whoever reads
                # the table (CharacterRig.cloak_entries), the same as in the
                # weapon and mount tables.
                texture_ref = "%s.png" % stem
            entries.append({
                "index": index,
                "texture": texture_ref,
                # The shoulder design that corresponds to this index.
                "shoulder": (index - 1) % SHOULDER_DESIGNS,
            })

    if not entries:
        return None

    # The meshes are written with the name that the game creates by convention
    # (<prefijo>_body.glb / <prefijo>_shoulder<NNN>.glb), so no need
    # return no route index.
    meshes = {}
    for prefix in RACE_PREFIXES[race]:
        if convert_body(mantles_dir, out_dir, prefix) is None:
            print('%s: no cloth body, ignored' % prefix)
            continue
        convert_shoulders(mantles_dir, out_dir, race_dir, prefix)
        meshes[prefix] = True

    print('%-11s %2d layouts, %d prefixes with mesh' % (race, len(entries), len(meshes)))
    return {"race": race, "entries": entries, "meshes": meshes}


def main() -> int:
    raw = sys.argv[1:]
    data_root = DEFAULT_ORIGINAL_DATA_ROOT
    if "--data-root" in raw:
        data_root = Path(raw[raw.index("--data-root") + 1])

    if not (data_root / "mantles").is_dir():
        print("no existe %s" % (data_root / "mantles"), file=sys.stderr)
        return 1

    out_dir = PROJECT_DATA_ROOT / "mantles"
    print("Converting cloaks -> %s" % out_dir)

    races = {}
    for race in RACE_ABBREV:
        result = convert_race(data_root, out_dir, race)
        if result is not None:
            races[race] = result

    if not races:
        return 1

    # The DESIGNS go to a table per race: they are flat, 24 rows of three
    # columns each.
    for race, result in races.items():
        write_table(out_dir / ("%s.txt" % race), ("index", "texture", "shoulder"),
                    result["entries"])

    total_meshes = len(list((out_dir / "meshes").glob("*.glb")))
    total_textures = len(list((out_dir / "textures").glob("*.png")))
    print('OK: %d races, %d meshes, %d textures -> %s'
          % (len(races), total_meshes, total_textures,
             ", ".join("%s.txt" % r for r in races)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
