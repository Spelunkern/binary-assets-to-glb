#!/usr/bin/env python3
'the source client .3DE (particle effect mesh) -> JSON.\n\nPython reimplementation of src/world/eft_mesh_loader.cpp::load_eft_mesh\n(original C++ engine). It is the geometry that the effects use with\nmeshIndex >= 0: instead of drawing a square billboard, the particle\ndraw this mesh (a waterfall, a lightning bolt, a cone of fire...).\n\nBinary format, little-endian, WITHOUT magic/signature -- boots right with\ntexture name:\n\n    string textureName # u32 byteLen + bytes, a trailing NUL is discarded\n    u32 vertexCount # sanity: > 1_000_000 -> invalid file\n    repeat vertexCount:\n        f32[3] position\n        i32 boneId # is discarded (effect meshes have no skin)\n        f32[2] uv\n    u32 faceCount # sanity: > 1_000_000 -> invalid file\n    repeat faceCount:\n        u16[3] indices # if any index >= vertexCount -> invalid file\n    -- animation per vertex, OPTIONAL (only if bytes remain) --\n    if bytes left: i32 maxKeyframe\n    if bytes left:\n        u32 frameCount # sanity: > 100_000 -> invalid file\n        repeat frameCount:\n            i32 key # ABSOLUTE keyframe tick\n            repeat vertexCount: # the same count as the base mesh, not its own\n                f32[3] position\n                f32[2] uv # NOTE: without boneId here (vertex base=24 bytes, frame=20)\n\nEach frame brings the FULL set of vertices (they are not deltas, just like\n.vani). Keyframes are "sparse": the engine linearly interpolates between\nthe previous and the next. maxKeyframe is inclusive, so the period\nis maxKeyframe + 1 ticks (read from file, not calculated), at 30 Hz. See\nsample_mesh_frame_index in effect_particle_system.cpp.\n\ntextureName is saved but the original engine does NOT use it for rendering --\ntexture leaves the EftEffect textureIds referencing the mesh\n(see select_texture_layer_for). It is issued the same, for traceability.\n\nUsage:\n    python convert_3de.py <entrada.3de> <salida.json>'

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from source_reader import Reader  # noqa: E402

MAX_VERTICES = 1_000_000
MAX_FACES = 1_000_000
MAX_FRAMES = 100_000


def parse_3de(path: Path) -> dict:
    data = path.read_bytes()
    if not data:
        raise ValueError(f'{path}: empty file')

    r = Reader(data)
    texture_name = r.string_length_prefixed()

    vertex_count = r.count(MAX_VERTICES)
    if not r.ok:
        raise ValueError(f'{path}: invalid vertexCount')

    positions, uvs = [], []
    for _ in range(vertex_count):
        positions.append(r.vec3())
        r.skip(4)  # boneId, sin uso en mallas de efecto
        uvs.append(r.vec2())
    if not r.ok:
        raise ValueError(f"{path}: truncado leyendo vertices")

    face_count = r.count(MAX_FACES)
    if not r.ok:
        raise ValueError(f'{path}: invalid faceCount')

    indices = []
    for _ in range(face_count):
        tri = (r.u16(), r.u16(), r.u16())
        if not r.ok:
            raise ValueError(f'{path}: truncated reading faces')
        if any(i >= vertex_count for i in tri):
            raise ValueError(f"{path}: indice de cara fuera de rango")
        indices.extend(tri)

    # The two animation blocks are optional and are checked by
    # separate -- a file can take maxKeyframe and end there.
    max_keyframe = 0
    frames = []
    if r.pos < len(data):
        max_keyframe = r.i32()
    if r.ok and r.pos < len(data):
        frame_count = r.count(MAX_FRAMES)
        if not r.ok:
            raise ValueError(f'{path}: invalid frameCount')
        for _ in range(frame_count):
            key = r.i32()
            frame_positions, frame_uvs = [], []
            for _ in range(vertex_count):
                frame_positions.append(r.vec3())
                frame_uvs.append(r.vec2())
            if not r.ok:
                raise ValueError(f"{path}: truncado leyendo frames")
            frames.append({"key": key, "positions": frame_positions, "uvs": frame_uvs})

    return {
        "textureName": texture_name,
        "positions": positions,
        "uvs": uvs,
        "indices": indices,
        "maxKeyframe": max_keyframe,
        "frames": frames,
    }


def main() -> int:
    if len(sys.argv) != 3:
        print('Usage: convert_3de.py <input.3de> <output.json>', file=sys.stderr)
        return 1

    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    result = parse_3de(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(result), encoding="utf-8")
    print(f"OK: {src.name} -> {dst} ({len(result['positions'])} vertices, {len(result['indices']) // 3}faces,{len(result['frames'])} frames, maxKeyframe={result['maxKeyframe']}, texture ={result['textureName']!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
