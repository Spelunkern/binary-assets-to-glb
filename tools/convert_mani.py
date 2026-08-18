#!/usr/bin/env python3
'the source client .mani (continuous rotation of a map object, e.g. a mill)\n-> JSON. Python reimplementation of src/world/mani_loader.cpp::load_mani\n(original C++ engine). Simple binary header, without geometry (unlike\nfrom .vani, which DOES come with vertex animation) -- only one axis and a speed of\ncontinuous rotation that the engine applies to the asset already positioned by the\n.wld (see maniInstances[].assetSection/assetName/maniName) and\nResearchRuntime::maniAnimationFor in reference_runtime.cpp:1119-1136):\n\n    (bytes 0..96, only some fields are read)\n    u32 version (offset 0, not used here)\n    u32 enableRotation (offset 60, !=0 -> true)\n    f32[3] rotationAxis (offset 64, in LOCAL space of the object)\n    f32 animationSpeed (offset 76, radians per motor tick)\n\nactual angular velocity = animationSpeed * kManiTicksPerSecond(30.0), see\nreference_runtime.cpp:36.1221 -- applied as radians/second around\nrotationAxis in LOCAL space of the object (rotate_object_local in Godot).\n\nUsage:\n    python convert_mani.py <entrada.mani> <salida.json>'

import json
import struct
import sys
from pathlib import Path


def parse_mani(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) < 96:
        raise ValueError(f"{path}: file too small ({len(data)} < 96)")

    enable_rotation = struct.unpack_from("<I", data, 60)[0] != 0
    rotation_axis = list(struct.unpack_from("<3f", data, 64))
    animation_speed = struct.unpack_from("<f", data, 76)[0]

    return {
        "enableRotation": enable_rotation,
        "rotationAxis": rotation_axis,
        "animationSpeed": animation_speed,
    }


def main() -> int:
    if len(sys.argv) != 3:
        print('Usage: convert_mani.py <input.mani> <output.json>', file=sys.stderr)
        return 1

    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    result = parse_mani(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(result), encoding="utf-8")
    print(f"OK: {src.name} -> {dst} (enableRotation={result['enableRotation']}, "
          f"axis={result['rotationAxis']}, speed={result['animationSpeed']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
