#!/usr/bin/env python3
'Converts the frames: meshes + textures + clips, and the CSV to JSON per class.\n\nA mount in the source client is another character: a .3dc with skin, its own\nskeleton and its own set of .ani (character_system.cpp:1525-1735). what\nWhat makes it different is that it has no equipment slots -- the mesh and the five\nclips come out whole from a record of data/vehicle/vehicle_<clase>_01.csv.\n\nThat\'s why this pipeline reuses convert_character.write_glb as is and\nconvert_ani.write_glb; The only thing that matters is where the list of files comes from.\n\nTHE SKELETON CANNOT BE DEDUCED FROM THE NAME\n-------------------------------------------\nIn characters the prefix (humf, elmr, ...) rules: all .3dc and .ani of\nthat prefix shares a skeleton and is found with a glob. Not here: a\n"vehicle" is a CSV RECORD, there are meshes shared between records\n(vehicle_Hu_01.3DC is used by records 0 and 2, which only change texture) and\nclip names do not follow the mesh name. The file list is\ntakes the CSV, which is the same authority that uses the original, and from there comes the\nskeleton with source_skeleton.build_skeleton_from_files.\n\nTHE SEAT BONE\n-------------------\nThe Bone column of the CSV indexes the skeleton of the MOUNT and is a locator\npure: measured over the 20 registers with a mesh, the index always falls within\nof the skeleton, is at x ~= 0 (the midline) at 1.19-2.00 height, and\nNO vertex weighs on him in any of the 20 mounts. So it is a\nbone added just to mark the saddle. Its bind position is output in the\nJSON (seatBind) because the camera needs it to know how much the\nrider -- see PlayerController.\n\nOutput:\n    data/vehicle/<clase>/\n        vehicle.txt records: parts, bones, clips, flags\n\nThere is no catalog file: the four classes are fixed (one per race) and live\nas a constant in scripts/mount_rig.gd, just like the original has them in its\nenum. A file generated to list four names that never change was\none more file.\n        meshes/<nombre>.glb geometry + Skeleton3D, WITHOUT material\n        anim/<clip>.glb skeleton + animation, no mesh\n        textures/<nombre>.png\n\nUsage:\n    python batch_convert_vehicles.py [--class hu | --all] [--data-root DIR]'

import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from batch_convert_characters import (DEFAULT_ORIGINAL_DATA_ROOT,  # noqa: E402
                                      PROJECT_DATA_ROOT, write_import_preset)
from data_table import write_table  # noqa: E402
from convert_ani import parse_ani, write_glb as write_ani_glb  # noqa: E402
from convert_character import parse_3dc, write_glb as write_mesh_glb  # noqa: E402
from source_skeleton import as_matrix, build_skeleton_from_files  # noqa: E402
from texture_utils import load_rgba_debled  # noqa: E402

## One mount class per playable race, with one CSV each. The labels
## They are those of the character selector (batch_convert_characters.PREFIX_RACE).
CLASSES = {"hu": "human", "el": "elf", "vi": "vile", "de": "deatheater"}

## CSV columns, in the order the original reads them
## (character_system.cpp:1560-1561).
COLUMNS = ("RecordIndex", "Name", "WalkAnimation", "RunAnimation",
           "JumpAnimation", "BreathAnimation", "IdleAnimation", "Objects",
           "Bone", "Bone2", "AlternateAnimation")

## Clip -> CSV column. The keys are what MountRig queries.
CLIP_COLUMNS = {"walk": "WalkAnimation", "run": "RunAnimation",
                "jump": "JumpAnimation", "br": "BreathAnimation",
                "idle": "IdleAnimation"}

## Dataset "no animation" flag (character_system.cpp:1714).
NO_ANIMATION = {"", "load"}

## Mesh and texture import presets. The values are those that Godot
## I would default to -- a mesh mount doesn't need anything special,
## the opposite of the animation .glb (ANIM_IMPORT_PARAMS).
##

## They are written the same, and with content: the .import has to EXIST because
## `godot --headless --import` does not discover new files (measured:
## first conversion entered the 120 clips, which write their .import, and
## none of the 32 meshes or 32 textures), and the [params] block does not
## can be EMPTY because then Godot neither matters nor can resolve the
## resource -- the .import stays as the pipeline left it and load() fails.
MESH_IMPORT_PARAMS = {
    "nodes/import_as_skeleton_bones": "false",
    "animation/remove_immutable_tracks": "true",
}
TEXTURE_IMPORT_PARAMS = {
    "compress/mode": "2",
    "mipmaps/generate": "true",
    "detect_3d/compress_to": "1",
}


def resolve_ci(directory: Path, name: str) -> Path | None:
    'File by name without being case sensitive. CSV write\n    "vehicle_Hu_01.3DC" and on disk is "vehicle_hu_01.3dc"; the original\n    resolves equal (resolve_ci).'
    if not name:
        return None
    direct = directory / name
    if direct.exists():
        return direct
    target = name.lower()
    for path in directory.iterdir():
        if path.name.lower() == target:
            return path
    return None


def read_vehicle_csv(path: Path) -> list:
    'CSV rows of a class. All fields are enclosed in quotes.'
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = [row for row in csv.DictReader(fh) if row.get("RecordIndex")]
    missing = [c for c in COLUMNS if rows and c not in rows[0]]
    if missing:
        raise ValueError("%s: missing columns %s" % (path.name, ", ".join(missing)))
    return rows


def parse_objects(objects: str) -> list:
    '"a.3DC:a.dds|b.3DC:b.dds" -> [(mesh, texture), ...].\n\n    Same parsing as the original (character_system.cpp:1673-1687): the parts\n    are separated with \'|\' and inside each one the mesh and the texture with \':\'.\n    The recolor records (Vehicle_Hu_03) use the second part to\n    add a small piece on the same base mesh.'
    parts = []
    for chunk in objects.split("|"):
        if ":" not in chunk:
            continue
        mesh, _, texture = chunk.partition(":")
        mesh, texture = mesh.strip(), texture.strip()
        if mesh and texture:
            parts.append((mesh, texture))
    return parts


def has_alpha_cutout(image_path: Path) -> bool:
    'Approximate dds_file_has_alpha_cutout (dds_loader.cpp:756-778).\n\n    The original looks at the alpha endpoints of each BC3 block and returns true if\n    more than 2% de blocks are not opaque. Here the texture is already\n    decompressed, so the fraction of PIXELS with alpha < 200 is measured --\n    same opacity threshold and same 2%, on the unit we have.\n\n    It is decided by the texture and not by the CSV because the CSV of vehicles does not\n    brings alpha mode column, just like with the layers: the original\n    calls the heuristic in character_system.cpp:1705.'
    try:
        alpha = np.asarray(load_rgba_debled(image_path, debleed=False))[:, :, 3]
    except Exception:
        return False
    if alpha.size == 0:
        return False
    return float((alpha < 200).mean()) > 0.02


def convert_texture(src_dds: Path, dst_png: Path) -> bool:
    '''Convert a vehicle texture using the original alpha-cutout heuristic.

    Vehicle tables do not supply alpha-mode metadata. The texture heuristic
    therefore decides whether transparent RGB can be safely dilated.'''
    if not dst_png.exists():
        try:
            image = load_rgba_debled(src_dds, debleed=has_alpha_cutout(src_dds))
        except Exception as exc:
            print('SKIP texture %s: %s' % (src_dds.name, exc))
            return False
        dst_png.parent.mkdir(parents=True, exist_ok=True)
        image.save(dst_png, format="PNG")
    write_import_preset(dst_png, TEXTURE_IMPORT_PARAMS,
                        importer="texture", res_type="CompressedTexture2D")
    return True


def record_files(vehicle_root: Path, row: dict) -> tuple:
    '(meshes, textures, clips) from a record, already resolved to real routes.\n\n    It also returns the names that the CSV requested and are not there, to be able to\n    skip the entire registration instead of exporting a half mount.'
    meshes, textures, missing = [], [], []
    for mesh_name, texture_name in parse_objects(row["Objects"]):
        mesh_path = resolve_ci(vehicle_root / "3dc", mesh_name)
        texture_path = resolve_ci(vehicle_root / "dds", texture_name)
        if mesh_path is None:
            missing.append(mesh_name)
            continue
        meshes.append(mesh_path)
        # A part without texture is drawn the same, in white: it is preferable to
        # lose the entire mount. The original does the opposite (discards
        # the part, :1698), but there the missing texture leaves a gap
        # invisible and here I would leave an incomplete frame in the catalog.
        textures.append(texture_path)
        if texture_path is None:
            missing.append(texture_name)

    clips = {}
    for key, column in CLIP_COLUMNS.items():
        name = (row.get(column) or "").strip()
        if name.lower() in NO_ANIMATION:
            continue
        path = resolve_ci(vehicle_root / "ani", name)
        if path is None:
            missing.append(name)
            continue
        clips[key] = path
    return meshes, textures, clips, missing


def convert_class(data_root: Path, code: str) -> dict | None:
    vehicle_root = data_root / "vehicle"
    csv_path = resolve_ci(vehicle_root, "vehicle_%s_01.csv" % code)
    if csv_path is None:
        print("CSV of class '%s' does not exist in %s" % (code, vehicle_root),
              file=sys.stderr)
        return None

    out_dir = PROJECT_DATA_ROOT / "vehicle" / code
    print("Converting mounts for '%s' (%s) -> %s" % (code, CLASSES[code], out_dir))

    done_meshes: dict = {}    # stem -> ruta relativa del .glb
    done_clips: dict = {}     # stem -> (ruta relativa, duracion)
    done_textures: dict = {}  # stem -> (ruta relativa, alphaCutout)
    entries = []
    stats = {"meshes": 0, "clips": 0, "textures": 0, "skip": 0}

    for row in read_vehicle_csv(csv_path):
        index = int(row["RecordIndex"])
        name = (row.get("Name") or "").strip()
        meshes, textures, clips, missing = record_files(vehicle_root, row)
        if not meshes or not clips:
            print("  SKIP record %d (%s): missing %s"
                  % (index, name, ", ".join(missing[:3]) or 'mesh or clips'))
            stats["skip"] += 1
            continue
        if missing:
            print('notice: registration %d (%s) without %s' % (index, name, ", ".join(missing[:3])))

        # The skeleton is from the REGISTER: its meshes and its clips. Records that
        # share mesh and clips (the recolors) give the same skeleton, and
        # that's why the .glb can be cached by file name.
        try:
            skeleton, warnings = build_skeleton_from_files(
                [str(p) for p in meshes], [str(p) for p in clips.values()], name.lower())
        except ValueError as exc:
            print("  SKIP registro %d (%s): %s" % (index, name, exc))
            stats["skip"] += 1
            continue
        for w in warnings:
            print("  aviso: %s" % w)

        parts = []
        seat_bind = None
        model_height = 0.0
        for mesh_path, texture_path in zip(meshes, textures):
            mesh_stem = mesh_path.stem.lower()
            model = parse_3dc(mesh_path)
            if mesh_stem not in done_meshes:
                mesh_glb = out_dir / "meshes" / ("%s.glb" % mesh_stem)
                write_mesh_glb(model, mesh_glb, texture_path=None, skeleton=skeleton)
                write_import_preset(mesh_glb, MESH_IMPORT_PARAMS)
                done_meshes[mesh_stem] = "meshes/%s.glb" % mesh_stem
                stats["meshes"] += 1

            texture_ref, cutout = "", False
            if texture_path is not None:
                texture_stem = texture_path.stem.lower()
                if texture_stem not in done_textures:
                    dst_png = out_dir / "textures" / ("%s.png" % texture_stem)
                    if convert_texture(texture_path, dst_png):
                        done_textures[texture_stem] = ("textures/%s.png" % texture_stem,
                                                       has_alpha_cutout(texture_path))
                        stats["textures"] += 1
                    else:
                        done_textures[texture_stem] = ("", False)
                texture_ref, cutout = done_textures[texture_stem]

            # Just the file name: the folder is added by whoever reads the
            # table (MountRig), the same as in the weapon tables.
            parts.append({"mesh": done_meshes[mesh_stem].rsplit("/", 1)[-1],
                          "texture": texture_ref.rsplit("/", 1)[-1] if texture_ref else "",
                          "alphaCutout": cutout})

            # The largest mesh controls the height and the seat: in
            # The recolors the second part is an ornament of 9-227 vertices.
            positions = np.asarray(model["positions"], dtype=np.float64)
            if positions.size and float(positions[:, 1].max()) > model_height:
                model_height = float(positions[:, 1].max())
                bone = int(row["Bone"])
                if 0 <= bone < len(model["bones"]):
                    # inv(B) = global bind of the bone; its translation is where
                    # The chair falls into the unanimated model.
                    seat_bind = np.linalg.inv(as_matrix(model["bones"][bone]))[:3, 3]

        clip_refs = {}
        for key, src in clips.items():
            clip_stem = src.stem.lower()
            if clip_stem not in done_clips:
                dst = out_dir / "anim" / ("%s.glb" % clip_stem)
                try:
                    _, duration, _ = write_ani_glb(parse_ani(src), src, dst, None,
                                                   skeleton=skeleton)
                except (ValueError, KeyError) as exc:
                    print("  SKIP %s: %s" % (src.name, exc))
                    continue
                write_import_preset(dst)
                done_clips[clip_stem] = ("anim/%s.glb" % clip_stem, duration)
                stats["clips"] += 1
            clip_refs[key] = "%s.glb" % clip_stem

        # One column per clip, with the entire file name. you can't
        # derive from nothing: the name comes from the original .ani and does not follow any
        # pattern -- Vehicle_de_11's walk is "vehicle_de_11_wa" and its
        # idle is "vehicle_de_11_bas", and also the recolors point to the
        # clips from the original record (Vehicle_de_03 animates with vehicle_de_01_*).
        # Empty when that record does not have that clip: Vehicle_de_05 is missing
        # the "br", and it is the only one of the 32.
        seat = [round(float(v), 4) for v in seat_bind] if seat_bind is not None             else [0.0, 0.0, 0.0]
        entries.append({
            "index": index,
            "name": name,
            "mesh": parts[0]["mesh"],
            # Second part, when there is one: 8 of the 32 records are meshes
            # two pieces. Empty in the rest.
            "mesh2": parts[1]["mesh"] if len(parts) > 1 else "",
            # A single texture and a single alphaCutout per record, too
            # verified over 32: the two parts share texture.
            "texture": parts[0]["texture"],
            "alphaCutout": parts[0]["alphaCutout"],
            "walk": clip_refs.get("walk", ""),
            "run": clip_refs.get("run", ""),
            "jump": clip_refs.get("jump", ""),
            "br": clip_refs.get("br", ""),
            "idle": clip_refs.get("idle", ""),
            # Bone where the rider sits, in the skeleton of the mount.
            "bone": int(row["Bone"]),
            # Bone2 is the placeholder of a second rider and at 32
            # dataset records is always -1. He behaves the same, so that
            # The data is if one day a vehicle appears that uses it.
            "bone2": int(row["Bone2"]),
            # AlternateAnimation = 1: rider uses alternate set
            # (actions 22 and 97 instead of 20 and 21), character_system.cpp:1601.
            "alternateAnimation": int(row["AlternateAnimation"]) == 1,
            "seatX": seat[0], "seatY": seat[1], "seatZ": seat[2],
            "height": round(model_height, 4),
        })

    if not entries:
        print('no convertible registrations')
        return None

    write_table(out_dir / "vehicle.txt",
                ("index", "name", "mesh", "mesh2", "texture", "alphaCutout",
                 "walk", "run", "jump", "br", "idle",
                 "bone", "bone2", "alternateAnimation", "seatX", "seatY", "seatZ",
                 "height"), entries)

    print('%d mounts, %d meshes, %d textures, %d clips (%d records skipped)'
          % (len(entries), stats["meshes"], stats["textures"], stats["clips"],
             stats["skip"]))
    return code


def main() -> int:
    raw = sys.argv[1:]
    data_root = DEFAULT_ORIGINAL_DATA_ROOT
    if "--data-root" in raw:
        data_root = Path(raw[raw.index("--data-root") + 1])

    if "--class" in raw:
        codes = [raw[raw.index("--class") + 1].lower()]
    else:
        codes = sorted(CLASSES)

    done = [c for c in (convert_class(data_root, c) for c in codes) if c is not None]
    if not done:
        return 1
    print('OK: %d mount class(es) -> %s' % (len(done), ", ".join(done)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
