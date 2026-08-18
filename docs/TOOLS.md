# Tool reference

This repository is deliberately limited to binary-format research and portable
export. It contains no gameplay pipeline, project catalog, audio helper,
benchmark, runtime profile, or extracted asset.

## Direct converters

| Tool | Input | Output | Purpose |
| --- | --- | --- | --- |
| convert_3do.py | .3do | .glb | Rigid meshes such as equipment or static objects. |
| convert_character.py | .3dc | .glb | Skinned meshes and optional skeleton data. |
| convert_ani.py | .ani | .glb or JSON | Skeleton animation clips. |
| convert_smod.py | .smod | .glb | Static world-object geometry and optional collision data. |
| convert_vani.py | .vani | .glb plus frame data | Vertex-animated object geometry. |
| convert_mani.py | .mani | JSON | Continuous object rotation metadata. |
| convert_dg.py | .dg plus optional textures/lightmaps | .glb plus collision JSON | Dungeon-style geometry. |
| convert_wld.py | .wld | JSON | Terrain, object placement, water, and map metadata. |
| convert_svmap.py | .svmap | JSON | Navigation, ladder, spawn, and placement data. |
| convert_eft.py | .eft, .ef2, .ef3 | JSON | Particle-effect library definitions. |
| convert_3de.py | .3de | JSON | Particle-effect mesh data and optional vertex frames. |
| convert_field_lightmap.py | DDS field files | PNG and manifest JSON | Terrain lightmaps and packed splat weights. |
| copy_texture.py | DDS or Pillow-readable image | PNG | RGBA-preserving texture conversion. |

## Export utilities

| Tool | Input | Output | Purpose |
| --- | --- | --- | --- |
| externalize_glb_textures.py | GLB with embedded images | GLB and PNG files | Rewrites embedded images as reusable external PNG textures. |

## Shared modules

These modules are imported by the commands above and are not standalone
converters:

- source_reader.py: bounds-checked binary reading primitives.
- source_skeleton.py: skeleton hierarchy, matrix, and pose helpers.
- gltf_writer.py: glTF 2.0 / GLB writing and external texture references.
- texture_utils.py: explicit alpha-aware image decoding.

## Opening GLB output

GLB geometry follows glTF 2.0. Import it in Blender through File -> Import ->
glTF 2.0, or copy it into a Godot project. Preserve every external PNG at the
relative path written by the converter. If a model is untextured by design,
assign an appropriate material and PNG in the destination application.
