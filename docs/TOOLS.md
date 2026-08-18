# Tool reference

This page documents every public converter and support utility in this
repository. Source data is always supplied by the user and is never included.

## Geometry and animation

| Tool | Input | Output | Notes |
| --- | --- | --- | --- |
| convert_character.py | .3dc | .glb | Reads skinned character-style meshes. It can attach a skeleton discovered from the matching animation directory. |
| convert_3do.py | .3do | .glb | Converts rigid weapon, shield, drop, or static meshes. |
| convert_ani.py | .ani | .glb or JSON | Exports a skeleton animation clip without mesh geometry. |
| convert_smod.py | .smod | .glb | Converts static map decoration meshes. |
| convert_vani.py | .vani | .glb and frame data | Exports frame-zero geometry and compact data for vertex animation. |
| convert_mani.py | .mani | JSON | Reads continuous local-axis rotation metadata for world objects. |
| convert_dg.py | .dg plus textures/lightmaps | .glb and collision JSON | Exports dungeon render geometry, baked lightmap references, and the collision mesh. |
| convert_worldobjects.py | .3do and DDS folders | .glb | Converts standalone world objects and writes shared external textures. |

## Maps and map content

| Tool | Input | Output | Notes |
| --- | --- | --- | --- |
| convert_wld.py | .wld | JSON | Reads open-world terrain, sections, object instances, water, and map metadata. |
| convert_svmap.py | .svmap | JSON | Reads map NPC, monster, and spawn placement data. |
| convert_field_lightmap.py | field DDS files | PNG and manifest JSON | Converts terrain lightmaps and packs splat-mask alpha channels into RGBA weight textures. |
| batch_convert_wld_objects.py | converted WLD and source assets | GLB files | Converts only the decoration assets referenced by one map. |
| convert_map.py | map ID and client roots | complete converted map set | Coordinates terrain, dungeon, decoration, creatures, effects, and field data conversion. |
| export_smod_collision.py | .smod | collision JSON | Extracts collision information from compatible world-object meshes. |

## Characters, items, and creatures

| Tool | Input | Output | Notes |
| --- | --- | --- | --- |
| batch_convert_characters.py | character CSV, .3dc, .ani, DDS | GLB, PNG, and flat tables | Exports playable equipment, skeletons, animation clips, and slot-to-texture mappings. |
| batch_convert_weapons.py | weapon CSV, .3do, DDS | GLB, PNG, and flat tables | Exports weapon and shield catalogs. |
| batch_convert_mantles.py | mantle CSV, .3dc, DDS | GLB, PNG, and cloth profiles | Separates cloth bodies from skinned shoulder geometry. |
| batch_convert_vehicles.py | vehicle CSV, .3dc, .ani, DDS | GLB, PNG, and flat tables | Exports mounts, clips, and seat-bone metadata. |
| convert_monster_catalog.py | monster CSV files | flat tables | Builds creature model and monster lookup tables. |
| convert_npc_catalog.py | NPC CSV files | flat tables | Builds NPC model and lookup tables. |
| batch_convert_creatures.py | SVMap, catalogs, .3dc, DDS | GLB and skeleton data | Converts only monster and NPC models referenced by a map. |
| generate_cloth_profiles.py | cloak geometry | cloth profile JSON | Generates editable cloth-grid metadata from converted mantle meshes. |

## Effects, textures, and audio

| Tool | Input | Output | Notes |
| --- | --- | --- | --- |
| convert_eft.py | .eft, .ef2, .ef3 | JSON | Reads effect libraries, components, keyframes, meshes, and texture references. |
| convert_3de.py | .3de | JSON | Reads effect mesh geometry and optional vertex-animation frames. |
| convert_gameplay_effects.py | gameplay effect folders | JSON, PNG, and mesh data | Builds a deduplicated gameplay-effect resource library. |
| convert_map_effect_catalogs.py | map effect folders | JSON, PNG, and mesh data | Builds self-contained map-effect assets with stable names. |
| prepare_wld_effects.py | map effect references and source roots | PNG and mesh data | Resolves assets required by WLD map effects. |
| normalize_map_effect_assets.py | converted effect assets | normalized files | Repairs naming and asset-layout inconsistencies in effect libraries. |
| copy_texture.py | DDS or another Pillow-readable image | PNG | Generic RGBA-preserving texture converter. Use --alpha-cutout only when the material discards transparent pixels. |
| externalize_glb_textures.py | GLB with embedded images | GLB and shared PNG files | Rewrites embedded GLB images as reusable external textures. |

## Shared modules

These Python files are imported by the tools above and normally are not run
directly:

- data_table.py: flat-table writer and reader helpers.
- creature_catalog.py: catalog loading and model lookup helpers.
- effect_asset_catalog.py: effect asset naming and deduplication helpers.
- gltf_writer.py: shared glTF and GLB writer with external texture support.
- source_reader.py: safe binary reading primitives.
- source_skeleton.py: skeleton construction and matrix helpers.
- texture_utils.py: explicit alpha-aware DDS/image decoding.
- weapon_bones.py: weapon-class to hand-bone mapping.

## Output portability

GLB files use standard glTF 2.0 geometry, skinning, and animation where the
source format permits it. Some game-specific systems need accompanying data:

- Character equipment needs the generated slot table and PNG variants because
  the same mesh can have many textures.
- VANI and GPU animation exports use sidecar frame or palette data.
- Dungeon collision and effect libraries use JSON sidecars.

For direct viewing, preserve relative paths and copy the external PNG files
with the GLB. See the main README for Blender and Godot import steps.
