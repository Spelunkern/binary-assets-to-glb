#!/usr/bin/env python3
'Convert source textures to canonical PNG files while preserving every RGBA\nchannel and the path relative to the project data directory.\n\nGodot applies GPU compression when importing, so source DDS files are never\nruntime assets. This generic helper preserves hidden RGB by default because it\ncannot infer whether alpha means visibility, glow, or packed data. Pass\n--alpha-cutout only for textures whose transparent pixels are discarded;\nthat safely dilates hidden RGB and prevents mipmap edge halos.\n\nUsage:\n    python copy_texture.py [--data-root SOURCE_ROOT] [--alpha-cutout] <texture.dds> [more ...]'

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from texture_utils import load_rgba_debled  # noqa: E402

DEFAULT_SRC_ROOT = Path(os.environ.get("ASSET_SOURCE_ROOT", "data/source"))
DEFAULT_DST_ROOT = Path(__file__).parent.parent / "data"


def convert_one(src_root: Path, dst_root: Path, rel_or_abs: str,
                alpha_cutout: bool = False) -> Path:
    '''Copy one texture without changing alpha semantics by default.

    Set ``alpha_cutout`` only for a material known to discard transparent
    pixels. It prevents colour fringes in generated mipmaps while preserving
    the source alpha channel.'''
    src = Path(rel_or_abs)
    if not src.is_absolute():
        src = src_root / rel_or_abs

    rel = src.relative_to(src_root) if src.is_relative_to(src_root) else Path(src.name)
    dst = (dst_root / rel).with_suffix(".png")
    dst.parent.mkdir(parents=True, exist_ok=True)

    # This generic helper has no material metadata, so callers opt in only
    # when they know that alpha is a visibility cutout.
    img = load_rgba_debled(src, debleed=alpha_cutout)
    img.save(dst, format="PNG")
    return dst


def main() -> int:
    args = sys.argv[1:]
    src_root = DEFAULT_SRC_ROOT
    alpha_cutout = False
    if args and args[0] == "--data-root":
        src_root = Path(args[1])
        args = args[2:]

    if args and args[0] == "--alpha-cutout":
        alpha_cutout = True
        args = args[1:]

    if not args:
        print(__doc__, file=sys.stderr)
        return 1

    for a in args:
        dst = convert_one(src_root, DEFAULT_DST_ROOT, a, alpha_cutout)
        print(f"OK: {a} -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
