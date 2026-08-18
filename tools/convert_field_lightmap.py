#!/usr/bin/env python3
'Converts the lightmap + splat-weights "field" system of the terrain\nopen world (dist/windows/data/world/field/<mapId>/) to PNG.\n\nReimplementation in Python of the loading logic in src/main.cpp (search\naround "Field alpha splat masks") + ResearchRuntime::field_lightmap_paths\n/field_alpha_mask_paths in src/runtime/reference_runtime.cpp. By map:\n\n  - Section grid: 2x2 if mapSize >= 1536, otherwise 1x1. Suffixes of\n    file "00","01","10","11" = "<secZ><secX>" (first digit row,\n    second column) -- same order used by the original shader\n    (maskBase = secZ*sections + secX).\n  - By section: "<stem>_<sec>_l.dds" = baked lightmap (RGB) that is\n    MULTIPLY on the final color of the terrain.\n  - Per section: "<stem>_<sec>_a{1..7}.dds" = a blend weight per layer of\n    terrain (index 1..7, layer 0/base NEVER has a mask -- see\n    "bit 0 = base layer, never blended" in main.cpp). The weight lives in the\n    ALPHA channel of each file (not in RGB). 4 pesos are packaged per\n    RGBA texture: layers 1-4 -> weights0.rgba, layers 5-7(-8) -> weights1.rgba\n    (channel = (n-1) & 3, texture = (n-1) >> 2, same as original).\n\nOutput by section: "<out_prefix>_<sec>_l.png" (lightmap),\n"<out_prefix>_<sec>_w0.png" / "_w1.png" (packaged pesos), plus a\nmanifest.json with the grid and which layers have real mask.\n\nUsage:\n    python convert_field_lightmap.py <mapStem> <mapSize> <out_dir> [--field-root DIR]'

import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from texture_utils import load_rgba_debled  # noqa: E402

DEFAULT_FIELD_ROOT = Path(
    os.environ.get(
        "ASSET_FIELD_ROOT",
        str(Path(os.environ.get("ASSET_DIST_ROOT", "data/source_dist")) / "world" / "field"),
    )
)
MASK_LAYERS = 8  # kFieldAlphaMaskLayers -- indices 0..7, 0 = base, nunca tiene mask


def section_suffixes(sections: int) -> list:
    if sections <= 1:
        return ["00"]
    return ["00", "01", "10", "11"]


def pack_weights(field_dir: Path, stem: str, suffix: str, size: tuple) -> tuple:
    'Returns (weights0_img, weights1_img, layer_flags) -- RGBA images\n    with the weight of each layer in its channel, and what bits (layers) had mask\n    real on disk.'
    packed = [np.zeros((size[1], size[0], 4), dtype=np.uint8) for _ in range(2)]
    layer_flags = 0

    for n in range(1, MASK_LAYERS):
        src = field_dir / f"{stem}_{suffix}_a{n}.dds"
        if not src.exists():
            continue
        layer_flags |= 1 << n

        img = load_rgba_debled(src, debleed=False)
        if img.size != size:
            img = img.resize(size, Image.BILINEAR)
        alpha = np.array(img.getchannel("A"))

        channel = (n - 1) & 3
        part = (n - 1) >> 2
        packed[part][:, :, channel] = alpha

    imgs = [Image.fromarray(p, mode="RGBA") for p in packed]
    return imgs[0], imgs[1], layer_flags


def convert(map_stem: str, map_size: int, out_dir: Path, field_root: Path | None = None) -> dict:
    field_root = field_root or DEFAULT_FIELD_ROOT
    field_dir = field_root / map_stem
    if not field_dir.is_dir():
        raise FileNotFoundError(f"no existe {field_dir}")

    sections = 2 if map_size >= 1536 else 1
    suffixes = section_suffixes(sections)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {"sections": sections, "entries": []}
    any_lightmap = False
    any_weights = False

    for suffix in suffixes:
        entry = {"suffix": suffix, "lightmap": None, "weights0": None, "weights1": None, "layerFlags": 0}

        lm_src = field_dir / f"{map_stem}_{suffix}_l.dds"
        size = (256, 256)
        if lm_src.exists():
            lm_img = load_rgba_debled(lm_src, debleed=False)
            size = lm_img.size
            lm_dst = out_dir / f"{map_stem}_field_{suffix}_l.png"
            lm_img.save(lm_dst, format="PNG")
            entry["lightmap"] = lm_dst.name
            any_lightmap = True

        w0_img, w1_img, layer_flags = pack_weights(field_dir, map_stem, suffix, size)
        if layer_flags != 0:
            w0_dst = out_dir / f"{map_stem}_field_{suffix}_w0.png"
            w1_dst = out_dir / f"{map_stem}_field_{suffix}_w1.png"
            w0_img.save(w0_dst, format="PNG")
            w1_img.save(w1_dst, format="PNG")
            entry["weights0"] = w0_dst.name
            entry["weights1"] = w1_dst.name
            entry["layerFlags"] = layer_flags
            any_weights = True

        manifest["entries"].append(entry)

    manifest["hasData"] = any_lightmap or any_weights
    manifest_path = out_dir / f"{map_stem}_field_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    return manifest


def main() -> int:
    args = sys.argv[1:]
    field_root = None
    if "--field-root" in args:
        i = args.index("--field-root")
        field_root = Path(args[i + 1])
        args = args[:i] + args[i + 2:]

    if len(args) != 3:
        print('Usage: convert_field_lightmap.py <mapStem> <mapSize> <out_dir> [--field-root DIR]', file=sys.stderr)
        return 1

    map_stem, map_size, out_dir = args[0], int(args[1]), Path(args[2])
    manifest = convert(map_stem, map_size, out_dir, field_root)

    n_lm = sum(1 for e in manifest["entries"] if e["lightmap"])
    n_w = sum(1 for e in manifest["entries"] if e["weights0"])
    print(f"OK: map{map_stem} -> {out_dir} ({manifest['sections']}x{manifest['sections']}sections,{n_lm} lightmaps, {n_w} pares de pesos)")
    for e in manifest["entries"]:
        flags = [n for n in range(1, MASK_LAYERS) if e["layerFlags"] & (1 << n)]
        print(f"  section {e['suffix']}: lightmap={bool(e['lightmap'])} weighted_layers={flags}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
