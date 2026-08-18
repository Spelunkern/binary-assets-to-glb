#!/usr/bin/env python3
'the source client .ani (character skeleton animation) -> GLB (or raw JSON).\n\nReimplementation in Python of\nsrc/world/character_loader.cpp::load_character_ani. binary format,\nlittle-endian:\n\n    char[6] "ANI_V2" (optional -- if present, it is the "EP6" format)\n    u32 startKeyframe\n    u32 endKeyframe\n    u16 boneCount (max 4096)\n    repeat boneCount:\n        i32 parentBoneIndex\n        f32[16] bind-relative array (column-first in file, see\n                note in convert_character.py::read_matrix)\n        u32 rotationFrameCount\n        repeat: { u32 frame; f32[4] quaternion xyzw }\n        u32 translationFrameCount\n        repeat: { u32 frame; f32[3] translation }\n\nThere is no mesh data here -- an .ani moves the SKELETON, not a mesh in\nparticular: all equipped parts of the character are deformed with the\nsame set of final arrays (character_system.cpp:2615-2653). The index\nbone count (0..boneCount-1) is implicit by position in the list, just like\nin .3dc, and it is what ties both formats together.\n\nThe output GLB carries the skeleton and the animation, without a mesh. In Godot the\nclues are re-pointed to the shared Skeleton3D of the already assembled character (see\nscripts/character_rig.gd).\n\nUsage:\n    python convert_ani.py [--race-dir <dir>] <entrada.ani> <salida.glb|salida.json>'

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from gltf_writer import GlbBuilder  # noqa: E402
from source_reader import Reader  # noqa: E402
from source_skeleton import (as_matrix, build_skeleton_cached, decompose,  # noqa: E402
                              globals_to_locals)

## Native playback at fixed 30 fps for all states -- the client
## retail does not apply speedups by state (character_system.cpp:2432).
FPS = 30.0


def read_matrix(r: Reader) -> list:
    raw = [r.f32() for _ in range(16)]
    cols = [raw[0:4], raw[4:8], raw[8:12], raw[12:16]]
    return [cols[c][row] for row in range(4) for c in range(4)]


def parse_ani(path: Path) -> dict:
    r = Reader(path.read_bytes())
    if len(r.data) < 10:
        raise ValueError(f"{path}: file too small")

    ep6 = False
    if len(r.data) >= 6 and r.data[0:6] == b"ANI_V2":
        ep6 = True
        r.pos = 6

    if r.pos + 10 > len(r.data):
        raise ValueError(f"{path}: truncated header")

    start_keyframe = r.u32()
    end_keyframe = r.u32()
    bone_count = r.u16()
    if bone_count > 4096:
        raise ValueError(f"{path}: implausible bone count")

    bones = []
    for _ in range(bone_count):
        if r.pos + 4 > len(r.data):
            raise ValueError(f"{path}: truncated bone header")
        parent_bone_index = r.i32()
        matrix = read_matrix(r)
        if not r.ok:
            raise ValueError(f"{path}: truncated bone matrix")

        rotation_count = r.count(100000)
        rotation_frames = []
        for _ in range(rotation_count):
            if not r.ok:
                break
            frame = r.u32()
            quat = [r.f32(), r.f32(), r.f32(), r.f32()]
            rotation_frames.append({"frame": frame, "quaternion": quat})

        translation_count = r.count(100000)
        translation_frames = []
        for _ in range(translation_count):
            if not r.ok:
                break
            frame = r.u32()
            translation = r.vec3()
            translation_frames.append({"frame": frame, "translation": translation})

        if not r.ok:
            raise ValueError(f"{path}: truncated animation frames")

        bones.append({
            "parentBoneIndex": parent_bone_index,
            "matrix": matrix,
            "rotationFrames": rotation_frames,
            "translationFrames": translation_frames,
        })

    return {
        "ep6": ep6,
        "startKeyframe": start_keyframe,
        "endKeyframe": end_keyframe,
        "bones": bones,
    }


def _align_quaternions(quats: list) -> list:
    "Align each quaternion with the previous one (negate if the dot product is\n    negative). It's the same thing that slerp_quat does in the original\n    (character_system.cpp:123-127) when interpolating; do it here leave the\n    correct result without depending on the importer taking the path\n    short on his own."
    out = []
    for q in quats:
        q = np.asarray(q, dtype=np.float64)
        n = np.linalg.norm(q)
        q = q / n if n > 1e-9 else np.array([0.0, 0.0, 0.0, 1.0])
        if out and float(np.dot(out[-1], q)) < 0.0:
            q = -q
        out.append(q)
    return out


def build_channels(ani: dict, skeleton, bone_nodes: list) -> tuple:
    'Rotation and translation tracks for ALL bones.\n\n    "Everyone" and not "those who have keyframes" on purpose: the matrix by bone\n    The .ani is the resting pose OF THAT CLIP, not the bind of the model, and\n    varies between files (29 of 30 humf .ani differ, up to 1.89). How\n    in Godot all the clips run on a single Skeleton3D -- the one you brought\n    the mesh, with the .3dc bind -- a bone without a clue would remain in the\n    wrong pose. Writing the constant costs one keyframe per bone.'
    start, end = ani["startKeyframe"], ani["endKeyframe"]
    t0, t1 = start / FPS, max(end, start + 1) / FPS

    # Clip rest, completed with skeletal consensus for the
    # bones that this .ani does not cover.
    globals_ = list(skeleton.rest_globals)
    for i, bone in enumerate(ani["bones"]):
        if i < len(globals_):
            globals_[i] = as_matrix(bone["matrix"])
    locals_ = globals_to_locals(globals_, skeleton.parents)

    channels = []
    for i in range(len(skeleton)):
        rest_t, rest_q, _ = decompose(locals_[i])
        bone = ani["bones"][i] if i < len(ani["bones"]) else None

        # compute_client_finals (character_system.cpp:317-331): the keyframe
        # rotation REPLACES the local rotation (it is not composed with it), and
        # The translation remains that of rest unless there are own keyframes.
        # The two canals are independent: a bone can have only one.
        rot_keys = bone["rotationFrames"] if bone else []
        if rot_keys:
            times = [k["frame"] / FPS for k in rot_keys]
            values = _align_quaternions([k["quaternion"] for k in rot_keys])
        else:
            times, values = [t0], [np.asarray(rest_q, dtype=np.float64)]
        times, values = _clamp_to_clip(times, values, t0, t1)
        channels.append({"node": bone_nodes[i], "path": "rotation",
                         "times": times, "values": np.stack(values)})

        trans_keys = bone["translationFrames"] if bone else []
        if trans_keys:
            times = [k["frame"] / FPS for k in trans_keys]
            values = [np.asarray(k["translation"], dtype=np.float64) for k in trans_keys]
        else:
            times, values = [t0], [np.asarray(rest_t, dtype=np.float64)]
        times, values = _clamp_to_clip(times, values, t0, t1)
        channels.append({"node": bone_nodes[i], "path": "translation",
                         "times": times, "values": np.stack(values)})

    return channels, t1 - t0


def _clamp_to_clip(times: list, values: list, t0: float, t1: float) -> tuple:
    'Secures keys at both ends of the clip, holding the value of the\n    edge. Without this the animation lasts until the last keyframe of each bone\n    instead of until endKeyframe, and each bone would end in a moment\n    different. The original motor maintains the last value out of range the same.\n    (character_system.cpp:264-265).'
    times, values = list(times), list(values)
    if times[0] > t0:
        times.insert(0, t0)
        values.insert(0, values[0])
    if times[-1] < t1:
        times.append(t1)
        values.append(values[-1])
    return times, values


def write_glb(ani: dict, src: Path, dst: Path, race_dir: Path | None,
              skeleton=None) -> tuple:
    'skeleton: already assembled skeleton. It is used by the vehicle pipeline, where\n    the skeleton is not deduced from the file prefix but from the list of\n    meshes and clips named by the CSV record (see\n    batch_convert_vehicles.py).'
    # 7 clips out of 3278 in the dataset have startKeyframe = 0xFFFFFFFF, or
    # be a -1 saved as u32: vehicle_de_05_br and 6 monster
    # (mob_demonic_idle, mob_golem1_att2/br, mob_orc4_die, mob_zomb_01/02_att1).
    #

    # You have to reject them BEFORE writing the .glb, not after. Converted
    # give a 0.03 s clip located 143 million seconds from the origin
    # (start/FPS), and with that the Godot scene importer does not fail: it
    # HANG UP. And since the reimport is a queue, a hung file leaves the
    # ENTIRE project regardless -- Godot never gets rewritten
    # .godot/editor/filesystem_cache*, so the next boot goes back to
    # enqueue everything and hang in the same file.
    #

    # The original discards them without warning: the save
    # `endKeyframe > startKeyframe` (character_system.cpp:2751) returns false and
    # It just doesn't encourage you. Here it is explicitly rejected so that the clip does not
    # come into existence.
    if ani["endKeyframe"] <= ani["startKeyframe"]:
        raise ValueError('invalid keyframe range (start=%d end=%d): the clip is not converted, the same as the original'
                         % (ani["startKeyframe"], ani["endKeyframe"]))

    warnings: list = []
    if skeleton is None:
        if race_dir is None:
            race_dir = src.parent.parent
        prefix = src.stem.split("_")[0].lower()
        skeleton, warnings = build_skeleton_cached(race_dir, prefix)

    builder = GlbBuilder()
    # The skeleton goes with the consensus rest: it serves only so that the GLB
    # is valid and openable on your own. In the game the clues are applied
    # to the Skeleton3D of the mesh, which has the .3dc bind.
    trs = skeleton.trs_for()
    bone_nodes, roots = builder.add_bone_nodes(skeleton.parents, trs, skeleton.names)

    channels, duration = build_channels(ani, skeleton, bone_nodes)
    builder.add_animation(src.stem.lower(), channels)
    builder.save(dst, root_node_indices=roots)
    return skeleton, duration, warnings


def main() -> int:
    raw = sys.argv[1:]
    race_dir: Path | None = None
    if "--race-dir" in raw:
        i = raw.index("--race-dir")
        race_dir = Path(raw[i + 1])
        raw = raw[:i] + raw[i + 2:]
    if len(raw) != 2:
        print('Usage: convert_ani.py [--race-dir <dir>] <input.ani> <output.glb|output.json>',
              file=sys.stderr)
        return 1

    src, dst = Path(raw[0]), Path(raw[1])
    result = parse_ani(src)
    dst.parent.mkdir(parents=True, exist_ok=True)

    n_rot = sum(len(b["rotationFrames"]) for b in result["bones"])
    n_trans = sum(len(b["translationFrames"]) for b in result["bones"])
    resumen = (f"ep6={result['ep6']}, {len(result['bones'])} huesos, "
               f"keyframes {result['startKeyframe']}-{result['endKeyframe']}, "
               f"{n_rot} frames de rotacion, {n_trans} frames de traslacion")

    if dst.suffix.lower() == ".json":
        dst.write_text(json.dumps(result), encoding="utf-8")
        print(f"OK: {src.name} -> {dst} ({resumen})")
        return 0

    skeleton, duration, warnings = write_glb(result, src, dst, race_dir)
    for w in warnings:
        print(f"  aviso: {w}", file=sys.stderr)
    print(f"OK: {src.name} -> {dst} ({resumen}, esqueleto de {len(skeleton)}, "
          f"{duration:.2f}s a {FPS:g} fps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
