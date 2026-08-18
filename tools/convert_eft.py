#!/usr/bin/env python3
'Phase 1 converter: the source client .eft/.ef2/.ef3 (particle effect library) -> JSON.\n\nPython reimplementation of src/world/eft_loader.cpp::load_eft (engine\noriginal in C++), using the same reading format as\nsrc/world/eft_binary_reader.h. Binary, little-endian format:\n\n    char[3] signature("EFT" | "EF2" | "EF3")\n    u32 meshCount (max 256)\n    repeat meshCount: string_lp -- associated mesh names\n    u32 textureCount (max 512)\n    repeat textureCount: string_lp -- texture names\n    u32 effectCount (max 1024)\n    repeat effectCount: EftEffect (see read_effect)\n    u32 sequenceCount (max 256)\n    repeat sequenceCount: EftEffectSequence { string_lp name; records[] }\n\nstring_lp = u32 byteCount + bytes (a trailing NUL is discarded if present).\n\nEftEffect: see fields in src/world/eft_loader.h and read_effect() in\nsrc/world/eft_loader.cpp -- the reading order must be respected exactly.\nEF3 adds two extra i32s (unused + distanceScaleMode) before the\ncolor keyframes.\n\nUsage:\n    python convert_eft.py <entrada.eft> <salida.json>'

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from source_reader import Reader  # noqa: E402


def read_effect(r: Reader, fmt: str) -> dict:
    effect = {"name": r.string_length_prefixed()}

    effect["velocityRandomEnabled"] = [r.i32() != 0, r.i32() != 0, r.i32() != 0]
    effect["loop"] = r.i32() != 0
    effect["destinationBlend"] = r.i32()
    effect["velocityMode"] = r.i32()
    effect["sourceBlend"] = r.i32()
    effect["textureLoop"] = r.i32() != 0
    effect["meshIndex"] = r.i32()
    effect["motionPathEnabled"] = r.i32() != 0

    effect["delayPerFrame"] = r.f32()
    effect["emitRateMax"] = r.f32()
    effect["lifeMax"] = r.f32()
    effect["emitRateMin"] = r.f32()
    effect["lifeMin"] = r.f32()
    effect["emitterDuration"] = r.f32()
    effect["swirlSpeed"] = r.f32()
    effect["unknown18"] = r.f32()

    effect["emitPositionSpread"] = r.vec3()
    effect["acceleration"] = r.vec3()
    effect["emitOrigin"] = r.vec3()
    effect["velocityMin"] = r.vec3()
    effect["velocityMax"] = r.vec3()

    effect["baseAxis"] = r.i32()
    effect["gravityEnabled"] = r.i32() != 0
    effect["attractEnabled"] = r.i32() != 0
    effect["attractPoint"] = r.vec3()
    effect["attractStrength"] = r.f32()

    effect["angularVelocityRandom"] = r.i32() != 0
    effect["rotationEnabled"] = r.i32() != 0
    effect["angularVelocity"] = r.f32()
    effect["rotationAxis"] = r.i32()

    if fmt == "EF3":
        r.i32()  # unused
        effect["distanceScaleMode"] = r.i32()
    else:
        effect["distanceScaleMode"] = 0

    color_frame_count = r.count(100000)
    effect["colorFrames"] = []
    for _ in range(color_frame_count):
        if not r.ok:
            break
        rr, g, b, a, t = r.f32(), r.f32(), r.f32(), r.f32(), r.f32()
        effect["colorFrames"].append({"r": rr, "g": g, "b": b, "a": a, "time": t})

    velocity_scale_count = r.count(100000)
    effect["velocityScaleFrames"] = []
    for _ in range(velocity_scale_count):
        if not r.ok:
            break
        value, t = r.f32(), r.f32()
        effect["velocityScaleFrames"].append({"value": value, "time": t})

    scale_frame_count = r.count(100000)
    effect["scaleFrames"] = []
    for _ in range(scale_frame_count):
        if not r.ok:
            break
        mn, mx, t = r.f32(), r.f32(), r.f32()
        effect["scaleFrames"].append({"min": mn, "max": mx, "time": t})

    effect["mirrorTexture"] = r.i32() != 0
    effect["initialRotationAxis"] = r.i32()
    effect["initialRotationMinDegrees"] = r.i32()
    effect["initialRotationMaxDegrees"] = r.i32()

    texture_id_count = r.count(100000)
    effect["textureIds"] = []
    for _ in range(texture_id_count):
        if not r.ok:
            break
        effect["textureIds"].append(r.i32())

    return effect


def read_sequence(r: Reader) -> dict:
    sequence = {"name": r.string_length_prefixed()}
    record_count = r.count(100000)
    sequence["records"] = []
    for _ in range(record_count):
        if not r.ok:
            break
        effect_id, t = r.i32(), r.f32()
        sequence["records"].append({"effectId": effect_id, "time": t})
    return sequence


def parse_eft(path: Path) -> dict:
    r = Reader(path.read_bytes())
    if len(r.data) < 3:
        raise ValueError(f"{path}: file too small")

    signature = r.bytes(3).decode("latin-1")
    if signature not in ("EFT", "EF2", "EF3"):
        raise ValueError(f"{path}: unknown signature {signature!r}")

    mesh_count = r.count(256)
    mesh_names = [r.string_length_prefixed() for _ in range(mesh_count) if r.ok]

    texture_count = r.count(512)
    texture_names = [r.string_length_prefixed() for _ in range(texture_count) if r.ok]

    effect_count = r.count(1024)
    effects = []
    for _ in range(effect_count):
        if not r.ok:
            break
        effects.append(read_effect(r, signature))

    sequence_count = r.count(256)
    sequences = []
    for _ in range(sequence_count):
        if not r.ok:
            break
        sequences.append(read_sequence(r))

    if not r.ok:
        raise ValueError(f"{path}: truncated/corrupt file")

    return {
        "format": signature,
        "meshNames": mesh_names,
        "textureNames": texture_names,
        "effects": effects,
        "sequences": sequences,
    }


def main() -> int:
    if len(sys.argv) != 3:
        print('Usage: convert_eft.py <input.eft> <output.json>', file=sys.stderr)
        return 1

    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    result = parse_eft(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"OK: {src.name} -> {dst}(format={result['format']}, {len(result['effects'])}effects,{len(result['sequences'])}sequences,{len(result['textureNames'])}textures,{len(result['meshNames'])}tights)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
