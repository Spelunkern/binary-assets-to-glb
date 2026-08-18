#!/usr/bin/env python3
"the source client .3do (weapon/shield mesh) -> GLB.\n\nPython reimplementation of src/world/character_loader.cpp::load_item_3do.\nBinary, little-endian format, much simpler than .3dc: there are no bones\nnor weights, because a weapon does not deform -- it hangs whole from a bone in the\nhand (character_system.cpp:2679-2731 transforms it with clientFinals[bone],\nWITHOUT inverse-bind, that is, its vertices are already in bone space).\n\n    u32 length of texture name (max 256)\n    char[] texture name\n    u32 vertexCount (max 1e6)\n    repeat vertexCount (32 bytes):\n        f32[3] position\n        f32[3] normal\n        f32[2] uv\n    u32 faceCount (max 1e6)\n    repeat faceCount: u16[3] indices\n\nThe name of the texture inside is ignored: the authoritative one is that of the\nTextureName column of the CSV of your weapon type, same as the original\n(resolve_part_from_table). That's why the .glb comes out without material -- put it together\nCharacterRig, as with character parts.\n\nUsage:\n    python convert_3do.py <entrada.3do> <salida.glb>"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gltf_writer import GlbBuilder  # noqa: E402
from source_reader import Reader  # noqa: E402


def parse_3do(path: Path) -> dict:
    r = Reader(path.read_bytes())
    if len(r.data) < 4:
        raise ValueError(f"{path}: file too small")

    name_len = r.u32()
    if name_len > 256:
        raise ValueError(f"{path}: implausible texture name length ({name_len})")
    texture_name = bytes(r.data[r.pos:r.pos + name_len]).decode("latin-1", "replace")
    r.skip(name_len)

    vertex_count = r.u32()
    if vertex_count > 1_000_000:
        raise ValueError(f"{path}: implausible vertex count")

    positions, normals, uvs = [], [], []
    for _ in range(vertex_count):
        positions.append(r.vec3())
        normals.append(r.vec3())
        uvs.append(r.vec2())
    if not r.ok:
        raise ValueError(f"{path}: truncated while reading vertices")

    face_count = r.u32()
    if face_count > 1_000_000:
        raise ValueError(f"{path}: implausible face count")
    faces = [(r.u16(), r.u16(), r.u16()) for _ in range(face_count)]
    if not r.ok:
        raise ValueError(f"{path}: truncated while reading faces")

    return {
        "textureName": texture_name.rstrip("\x00"),
        "positions": positions,
        "normals": normals,
        "uvs": uvs,
        "faces": faces,
    }


def write_glb(model: dict, dst_glb: Path, texture_path: Path | None = None,
              alpha_cutout: bool | None = None) -> None:
    builder = GlbBuilder()
    material_idx = builder.add_material(texture_path, alpha_cutout=alpha_cutout)
    indices = [i for face in model["faces"] for i in face]
    mesh_idx = builder.add_mesh("item", model["positions"], model["normals"],
                                 model["uvs"], indices, material_idx)
    node = builder.add_node(mesh_idx=mesh_idx, name="item")
    builder.save(dst_glb, root_node_indices=[node])


def main() -> int:
    if len(sys.argv) != 3:
        print('Usage: convert_3do.py <input.3do> <output.glb>', file=sys.stderr)
        return 1

    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    model = parse_3do(src)
    write_glb(model, dst)
    print(f"OK: {src.name} -> {dst}(internal texture '{model['textureName']}', {len(model['positions'])} vertices, {len(model['faces'])}faces)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
