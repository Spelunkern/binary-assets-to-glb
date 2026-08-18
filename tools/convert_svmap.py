#!/usr/bin/env python3
'Phase 1 prototype converter: the source client .svmap -> JSON.\n\nPython reimplementation of src/world/svmap_loader.cpp (original engine in\nC++). Binary, little-endian format:\n\n    int32 mapSize\n    bytes accessibility mask, (mapSize*mapSize/8) bytes\n    int32 cellSize (not used)\n    int32 ladderCount\n    Vec3[] ladders (12 bytes each)\n    int32 monsterAreaCount\n    repeat monsterAreaCount:\n        f32 minX,minY,minZ,maxX,maxY,maxZ\n        int32 spawnCount\n        repeat spawnCount: { u32 mobId, u32 count }\n    int32 npcGroupCount\n    repeat npcGroupCount:\n        int32 npcType\n        int32 npcId\n        int32 waypointCount\n        repeat waypointCount: { f32 x,y,z,yaw }\n\nUsage:\n    python convert_svmap.py <entrada.svmap> <salida.json>'

import json
import struct
import sys
from pathlib import Path


class Cursor:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.ok = True

    def _ensure(self, n: int) -> bool:
        if not self.ok or self.pos + n > len(self.data):
            self.ok = False
            return False
        return True

    def i32(self) -> int:
        if not self._ensure(4):
            return 0
        v = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def f32(self) -> float:
        if not self._ensure(4):
            return 0.0
        v = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def skip(self, n: int) -> None:
        if self._ensure(n):
            self.pos += n


def parse_svmap(path: Path) -> dict:
    data = path.read_bytes()
    cur = Cursor(data)

    map_size = cur.i32()
    if not cur.ok or map_size <= 0:
        raise ValueError(f"{path}: invalid mapSize")

    mask_bytes = (map_size * map_size) // 8
    cur.skip(mask_bytes)
    cur.i32()  # cellSize, unused

    ladder_count = cur.i32()
    if ladder_count > 0:
        cur.skip(ladder_count * 12)

    monster_areas = []
    area_count = cur.i32()
    if not cur.ok or area_count < 0:
        raise ValueError(f"{path}: invalid monster area count")
    for _ in range(area_count):
        min_x, min_y, min_z = cur.f32(), cur.f32(), cur.f32()
        max_x, max_y, max_z = cur.f32(), cur.f32(), cur.f32()
        spawn_count = cur.i32()
        if not cur.ok or spawn_count < 0:
            raise ValueError(f"{path}: invalid spawn count")
        mobs = []
        for _ in range(spawn_count):
            mob_id = cur.i32() & 0xFFFFFFFF
            count = cur.i32() & 0xFFFFFFFF
            mobs.append({"mobId": mob_id, "count": count})
        if mobs:
            monster_areas.append({
                "min": [min_x, min_y, min_z],
                "max": [max_x, max_y, max_z],
                "mobs": mobs,
            })

    npc_groups = []
    npc_group_count = cur.i32()
    if not cur.ok or npc_group_count < 0:
        raise ValueError(f"{path}: invalid npc group count")
    for _ in range(npc_group_count):
        npc_type = cur.i32()
        npc_id = cur.i32()
        waypoint_count = cur.i32()
        if not cur.ok or waypoint_count < 0:
            raise ValueError(f"{path}: invalid waypoint count")
        waypoints = []
        for _ in range(waypoint_count):
            x, y, z, yaw = cur.f32(), cur.f32(), cur.f32(), cur.f32()
            waypoints.append({"x": x, "y": y, "z": z, "yaw": yaw})
        if waypoints:
            npc_groups.append({
                "npcType": npc_type,
                "npcId": npc_id,
                "waypoints": waypoints,
            })

    if not cur.ok:
        raise ValueError(f"{path}: truncated/corrupt file")

    return {
        "mapSize": map_size,
        "monsterAreas": monster_areas,
        "npcGroups": npc_groups,
    }


def main() -> int:
    if len(sys.argv) != 3:
        print('Usage: convert_svmap.py <input.svmap> <output.json>', file=sys.stderr)
        return 1

    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    result = parse_svmap(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"OK: {src.name} -> {dst} ({len(result['npcGroups'])} grupos NPC, {len(result['monsterAreas'])}monster areas, mapSize={result['mapSize']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
