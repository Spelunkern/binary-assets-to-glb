#!/usr/bin/env python3
'Phase 2 converter: data/monster/monster.csv + monsterdata.csv -> .txt tables.\n\nPython reimplementation of MonsterManager::load_catalog (original engine,\nsrc/character/monster_manager.cpp). They are two CSVs with a header (it is discarded\nthe first line of each):\n\nmonster.csv (one row per visual part of a model; multiple parts per\nmodelIndex, sorted by objectIndex): columns used (0-indexed)\n    [0] modelIndex [2] objectIndex [4] meshName (.3dc, in monster/3dc/)\n    [5] textureName (.dds, in monster/dds/)\n    [7] walkAni [8] runAni [9] attack1Ani [10] attack2Ani [11] attack3Ani\n    [12] deathAni [13] breathAni [14] damageAni [15] idleAni (in monster/ani/)\nRows with less than 16 columns are discarded (same as the original).\n\nmonsterdata.csv (one row per monster/catalog): columns used\n    [0] monsterId [1] name [2] modelIndex [3] size\nOnly entries whose modelIndex has rows in monster.csv are kept\n(same filter as the original).\n\nOutput: two flat tables (see tools/data_table.py)\n    <destino>/catalog.txt one row per monster\n    <destino>/visuals.txt one row per visual part\n\nNo index table is output. The old JSON had a catalogByMobId that was\npure duplication -- monsterId -> row number -- and exits a loop at\nload, both in the pipeline (tools/creature_catalog.py) and in the game\n(scripts/creature_catalog.gd).\n\nUsage:\n    python convert_monster_catalog.py <data_root> <destino>\n    (data_root = folder containing "monster/monster.csv" etc., e.g.\n    "data/" from the original repo)'

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from creature_catalog import MONSTER_COLUMNS, VISUAL_COLUMNS  # noqa: E402
from data_table import write_table  # noqa: E402


def parse_csv_rows(path: Path) -> list:
    with path.open("r", encoding="latin-1", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    return rows[1:] if rows else []  # skip header


def load_monster_catalog(data_root: Path) -> dict:
    monster_root = data_root / "monster"

    visual_rows = {}  # modelIndex(str) -> [part dict]
    for row in parse_csv_rows(monster_root / "monster.csv"):
        if len(row) < 16:
            continue
        model_index = int(row[0])
        part = {
            # The modelIndex goes INSIDE the part and not just as a key to the part.
            # grouping: in the flat table each row has to say what
            # model belongs.
            "modelIndex": model_index,
            "objectIndex": int(row[2]),
            "meshName": row[4].strip(),
            "textureName": row[5].strip(),
            "walkAni": row[7].strip(),
            "runAni": row[8].strip(),
            "attack1Ani": row[9].strip(),
            "attack2Ani": row[10].strip(),
            "attack3Ani": row[11].strip(),
            "deathAni": row[12].strip(),
            "breathAni": row[13].strip(),
            "damageAni": row[14].strip(),
            "idleAni": row[15].strip(),
        }
        visual_rows.setdefault(str(model_index), []).append(part)

    for parts in visual_rows.values():
        parts.sort(key=lambda p: p["objectIndex"])

    catalog = []
    for row in parse_csv_rows(monster_root / "monsterdata.csv"):
        if len(row) < 4:
            continue
        monster_id = int(row[0])
        model_index = int(row[2])
        if str(model_index) not in visual_rows:
            continue
        entry = {
            "monsterId": monster_id,
            "name": row[1].strip(),
            "modelIndex": model_index,
            "size": int(row[3]) if row[3].strip().isdigit() else 0,
        }
        catalog.append(entry)

    return {"catalog": catalog, "visualRows": visual_rows}


def write_catalog(result: dict, dst_dir: Path) -> None:
    write_table(dst_dir / "catalog.txt", MONSTER_COLUMNS, result["catalog"])
    write_table(dst_dir / "visuals.txt", VISUAL_COLUMNS,
                [p for parts in result["visualRows"].values() for p in parts])


def main() -> int:
    if len(sys.argv) != 3:
        print('Usage: convert_monster_catalog.py <data_root> <destino>', file=sys.stderr)
        return 1

    data_root, dst = Path(sys.argv[1]), Path(sys.argv[2])
    result = load_monster_catalog(data_root)
    write_catalog(result, dst)
    print(f"OK: {data_root} -> {dst} ({len(result['catalog'])}monsters,{len(result['visualRows'])}models with visual parts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
