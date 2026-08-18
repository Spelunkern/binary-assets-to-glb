#!/usr/bin/env python3
'Saves the authored grid of the layers from their converted GLB.\n\nGodot can reorder vertices when importing a GLB. The layers are born as\nan exact five-column grid, so inferring it later by Y/X fails\nin variants with different folds or proportions. The GLB written by\nour converter still preserves the order of .3dc; this script extracts it and\ngenerates data/mantles/profiles/<prefijo>.cloth.json for ClothSim.'

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
MESHES = ROOT / "data" / "mantles" / "meshes"
OUT = ROOT / "data" / "mantles" / "profiles"
PREFIXES = ("demf", "demr", "dewf", "dewr", "elmm", "elmr", "elwm", "elwr",
            "humf", "humm", "huwf", "huwm", "vimm", "vimr", "viwm", "viwr")
COLS = 5
CALIBRATED_PREFIXES = {"humf", "huwf"}
# Settings validated from the temporary panel. They are hitch corrections,
# not from the simulation; a design exception will go into `cloakOffsets`.
SHOULDER_OFFSETS = {"humm": [0.0, 0.011, -0.013]}

COMPONENTS = {
    5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2),
    5125: ("I", 4), 5126: ("f", 4),
}
TYPE_SIZE = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def read_glb(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    if len(raw) < 20 or raw[:4] != b"glTF":
        raise ValueError('not a GLB')
    total = struct.unpack_from("<I", raw, 8)[0]
    offset, doc, blob = 12, None, b""
    while offset + 8 <= min(total, len(raw)):
        size, kind = struct.unpack_from("<II", raw, offset)
        chunk = raw[offset + 8:offset + 8 + size]
        if kind == 0x4E4F534A:
            doc = json.loads(chunk.decode("utf-8"))
        elif kind == 0x004E4942:
            blob = chunk
        offset += 8 + size
    if doc is None:
        raise ValueError('without JSON chunk')
    return doc, blob


def accessor(doc: dict, blob: bytes, index: int) -> list:
    acc = doc["accessors"][index]
    view = doc["bufferViews"][acc["bufferView"]]
    code, component_size = COMPONENTS[acc["componentType"]]
    components = TYPE_SIZE[acc["type"]]
    element_size = component_size * components
    stride = view.get("byteStride", element_size)
    offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    fmt = "<" + code * components
    out = []
    for item in range(acc["count"]):
        value = struct.unpack_from(fmt, blob, offset + item * stride)
        out.append(value[0] if components == 1 else list(value))
    return out


def profile_from_glb(path: Path, prefix: str) -> dict:
    doc, blob = read_glb(path)
    primitive = doc["meshes"][0]["primitives"][0]
    attrs = primitive["attributes"]
    positions = accessor(doc, blob, attrs["POSITION"])
    normals = accessor(doc, blob, attrs["NORMAL"])
    uvs = accessor(doc, blob, attrs["TEXCOORD_0"])
    indices = accessor(doc, blob, primitive["indices"])
    if len(positions) < COLS * 2 or len(positions) % COLS:
        raise ValueError('%d vertices do not form a %d grid' % (len(positions), COLS))
    calibrated = prefix in CALIBRATED_PREFIXES
    profile = {"version": 1, "columns": COLS, "positions": positions,
            "normals": normals, "uvs": uvs, "indices": indices,
            # humf/huwf are the already validated visual reference. The rest use
            # the actual seam between shoulder and fabric; It doesn't get crushed with tuning
            # human that had been applied globally.
            "pinSeam": 0.0 if calibrated else 1.0,
            # The small collider was a specific visual adjustment of
            # humf. Outside of these rigs, the original solver is preferred without
            # artificial torso thrust until an anatomical profile is obtained.
            "colliderEnabled": calibrated}
    if prefix in SHOULDER_OFFSETS:
        profile["shoulderOffset"] = SHOULDER_OFFSETS[prefix]
    return profile


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    written = 0
    for prefix in PREFIXES:
        source = MESHES / (prefix + "_body.glb")
        if not source.exists():
            print('SKIP %s: %s missing' % (prefix, source.name))
            continue
        try:
            profile = profile_from_glb(source, prefix)
        except (KeyError, IndexError, ValueError, struct.error) as exc:
            print("SKIP %s: %s" % (prefix, exc))
            continue
        target = OUT / (prefix + ".cloth.json")
        target.write_text(json.dumps(profile, separators=(",", ":")), encoding="utf-8")
        print("OK %s: %d vertices, %d filas" % (
            prefix, len(profile["positions"]), len(profile["positions"]) // COLS))
        written += 1
    print("cloth profiles: %d" % written)
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
