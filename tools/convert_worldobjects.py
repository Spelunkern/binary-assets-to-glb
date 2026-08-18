#!/usr/bin/env python3
'Converts static .3DO models of world objects.\n\nThe drops and the arrow projectile are .3DO mesh like the weapons, but not\nbelong to an equipment slot. Each model uses the DDS with the same name\nbase within the source. The output is in data/effects/worldobjects/ and\nGlbBuilder externalizes the DDS as a shared canonical PNG in data/textures/.\n\nUsage:\n    python tools/convert_worldobjects.py <directorio-Item>'

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from convert_3do import parse_3do, write_glb  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = PROJECT_ROOT / "data" / "effects" / "worldobjects"


def _files_by_stem(directory: Path, suffix: str) -> dict[str, Path]:
    return {path.stem.lower(): path for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() == suffix}


def convert(source_root: Path) -> int:
    mesh_root = source_root / "3DO"
    texture_root = source_root / "DDS"
    if not mesh_root.is_dir() or not texture_root.is_dir():
        print('3DO and DDS folders are expected inside %s' % source_root,
              file=sys.stderr)
        return 1

    meshes = _files_by_stem(mesh_root, ".3do")
    textures = _files_by_stem(texture_root, ".dds")
    if not meshes:
        print("No hay modelos .3DO en %s" % mesh_root, file=sys.stderr)
        return 1

    missing = sorted(stem for stem in meshes if stem not in textures)
    if missing:
        print('Missing DDS homonymous for: %s' % ", ".join(missing),
              file=sys.stderr)
        return 1

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    for stem in sorted(meshes):
        model_path = meshes[stem]
        texture_path = textures[stem]
        try:
            model = parse_3do(model_path)
        except ValueError as exc:
            print("SKIP %s: %s" % (model_path.name, exc), file=sys.stderr)
            return 1
        output_path = OUTPUT_ROOT / (stem + ".glb")
        write_glb(model, output_path, texture_path)
        print("OK: %s + %s -> %s" % (model_path.name, texture_path.name,
                                       output_path.relative_to(PROJECT_ROOT)))
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 1
    return convert(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
