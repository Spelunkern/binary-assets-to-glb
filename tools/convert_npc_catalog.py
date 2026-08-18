#!/usr/bin/env python3
'Phase 2 converter: data/npc/npc.csv + npcdata.csv -> JSON.\n\nPython reimplementation of NpcManager::load_catalog (original engine,\nsrc/character/npc_manager.cpp). Same two CSV scheme as\nconvert_monster_catalog.py; columns used (0-indexed):\n\nnpc.csv (rows < 14 columns are discarded):\n    [0] modelIndex [2] objectIndex [3] meshName [4] textureName\n    [5] walkAni [6] runAni [7] attack1Ani [8] attack2Ani [9] attack3Ani\n    [10] deathAni [11] breathAni [12] damageAni [13] idleAni\n\nnpcdata.csv (rows < 7 columns are discarded):\n    [0] npcIndex [1] npcId(string) [2] npcType [3] npcTypeName\n    [4] npcTypeId [5] modelIndex [6] name\n\nThe .svmap saves (npcType, npcId) per group -- that npcId is actually\nnpcTypeId of this table (see the comment in npc_manager.cpp). The key of\ncatalog is "npcType:npcTypeId" to match the format it outputs\nconvert_svmap.py (npcType/npcId per group).\n\nUsage:\n    python convert_npc_catalog.py <data_root> <destino>\n\nOutput: two flat tables, same as convert_monster_catalog.py --\n<destino>/catalog.txt and <destino>/visuals.txt, without index table.'

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from creature_catalog import NPC_COLUMNS, VISUAL_COLUMNS  # noqa: E402
from data_table import write_table  # noqa: E402


def parse_csv_rows(path: Path) -> list:
    with path.open("r", encoding="latin-1", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    return rows[1:] if rows else []


def load_npc_catalog(data_root: Path) -> dict:
    npc_root = data_root / "npc"

    visual_rows = {}
    for row in parse_csv_rows(npc_root / "npc.csv"):
        if len(row) < 14:
            continue
        model_index = int(row[0])
        part = {
            # The modelIndex goes INSIDE the part and not just as a key to the part.
            # grouping: in the flat table each row has to say what
            # model belongs.
            "modelIndex": model_index,
            "objectIndex": int(row[2]),
            "meshName": row[3].strip(),
            "textureName": row[4].strip(),
            "walkAni": row[5].strip(),
            "runAni": row[6].strip(),
            "attack1Ani": row[7].strip(),
            "attack2Ani": row[8].strip(),
            "attack3Ani": row[9].strip(),
            "deathAni": row[10].strip(),
            "breathAni": row[11].strip(),
            "damageAni": row[12].strip(),
            "idleAni": row[13].strip(),
        }
        visual_rows.setdefault(str(model_index), []).append(part)

    for parts in visual_rows.values():
        parts.sort(key=lambda p: p["objectIndex"])

    catalog = []
    for row in parse_csv_rows(npc_root / "npcdata.csv"):
        if len(row) < 7:
            continue
        model_index = int(row[5])
        if str(model_index) not in visual_rows:
            continue
        npc_type = int(row[2])
        npc_type_id = int(row[4])
        entry = {
            "npcIndex": int(row[0]),
            "npcId": row[1].strip(),
            "npcType": npc_type,
            "npcTypeName": row[3].strip(),
            "npcTypeId": npc_type_id,
            "modelIndex": model_index,
            "name": row[6].strip(),
        }
        catalog.append(entry)

    return {"catalog": catalog, "visualRows": visual_rows}


def write_catalog(result: dict, dst_dir: Path) -> None:
    write_table(dst_dir / "catalog.txt", NPC_COLUMNS, result["catalog"])
    write_table(dst_dir / "visuals.txt", VISUAL_COLUMNS,
                [p for parts in result["visualRows"].values() for p in parts])


def main() -> int:
    if len(sys.argv) != 3:
        print('Usage: convert_npc_catalog.py <data_root> <destino>', file=sys.stderr)
        return 1

    data_root, dst = Path(sys.argv[1]), Path(sys.argv[2])
    result = load_npc_catalog(data_root)
    write_catalog(result, dst)
    print(f"OK: {data_root} -> {dst} ({len(result['catalog'])} NPCs, {len(result['visualRows'])}models with visual parts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
