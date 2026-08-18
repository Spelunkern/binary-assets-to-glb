#!/usr/bin/env python3
'Extracts .EFT catalogs of map effects as singular assets.\n\nIt is incremental by design: it never replaces definitions or resources anymore\npublished. When a new dependency has the same name as a\nexisting but different content, generates a stable alias by hash and remaps\nonly the new assets.\n\nUsage:\n    python tools/convert_map_effect_catalogs.py /path/to/data/effects'

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from convert_3de import parse_3de  # noqa: E402
from convert_eft import parse_eft  # noqa: E402
from convert_gameplay_effects import (  # noqa: E402
    CATALOG_LIBRARY_NAMES,
    MAP_LIBRARY_PATTERNS,
    _normalize_resource_names,
)
from effect_asset_catalog import (  # noqa: E402
    build_effect_asset,
    effect_asset_name,
    sanitize_library_name,
)
from texture_utils import load_rgba_debled  # noqa: E402


PROJECT_ROOT = Path(__file__).parent.parent


def _source_index(folder: Path, extension: str) -> dict[str, Path]:
    if not folder.is_dir():
        return {}
    return {
        path.stem.lower(): path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() == extension
    }


def _is_map_library(path: Path) -> bool:
    stem = sanitize_library_name(path.stem)
    return stem not in CATALOG_LIBRARY_NAMES \
        and any(pattern.search(stem) for pattern in MAP_LIBRARY_PATTERNS)


def _texture_payload(source: Path) -> tuple[str, bytes]:
    image = load_rgba_debled(source, debleed=False)
    rgba = image.convert("RGBA")
    digest = hashlib.sha256(
        f"{rgba.width}x{rgba.height}:".encode("ascii") + rgba.tobytes()
    ).hexdigest()
    output = io.BytesIO()
    rgba.save(output, format="PNG")
    return digest, output.getvalue()


def _texture_digest(path: Path) -> str:
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        return hashlib.sha256(
            f"{rgba.width}x{rgba.height}:".encode("ascii") + rgba.tobytes()
        ).hexdigest()


def _mesh_payload(source: Path) -> tuple[str, bytes]:
    mesh = parse_3de(source)
    canonical = json.dumps(mesh, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    text = json.dumps(mesh, ensure_ascii=False, indent="\t")
    return digest, text.encode("utf-8")


def _mesh_digest(path: Path) -> str:
    mesh = json.loads(path.read_text(encoding="utf-8"))
    canonical = json.dumps(mesh, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resource_digest_index(destination: Path, extension: str,
        digest_reader) -> tuple[dict[str, str], dict[str, str]]:
    by_digest: dict[str, str] = {}
    by_name: dict[str, str] = {}
    if not destination.is_dir():
        return by_digest, by_name
    for path in sorted(destination.glob(f"*{extension}"), key=lambda item: item.name):
        digest = digest_reader(path)
        stem = path.stem.lower()
        by_name[stem] = digest
        by_digest.setdefault(digest, stem)
    return by_digest, by_name


def _resolved_resource_name(stem: str, digest: str, destination: Path,
        extension: str, digest_reader, by_digest: dict[str, str],
        by_name: dict[str, str]) -> tuple[str, bool, bool]:
    'Returns name, requires writing and if there was a real collision.'
    direct = destination / f"{stem}{extension}"
    if direct.exists() and by_name.get(stem) == digest:
        return stem, False, False
    if digest in by_digest:
        return by_digest[digest], False, direct.exists()
    if not direct.exists():
        by_digest[digest] = stem
        by_name[stem] = digest
        return stem, True, False

    alias = f"{stem}__map_{digest[:8]}"
    aliased = destination / f"{alias}{extension}"
    if not aliased.exists():
        by_digest[digest] = alias
        by_name[alias] = digest
        return alias, True, True
    if digest_reader(aliased) != digest:
        raise ValueError(f'hash collision for{aliased}')
    by_digest[digest] = alias
    by_name[alias] = digest
    return alias, False, True


def _effect_signature(asset: dict, texture_digests: dict[str, str],
        mesh_digests: dict[str, str]) -> str:
    texture_names = [
        Path(str(name)).stem.lower() for name in asset.get("textureNames", [])
    ]
    mesh_names = [
        Path(str(name)).stem.lower() for name in asset.get("meshNames", [])
    ]
    effects = []
    for raw_effect in asset.get("effects", []):
        effect = json.loads(json.dumps(raw_effect))
        effect.pop("name", None)
        # Metadata derived by tag_effect_lights.py; not part of EFT
        # source nor should you convert the same effect to a different asset.
        effect.pop("ambientLight", None)
        effect["textureContent"] = [
            texture_digests.get(texture_names[int(index)], "missing")
            for index in effect.pop("textureIds", [])
            if 0 <= int(index) < len(texture_names)
        ]
        mesh_index = int(effect.pop("meshIndex", -1))
        effect["meshContent"] = mesh_digests.get(mesh_names[mesh_index], "") \
            if 0 <= mesh_index < len(mesh_names) else ""
        effects.append(effect)

    records = []
    sequences = asset.get("sequences", [])
    if sequences:
        for record in sequences[0].get("records", []):
            effect_index = int(record.get("effectId", -1))
            records.append({
                "time": float(record.get("time", 0.0)),
                "effect": effects[effect_index]
                    if 0 <= effect_index < len(effects) else None,
            })
    canonical = json.dumps({"records": records}, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _asset_base(name: str) -> str:
    return re.sub(r"_seq_\d+$", "", name.lower())


def _write_staged(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _publish_new_files(staging: Path, destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    published = 0
    for source in sorted(staging.iterdir()) if staging.is_dir() else ():
        if not source.is_file():
            continue
        target = destination / source.name
        if target.exists():
            continue
        source.replace(target)
        published += 1
    return published


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="carpeta original data/effects")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source = args.source.resolve()
    effects_root = args.project_root.resolve() / "data" / "effects"
    assets_root = effects_root / "mapeffects"
    textures_root = effects_root / "textures"
    meshes_root = effects_root / "meshes"
    aliases_path = effects_root / "catalogs" / "map_effect_aliases.json"
    staging = effects_root / ".map_catalog_staging"
    staged_assets = staging / "mapeffects"
    staged_textures = staging / "textures"
    staged_meshes = staging / "meshes"

    if not source.is_dir():
        print(f'Effects folder does not exist:{source}', file=sys.stderr)
        return 1
    if staging.exists():
        shutil.rmtree(staging)

    texture_sources = _source_index(source / "DDS", ".dds")
    mesh_sources = _source_index(source / "3DE", ".3de")
    existing_assets = {
        path.stem.lower() for path in assets_root.glob("*.json")
    }
    existing_aliases: dict[str, str] = {}
    if aliases_path.exists():
        aliases_payload = json.loads(aliases_path.read_text(encoding="utf-8"))
        raw_aliases = aliases_payload.get("aliases", {})
        if isinstance(raw_aliases, dict):
            existing_aliases = {
                str(alias).lower(): str(target).lower()
                for alias, target in raw_aliases.items()
                if str(target).lower() in existing_assets
            }
    texture_by_digest, texture_by_name = _resource_digest_index(
        textures_root, ".png", _texture_digest)
    mesh_by_digest, mesh_by_name = _resource_digest_index(
        meshes_root, ".json", _mesh_digest)

    candidates: list[tuple[Path, int, str, dict]] = []
    invalid_libraries: list[str] = []
    map_libraries = sorted(
        (path for path in source.iterdir()
         if path.is_file() and path.suffix.lower() == ".eft"
         and _is_map_library(path)),
        key=lambda path: path.name.lower(),
    )
    for library_path in map_libraries:
        try:
            eft = parse_eft(library_path)
        except (OSError, ValueError) as exc:
            invalid_libraries.append(f"{library_path.name}: {exc}")
            continue
        _normalize_resource_names(eft)
        for sequence_index in range(len(eft.get("sequences", []))):
            name = effect_asset_name(library_path.stem, sequence_index)
            canonical = existing_aliases.get(name.lower(), name.lower())
            if canonical in existing_assets:
                continue
            asset = build_effect_asset(name, eft, sequence_index)
            candidates.append((library_path, sequence_index, name, asset))

    needed_textures = {
        Path(str(name)).stem.lower()
        for _library, _index, _name, asset in candidates
        for name in asset.get("textureNames", [])
    }
    needed_meshes = {
        Path(str(name)).stem.lower()
        for _library, _index, _name, asset in candidates
        for name in asset.get("meshNames", [])
    }

    texture_names: dict[str, str] = {}
    texture_errors: dict[str, str] = {}
    texture_collisions: list[str] = []
    for stem in sorted(needed_textures):
        source_path = texture_sources.get(stem)
        if source_path is None:
            texture_errors[stem] = "archivo ausente"
            continue
        try:
            digest, payload = _texture_payload(source_path)
            resolved, write, collision = _resolved_resource_name(
                stem, digest, textures_root, ".png", _texture_digest,
                texture_by_digest, texture_by_name)
            texture_names[stem] = resolved
            if collision:
                texture_collisions.append(f"{stem} -> {resolved}")
            if write:
                _write_staged(staged_textures / f"{resolved}.png", payload)
        except (OSError, ValueError) as exc:
            texture_errors[stem] = str(exc)

    mesh_names: dict[str, str] = {}
    mesh_errors: dict[str, str] = {}
    mesh_collisions: list[str] = []
    for stem in sorted(needed_meshes):
        source_path = mesh_sources.get(stem)
        if source_path is None:
            mesh_errors[stem] = "archivo ausente"
            continue
        try:
            digest, payload = _mesh_payload(source_path)
            resolved, write, collision = _resolved_resource_name(
                stem, digest, meshes_root, ".json", _mesh_digest,
                mesh_by_digest, mesh_by_name)
            mesh_names[stem] = resolved
            if collision:
                mesh_collisions.append(f"{stem} -> {resolved}")
            if write:
                _write_staged(staged_meshes / f"{resolved}.json", payload)
        except (OSError, ValueError) as exc:
            mesh_errors[stem] = str(exc)

    skipped_assets: list[dict] = []
    effect_aliases: dict[str, str] = dict(existing_aliases)
    new_effect_aliases = 0
    effects_by_signature: dict[str, list[tuple[str, str]]] = {}
    for path in sorted(assets_root.glob("*.json"), key=lambda item: item.name):
        existing_asset = json.loads(path.read_text(encoding="utf-8"))
        signature = _effect_signature(
            existing_asset, texture_by_name, mesh_by_name)
        effects_by_signature.setdefault(signature, []).append(
            (_asset_base(path.stem), path.stem.lower()))

    staged_asset_count = 0
    used_resolved_textures: set[str] = set()
    used_resolved_meshes: set[str] = set()
    for library_path, sequence_index, name, asset in candidates:
        asset_texture_stems = [
            Path(str(raw)).stem.lower() for raw in asset.get("textureNames", [])
        ]
        asset_mesh_stems = [
            Path(str(raw)).stem.lower() for raw in asset.get("meshNames", [])
        ]
        failed_textures = sorted(set(asset_texture_stems) & texture_errors.keys())
        failed_meshes = sorted(set(asset_mesh_stems) & mesh_errors.keys())
        records = (asset.get("sequences", [{}]) or [{}])[0].get("records", [])
        if failed_textures or failed_meshes or not records or not asset.get("effects"):
            skipped_assets.append({
                "name": name,
                "library": library_path.name,
                "sequenceIndex": sequence_index,
                "missingTextures": failed_textures,
                "missingMeshes": failed_meshes,
                "empty": not records or not asset.get("effects"),
            })
            continue

        asset["textureNames"] = [
            f"{texture_names[stem]}.dds" for stem in asset_texture_stems
        ]
        asset["meshNames"] = [
            f"{mesh_names[stem]}.3DE" for stem in asset_mesh_stems
        ]
        signature = _effect_signature(asset, texture_by_name, mesh_by_name)
        asset_base = sanitize_library_name(library_path.stem)
        canonical_name = next((existing_name
            for existing_base, existing_name in effects_by_signature.get(signature, [])
            if existing_base != asset_base), "")
        if canonical_name:
            effect_aliases[name] = canonical_name
            new_effect_aliases += 1
            continue

        payload = json.dumps(asset, ensure_ascii=False, indent="\t").encode("utf-8")
        _write_staged(staged_assets / f"{name}.json", payload)
        staged_asset_count += 1
        used_resolved_textures.update(
            Path(str(raw)).stem.lower() for raw in asset.get("textureNames", []))
        used_resolved_meshes.update(
            Path(str(raw)).stem.lower() for raw in asset.get("meshNames", []))
        effects_by_signature.setdefault(signature, []).append((asset_base, name))

    for path in staged_textures.glob("*.png"):
        if path.stem.lower() not in used_resolved_textures:
            path.unlink()
    for path in staged_meshes.glob("*.json"):
        if path.stem.lower() not in used_resolved_meshes:
            path.unlink()

    staged_texture_count = len(list(staged_textures.glob("*.png")))
    staged_mesh_count = len(list(staged_meshes.glob("*.json")))
    print(f'Map catalogs:{len(map_libraries)}')
    print(f"Assets existentes preservados: {len(existing_assets)}")
    print(f"Assets singulares nuevos: {staged_asset_count}")
    print(f'New textures:{staged_texture_count}(collision aliases:{len(texture_collisions)})')
    print(f'New tights:{staged_mesh_count}(collision aliases:{len(mesh_collisions)})')
    print(f"Aliases legacy conservados: {len(existing_aliases)}")
    print(f'Duplicate effects omitted:{new_effect_aliases}')
    print(f'Assets omitted:{len(skipped_assets)}')
    if invalid_libraries:
        print(f'Invalid libraries:{len(invalid_libraries)}')

    if args.dry_run:
        if staging.exists():
            shutil.rmtree(staging)
        return 0
    if invalid_libraries or skipped_assets:
        print('Conversion canceled: the catalog is not complete', file=sys.stderr)
        if staging.exists():
            shutil.rmtree(staging)
        return 1

    published_textures = _publish_new_files(staged_textures, textures_root)
    published_meshes = _publish_new_files(staged_meshes, meshes_root)
    published_assets = _publish_new_files(staged_assets, assets_root)
    aliases_path.parent.mkdir(parents=True, exist_ok=True)
    aliases_payload = {
        "format": "Research map effect aliases",
        "aliases": dict(sorted(effect_aliases.items())),
    }
    aliases_temporary = aliases_path.with_suffix(".json.tmp")
    aliases_temporary.write_text(
        json.dumps(aliases_payload, ensure_ascii=False, indent="\t"),
        encoding="utf-8")
    aliases_temporary.replace(aliases_path)
    if staging.exists():
        shutil.rmtree(staging)

    print(f'Published:{published_assets} assets, {published_textures}textures,{published_meshes}tights')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
