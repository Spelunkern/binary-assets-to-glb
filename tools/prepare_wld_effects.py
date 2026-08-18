#!/usr/bin/env python3
'Phase 3: prepare the particle effects that a .wld actually uses.\n\nThe .wld has a list of "effect instances" (position + effectId) that\npoint to SEQUENCES within the map\'s .eft library (not an effect\nindividual -- see ResearchRuntime::state_.effectPlacements in\nsrc/runtime/reference_runtime.cpp: "placement.sequenceIndex = inst.effectId",\nand each EftEffectSequence brings a list of {effectId, time} which in turn\nThey are indexes within EftLibrary::effects). This script:\n\n  1. Convert map .eft library (wld.effectFileName) to JSON if\n     It doesn\'t exist yet (reuse convert_eft.py).\n  2. Solve, for each effect instance of the .wld, which EftEffect ends\n     playing (via sequence -> records -> effects) and converts to PNG (see\n     copy_texture.py -- canonical file always .png, never .dds)\n     first texture of each from data/effects/textures/ (or the folder\n     legacy effects/dds/ from the source if it is not in data/).\n\nUsage:\n    python prepare_wld_effects.py <wld.json> [--original-data-root DIR]'

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import convert_3de  # noqa: E402
import effect_asset_catalog  # noqa: E402
from convert_eft import parse_eft  # noqa: E402
from texture_utils import load_rgba_debled  # noqa: E402

DEFAULT_ORIGINAL_DATA_ROOT = Path(os.environ.get("ASSET_SOURCE_ROOT", "data/source"))
DEFAULT_DIST_DATA_ROOT = Path(os.environ.get("ASSET_DIST_ROOT", "data/source_dist"))
PROJECT_DATA_ROOT = Path(__file__).parent.parent / "data"


def ensure_eft_json(effect_file_name: str, original_data_root: Path) -> Path:
    stem = Path(effect_file_name).stem
    dst_json = PROJECT_DATA_ROOT / "effects" / f"{stem}.json"
    if dst_json.exists():
        return dst_json

    src = original_data_root / "effects" / effect_file_name
    if not src.exists():
        src = DEFAULT_DIST_DATA_ROOT / "effects" / effect_file_name
    if not src.exists():
        raise FileNotFoundError(f'was not found{effect_file_name} en data/ ni dist/')

    result = parse_eft(src)
    dst_json.parent.mkdir(parents=True, exist_ok=True)
    dst_json.write_text(json.dumps(result), encoding="utf-8")
    print(f"OK: {src.name} -> {dst_json}(format={result['format']}, {len(result['effects'])}effects,{len(result['sequences'])}sequences)")
    return dst_json


def copy_effect_texture(texture_name: str, original_data_root: Path) -> bool:
    if not texture_name:
        return False
    stem = Path(texture_name).stem.lower()
    dst = PROJECT_DATA_ROOT / "effects" / "textures" / f"{stem}.png"
    if dst.exists():
        return True

    for root in (original_data_root, DEFAULT_DIST_DATA_ROOT):
        src = root / "effects" / "dds" / f"{stem}.dds"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            # The effects share this catalog with gameplay. His alpha is
            # part of the blend, not a clipping mask: preserve the RGB
            # transparent avoids altering components with blend ONE.
            load_rgba_debled(src, debleed=False).save(dst, format="PNG")
            return True
    return False


def convert_effect_mesh(mesh_name: str, original_data_root: Path) -> bool:
    '.3DE mesh of an effect (meshIndex >= 0) -> JSON. The original engine\n    resolves the raw name of the .eft (e.g. "flareb01.3DE") against the index\n    global assets, which in practice is found in the legacy folder\n    effects/3de/ in lowercase (see DataIndex::resolve, data_index.cpp).'
    if not mesh_name:
        return False
    stem = Path(mesh_name).stem.lower()
    dst = PROJECT_DATA_ROOT / "effects" / "meshes" / f"{stem}.json"
    if dst.exists():
        return True

    for root in (original_data_root, DEFAULT_DIST_DATA_ROOT):
        src = root / "effects" / "3de" / f"{stem}.3de"
        if not src.exists():
            match = next((c for c in (root / "effects" / "3de").glob("*.3de")
                          if c.name.lower() == f"{stem}.3de"), None) \
                if (root / "effects" / "3de").is_dir() else None
            src = match if match else src
        if src.exists():
            try:
                result = convert_3de.parse_3de(src)
            except ValueError as exc:
                # Same criteria as the original: a corrupt mesh is
                # discards (parsed=false) and the effect falls to billboard.
                print(f'SKIP mesh{mesh_name}: {exc}')
                return False
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(json.dumps(result), encoding="utf-8")
            return True
    return False


def prepare_library_assets(eft: dict, original_data_root: Path) -> dict:
    'Convert ALL assets referenced by an .eft library now\n    parsed: each texture to PNG and each .3DE mesh to JSON. It is done on the\n    entire library (not just about the effects a map uses) because\n    .eft texture/mesh indices are positional -- convert\n    only a subset would require remapping.'
    stats = {"textures_ok": 0, "textures_missing": 0, "meshes_ok": 0, "meshes_missing": 0}

    for texture_name in eft.get("textureNames", []):
        if copy_effect_texture(texture_name, original_data_root):
            stats["textures_ok"] += 1
        elif texture_name:
            stats["textures_missing"] += 1

    for mesh_name in eft.get("meshNames", []):
        if convert_effect_mesh(mesh_name, original_data_root):
            stats["meshes_ok"] += 1
        elif mesh_name:
            stats["meshes_missing"] += 1

    return stats


def main() -> int:
    args = sys.argv[1:]
    original_data_root = DEFAULT_ORIGINAL_DATA_ROOT
    if "--original-data-root" in args:
        i = args.index("--original-data-root")
        original_data_root = Path(args[i + 1])
        args = args[:i] + args[i + 2:]

    if len(args) != 1:
        print('Usage: prepare_wld_effects.py <wld.json> [--original-data-root DIR]', file=sys.stderr)
        return 1

    wld = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    effect_file_name = wld.get("effectFileName", "")
    instances = wld.get("effectInstances", [])
    if not effect_file_name or not instances:
        print('This .wld has no effects (empty effectFileName or no instances).')
        return 0

    eft_json_path = ensure_eft_json(effect_file_name, original_data_root)
    eft = json.loads(eft_json_path.read_text(encoding="utf-8"))
    effect_asset_catalog.build_assets_for_library(eft_json_path)

    used_effect_indices = set()
    for inst in instances:
        seq_idx = inst["effectId"]
        if not (0 <= seq_idx < len(eft["sequences"])):
            continue
        for record in eft["sequences"][seq_idx]["records"]:
            effect_idx = record["effectId"]
            if 0 <= effect_idx < len(eft["effects"]):
                used_effect_indices.add(effect_idx)

    copied = 0
    missing = 0
    for effect_idx in used_effect_indices:
        effect = eft["effects"][effect_idx]
        for texture_id in effect["textureIds"]:
            if not (0 <= texture_id < len(eft["textureNames"])):
                continue
            tex_name = eft["textureNames"][texture_id]
            if copy_effect_texture(tex_name, original_data_root):
                copied += 1
            else:
                missing += 1
                print(f"SKIP texture not found:{tex_name}(effect '{effect['name']}')")

    print(f'\nOK: {len(used_effect_indices)}effects used by{len(instances)}map instances,{copied}ready textures,{missing}missing')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
