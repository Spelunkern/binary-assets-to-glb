#!/usr/bin/env python3
'Convert gameplay effects libraries from the original client.\n\nThe definitions are separated from the map assets, but both systems\nThey use the same resource catalog:\n\n  data/effects/gameplayeffects/*.json assets per stream\n  data/effects/textures/*.png shared textures\n  data/effects/meshes/*.json shared meshes\n\nTextures and meshes are deduplicated by name, just like in the runtime.\neffects. The script does not copy the original binaries or modify data/source.\n\nUsage:\n    python tools/convert_gameplay_effects.py /path/to/data/effects'

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from convert_3de import parse_3de  # noqa: E402
from convert_eft import parse_eft  # noqa: E402
from effect_asset_catalog import build_effect_asset, sanitize_library_name  # noqa: E402
from texture_utils import load_rgba_debled  # noqa: E402


PROJECT_ROOT = Path(__file__).parent.parent

# Libraries that describe complete scenes or global catalogs. The
# Individual attacks/deaths of creatures if they are gameplay and do not enter here.
CATALOG_LIBRARY_NAMES = {
    "login",
    "login_threemax",
    "monster",
    "select",
    "select_a",
    "select_b",
    "start",
    "weather",
}

# Content deliberately discarded by the project. Keeping it here prevents
# that reappears when regenerating the catalog from a complete original data.
UNUSED_LIBRARY_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"^revengemark_",
))

# Client environmental library conventions. They contrast
# also with the stems already present in mapeffects, so that a library of
# Known map will never be duplicated by accident in gameplay effects.
MAP_LIBRARY_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"^world(?:_|$)",
    r"^[abr]\d+_world$",
    r"^[ab]\d+_dun\d",
    r"^ab\d+_dun\d",
    r"^[fl]_[abr]\d+_dun\d",
    r"^f_dun(?:_|$)",
    r"^l_dun(?:_|$)",
    r"^r\d+_dun(?:\d|$)",
    r"^r\d+_small$",
    r"^r\d+_d\d",
    r"^r\d+_trade(?:_|$)",
    r"^[ab]\d+_pvp$",
    r"^ev_(?:wedding|worldcup)$",
    r"^f_b\d+_stardust$",
    r"^g\d+_.*station$",
    r"^guildanteroom$",
    r"^l_dun_.*arena$",
    r"^prison(?:_|$)",
    r"^strip_(?:npc|room)$",
))


def _find_case_insensitive(folder: Path, filename: str) -> Path | None:
    direct = folder / filename
    if direct.exists():
        return direct
    wanted = filename.lower()
    for candidate in folder.iterdir() if folder.is_dir() else ():
        if candidate.name.lower() == wanted:
            return candidate
    return None


def _write_json(path: Path, value: dict) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent="\t")
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def _convert_texture(source: Path, destination: Path) -> bool:
    existed = destination.exists()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # These textures are drawn with additive blend or alpha-blend. The weak
    # used by alpha-cutout fills transparent RGB with opaque colors and
    # generates halos/whites by adding the color even if the alpha is zero.
    load_rgba_debled(source, debleed=False).save(destination, format="PNG")
    return not existed


def _convert_mesh(source: Path, destination: Path) -> bool:
    if destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_json(destination, parse_3de(source))
    return True


def _known_map_library_stems(project_root: Path) -> set[str]:
    result: set[str] = set()
    root = project_root / "data" / "effects" / "mapeffects"
    if not root.is_dir():
        return result
    for path in root.glob("*_seq_*.json"):
        stem = re.sub(r"_seq_\d+$", "", path.stem, flags=re.IGNORECASE)
        result.add(sanitize_library_name(stem))
    return result


def _excluded_library_reason(path: Path, known_map_stems: set[str]) -> str | None:
    stem = sanitize_library_name(path.stem)
    if any(pattern.search(stem) for pattern in UNUSED_LIBRARY_PATTERNS):
        return "unused"
    if stem in CATALOG_LIBRARY_NAMES:
        return "catalog"
    if stem in known_map_stems:
        return "map"
    if any(pattern.search(stem) for pattern in MAP_LIBRARY_PATTERNS):
        return "map"
    return None


def _normalize_resource_names(eft: dict) -> None:
    # The runtime builds paths from these names. Save them in lowercase
    # prevents authored data with inconsistent casing from failing when exporting to
    # a case-sensitive file system.
    eft["textureNames"] = [
        f"{Path(str(name)).stem.lower()}.dds" for name in eft.get("textureNames", [])
    ]
    eft["meshNames"] = [
        f"{Path(str(name)).stem.lower()}.3DE" for name in eft.get("meshNames", [])
    ]


def _replace_generated_directory(staging: Path, output: Path) -> None:
    parent = output.parent.resolve()
    if output.resolve().parent != parent or staging.resolve().parent != parent:
        raise ValueError("gameplay output must stay inside data/effects")
    previous = parent / f".{output.name}_previous"
    if previous.exists():
        shutil.rmtree(previous)
    if output.exists():
        output.rename(previous)
    try:
        staging.rename(output)
    except Exception:
        if previous.exists() and not output.exists():
            previous.rename(output)
        raise
    if previous.exists():
        shutil.rmtree(previous)


def _merge_generated_resources(staging: Path, destination: Path) -> None:
    'Publish already validated resources without deleting those used by maps.'
    destination.mkdir(parents=True, exist_ok=True)
    for source in staging.iterdir() if staging.is_dir() else ():
        if not source.is_file():
            continue
        target = destination / source.name
        temporary = destination / f".{source.name}.gameplay_tmp"
        shutil.copy2(source, temporary)
        temporary.replace(target)


def _normalize_shared_resource_names(folder: Path) -> None:
    'Fixed portable casing even for files created before merge.'
    if not folder.is_dir():
        return
    for index, source in enumerate(list(folder.iterdir())):
        if not source.is_file() or source.name == source.name.lower():
            continue
        target = folder / source.name.lower()
        temporary = folder / f".case_tmp_{index}"
        if temporary.exists():
            temporary.unlink()
        source.rename(temporary)
        temporary.replace(target)

    # A renamed sidecar can still internally declare source_file with
    # the old casing. Only that obsolete import is removed; Godot recreates it
    # next to the canonical PNG in the next scan.
    for sidecar in folder.glob("*.png.import"):
        source_name = sidecar.name.removesuffix(".import")
        text = sidecar.read_text(encoding="utf-8", errors="replace")
        match = re.search(r'^source_file="[^"]*/([^"]+)"$', text, re.MULTILINE)
        if match and match.group(1) != source_name:
            sidecar.unlink()


def _referenced_resource_stems(asset_root: Path) -> tuple[set[str], set[str]]:
    textures: set[str] = set()
    meshes: set[str] = set()
    if not asset_root.is_dir():
        return textures, meshes
    for path in asset_root.glob("*.json"):
        if path.name.lower() == "manifest.json":
            continue
        try:
            asset = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        textures.update(
            Path(str(name)).stem.lower() for name in asset.get("textureNames", [])
        )
        meshes.update(
            Path(str(name)).stem.lower() for name in asset.get("meshNames", [])
        )
    return textures, meshes


def _remove_shared_resources(folder: Path, stems: set[str], extension: str) -> int:
    removed = 0
    for stem in stems:
        path = folder / f"{stem}{extension}"
        if not path.exists():
            continue
        path.unlink()
        removed += 1
        sidecar = Path(f"{path}.import")
        if sidecar.exists():
            sidecar.unlink()
    return removed


def _gameplay_asset_name(library_stem: str, sequence: dict, index: int) -> str:
    'Stable name: each level remains an independent effect.'
    library = sanitize_library_name(library_stem)
    label = sanitize_library_name(str(sequence.get("name", "")))
    return f"{library}_{label}" if label != "effect" else f"{library}_variant_{index + 1:02d}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="carpeta original data/effects")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()

    source = args.source.resolve()
    effects_root = args.project_root / "data" / "effects"
    final_output = effects_root / "gameplayeffects"
    output = final_output.parent / f".{final_output.name}_staging"
    resources_staging = effects_root / ".gameplayeffects_resources_staging"
    previous_texture_stems, previous_mesh_stems = \
        _referenced_resource_stems(final_output)
    map_texture_stems, map_mesh_stems = \
        _referenced_resource_stems(effects_root / "mapeffects")
    if output.exists():
        shutil.rmtree(output)
    if resources_staging.exists():
        shutil.rmtree(resources_staging)
    textures_output = resources_staging / "textures"
    meshes_output = resources_staging / "meshes"

    if not source.is_dir():
        print(f'Effects folder does not exist:{source}', file=sys.stderr)
        return 1

    libraries = sorted(
        (path for path in source.iterdir()
         if path.is_file() and path.suffix.lower() == ".eft"),
        key=lambda p: p.name.lower())
    if not libraries:
        print(f'No .eft libraries found in{source}', file=sys.stderr)
        return 1

    known_map_stems = _known_map_library_stems(args.project_root)
    excluded_libraries: list[dict[str, str]] = []
    included_libraries: list[Path] = []
    for library_path in libraries:
        reason = _excluded_library_reason(library_path, known_map_stems)
        if reason is None:
            included_libraries.append(library_path)
        else:
            excluded_libraries.append({"name": library_path.name, "reason": reason})

    parsed_libraries: list[tuple[Path, dict]] = []
    bad_libraries: list[str] = []

    for library_path in included_libraries:
        try:
            eft = parse_eft(library_path)
        except (OSError, ValueError) as exc:
            bad_libraries.append(f"{library_path.name}: {exc}")
            continue
        _normalize_resource_names(eft)
        parsed_libraries.append((library_path, eft))

    # First we make the sequences independent. build_effect_asset prunes the
    # library resources that that variant does not use, allowing you to skip
    # just an incomplete effect instead of losing your entire library.
    pending_assets: list[tuple[Path, int, dict]] = []
    occupied_asset_names: set[str] = set()
    for library_path, eft in parsed_libraries:
        sequences = eft.get("sequences", [])
        for sequence_index, sequence in enumerate(sequences):
            name = _gameplay_asset_name(library_path.stem, sequence, sequence_index)
            if name in occupied_asset_names:
                base_name = f"{name}_variant_{sequence_index + 1:02d}"
                name = base_name
                suffix = 2
                while name in occupied_asset_names:
                    name = f"{base_name}_{suffix}"
                    suffix += 1
            occupied_asset_names.add(name)

            asset = build_effect_asset(name, eft, sequence_index)
            asset["baseEffect"] = sanitize_library_name(library_path.stem)
            asset["variantIndex"] = sequence_index
            asset["variantLabel"] = str(sequence.get("name", ""))
            asset["variantCount"] = len(sequences)
            pending_assets.append((library_path, sequence_index, asset))

    texture_stems = {
        Path(str(raw_name)).stem.lower()
        for _library_path, _sequence_index, asset in pending_assets
        for raw_name in asset.get("textureNames", [])
        if Path(str(raw_name)).stem
    }
    mesh_stems = {
        Path(str(raw_name)).stem.lower()
        for _library_path, _sequence_index, asset in pending_assets
        for raw_name in asset.get("meshNames", [])
        if Path(str(raw_name)).stem
    }
    dds_root = source / "dds"
    mesh_root = source / "3de"
    texture_sources: dict[str, Path | None] = {
        stem: _find_case_insensitive(dds_root, f"{stem}.dds")
        for stem in texture_stems
    }
    mesh_sources: dict[str, Path | None] = {
        stem: _find_case_insensitive(mesh_root, f"{stem}.3de")
        for stem in mesh_stems
    }

    texture_failures: dict[str, str] = {}
    for stem, source_path in sorted(texture_sources.items()):
        if source_path is None:
            texture_failures[stem] = "archivo ausente"
            continue
        try:
            _convert_texture(source_path, textures_output / f"{stem}.png")
        except (OSError, ValueError) as exc:
            texture_failures[stem] = str(exc)

    mesh_failures: dict[str, str] = {}
    for stem, source_path in sorted(mesh_sources.items()):
        if source_path is None:
            mesh_failures[stem] = "archivo ausente"
            continue
        try:
            _convert_mesh(source_path, meshes_output / f"{stem}.json")
        except (OSError, ValueError) as exc:
            mesh_failures[stem] = str(exc)

    assets_written = 0
    asset_names: list[str] = []
    excluded_assets: list[dict] = []
    used_texture_stems: set[str] = set()
    used_mesh_stems: set[str] = set()
    used_libraries: set[str] = set()
    for library_path, sequence_index, asset in pending_assets:
        asset_texture_stems = {
            Path(str(name)).stem.lower() for name in asset.get("textureNames", [])
        }
        asset_mesh_stems = {
            Path(str(name)).stem.lower() for name in asset.get("meshNames", [])
        }
        failed_textures = sorted(asset_texture_stems & texture_failures.keys())
        failed_meshes = sorted(asset_mesh_stems & mesh_failures.keys())
        if failed_textures or failed_meshes:
            excluded_assets.append({
                "name": asset["name"],
                "library": library_path.name,
                "variantIndex": sequence_index,
                "missingTextures": failed_textures,
                "missingMeshes": failed_meshes,
            })
            continue

        if _write_json(output / f"{asset['name']}.json", asset):
            assets_written += 1
        asset_names.append(asset["name"])
        used_texture_stems.update(asset_texture_stems)
        used_mesh_stems.update(asset_mesh_stems)
        used_libraries.add(library_path.name)

    # We do not retain resources that only belonged to discarded variants.
    for path in textures_output.glob("*.png"):
        if path.stem.lower() not in used_texture_stems:
            path.unlink()
    for path in meshes_output.glob("*.json"):
        if path.stem.lower() not in used_mesh_stems:
            path.unlink()

    manifest = {
        "format": "Research gameplay effects",
        "source": str(source),
        "libraries": len(used_libraries),
        "candidateLibraries": len(parsed_libraries),
        "assets": len(asset_names),
        "candidateAssets": len(pending_assets),
        "assetNames": asset_names,
        "textures": len(used_texture_stems),
        "meshes": len(used_mesh_stems),
        "textureRoot": "../textures",
        "meshRoot": "../meshes",
        "missingTextures": texture_failures,
        "missingMeshes": mesh_failures,
        "invalidLibraries": bad_libraries,
        "excludedLibraries": excluded_libraries,
        "excludedAssets": excluded_assets,
    }

    stale_texture_stems = previous_texture_stems - used_texture_stems - map_texture_stems
    stale_mesh_stems = previous_mesh_stems - used_mesh_stems - map_mesh_stems
    manifest["removedStaleTextures"] = sorted(stale_texture_stems)
    manifest["removedStaleMeshes"] = sorted(stale_mesh_stems)
    _write_json(output / "manifest.json", manifest)

    _normalize_shared_resource_names(effects_root / "textures")
    _normalize_shared_resource_names(effects_root / "meshes")
    _merge_generated_resources(textures_output, effects_root / "textures")
    _merge_generated_resources(meshes_output, effects_root / "meshes")
    _replace_generated_directory(output, final_output)
    removed_textures = _remove_shared_resources(
        effects_root / "textures", stale_texture_stems, ".png")
    removed_meshes = _remove_shared_resources(
        effects_root / "meshes", stale_mesh_stems, ".json")
    shutil.rmtree(resources_staging)

    print(f'OK: {len(used_libraries)}libraries ->{len(asset_names)} assets de gameplay')
    print(f"  assets nuevos/actualizados: {assets_written}")
    print(f'textures used:{len(used_texture_stems)}')
    print(f'used tights:{len(used_mesh_stems)}')
    print(f'Excluded libraries:{len(excluded_libraries)}')
    print(f"  variantes incompletas excluidas: {len(excluded_assets)}")
    print(f'Obsolete resources removed:{removed_textures}textures,{removed_meshes}tights')
    if texture_failures:
        print(f'missing/invalid textures:{len(texture_failures)}')
    if mesh_failures:
        print(f'missing/invalid meshes:{len(mesh_failures)}')
    if bad_libraries:
        print(f'invalid libraries:{len(bad_libraries)}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
