#!/usr/bin/env python3
'the source client .3dc (character/mantle mesh + skeleton) -> GLB.\n\nPython reimplementation of src/world/character_loader.cpp::load_character_3dc\n(original C++ engine). Binary, little-endian format:\n\n    i32 version -- 444 means "EP6" (format with more data per vertex)\n    u32 boneCount (max 4096)\n    repeat boneCount: f32[16] -- bind pose array, serialized column-first\n    u32 vertexCount (max 1e6)\n    repeat vertexCount (40 bytes normal / 48 bytes EP6):\n        f32[3] position\n        f32 boneWeight0\n        if EP6: f32 boneWeight1, f32 boneWeight2\n        else: boneWeight1 = 1 - boneWeight0 (only 2 bones influence)\n        u8[3] boneIndices, u8 unknown (padding)\n        f32[3] normal\n        f32[2] uv\n    u32 faceCount (max 1e6)\n    repeat faceCount: u16[3] indices\n\nExport the mesh with real Skeleton3D and skinning. The .3dc matrices\nare the INVERSE-BIND (see the convention note in source_skeleton.py) and they go\ndirect to inverseBindMatrices; The parent/child hierarchy is not in .3dc,\ncomes out of the .ani of the same race (source_skeleton.build_skeleton).\n\nThe texture is not referenced within the .3dc (it comes from a CSV table of\nseparate equipment) -- pass it with --texture if known.\n\nUsage:\n    python convert_character.py [--cloak] [--no-skin] [--texture <ruta.dds>]\n                                [--race-dir <dir>] <entrada.3dc> <salida.glb>'

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from gltf_writer import GlbBuilder  # noqa: E402
from source_reader import Reader  # noqa: E402
from source_skeleton import as_matrix, build_skeleton_cached  # noqa: E402


def read_matrix(r: Reader) -> list:
    # Same as read_matrix in character_loader.cpp: column-first in
    # file, reordered here to row-first (m[row][col] standard) so that
    # It is easier to consume later.
    raw = [r.f32() for _ in range(16)]
    if len(raw) != 16:
        return [0.0] * 16
    cols = [raw[0:4], raw[4:8], raw[8:12], raw[12:16]]
    return [cols[c][row] for row in range(4) for c in range(4)]


def _parse_faces(r: Reader) -> list:
    face_count = r.u32()
    if face_count > 1_000_000:
        raise ValueError("implausible face count")
    return [(r.u16(), r.u16(), r.u16()) for _ in range(face_count)]


def parse_3dc(path: Path) -> dict:
    """load_character_3dc: version header + bones + skinned vertices."""
    r = Reader(path.read_bytes())
    if len(r.data) < 4:
        raise ValueError(f"{path}: file too small")

    version = r.i32()
    ep6 = version == 444

    bone_count = r.u32()
    if bone_count > 4096:
        raise ValueError(f'{path}: implausible bone count (not a load_character_3dc file? try --cloak, or it may be a 3DO-style static part -- see ItemModel in character_loader.h)')
    bones = [read_matrix(r) for _ in range(bone_count)]

    vertex_count = r.u32()
    if vertex_count > 1_000_000:
        raise ValueError(f"{path}: implausible vertex count")

    positions, normals, uvs, weights, bone_indices = [], [], [], [], []
    for _ in range(vertex_count):
        positions.append(r.vec3())
        w0 = r.f32()
        if ep6:
            w1, w2 = r.f32(), r.f32()
        else:
            w1, w2 = 1.0 - w0, 0.0
        weights.append([w0, w1, w2])
        bi = [r.u8(), r.u8(), r.u8()]
        r.u8()  # unknown padding byte
        bone_indices.append(bi)
        normals.append(r.vec3())
        uvs.append(r.vec2())

    if not r.ok:
        raise ValueError(f"{path}: truncated while reading vertices")

    faces = _parse_faces(r)

    if not r.ok:
        raise ValueError(f"{path}: truncated while reading faces")

    return {
        "ep6": ep6,
        "bones": bones,
        "positions": positions,
        "normals": normals,
        "uvs": uvs,
        "weights": weights,
        "boneIndices": bone_indices,
        "faces": faces,
    }


def parse_headerless_skinned_3dc(path: Path) -> dict:
    """Headerless skinned .3dc variant.

    Some monster meshes use the same skinned vertex layout as
    load_character_3dc, but start directly with boneCount instead of the
    version field. They are not cloaks: boneCount is non-zero and the vertices
    still carry weights and bone indices.
    """
    r = Reader(path.read_bytes())
    if len(r.data) < 8:
        raise ValueError(f"{path}: file too small")

    bone_count = r.u32()
    if bone_count == 0 or bone_count > 4096:
        raise ValueError(f"{path}: not a headerless skinned mesh "
                          f"(boneCount={bone_count})")
    bones = [read_matrix(r) for _ in range(bone_count)]

    vertex_count = r.u32()
    if vertex_count == 0 or vertex_count > 1_000_000:
        raise ValueError(f"{path}: implausible vertex count")

    positions, normals, uvs, weights, bone_indices = [], [], [], [], []
    for _ in range(vertex_count):
        positions.append(r.vec3())
        w0 = r.f32()
        weights.append([w0, 1.0 - w0, 0.0])
        bi = [r.u8(), r.u8(), r.u8()]
        r.u8()
        bone_indices.append(bi)
        normals.append(r.vec3())
        uvs.append(r.vec2())

    if not r.ok:
        raise ValueError(f"{path}: truncated while reading vertices")

    faces = _parse_faces(r)
    if not r.ok:
        raise ValueError(f"{path}: truncated while reading faces")

    return {
        "ep6": False,
        "bones": bones,
        "positions": positions,
        "normals": normals,
        "uvs": uvs,
        "weights": weights,
        "boneIndices": bone_indices,
        "faces": faces,
    }


def parse_textured_header_3dc(path: Path) -> dict:
    """Textured-header skinned .3dc variant.

    A few monster meshes, such as Mob_Rend_01_A/B, start with an embedded
    texture-name header:

        u32 nameLength
        char[nameLength] textureName
        u32 boneCount
        ...

    The actual mesh payload after that is the regular non-EP6 skinned 3DC
    layout. Normalize it in memory to the canonical `version=0, boneCount`
    header, then parse through `parse_3dc`.
    """
    data = path.read_bytes()
    if len(data) < 12:
        raise ValueError(f"{path}: file too small")
    name_len = int.from_bytes(data[0:4], "little")
    if name_len <= 0 or name_len > 512 or 4 + name_len + 4 > len(data):
        raise ValueError(f"{path}: not a textured-header mesh")
    raw_name = data[4:4 + name_len].rstrip(b"\0")
    if b"." not in raw_name:
        raise ValueError(f"{path}: textured-header name is not a file name")
    bone_offset = 4 + name_len
    bone_count = int.from_bytes(data[bone_offset:bone_offset + 4], "little")
    if bone_count == 0 or bone_count > 4096:
        raise ValueError(f"{path}: textured-header boneCount={bone_count} invalid")
    normalized = (0).to_bytes(4, "little") + data[bone_offset:]
    from tempfile import NamedTemporaryFile
    with NamedTemporaryFile(delete=False, suffix=".3dc") as tmp:
        tmp.write(normalized)
        tmp_path = Path(tmp.name)
    try:
        return parse_3dc(tmp_path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def parse_cloak_3dc(path: Path) -> dict:
    """load_cloak_3dc: no version header, starts directly with boneCount
    (always 0) -- see src/world/character_loader.cpp."""
    r = Reader(path.read_bytes())
    if len(r.data) < 8:
        raise ValueError(f"{path}: file too small")

    bone_count = r.u32()
    if bone_count != 0:
        raise ValueError(f"{path}: not a boneless cloak mesh (boneCount={bone_count})")

    vertex_count = r.u32()
    if vertex_count == 0 or vertex_count > 1_000_000:
        raise ValueError(f"{path}: implausible vertex count")

    positions, normals, uvs = [], [], []
    for _ in range(vertex_count):
        positions.append(r.vec3())
        r.skip(8)  # weight(4) + boneIdx(1) + unknown(3), unused for cloaks
        normals.append(r.vec3())
        uvs.append(r.vec2())

    if not r.ok:
        raise ValueError(f"{path}: truncated while reading vertices")

    faces = _parse_faces(r)
    if not r.ok:
        raise ValueError(f"{path}: truncated while reading faces")

    return {
        "ep6": False,
        "bones": [],
        "positions": positions,
        "normals": normals,
        "uvs": uvs,
        "weights": [[1.0, 0.0, 0.0]] * vertex_count,
        "boneIndices": [[0, 0, 0]] * vertex_count,
        "faces": faces,
    }


def build_skin_attributes(model: dict) -> tuple:
    "(JOINTS_0, WEIGHTS_0) in VEC4 from the 3 influences of .3dc.\n\n    glTF does not have a set of 3; The fourth goes at 0 weight and does not contribute. The weights\n    are normalized because the original divides by totalWeight after\n    accumulate (character_system.cpp:2648-2660) instead of trusting that\n    They add up to 1, and in the real dataset they don't always add up."
    joints = np.zeros((len(model["positions"]), 4), dtype=np.uint8)
    weights = np.zeros((len(model["positions"]), 4), dtype=np.float32)

    bone_idx = np.array(model["boneIndices"], dtype=np.int32)
    raw = np.array(model["weights"], dtype=np.float32)
    raw = np.clip(raw, 0.0, None)

    joints[:, :3] = np.clip(bone_idx, 0, 255).astype(np.uint8)
    weights[:, :3] = raw

    # An influence with an index outside the skeleton of the part does not exist: the
    # original skips it (character_system.cpp:2634) instead of clamping it,
    # so here its weight is canceled, not its index.
    bone_count = max(1, len(model["bones"]))
    weights[:, :3][bone_idx >= bone_count] = 0.0

    total = weights.sum(axis=1, keepdims=True)
    safe = total[:, 0] > 1e-6
    weights[safe] /= total[safe]
    # Vertex without any valid weight: it is tied rigidly to bone 0 so that it does not
    # collapse to the origin when deformed.
    weights[~safe] = [1.0, 0.0, 0.0, 0.0]
    joints[~safe] = 0
    return joints, weights


def write_glb(model: dict, dst_glb: Path, texture_path: Path | None = None,
              skeleton=None, alpha_cutout: bool | None = None,
              write_skeleton_json: bool = False) -> None:
    "texture_path None leaves the mesh WITHOUT material. It's what he uses\n    character pipeline: same mesh is reused with many textures\n    different (CSVs have MeshIndex and TextureIndex separately, and above all\n    2112 rows there are 832 meshes against 1676 textures), so embed the\n    texture in the .glb would force the geometry to be duplicated per variant. The\n    material is created by CharacterRig with the data from slots/<ranura>.json.\n\n    write_skeleton_json: raw dump of bones and weights, only for\n    diagnosis. By default it is not written -- the .glb already has the skeleton\n    and the skinning inside, so no one reads it."
    builder = GlbBuilder()
    material_idx = builder.add_material(texture_path, alpha_cutout=alpha_cutout)

    indices = [i for face in model["faces"] for i in face]
    bone_count = len(model["bones"])
    use_skin = skeleton is not None and bone_count > 0

    joints = weights = None
    if use_skin:
        joints, weights = build_skin_attributes(model)

    mesh_idx = builder.add_mesh("character", model["positions"], model["normals"],
                                 model["uvs"], indices, material_idx,
                                 joints=joints, weights=weights)

    skin_idx = None
    if use_skin:
        # The canonical skeleton is COMPLETE although this part uses less
        # bones: it is the same skeleton for all parts and for everyone
        # the clips, so the nodes have to exist with the same
        # names and indexes in each .glb so that Godot can upload all
        # the meshes of a single Skeleton3D.
        # Rest from the inv(B) of THIS part: it's your actual bind, so the
        # Unanimated .glb renders exactly the original mesh.
        part_globals = [np.linalg.inv(as_matrix(b)) for b in model["bones"]]
        trs = skeleton.trs_for(part_globals)
        bone_nodes, roots = builder.add_bone_nodes(skeleton.parents, trs, skeleton.names)

        # skin.joints carries the ENTIRE skeleton, not just the bones that are
        # part weighs. Godot creates bones of the Skeleton3D only for the
        # skin joints: with the list trimmed, humf_lower001 gave a
        # Skeleton3D of 36 and bones 36-41 were left as Node3D loose,
        # so the 38 parts and the 42 clips didn't fit in the same
        # skeleton. With the complete list all parts produce the same.
        #

        # JOINTS_0 indexes this list, and the bone index of the .3dc is
        # directly the canonical index (character_system.cpp:2640 uses the
        # same boneIndex for meshBones and for clientFinals), so
        # completing at the end moves nothing. The bones that the part does not have
        # They carry weight 0 and their inverse-bind leaves the consensus of the skeleton.
        ibm = [as_matrix(b) for b in model["bones"]]
        ibm += [np.linalg.inv(g) for g in skeleton.rest_globals[bone_count:]]
        skin_idx = builder.add_skin(bone_nodes, np.stack(ibm), skeleton_root=roots[0])

    mesh_node = builder.add_node(mesh_idx=mesh_idx, name="character", skin_idx=skin_idx)

    # Mesh node + bone roots at the same level. Hang the mesh
    # skeleton would cause your transform to be applied twice (once for the node
    # and another for skinning).
    roots_out = [mesh_node] + (roots if use_skin else [])
    builder.save(dst_glb, root_node_indices=roots_out)

    if write_skeleton_json:
        skeleton_path = dst_glb.with_suffix(".skeleton.json")
        skeleton_path.write_text(json.dumps({
            "ep6": model["ep6"],
            "bones": model["bones"],
            "vertexWeights": model["weights"],
            "vertexBoneIndices": model["boneIndices"],
        }), encoding="utf-8")


def resolve_skeleton(src: Path, race_dir: Path | None):
    'Canonical skeleton of the race to which this .3dc belongs.\n\n    By convention of the dataset the file lives in <raza>/3dc/<prefijo>_*.3dc\n    and the clips in <raza>/ani/, so it can be deducted without asking. The\n    prefix (humf, elmr, ...) identifies race+gender, which is the granularity\n    real skeleton: humf has 36 bones and humm 56.'
    if race_dir is None:
        race_dir = src.parent.parent
    prefix = src.stem.split("_")[0].lower()
    if not (race_dir / "ani").is_dir():
        return None, ["sin directorio ani/ en %s: se exporta sin esqueleto" % race_dir]
    try:
        return build_skeleton_cached(race_dir, prefix)
    except ValueError as exc:
        return None, ["%s: se exporta sin esqueleto" % exc]


def main() -> int:
    raw = sys.argv[1:]
    force_cloak = "--cloak" in raw
    no_skin = "--no-skin" in raw
    texture_path: Path | None = None
    race_dir: Path | None = None
    if "--texture" in raw:
        i = raw.index("--texture")
        texture_path = Path(raw[i + 1])
        raw = raw[:i] + raw[i + 2:]
    if "--race-dir" in raw:
        i = raw.index("--race-dir")
        race_dir = Path(raw[i + 1])
        raw = raw[:i] + raw[i + 2:]
    args = [a for a in raw if a not in ("--cloak", "--no-skin")]
    if len(args) != 2:
        print('Usage: convert_character.py [--cloak] [--no-skin] [--texture <path.dds>] [--race-dir <dir>] <input.3dc> <output.glb>', file=sys.stderr)
        print('--cloak: forces the load_cloak_3dc layout (mantos, no version header).', file=sys.stderr)
        print('--no-skin: exports the static mesh in bind pose, without Skeleton3D.', file=sys.stderr)
        return 1

    src, dst = Path(args[0]), Path(args[1])

    if force_cloak:
        model = parse_cloak_3dc(src)
    else:
        try:
            model = parse_3dc(src)
        except ValueError:
            try:
                model = parse_textured_header_3dc(src)
            except ValueError:
                try:
                    model = parse_headerless_skinned_3dc(src)
                except ValueError:
                    # Mantle-style files (data/mantles/3dc/*) share the .3dc
                    # extension but use load_cloak_3dc's headerless layout in
                    # the original engine (the caller picks the loader by
                    # asset type, not by sniffing the file -- we approximate
                    # that here with a fallback).
                    model = parse_cloak_3dc(src)

    skeleton, warnings = (None, []), None
    if no_skin or not model["bones"]:
        skeleton, warnings = None, []
    else:
        skeleton, warnings = resolve_skeleton(src, race_dir)
    for w in warnings:
        print(f"  aviso: {w}", file=sys.stderr)

    # Loose use from the command line: the raw dump is written
    # because there it is useful to inspect a specific .3dc.
    write_glb(model, dst, texture_path, skeleton=skeleton, write_skeleton_json=True)
    huesos = (f"{len(model['bones'])} huesos / esqueleto de {len(skeleton)}"
              if skeleton is not None else f"{len(model['bones'])} huesos, sin skin")
    print(f"OK: {src.name} -> {dst} (ep6={model['ep6']}, {huesos}, {len(model['positions'])} vertices, {len(model['faces'])} faces) + {dst.with_suffix('.skeleton.json').name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
