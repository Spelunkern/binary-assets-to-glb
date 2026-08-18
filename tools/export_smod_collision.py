#!/usr/bin/env python3
'Extracts the AUTHORIZED collision mesh from each .smod and leaves it as\n`<nombre>.collision.json` next to the already converted .glb.\n\nIt is a separate step from batch_convert_wld_objects.py on purpose: reconvert the\n.glb takes a long time and, above all, it changes files that Godot would have to reimport\nwhole. Only the .json are written here, which Godot reads hot with\nFileAccess and do not go through the importer.\n\nUsage:\n    python tools/export_smod_collision.py'

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from convert_smod import parse_smod, write_collision  # noqa: E402

DIST_ROOT = Path(os.environ.get("ASSET_DIST_ROOT", "data/source_dist"))
PROJECT_DATA_ROOT = Path(__file__).resolve().parent.parent / "data"

# Same folders as SECTION_FOLDERS in terrain_builder.gd. Ladder is read
# from "object" in the original data, but lives as "ladder" in the project.
# Even if it does not collide today, the data is exported and the decision to use it lives in
# the game, not in the pipeline.
FOLDERS = [
    ("building", "building"),
    ("shape", "shape"),
    ("tree", "tree"),
    ("object", "ladder"),
    ("grass", "grass"),
    ("vani", "vani"),
]


def main() -> int:
    total = with_collision = faces = 0
    for source_folder, project_folder in FOLDERS:
        src_dir = DIST_ROOT / "entity" / source_folder
        dst_dir = PROJECT_DATA_ROOT / "entity" / project_folder
        if not src_dir.is_dir() or not dst_dir.is_dir():
            continue
        for src in sorted(src_dir.glob("*.smod")):
            dst_glb = dst_dir / f"{src.stem.lower()}.glb"
            if not dst_glb.exists():
                continue  # This asset was not converted, so there is nothing to pair.
            total += 1
            try:
                collision = parse_smod(src)["collision"]
            except ValueError as exc:
                print(f"  omitido {src.name}: {exc}", file=sys.stderr)
                continue
            write_collision(collision, dst_glb)
            if collision["indices"]:
                with_collision += 1
                faces += len(collision["indices"]) // 3
        print(f"{project_folder}: listo")

    print(f'OK: {with_collision}/{total}assets with authored collision,{faces} triangulos en total')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
