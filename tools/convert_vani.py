#!/usr/bin/env python3
'the source client .vani (object with vertex animation) -> GLB + binary frames.\n\nPython reimplementation of src/world/vani_loader.cpp::load_vani. Despite\nSince the header (vani_loader.h) says "reuse SmodModel", the binary layout\nNOT equal to .smod -- differs in order of faces/vertices and adds\nanimation frames:\n\n    f32[3] center\n    f32 radius\n    bytes view bbox (24 bytes, unused)\n    u32 meshCount\n    u32 frameCount\n    u32 (unused, 4 bytes)\n    repeat meshCount:\n        u32 textureNameLen\n        char[] textureName\n        u32 faceCount\n        repeat faceCount: u16[3] indices -- FACES BEFORE vertices\n        u32 vertexCount\n        repeat frameCount:\n            repeat vertexCount:\n                f32[3] position\n                f32[3] normal\n                u32 (unused, 4 bytes)\n                f32[2] uv\n\nThis converter exports frame 0 (bind pose / first frame) as a mesh\nstatic in the .glb and animation per vertex in a compact .frames.bin\nfor the Godot runtime shader.\n\nUsage:\n    python convert_vani.py <entrada.vani> <salida.glb> [--texture-dir DIR]...'

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from convert_smod import find_texture  # noqa: E402
from gltf_writer import GlbBuilder  # noqa: E402
from source_reader import Reader  # noqa: E402


FRAMES_BIN_MAGIC = b"PVAN"
FRAMES_BIN_VERSION = 1


def parse_vani(src: Path) -> dict:
    r = Reader(src.read_bytes())
    if len(r.data) < 48:
        raise ValueError(f"{src}: file too small")

    r.vec3()  # center
    r.f32()   # radius
    r.skip(24)  # view bbox

    mesh_count = r.u32()
    frame_count = r.u32()
    r.skip(4)  # unused

    if mesh_count > 10000 or frame_count == 0 or frame_count > 4096 or not r.ok:
        raise ValueError(f"{src}: implausible mesh/frame count")

    meshes = []
    for _ in range(mesh_count):
        texture_name = r.string_length_prefixed()

        face_count = r.u32()
        if face_count > 1_000_000 or not r.ok:
            raise ValueError(f"{src}: implausible face count")
        faces = [(r.u16(), r.u16(), r.u16()) for _ in range(face_count)]

        vertex_count = r.u32()
        if vertex_count > 1_000_000 or not r.ok:
            raise ValueError(f"{src}: implausible vertex count")

        frames = []
        for _ in range(frame_count):
            positions, normals, uvs = [], [], []
            for _ in range(vertex_count):
                positions.append(r.vec3())
                normals.append(r.vec3())
                r.skip(4)  # unused
                uvs.append(r.vec2())
            frames.append({"positions": positions, "normals": normals, "uvs": uvs})

        if not r.ok:
            raise ValueError(f"{src}: truncated while reading vertex frames")

        meshes.append({"textureName": texture_name, "faces": faces, "frames": frames})

    return {"frameCount": frame_count, "meshes": meshes}


def convert(src: Path, dst_glb: Path, texture_search_dirs: list[Path] | None = None) -> dict:
    model = parse_vani(src)
    search_dirs = texture_search_dirs or [src.parent]

    builder = GlbBuilder()
    total_verts = 0
    total_faces = 0
    texture_names = []
    parts = []

    for mesh_index, mesh in enumerate(model["meshes"]):
        texture_names.append(mesh["textureName"])
        tex_path = find_texture(mesh["textureName"], search_dirs)
        material_idx = builder.add_material(tex_path)

        frame0 = mesh["frames"][0]
        parts.append({
            "positions": frame0["positions"], "normals": frame0["normals"], "uvs": frame0["uvs"],
            "indices": [i for face in mesh["faces"] for i in face],
            "material_idx": material_idx,
        })
        total_verts += len(frame0["positions"])
        total_faces += len(mesh["faces"])

    # add_mesh_multi_primitive (NOT add_multi_part_mesh): one surface per
    # ORIGINAL mesh, unfused by material -- so the order of
    # The surfaces of the .glb match 1:1 with the order of the .frames.bin, and the
    # Shader can animate each surface without re-derivating merged ranges.
    mesh_idx = builder.add_mesh_multi_primitive(src.stem, parts)
    builder.add_node(mesh_idx=mesh_idx, name=src.stem)
    builder.save(dst_glb)

    if model["frameCount"] > 1:
        write_frames_bin(model, dst_glb.with_suffix(".frames.bin"))

    return {
        "meshCount": len(model["meshes"]),
        "frameCount": model["frameCount"],
        "vertexCount": total_verts,
        "faceCount": total_faces,
        "textureNames": texture_names,
    }


def write_frames_bin(model: dict, dst_bin: Path) -> None:
    'Compact runtime format for Godot: no JSON keys or floats in text.'
    with dst_bin.open("wb") as f:
        f.write(FRAMES_BIN_MAGIC)
        f.write(struct.pack("<III", FRAMES_BIN_VERSION, model["frameCount"], len(model["meshes"])))
        for mesh in model["meshes"]:
            indices = [i for face in mesh["faces"] for i in face]
            frames = mesh["frames"]
            vertex_count = len(frames[0]["positions"]) if frames else 0
            f.write(struct.pack("<II", len(indices), vertex_count))
            if indices:
                f.write(struct.pack(f"<{len(indices)}I", *indices))
            for frame in frames:
                positions = frame["positions"]
                normals = frame["normals"]
                uvs = frame["uvs"]
                for i in range(vertex_count):
                    p = positions[i]
                    n = normals[i] if i < len(normals) else (0.0, 1.0, 0.0)
                    uv = uvs[i] if i < len(uvs) else (0.0, 0.0)
                    f.write(struct.pack("<8f", p[0], p[1], p[2], n[0], n[1], n[2], uv[0], uv[1]))


def main() -> int:
    args = sys.argv[1:]
    texture_dirs = []
    while "--texture-dir" in args:
        i = args.index("--texture-dir")
        texture_dirs.append(Path(args[i + 1]))
        args = args[:i] + args[i + 2:]

    if len(args) != 2:
        print('Usage: convert_vani.py <input.vani> <output.glb> [--texture-dir DIR]...', file=sys.stderr)
        return 1

    src, dst = Path(args[0]), Path(args[1])
    stats = convert(src, dst, texture_dirs or None)
    print(f"OK: {src.name} -> {dst} ({stats['meshCount']} meshes, {stats['frameCount']}frames [frame 0 in the .glb, animation in .frames.bin],{stats['vertexCount']} vertices, {stats['faceCount']}faces)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
