#!/usr/bin/env python3
'the source client .dg (dungeon geometry) -> GLB.\n\nPython reimplementation of src/world/dg_loader.cpp::load_dg (engine\noriginal in C++). Binary format, little-endian, recursive octree tree:\n\n    f32[3] bbox min, f32[3] bbox max -> center/extent (24 bytes)\n    u32 textureCount\n    char[256] x textureCount -- texture names (.tga)\n    u32 lightmapCount -- pages <name>_L<i>.dds\n    u32 hasRoot\n    if hasRoot: root node (recursive, see read_node)\n\nEach node:\n    f32[3] center (ignored)\n    f32[6] view bbox (ignored)\n    f32[6] collision bbox (ignored)\n    u32 meshGroupCount\n    repeat meshGroupCount:\n        u32 textureIndex\n        u32 meshCount\n        repeat meshCount: mesh (see read_mesh)\n    u32 collisionType -- if 1, a collision mesh follows:\n                              u32 vertexCount, f32[3]*count,\n                              u32 faceCount, u16[3]*count\n    8x { u32 hasChild; if hasChild: nodo hijo recursivo }\n\nEach mesh (read_mesh):\n    u32 lightmapIndex (0xFFFFFFFF or >=4096 => no lightmap)\n    u32 vertexCount\n    repeat vertexCount:\n        f32[3] position, f32[3] normal, u32 boneId(ignored),\n        f32[2] uv, f32[2] lightmapUv\n    u32 faceCount\n    repeat faceCount: u16[3] indices\n\nExport the visual geometry AND the COLLISION mesh that the .dg itself brings.\nThat mesh is the one the game used to crash: it is much lighter than the\nvisual and has neither decoration nor detail, which is exactly what is needed\nfor a ConcavePolygonShape3D. It goes in a .collision.json next to the .glb, no\ninside the .glb, so that Godot does not import it as another mesh that would have\nwhat to hide\n\nUse the wall texture\nactual floor as albedo (dist/windows/data/entity/texture/, see README) and\ncombines the actual lightmap (<dg_dir>/<stem>_l<indice>.dds) as occlusion\nglTF texture over UV2 -- Godot multiplies it over the albedo\nimport, same effect as "color *= lm" in the original shader, without\nneed your own shader (see gltf_writer.py::add_material).\n\nUsage:\n    python convert_dg.py <entrada.dg> <salida.glb> [--texture-dir DIR]... [--lightmap-dir DIR]'

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from convert_smod import find_texture  # noqa: E402
from gltf_writer import GlbBuilder  # noqa: E402
from source_reader import Reader  # noqa: E402


def read_bbox(r: Reader):
    min_v = r.vec3()
    max_v = r.vec3()
    center = [(min_v[i] + max_v[i]) * 0.5 for i in range(3)]
    extent = [abs(max_v[i] - min_v[i]) * 0.5 for i in range(3)]
    return center, extent


def read_mesh(r: Reader, texture_index: int, textures: list) -> dict:
    lightmap_index_raw = r.u32()
    lightmap_index = lightmap_index_raw if lightmap_index_raw < 4096 else -1

    vertex_count = r.u32()
    if vertex_count > 250_000:
        r.ok = False
        return {}

    positions, normals, uvs, lightmap_uvs = [], [], [], []
    for _ in range(vertex_count):
        positions.append(r.vec3())
        normals.append(r.vec3())
        r.skip(4)  # bone id, unused
        uvs.append(r.vec2())
        lightmap_uvs.append(r.vec2())

    face_count = r.u32()
    if face_count > 500_000:
        r.ok = False
        return {}
    indices = [(r.u16(), r.u16(), r.u16()) for _ in range(face_count)]

    texture_name = textures[texture_index] if texture_index < len(textures) else ""
    return {
        "textureIndex": texture_index,
        "textureName": texture_name,
        "lightmapIndex": lightmap_index,
        "positions": positions,
        "normals": normals,
        "uvs": uvs,
        "lightmapUvs": lightmap_uvs,
        "indices": indices,
    }


def read_node(r: Reader, textures: list, meshes: list, collision: dict,
              depth: int = 0) -> bool:
    if depth > 64:
        return False

    r.skip(12)  # center
    r.skip(24)  # view bbox
    r.skip(24)  # collision bbox

    mesh_group_count = r.u32()
    if mesh_group_count > 4096 or not r.ok:
        return False

    for _ in range(mesh_group_count):
        texture_index = r.u32()
        mesh_count = r.u32()
        if mesh_count > 4096 or not r.ok:
            return False
        for _ in range(mesh_count):
            mesh = read_mesh(r, texture_index, textures)
            if not r.ok:
                return False
            meshes.append(mesh)

    collision_type = r.u32()
    if collision_type == 1:
        # The indices are LOCAL to this node of the octree, so we must
        # run them by the number of vertices already accumulated from other nodes;
        # If not, all nodes would point to the first one.
        base = len(collision["positions"])
        vertex_count = r.u32()
        if vertex_count > 250_000:
            return False
        for _ in range(vertex_count):
            collision["positions"].append(r.vec3())
        face_count = r.u32()
        if face_count > 500_000:
            return False
        for _ in range(face_count):
            collision["indices"].extend(
                (base + r.u16(), base + r.u16(), base + r.u16()))

    for _ in range(8):
        has_child = r.u32()
        if has_child > 0:
            if not read_node(r, textures, meshes, collision, depth + 1):
                return False

    return r.ok


def parse_dg(path: Path) -> dict:
    r = Reader(path.read_bytes())
    if len(r.data) < 36:
        raise ValueError(f"{path}: file too small")

    center, extent = read_bbox(r)

    texture_count = r.u32()
    if texture_count > 4096:
        raise ValueError(f"{path}: implausible texture count")
    textures = [r.string256() for _ in range(texture_count)]

    lightmap_count = r.u32()
    if lightmap_count > 4096:
        lightmap_count = 0
    has_root = r.u32()

    meshes = []
    collision = {"positions": [], "indices": []}
    if has_root > 0:
        read_node(r, textures, meshes, collision)

    if not meshes:
        raise ValueError(f"{path}: no meshes parsed (truncated or unsupported)")

    return {
        "center": center,
        "extent": extent,
        "textures": textures,
        "lightmapCount": lightmap_count,
        "meshes": meshes,
        "collision": collision,
    }


def _chunk_parts_by_material(parts: list[dict], max_materials: int) -> list[list[dict]]:
    'Divide the parts into groups of at most max_materials materials\n    UNIQUE each one -- a dungeon can have hundreds of combinations\n    different texture+lightmap (one per wall/lightmap page pair), very\n    above the limit of 256 surfaces of Godot per ArrayMesh. Each\n    group becomes its own mesh/node (see convert()); glTF/Godot\n    They accept several root nodes in the same scene without problem.'
    chunks: list[list[dict]] = []
    current: list[dict] = []
    seen: set = set()
    for part in parts:
        material_idx = part["material_idx"]
        if material_idx not in seen and len(seen) >= max_materials:
            chunks.append(current)
            current = []
            seen = set()
        seen.add(material_idx)
        current.append(part)
    if current:
        chunks.append(current)
    return chunks


## The collision goes in a JSON next to the .glb and not inside the .glb: messed up
## as one more mesh, Godot would import it, draw it and we would have to
## Hide it by hand for each use. Besides, it also prevents the .glb from growing to
## who only wants visual geometry.
def _write_collision(collision: dict, dst_glb: Path) -> None:
    positions = collision.get("positions") or []
    indices = collision.get("indices") or []
    dst = dst_glb.with_suffix(".collision.json")
    if not positions or not indices:
        # No data: an old file is deleted to avoid a collision
        # outdated from a previous conversion.
        dst.unlink(missing_ok=True)
        return
    dst.write_text(json.dumps({
        "positions": [c for p in positions for c in p],
        "indices": indices,
    }), encoding="utf-8")


def convert(src: Path, dst_glb: Path, texture_search_dirs: list[Path] | None = None,
            lightmap_dir: Path | None = None) -> dict:
    model = parse_dg(src)
    search_dirs = texture_search_dirs or [src.parent]
    lightmap_dir = lightmap_dir or src.parent
    _write_collision(model["collision"], dst_glb)

    builder = GlbBuilder()
    total_verts = 0
    total_faces = 0
    n_textured = 0
    n_lightmapped = 0
    parts = []

    for mesh in model["meshes"]:
        tex_path = find_texture(mesh["textureName"], search_dirs)
        if tex_path is not None:
            n_textured += 1

        lm_path = None
        if mesh["lightmapIndex"] >= 0:
            candidate = lightmap_dir / f"{src.stem}_l{mesh['lightmapIndex']}.dds"
            if candidate.exists():
                lm_path = candidate
                n_lightmapped += 1

        material_idx = builder.add_material(tex_path, lm_path)

        parts.append({
            "positions": mesh["positions"], "normals": mesh["normals"], "uvs": mesh["uvs"],
            "uvs2": mesh["lightmapUvs"] if lm_path is not None else None,
            "indices": [i for face in mesh["indices"] for i in face],
            "material_idx": material_idx,
        })
        total_verts += len(mesh["positions"])
        total_faces += len(mesh["indices"])

    chunks = _chunk_parts_by_material(parts, GlbBuilder.MAX_SURFACES)
    for i, chunk in enumerate(chunks):
        name = src.stem if len(chunks) == 1 else f"{src.stem}_part{i}"
        mesh_idx = builder.add_multi_part_mesh(name, chunk)
        builder.add_node(mesh_idx=mesh_idx, name=name)
    builder.save(dst_glb)

    return {
        "meshCount": len(model["meshes"]),
        "vertexCount": total_verts,
        "faceCount": total_faces,
        "texturedMeshCount": n_textured,
        "lightmappedMeshCount": n_lightmapped,
        "lightmapCount": model["lightmapCount"],
        "textureNamesReferenced": len(model["textures"]),
    }


def main() -> int:
    args = sys.argv[1:]
    texture_dirs = []
    while "--texture-dir" in args:
        i = args.index("--texture-dir")
        texture_dirs.append(Path(args[i + 1]))
        args = args[:i] + args[i + 2:]

    lightmap_dir = None
    if "--lightmap-dir" in args:
        i = args.index("--lightmap-dir")
        lightmap_dir = Path(args[i + 1])
        args = args[:i] + args[i + 2:]

    if len(args) != 2:
        print('Usage: convert_dg.py <input.dg> <output.glb> [--texture-dir DIR]... [--lightmap-dir DIR]', file=sys.stderr)
        return 1

    src, dst = Path(args[0]), Path(args[1])
    stats = convert(src, dst, texture_dirs or None, lightmap_dir)
    print(f"OK: {src.name} -> {dst} ({stats['meshCount']} meshes, {stats['vertexCount']} vertices, {stats['faceCount']}faces,{stats['texturedMeshCount']}/{stats['meshCount']}with real texture,{stats['lightmappedMeshCount']}/{stats['meshCount']}with real lightmap)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
