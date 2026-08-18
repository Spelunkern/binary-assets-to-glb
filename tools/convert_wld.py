#!/usr/bin/env python3
'Phase 1 prototype converter: the source client .wld / .dg map file -> JSON.\n\nPython reimplementation of src/world/wld_loader.cpp::analyze_wld (engine\noriginal in C++). Two variants according to the magic of 3 bytes at the beginning:\n\n  "FLD" -- open field map:\n    char[3] magic\n    u32 mapSize\n    u16[] heightSamples, (mapSize/2+1)^2 samples\n    u8[] terrainTextureMap, same count as heightSamples\n    u32 terrainLayerCount\n    repeat terrainLayerCount:\n        char[256] textureFileName\n        f32 tileSize\n        char[256] walkSoundFileName\n    -- "field tail": layout name + object sections (Building,\n       Shape, Tree, Grass, PrimaryVani, SecondaryVani, Dungeon, Ladder),\n       MAni, effects, music, sounds, portals, sky/fog. See\n       parse_field_tail in the original for the exact order.\n\n  "DUN" -- dungeon:\n    char[3] magic\n    char padding (offset 3, see read_string256 at offset 4 not offset 3\n              -- the original reads dungeonDgFileName at offset 4)\n    char[256] dungeonDgFileName (offset 4..260)\n    -- same object sections as the field tail, without heightmap/terrain\n       (the dungeon geometry lives in the corresponding .dg).\n\nEach "object section" (Building/Shape/.../Ladder) is:\n    u32 assetCount\n    char[256] assetName x assetCount\n    u32 instanceCount\n    repeat instanceCount (40 bytes):\n        i32 assetIndex\n        f32[3] position\n        f32[3] rotationForward\n        f32[3] rotationUp\n\nThis converter does NOT reconstruct byte-by-byte all intermediate sections\n(music zones/portals/etc. via skip_*) -- they are skipped with the same\nrecord size than the original so as not to lose alignment, but it is not\noutput to JSON unless they are relevant to the port (terrain, objects,\nportals, sky/fog, mani).\n\nUsage:\n    python convert_wld.py <entrada.wld|.dg> <salida.json>'

import json
import struct
import sys
from pathlib import Path


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def _need(self, n: int) -> None:
        if self.pos + n > len(self.data):
            raise ValueError(f"unexpected end of file at {self.pos} (+{n} > {len(self.data)})")

    def u32(self) -> int:
        self._need(4)
        v = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def i32(self) -> int:
        self._need(4)
        v = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def u16(self) -> int:
        self._need(2)
        v = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def f32(self) -> float:
        self._need(4)
        v = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def vec3(self) -> list:
        return [self.f32(), self.f32(), self.f32()]

    def skip(self, n: int) -> None:
        self._need(n)
        self.pos += n

    def bytes(self, n: int) -> bytes:
        self._need(n)
        v = self.data[self.pos:self.pos + n]
        self.pos += n
        return v

    def string256(self) -> str:
        raw = self.bytes(256)
        nul = raw.find(b"\0")
        if nul != -1:
            raw = raw[:nul]
        return raw.decode("latin-1")

    def eof(self) -> bool:
        return self.pos >= len(self.data)


def read_names(r: Reader) -> list:
    count = r.u32()
    if count > 100000:
        raise ValueError("implausible name count")
    return [r.string256() for _ in range(count)]


def read_object_instances(r: Reader) -> list:
    count = r.u32()
    if count > 1_000_000:
        raise ValueError("implausible instance count")
    instances = []
    for _ in range(count):
        asset_index = r.i32()
        position = r.vec3()
        rotation_forward = r.vec3()
        rotation_up = r.vec3()
        instances.append({
            "assetIndex": asset_index,
            "position": position,
            "rotationForward": rotation_forward,
            "rotationUp": rotation_up,
        })
    return instances


def read_object_section(r: Reader, name: str) -> dict:
    return {
        "name": name,
        "assets": read_names(r),
        "instances": read_object_instances(r),
    }


def read_mani_instances(r: Reader) -> list:
    count = r.u32()
    if count > 100000:
        raise ValueError("implausible mani instance count")
    instances = []
    for _ in range(count):
        instances.append({
            "buildingAssetId": r.i32(),
            "maniAssetIndex": r.i32(),
            "position": r.vec3(),
            "rotationForward": r.vec3(),
            "rotationUp": r.vec3(),
        })
    return instances


def canonical_name(name: str) -> str:
    clean = Path(str(name)).name.lower()
    for suffix in (".mani", ".smod", ".3dc", ".3de", ".dg"):
        if clean.endswith(suffix):
            clean = clean[:-len(suffix)]
    return clean


def audio_name(name: str) -> str:
    return Path(str(name)).name.lower().rsplit(".", 1)[0].strip()


def resolve_mani_instances(mani_assets: list, mani_instances: list, object_sections: list) -> list:
    asset_assets = []
    for section in object_sections:
        if section.get("name") == "Building":
            asset_assets = section.get("assets", [])
            break

    resolved = []
    for inst in mani_instances:
        asset_id = int(inst.get("buildingAssetId", -1))
        mani_id = int(inst.get("maniAssetIndex", -1))
        asset_name = canonical_name(asset_assets[asset_id]) \
            if 0 <= asset_id < len(asset_assets) else ""
        mani_name = canonical_name(mani_assets[mani_id]) \
            if 0 <= mani_id < len(mani_assets) else ""
        if not asset_name or not mani_name:
            continue
        resolved.append({
            "assetSection": "Building",
            "assetName": asset_name,
            "maniName": mani_name,
            "position": inst["position"],
            "rotationForward": inst["rotationForward"],
            "rotationUp": inst["rotationUp"],
        })
    return resolved


def resolve_music_zones(audio_assets: list, music_zones: list) -> list:
    resolved = []
    for zone in music_zones:
        asset_id = int(zone.get("audioAssetIndex", -1))
        name = audio_name(audio_assets[asset_id]) \
            if 0 <= asset_id < len(audio_assets) else ""
        if not name:
            continue
        resolved.append({
            "box": zone["box"],
            "radius": zone["radius"],
            "musicName": name,
        })
    return resolved


def resolve_sound_effects(audio_assets: list, sound_effects: list) -> list:
    resolved = []
    for sound in sound_effects:
        asset_id = int(sound.get("audioAssetIndex", -1))
        name = audio_name(audio_assets[asset_id]) \
            if 0 <= asset_id < len(audio_assets) else ""
        if not name:
            continue
        resolved.append({
            "soundName": name,
            "center": sound["center"],
            "radius": sound["radius"],
        })
    return resolved


def read_effect_instances(r: Reader) -> list:
    count = r.u32()
    if count > 100000:
        raise ValueError("implausible effect instance count")
    instances = []
    for _ in range(count):
        position = r.vec3()
        rotation_forward = r.vec3()
        rotation_up = r.vec3()
        effect_id = r.i32()
        instances.append({
            "position": position,
            "rotationForward": rotation_forward,
            "rotationUp": rotation_up,
            "effectId": effect_id,
        })
    return instances


def read_bounding_box(r: Reader) -> dict:
    return {"min": r.vec3(), "max": r.vec3()}


def read_music_zones(r: Reader) -> list:
    count = r.u32()
    if count > 100000:
        raise ValueError("implausible music zone count")
    zones = []
    for _ in range(count):
        box = read_bounding_box(r)
        radius = r.f32()
        audio_asset_index = r.i32()
        r.i32()  # unused
        zones.append({"box": box, "radius": radius, "audioAssetIndex": audio_asset_index})
    return zones


def read_sound_effects(r: Reader) -> list:
    count = r.u32()
    if count > 100000:
        raise ValueError("implausible sound effect count")
    sounds = []
    for _ in range(count):
        audio_asset_index = r.i32()
        center = r.vec3()
        radius = r.f32()
        sounds.append({"audioAssetIndex": audio_asset_index, "center": center, "radius": radius})
    return sounds


def read_portals(r: Reader) -> list:
    count = r.u32()
    if count > 100000:
        raise ValueError("implausible portal count")
    portals = []
    for _ in range(count):
        box = read_bounding_box(r)
        radius = r.f32()
        text1 = r.string256()
        text2 = r.string256()
        map_id = r.bytes(1)[0]
        faction = struct.unpack("<h", r.bytes(2))[0]
        r.bytes(1)  # unknown
        destination_position = r.vec3()
        portals.append({
            "box": box, "radius": radius, "text1": text1, "text2": text2,
            "mapId": map_id, "faction": faction, "destinationPosition": destination_position,
        })
    return portals


def skip_zones(r: Reader) -> None:
    zone_count = r.u32()
    for _ in range(zone_count):
        r.skip(24)
        identifier_count = r.u32()
        r.skip(identifier_count * 4)


def skip_npcs(r: Reader) -> None:
    npc_record_counter = r.i32()
    while npc_record_counter > 0:
        r.skip(24)
        patrol_count = r.u32()
        r.skip(patrol_count * 12)
        npc_record_counter -= patrol_count
        npc_record_counter -= 1


OBJECT_SECTION_NAMES = ["Building", "Shape", "Tree", "Grass", "PrimaryVani", "SecondaryVani", "Dungeon"]


def parse_field_tail(r: Reader) -> dict:
    result = {}
    result["layoutName"] = r.string256()

    object_sections = [read_object_section(r, name) for name in OBJECT_SECTION_NAMES]

    mani_assets = read_names(r)
    mani_instances = read_mani_instances(r)

    effect_file_name = r.string256()
    effect_instances = read_effect_instances(r)
    r.skip(12)

    object_sections.append(read_object_section(r, "Ladder"))

    music_audio_catalog = read_names(r)
    music_zones = read_music_zones(r)
    sound_audio_catalog = read_names(r)
    skip_zones(r)
    sound_effects = read_sound_effects(r)
    # skip_fixed_list(data, offset, 28): read u32 count, skip count*28 bytes.
    count = r.u32()
    r.skip(count * 28)
    portals = read_portals(r)
    count = r.u32()
    r.skip(count * 40)
    count = r.u32()
    r.skip(count * 548)
    skip_npcs(r)

    # Sky and cloud filenames exist in the legacy binary layout, but belong to
    # the global environment rather than to individual map data. Consume their
    # fixed-width fields without exporting unused JSON properties.
    r.skip(256 * 3)

    fog = {"color": [0.42, 0.58, 0.74], "startDistance": 800.0, "endDistance": 4200.0}
    if r.pos + 44 <= len(r.data):
        r.skip(24)  # two unused colors
        color = []
        for _ in range(3):
            v = r.f32()
            if v > 1.0:
                v /= 255.0
            color.append(v)
        fog["color"] = color
        fog["startDistance"] = r.f32()
        fog["endDistance"] = r.f32()

    result.update({
        "objectSections": object_sections,
        "maniInstances": resolve_mani_instances(mani_assets, mani_instances, object_sections),
        "effectFileName": effect_file_name,
        "effectInstances": effect_instances,
        "musicZones": resolve_music_zones(music_audio_catalog, music_zones),
        "soundEffects": resolve_sound_effects(sound_audio_catalog, sound_effects),
        "portals": portals,
        "fog": fog,
    })
    return result


def parse_dun(r: Reader) -> dict:
    r.skip(1)  # byte 3, padding before the string at offset 4 (matches read_string256(data, 4))
    dungeon_dg_file_name = r.string256()

    object_sections = [read_object_section(r, name) for name in OBJECT_SECTION_NAMES]

    read_names(r)  # skip_names: mani asset names, unused for DUN
    count = r.u32()
    r.skip(count * 44)  # skip_fixed_list(data, offset, 44): mani instances

    effect_file_name = r.string256()
    effect_instances = read_effect_instances(r)
    r.skip(12)

    object_sections.append(read_object_section(r, "Ladder"))

    music_audio_catalog = read_names(r)
    music_zones = read_music_zones(r)
    sound_audio_catalog = read_names(r)
    skip_zones(r)
    sound_effects = read_sound_effects(r)
    count = r.u32()
    r.skip(count * 28)
    portals = read_portals(r)

    return {
        "isDungeon": True,
        "dungeonDgFileName": dungeon_dg_file_name,
        "objectSections": object_sections,
        "effectFileName": effect_file_name,
        "effectInstances": effect_instances,
        "musicZones": resolve_music_zones(music_audio_catalog, music_zones),
        "soundEffects": resolve_sound_effects(sound_audio_catalog, sound_effects),
        "portals": portals,
    }


def parse_fld(r: Reader) -> dict:
    map_size = r.u32()
    height_map_side = (map_size // 2) + 1
    sample_count = height_map_side * height_map_side

    height_samples = [r.u16() for _ in range(sample_count)]
    terrain_texture_map = list(r.bytes(sample_count))

    terrain_layer_count = r.u32()
    if terrain_layer_count > 256:
        raise ValueError("implausible terrain layer count")

    terrain_layers = []
    for _ in range(terrain_layer_count):
        texture_file_name = r.string256()
        tile_size = r.f32()
        walk_sound_file_name = r.string256()
        terrain_layers.append({
            "textureFileName": texture_file_name,
            "tileSize": tile_size,
            "walkSoundFileName": walk_sound_file_name,
        })

    result = {
        "isDungeon": False,
        "mapSize": map_size,
        "heightMapSide": height_map_side,
        "heightSamples": height_samples,
        "terrainTextureMap": terrain_texture_map,
        "terrainLayers": terrain_layers,
    }

    try:
        result.update(parse_field_tail(r))
    except ValueError as exc:
        result["fieldTailError"] = str(exc)

    return result


def parse_wld(path: Path) -> dict:
    r = Reader(path.read_bytes())
    if len(r.data) < 12:
        raise ValueError(f"{path}: file too small")

    magic = r.data[0:3].decode("latin-1")
    if magic not in ("FLD", "DUN"):
        raise ValueError(f"{path}: unknown magic {magic!r}")

    if magic == "DUN":
        r.pos = 3
        return {"magic": magic, **parse_dun(r)}

    # mapSize is read from absolute offset 4 in the original (read_u32(data, 4)),
    # i.e. one padding byte follows the 3-byte magic before it.
    r.pos = 4
    return {"magic": magic, **parse_fld(r)}


def main() -> int:
    if len(sys.argv) != 3:
        print('Usage: convert_wld.py <input.wld|.dg> <output.json>', file=sys.stderr)
        return 1

    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    result = parse_wld(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(result), encoding="utf-8")

    if result.get("isDungeon"):
        print(f"OK: {src.name} -> {dst} (DUN, dg={result.get('dungeonDgFileName')!r}, {sum((len(s['instances']) for s in result['objectSections']))}object instances)")
    else:
        n_layers = len(result.get("terrainLayers", []))
        n_objects = sum(len(s["instances"]) for s in result.get("objectSections", []))
        print(f"OK: {src.name} -> {dst} (FLD, mapSize={result['mapSize']}, {n_layers}layers of terrain,{n_objects}object instances)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
