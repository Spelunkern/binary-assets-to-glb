#!/usr/bin/env python3
'the source client .smod -> GLB (glTF binary).\n\nPython reimplementation of src/world/smod_loader.cpp (original engine in\nC++). Binary format, little-endian, all integers/floats without padding:\n\n    f32[3] center\n    f32 radius\n    bytes view bbox (min/max Vec3), 24 bytes, not used here\n    u32 meshCount\n    repeat meshCount:\n        u32 textureNameLen\n        char[] textureName (may include final)\n        u32 vertexCount\n        repeat vertexCount:\n            f32[3] position\n            f32[3] normal\n            i32 boneId (always -1 on SMOD, discarded)\n            f32[2] uv\n        u32 faceCount\n        repeat faceCount: u16[3] indices\n    f32[3] collision bbox min\n    f32[3] collision bbox max\n    u32 collisionType (1 = collision mesh exists; other value = none)\n    if collisionType == 1:\n        u32 vertexCount\n        repeat vertexCount: f32[3] position\n        u32 faceCount\n        repeat faceCount: u16[3] indices\n\nExport the visual geometry to the .glb and, if the .smod brings collision mesh\nAUTHORIZED, write it separately as `<nombre>.collision.json`.\n\nWHY THE COLLISION GOES SEPARATELY AND DOES NOT DERIVE FROM THE VISUAL MESH\n-----------------------------------------------------------\nMeasured on data/entity: buildings bring 271 collision triangles of\naverage against 2,245 visuals, and the shapes 71 against 665. In addition to being ~8x more\ncheap, they are made TO collide: the arch of a bridge is hollow, the\ndoors open, and the foliage of a tree is not there. None of that is\ncan deduce from the visual mesh, which is exactly what made it fail so much\nto the convex hull as well as to the visual trimesh.\n\nDoes not export animation per vertex.\nThe texture is searched in `texture_search_dirs` (several possible folders,\nin order) and embeds directly into the .glb (Pillow decodes the .dds) --\nIt is no longer necessary to copy/fix the .dds separately as with the\nold OBJ pipeline.\n\nUsage:\n    python convert_smod.py <entrada.smod> <salida.glb> [--texture-dir DIR]...'

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gltf_writer import GlbBuilder  # noqa: E402
from source_reader import Reader  # noqa: E402


def find_texture(texture_name: str, search_dirs: list[Path]) -> Path | None:
    if not texture_name:
        return None
    stem = Path(texture_name).stem.lower()
    for d in search_dirs:
        if not d.is_dir():
            continue
        for candidate in d.iterdir():
            if candidate.suffix.lower() == ".dds" and candidate.stem.lower() == stem:
                return candidate
    return None


def parse_smod(src: Path) -> dict:
    r = Reader(src.read_bytes())

    r.vec3()  # center, unused
    r.f32()   # radius
    r.skip(24)  # view bbox

    mesh_count = r.u32()
    if mesh_count > 10000 or not r.ok:
        raise ValueError(f"{src}: implausible mesh count")

    meshes = []
    for _ in range(mesh_count):
        texture_name = r.string_length_prefixed()
        vertex_count = r.u32()
        if vertex_count > 1_000_000 or not r.ok:
            raise ValueError(f"{src}: implausible vertex count")

        positions, normals, uvs = [], [], []
        for _ in range(vertex_count):
            positions.append(r.vec3())
            normals.append(r.vec3())
            r.i32()  # boneId, always -1 for SMOD
            uvs.append(r.vec2())

        face_count = r.u32()
        if face_count > 1_000_000 or not r.ok:
            raise ValueError(f"{src}: implausible face count")
        faces = [(r.u16(), r.u16(), r.u16()) for _ in range(face_count)]
        if not r.ok:
            raise ValueError(f"{src}: truncated while reading faces")

        meshes.append({
            "textureName": texture_name,
            "positions": positions,
            "normals": normals,
            "uvs": uvs,
            "faces": faces,
        })

    return {"meshes": meshes, "collision": _parse_collision(r)}


def _parse_collision(r: Reader) -> dict:
    'Collision block, after visual meshes. The limits\n    (250k vertices / 500k faces) are the same as the original engine\n    (smod_loader.cpp:168-200): used to detect that the offset is misaligned\n    and not to limit real data, which are two orders less.'
    empty: dict = {"positions": [], "indices": []}

    r.skip(24)  # bbox de colision: no se usa, la malla ya lo define
    if not r.ok:
        return empty
    if r.u32() != 1 or not r.ok:
        return empty

    vertex_count = r.u32()
    if not r.ok or vertex_count > 250_000:
        return empty
    positions = [r.f32() for _ in range(vertex_count * 3)]
    if not r.ok:
        return empty

    face_count = r.u32()
    if not r.ok or face_count > 500_000:
        return empty
    indices = [r.u16() for _ in range(face_count * 3)]
    if not r.ok:
        return empty

    # An index out of range means that garbage was read: everything is discarded
    # instead of exporting a collision with invented triangles.
    if any(i >= vertex_count for i in indices):
        return empty

    return {"positions": positions, "indices": indices}


def write_collision(collision: dict, dst_glb: Path) -> None:
    'Write `<nombre>.collision.json` next to the .glb, with the same format\n    which convert_dg.py emits for dungeons: flat positions + indices.'
    dst = dst_glb.with_suffix(".collision.json")
    if not collision.get("positions") or not collision.get("indices"):
        # Without data, the old file is deleted, so as not to leave a collision
        # outdated from a previous conversion.
        dst.unlink(missing_ok=True)
        return
    dst.write_text(json.dumps({
        "positions": collision["positions"],
        "indices": collision["indices"],
    }), encoding="utf-8")


def convert(src: Path, dst_glb: Path, texture_search_dirs: list[Path] | None = None) -> dict:
    model = parse_smod(src)
    search_dirs = texture_search_dirs or [src.parent]
    write_collision(model["collision"], dst_glb)

    builder = GlbBuilder()
    total_verts = 0
    total_faces = 0
    texture_names = []
    parts = []

    for mesh_index, mesh in enumerate(model["meshes"]):
        texture_names.append(mesh["textureName"])
        tex_path = find_texture(mesh["textureName"], search_dirs)
        material_idx = builder.add_material(tex_path)

        parts.append({
            "positions": mesh["positions"], "normals": mesh["normals"], "uvs": mesh["uvs"],
            "indices": [i for face in mesh["faces"] for i in face],
            "material_idx": material_idx,
        })
        total_verts += len(mesh["positions"])
        total_faces += len(mesh["faces"])

    mesh_idx = builder.add_multi_part_mesh(src.stem, parts)
    builder.add_node(mesh_idx=mesh_idx, name=src.stem)
    builder.save(dst_glb)

    return {
        "meshCount": len(model["meshes"]),
        "vertexCount": total_verts,
        "faceCount": total_faces,
        "textureNames": texture_names,
        "collisionFaceCount": len(model["collision"]["indices"]) // 3,
    }


def main() -> int:
    args = sys.argv[1:]
    texture_dirs = []
    while "--texture-dir" in args:
        i = args.index("--texture-dir")
        texture_dirs.append(Path(args[i + 1]))
        args = args[:i] + args[i + 2:]

    if len(args) != 2:
        print('Usage: convert_smod.py <input.smod> <output.glb> [--texture-dir DIR]...', file=sys.stderr)
        return 1

    src, dst = Path(args[0]), Path(args[1])
    stats = convert(src, dst, texture_dirs or None)
    print(f"OK: {src.name} -> {dst} ({stats['meshCount']} meshes, {stats['vertexCount']} vertices, {stats['faceCount']}faces,{stats['collisionFaceCount']}collision faces)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
