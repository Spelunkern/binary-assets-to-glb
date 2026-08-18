#!/usr/bin/env python3
'Convert a complete playable character: equippable parts + animations.\n\nA "character" in the source client is not a model but a combination: seven slots\n(torso, legs, hands, boots, face, hair, helmet) that are assembled on a\nsingle skeleton, and an .ani game that moves that entire skeleton\n(character_system.cpp:2615-2653 warps ALL parts with the same\nfinal matrices). The prefix (humf, elmr, ...) identifies race+gender+class,\nwhich is the actual granularity of the skeleton: humf has 42 bones and huwm 65.\n\nMESH AND TEXTURE ARE SEPARATE\n-----------------------------\nTeam CSVs have MeshIndex and TextureIndex as separate columns:\nsame mesh is reused with many textures. About the 2112 rows of the 16\nprefixes there are 832 unique meshes against 1676 unique textures -- so almost\neach row has its own texture. Embedding the texture in the .glb would force\nduplicate the geometry once per variant, and deduplicate per mesh (which\ntowards the previous version) makes 1137 variants, more than half of the\ncatalog, are drawn with the texture of another piece.\n\nThat\'s why the .glb has only geometry + skin, the textures are loose at the level\nrace (several are shared between prefixes), and the crossover lives in a JSON per\nslot -- the direct CSV equivalent of the original. CharacterRig assembles the\nmaterial with that data.\n\nOutput:\n    (without general catalog: the list of characters comes from going through the\n     folders, see CharacterRig.catalog)\n    data/character/<raza>/textures/<nombre>.png 1 for unique race texture\n    data/character/<raza>/<prefijo>/\n        character.txt key/value: bones, slots, anchors\n        animations.txt key -> clip + duration\n        slots/<ranura>.txt index -> mesh + texture + alpha clipping\n        meshes/<nombre>.glb geometry + Skeleton3D, WITHOUT material\n        anim/<clip>.glb skeleton + animation, no mesh\n\nUsage:\n    python batch_convert_characters.py [--prefix humf | --all] [--core-anims]\n                                       [--data-root DIR]'

import csv
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from data_table import write_table  # noqa: E402
from convert_ani import parse_ani, write_glb as write_ani_glb  # noqa: E402
from convert_character import parse_3dc, resolve_skeleton, write_glb  # noqa: E402
from texture_utils import load_rgba_debled  # noqa: E402
from weapon_bones import attach_for_prefix  # noqa: E402

DEFAULT_ORIGINAL_DATA_ROOT = Path(os.environ.get("ASSET_SOURCE_ROOT", "data/source"))
PROJECT_DATA_ROOT = Path(__file__).parent.parent / "data"

## The file prefix does not say the race, and the race is the directory where
## .3dc/.ani/.dds live. It is mapped explicitly instead of guessing by the
## first two letters, because "hu" covers humf/humm/huwf/huwm (human) but
## "el"/"vi"/"de" have their own combinations.
PREFIX_RACE = {
    "humf": "human", "humm": "human", "huwf": "human", "huwm": "human",
    "elmm": "elf", "elmr": "elf", "elwm": "elf", "elwr": "elf",
    "vimm": "vile", "vimr": "vile", "viwm": "vile", "viwr": "vile",
    "demf": "deatheater", "demr": "deatheater", "dewf": "deatheater", "dewr": "deatheater",
}

## Slot -> suffix of the CSV that catalogs it. The names do not match the
## mesh: slot "upper" list humf_torso*.3DC and "foot" list humf_boots*.
SLOTS = ("upper", "lower", "hand", "foot", "face", "helmet", "hair")

## Minimum locomotion set, for --core-anims.
##

## It is NOT the default, and the reason is worth it: the manifest lists ONLY the
## clips that this run converted, so regenerate a character with the set
## At the very least, it deletes from the manifest the 80 clips that it already had on disk -- it
## the files remain and the character loses attacks, emotes and
## mount animations, without any errors. Exactly that happened, and since
## Outside it looks like "the character is broken."
CORE_ANIMS = ("000_normal", "001_walk", "002_run", "008_jump", "012_sit",
              "016_idle1", "017_idle2", "009_die")

## Import preset for the animation .glb. The two parameters are
## necessary and the defaults of Godot break the clip:
##

##   import_as_skeleton_bones: the animation .glb does not have a mesh, and without
##   mesh does not have skin; Godot creates Skeleton3D only from the joints
##   of a skin, so by default it imports the bones as nested Node3D.
##   True converts the hierarchy into a real Skeleton3D.
##

##   remove_immutable_tracks: by default Godot deletes tracks whose value
##   coincides with the rest of the node, and are precisely the constants that
##   this pipeline writes on purpose (measured: 78 tracks out of 84 survived).
##   Integers are needed because the rest pose of the .ani changes from clip to clip.
##   clip and in Godot they all run on a single Skeleton3D -- see
##   convert_ani.build_channels.
ANIM_IMPORT_PARAMS = {
    "nodes/import_as_skeleton_bones": "true",
    "animation/remove_immutable_tracks": "false",
}


def write_import_preset(asset_path: Path, params: dict | None = None,
                        importer: str = "scene", res_type: str = "PackedScene") -> None:
    "Write/patch the .import of an asset. Godot preserves [params] at\n    reimport and regenerates [remap]/[deps] on its own, so it's enough with\n    leave the parameters set.\n\n    It is necessary to write it even when the default parameters serve\n    (params={}): `godot --headless --import` DOES NOT scan the project for\n    new files, only reimport what already has a .import. an asset\n    newly converted without .import remains invisible to ResourceLoader until\n    Someone opens the editor. Measured: from the first frame conversion\n    The 120 clips entered (they do write it) and none of the 32 meshes or\n    of the 32 textures."
    import_path = Path(str(asset_path) + ".import")
    pending = dict(ANIM_IMPORT_PARAMS if params is None else params)

    if import_path.exists():
        out = []
        for line in import_path.read_text(encoding="utf-8").splitlines():
            key = line.split("=", 1)[0].strip()
            if key in pending:
                out.append("%s=%s" % (key, pending.pop(key)))
            else:
                out.append(line)
        if pending:
            # Parameter that this version of Godot has not yet written: goes to
            # end of the [params] block, which is the last one in the file.
            out.extend("%s=%s" % kv for kv in pending.items())
        import_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return

    body = ["[remap]", "", 'importer="%s"' % importer, 'type="%s"' % res_type, "",
            "[params]", ""]
    body.extend("%s=%s" % kv for kv in pending.items())
    import_path.write_text("\n".join(body) + "\n", encoding="utf-8")


def read_slot_csv(path: Path) -> list:
    "CSV rows from a slot. utf-8-sig because several bring BOM\n    (humf_hair.csv, humf_face.csv) and without that the first column is called\n    '\\ufeffRecordIndex' and does not match."
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [row for row in csv.DictReader(fh) if row.get("MeshName")]


## Color variant suffix: "humf_torso0041" is variant 1 of
## "humf_torso004". The group captures the base.
VARIANT_SUFFIX = re.compile(r"^(.*?[a-z]+\d{3})\d+$", re.IGNORECASE)


def resolve_texture(race_dir: Path, texture_name: str) -> Path | None:
    '.dds file of a team texture, with fallback to its base.\n\n    The CSVs reference 192 textures that this dataset does not include, and 128 of those\n    are color variants -- humf_boots0021.dds when only this\n    humf_boots002.dds. The original does not have fallback: it sets the route the same and\n    load_dds fails (renderer_uploads.cpp:140), so those pieces are left\n    Broken too. Here it falls to the base, which gives the piece its original color\n    instead of a blank mesh.\n\n    DIVERGE from the original on purpose: a plausible piece is preferable to a\n    broken, since the correct data does not exist anywhere.'
    direct = race_dir / "dds" / texture_name
    if direct.exists():
        return direct

    match = VARIANT_SUFFIX.match(Path(texture_name).stem)
    if match:
        base = race_dir / "dds" / ("%s.dds" % match.group(1))
        if base.exists():
            return base
    return None


def convert_texture(src_dds: Path, dst_png: Path, alpha_cutout: bool = False) -> bool:
    '.dds -> .png. The canonical project file is always .png: Godot\n    compresses to VRAM format when importing, and thus avoids the problem of\n    truncated mipmaps from the dataset\'s .dds (see copy_texture.py).\n\n    alpha_cutout comes from the CSV and decides whether to dilate the color under the pixels\n    transparent. ONLY applies when the part is cropped by alpha; in\n    a "Glow" the alpha is a glow mask, it is worth ~0 in almost all\n    image and the piece is drawn opaque -- dilate there replaces the texture\n    entire by radial veins. See texture_utils.load_rgba_debled.'
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


def convert_slots(race_dir: Path, out_dir: Path, texture_dir: Path, prefix: str) -> tuple:
    'Converts meshes and textures from all seven slots and writes a JSON per\n    slot. Returns (data_slots, stats).'
    stats = {"meshes": 0, "textures": 0, "rows": 0, "skip": 0}
    done_meshes: dict = {}
    done_textures: dict = {}
    slots_present = []

    for slot in SLOTS:
        rows = read_slot_csv(race_dir / ("%s_%s.csv" % (prefix, slot)))
        entries = []

        for row in rows:
            mesh_name = row["MeshName"].strip()
            src_3dc = race_dir / "3dc" / mesh_name
            if not src_3dc.exists():
                # The CSV lists more records than meshes present (team
                # episodes that this dataset does not include).
                stats["skip"] += 1
                continue

            mesh_stem = src_3dc.stem.lower()
            if mesh_stem not in done_meshes:
                try:
                    model = parse_3dc(src_3dc)
                except ValueError as exc:
                    print("  SKIP %s: %s" % (mesh_name, exc))
                    stats["skip"] += 1
                    continue
                skeleton, _ = resolve_skeleton(src_3dc, race_dir)
                write_glb(model, out_dir / "meshes" / ("%s.glb" % mesh_stem),
                          texture_path=None, skeleton=skeleton)
                done_meshes[mesh_stem] = "meshes/%s.glb" % mesh_stem
                stats["meshes"] += 1

            # The alpha mode is read BEFORE converting the texture: decide if
            # It dilates (see convert_texture) in addition to whether the mesh cuts.
            alpha_mode = (row.get("AlphaBlendingMode") or "").strip()
            alpha_cutout = alpha_mode in ("Alpha", "Visibility")

            texture_ref = ""
            texture_name = (row.get("TextureName") or "").strip()
            if texture_name:
                texture_stem = Path(texture_name).stem.lower()
                if texture_stem not in done_textures:
                    src_dds = resolve_texture(race_dir, texture_name)
                    # The png is named after the file ACTUALLY found, not
                    # for which the CSV asked: when several rows fall to the
                    # same base share a single png.
                    out_stem = src_dds.stem.lower() if src_dds else ""
                    if src_dds is not None and convert_texture(
                            src_dds, texture_dir / ("%s.png" % out_stem),
                            alpha_cutout):
                        done_textures[texture_stem] = "../textures/%s.png" % out_stem
                        stats["textures"] += 1
                    else:
                        done_textures[texture_stem] = ""
                texture_ref = done_textures[texture_stem]

            # Just the file name: the folders ("meshes/" and
            # "../textures/") are fixed per column and are added by whoever reads the
            # table, the same as in the others.
            entries.append({
                "index": int(row["RecordIndex"]),
                "mesh": done_meshes[mesh_stem].rsplit("/", 1)[-1],
                "texture": texture_ref.rsplit("/", 1)[-1] if texture_ref else "",
                "alphaCutout": alpha_cutout,
            })
            stats["rows"] += 1

        if entries:
            slots_present.append(slot)
            write_table(out_dir / "slots" / ("%s.txt" % slot),
                        ("index", "mesh", "texture", "alphaCutout"), entries)

    return slots_present, stats


## The character manifest is NOT a table of records but a handful of
## loose values, so it goes as a KEY/VALUE table: two columns, one
## row by data.
##

## Two conventions, girls but you have to know them:
##   - lists (slots, dualWieldTypes) and vectors are separated by
##     SPACES in a single cell, so as not to fight with the column separator;
##   - Weapon bones by type, which are an exception per ranged class
##     and only exist in 4 of the 16 prefixes, they go like "weaponBone.<tipo>" in
##     instead of in a separate file for 4 characters.
def write_manifest(out_dir: Path, prefix: str, race: str, bone_count: int,
                   slots_present: list, attach: dict | None) -> None:
    rows = [
        ("prefix", prefix),
        ("race", race),
        ("boneCount", bone_count),
        ("slots", " ".join(slots_present)),
    ]
    if attach is not None:
        dual = attach["dual"]
        rows += [
            ("weaponBone", attach["weaponBone"]),
            ("shieldBone", attach["shieldBone"]),
            ("dualWieldTypes", " ".join(attach["dualWieldTypes"])),
            ("dualBone", dual["bone"]),
            ("dualOffsetPos", " ".join(str(v) for v in dual["offsetPos"])),
            ("dualOffsetRotDeg", " ".join(str(v) for v in dual["offsetRotDeg"])),
        ]
        for weapon_type, bone in sorted(attach["weaponBoneOverrides"].items()):
            rows.append(("weaponBone.%s" % weapon_type, bone))

    write_table(out_dir / "character.txt", ("key", "value"),
                [{"key": k, "value": v} for k, v in rows])


## Clip key by ACTION INDEX, not by file suffix.
##

## The suffix is NOT reliable: the dataset writes it wrong often, and not always
## in the same prefixes. The four vile call action 0 'normal', which is
## the idle base -- with the keying by suffix those characters were left WITHOUT
## idle and in a bind pose all the time, which is how they looked broken. There is more:
## action 6 is `swmormal` in male elves and `swnomal` in two vile, action 39
## is `ondamgage` in elwr, the 100 is `skil001` in vimm.
##

## The index, however, is the same for all prefixes: it is kActionXxx of the customer, and that is where the original engine looks for them. The names here are the majority graph of the dataset (12 to 16 prefixes of 16 depending on the action).
##
##
##


## Only actions that the game uses by name are listed (see
## PlayerController.CLIP_* and CharacterRig.PRELOAD_CLIPS). The rest -- attacks,
## abilities -- continues to fall to the suffix, which is enough for that.
CANONICAL_ACTION_KEYS = {
    0: "normal", 1: "walk", 2: "run", 3: "bstep", 4: "lstep", 5: "rstep",
    6: "swnormal", 7: "swim", 8: "jump", 10: "down", 11: "up", 12: "sit",
    13: "bdumb", 14: "ldumb", 15: "rdumb", 16: "idle1", 17: "idle2",
    18: "ladder", 29: "thrun", 40: "onrun", 47: "durun", 54: "sprun",
}


def anim_key(stem: str) -> str:
    "humf_001_walk -> 'walk'. The number is the customer's action rate.\n    (RecordIndex column of <prefijo>_action.csv); the suffix is just a name\n    legible and comes with typos, so send the index -- see\n    CANONICAL_ACTION_KEYS."
    parts = stem.lower().split("_")
    if len(parts) > 2 and parts[1].isdigit():
        canonical = CANONICAL_ACTION_KEYS.get(int(parts[1]))
        if canonical is not None:
            return canonical
    return "_".join(parts[2:]) if len(parts) > 2 else stem.lower()


## The four mount actions CANNOT be keyed by suffix: each
## prefix brings TWO "_veh_run" files (actions 20 and 22) and TWO "_veh_br"
## (21 and 97), so the suffix collides and the last one steps on the first. with
## keying by suffix the manifest exposed only the alternative set (022 and
## 097) and the normal remained unattainable -- just the distinction that decides
## the AlternateAnimation column of the vehicle CSV.
##

## The ids are those of the client: kActionVehicleRun1 = 20, kActionVehicleIdle
## = 21, kActionVehicleRun2 = 22 (character_system.cpp:399-401). The 97 does not
## has its own constant; The original asks for it by number when the vehicle
## flag AlternateAnimation (character_system.cpp:1601-1605).
VEHICLE_ACTION_KEYS = {20: "veh_run", 21: "veh_idle", 22: "veh_run2", 97: "veh_br2"}


def read_action_csv(race_dir: Path, prefix: str) -> dict:
    '{stem del .ani en minuscula: RecordIndex} from <prefijo>_action.csv.\n\n    It is the authority on which file each action is, and it must be consulted\n    instead of reading the name number: elwr_021_veh-br.ANI (with hyphen\n    medium) is action 21 of elwr, and elwr_021_veh_br.ANI which also\n    It exists, no one refers to it. It is the same thing that the original does, which\n    Build your animation vector from this CSV\n    (character_system.cpp:655-659) and indexes by position.'
    path = race_dir / ("%s_action.csv" % prefix)
    if not path.exists():
        return {}
    out = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("Name") or "").strip()
            try:
                index = int(row["RecordIndex"])
            except (KeyError, TypeError, ValueError):
                continue
            if name:
                out[Path(name).stem.lower()] = index
    return out


def convert_anims(race_dir: Path, out_dir: Path, prefix: str, all_anims: bool) -> tuple:
    animations, stats = {}, {"ok": 0, "skip": 0}
    ani_dir = race_dir / "ani"
    action_by_stem = read_action_csv(race_dir, prefix)
    converted: list = []

    paths = sorted(p for p in ani_dir.glob("*") if p.suffix.lower() == ".ani"
                   and p.stem.lower().startswith(prefix + "_"))
    if not all_anims:
        wanted = {("%s_%s" % (prefix, a)).lower() for a in CORE_ANIMS}
        paths = [p for p in paths if p.stem.lower() in wanted]

    for src in paths:
        stem = src.stem.lower()
        dst = out_dir / "anim" / ("%s.glb" % stem)
        try:
            ani = parse_ani(src)
            _, duration, _ = write_ani_glb(ani, src, dst, race_dir)
        except (ValueError, KeyError) as exc:
            print("  SKIP %s: %s" % (src.name, exc))
            stats["skip"] += 1
            continue

        write_import_preset(dst)
        converted.append((stem, duration))
        stats["ok"] += 1

    # Two passes so that a key is never lost due to collision. Those of
    # mount they rule because they come from the CSV of actions, which is the authority;
    # the rest are keyed by suffix and, if that suffix is already taken, it stays
    # with his full name instead of stepping on the one who was there.
    #

    # Without this the leftovers kept colliding: demr_022_veh_run.ani
    # exists on disk but demr's CSV does not reference it (it is the only one
    # prefix without actions 22 or 97), so it fell to the suffix "veh_run" and
    # stepped on action 20.
    for stem, duration in converted:
        key = VEHICLE_ACTION_KEYS.get(action_by_stem.get(stem, -1))
        if key is not None:
            animations[key] = {"clip": "anim/%s.glb" % stem,
                               "duration": round(duration, 4)}
    for stem, duration in converted:
        if VEHICLE_ACTION_KEYS.get(action_by_stem.get(stem, -1)) is not None:
            continue
        key = anim_key(stem)
        if key in animations:
            key = stem
        animations[key] = {"clip": "anim/%s.glb" % stem,
                           "duration": round(duration, 4)}
    return animations, stats


def convert_prefix(data_root: Path, prefix: str, all_anims: bool) -> dict | None:
    race = PREFIX_RACE.get(prefix)
    if race is None:
        print("prefijo desconocido: %s (esperaba uno de %s)"
              % (prefix, ", ".join(sorted(PREFIX_RACE))), file=sys.stderr)
        return None

    race_dir = data_root / "character" / race
    if not race_dir.is_dir():
        print("no existe %s" % race_dir, file=sys.stderr)
        return None

    out_dir = PROJECT_DATA_ROOT / "character" / race / prefix
    texture_dir = PROJECT_DATA_ROOT / "character" / race / "textures"
    print("Convirtiendo %s (%s) -> %s" % (prefix, race, out_dir))

    slots_present, slot_stats = convert_slots(race_dir, out_dir, texture_dir, prefix)
    animations, anim_stats = convert_anims(race_dir, out_dir, prefix, all_anims)

    skeleton, warnings = resolve_skeleton(race_dir / "3dc" / ("%s_x.3dc" % prefix), race_dir)
    for w in warnings:
        print("  aviso: %s" % w)

    # Bones where weapons and shields are hung. They are in the manifesto of the
    # character and not on the weapon because they depend on the skeleton: each
    # prefix numbers your bones differently (see tools/weapon_bones.py).
    attach = attach_for_prefix(prefix)
    if attach is None:
        print('notice: without anchor bone table, weapon cannot be equipped')

    bone_count = len(skeleton) if skeleton else 0
    write_manifest(out_dir, prefix, race, bone_count, slots_present, attach)
    write_table(out_dir / "animations.txt", ("key", "clip", "duration"),
                [{"key": k, "clip": a["clip"].rsplit("/", 1)[-1],
                  "duration": a["duration"]} for k, a in animations.items()])

    print('%d meshes, %d textures, %d logs (%d omitted), %d animations, %d skeleton bones'
          % (slot_stats["meshes"], slot_stats["textures"], slot_stats["rows"],
             slot_stats["skip"], anim_stats["ok"], bone_count))

    # The original discards the combinations without face or hair: they are folders
    # incomplete data, no playable characters
    # (character_options.cpp:112).
    if "face" not in slots_present or "hair" not in slots_present:
        print('omitted from the catalog: faceless or hairless')
        return None

    return prefix


def main() -> int:
    raw = sys.argv[1:]
    # By default ALL clips go. --core-anims leaves the locomotion alone and
    # It is useful for fast iteration, but truncates the manifest: see CORE_ANIMS.
    all_anims = "--core-anims" not in raw
    data_root = DEFAULT_ORIGINAL_DATA_ROOT
    if "--data-root" in raw:
        data_root = Path(raw[raw.index("--data-root") + 1])

    if "--prefix" in raw:
        prefixes = [raw[raw.index("--prefix") + 1].lower()]
    elif "--all" in raw:
        prefixes = sorted(PREFIX_RACE)
    else:
        prefixes = ["humf"]

    done = []
    for prefix in prefixes:
        if convert_prefix(data_root, prefix, all_anims) is not None:
            done.append(prefix)

    if not done:
        return 1

    print('OK: %d converted character(s) -> %s' % (len(done), ", ".join(done)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
