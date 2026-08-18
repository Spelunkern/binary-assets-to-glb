#!/usr/bin/env python3
'Reading the already converted monster/NPC catalogs.\n\nEach category is TWO tables (see tools/data_table.py):\n\n    data/<categoria>/catalog.txt one row per monster/NPC\n    data/<categoria>/visuals.txt one row per visual PART of a model\n\nThere is no index table. The old JSON had a catalogByMobId/catalogByTypeKey\nwhich was pure duplication -- key -> row number -- and it resets in a loop\nupon loading, both here and in the game (scripts/creature_catalog.gd).'

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from data_table import read_table  # noqa: E402

## Columns of visuals.txt, in order. The nine animations are broadcast although
## They are not used today: they are part of the original data and cost nothing.
VISUAL_COLUMNS = ("modelIndex", "objectIndex", "meshName", "textureName",
                  "walkAni", "runAni", "attack1Ani", "attack2Ani", "attack3Ani",
                  "deathAni", "breathAni", "damageAni", "idleAni")

MONSTER_COLUMNS = ("monsterId", "name", "modelIndex", "size")
NPC_COLUMNS = ("npcIndex", "npcId", "npcType", "npcTypeName", "npcTypeId",
               "modelIndex", "name")


def load(catalog_dir: Path, key_field) -> dict:
    'Returns {"catalog": [...], "visualRows": {modelIndex: [partes]},\n    "lookup": {clave: numero de fila}}.\n\n    `key_field` receives a row from the catalog and returns its search key:\n    the monsterId for monsters, "npcType:npcTypeId" for NPCs.'
    visual_rows: dict = {}
    for part in read_table(catalog_dir / "visuals.txt"):
        visual_rows.setdefault(part["modelIndex"], []).append(part)
    for parts in visual_rows.values():
        parts.sort(key=lambda p: int(p["objectIndex"]))

    catalog = read_table(catalog_dir / "catalog.txt")
    lookup = {}
    for i, row in enumerate(catalog):
        # With repeated ids the first one wins, just like the original engine.
        lookup.setdefault(key_field(row), i)

    return {"catalog": catalog, "visualRows": visual_rows, "lookup": lookup}


def load_monsters(data_root: Path) -> dict:
    return load(data_root / "monster", lambda r: r["monsterId"])


def load_npcs(data_root: Path) -> dict:
    return load(data_root / "npc", lambda r: "%s:%s" % (r["npcType"], r["npcTypeId"]))
