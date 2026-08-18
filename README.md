# Binary Assets to GLB

Open-source Python tools for investigating binary asset formats and exporting
portable data, primarily glTF binary (.glb) models. The repository contains no
source-client data, extracted assets, textures, audio, trademarks, or
proprietary data.

The tools are useful for research, preservation, modding workflows where
permitted, and inspection of assets you are authorized to use.

## Supported formats

| Source format | Export | Purpose |
| --- | --- | --- |
| .3dc | .glb | Skinned character, creature, cloak, and mount meshes. |
| .3do | .glb | Weapons, shields, drops, and static meshes. |
| .ani | .glb or JSON | Skeleton animation clips. |
| .smod | .glb | Static world decoration. |
| .vani | .glb plus frame data | Vertex-animated world objects. |
| .mani | JSON | Continuous object rotation metadata. |
| .dg | .glb plus collision data | Dungeon geometry. |
| .wld | JSON | Open-world terrain, object instances, and map metadata. |
| .svmap | JSON | NPC, monster, and spawn placement data. |
| .eft, .ef2, .ef3 | JSON | Particle-effect libraries. |
| .3de | JSON | Particle-effect meshes. |
| .dds | .png | Runtime textures, lightmaps, and effect textures. |
| Client CSV files | flat .txt or JSON | Character, weapon, mount, NPC, and monster catalogs. |

## Setup

Python 3.11 or newer is recommended.

    git clone https://github.com/Spelunkern/binary-assets-to-glb.git
    cd binary-assets-to-glb
    python -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt

Every command is explicit about its input and output. Run a tool without
arguments to see its command-line usage:

    .venv/bin/python tools/convert_3do.py
    .venv/bin/python tools/convert_character.py

Many batch tools read source roots from these optional variables:

    export ASSET_SOURCE_ROOT=/path/to/client/data
    export ASSET_DIST_ROOT=/path/to/client/dist/windows/data

Keep source data outside this repository; it is intentionally ignored by Git.

## Opening exported GLB models

GLB is the binary form of the open glTF 2.0 format.

- **Blender:** choose **File -> Import -> glTF 2.0 (.glb/.gltf)**. Keep the
  GLB and every referenced PNG in the relative directory layout produced by
  the converter. Blender resolves external image URIs automatically.
- **Godot:** copy or drag the GLB into the project. Godot imports it as a
  scene; external PNG textures must remain at their original relative paths.
- **Other applications:** use any glTF 2.0-capable viewer or DCC application.
  If textures appear white or missing, inspect the model's image URI and
  restore the matching PNG at that relative path.

### Texture behavior

There are two intentional output patterns:

1. **Static models** such as .3do, .smod, and dungeon meshes can reference
   external PNG textures directly from their GLB material. Do not move only
   the GLB: copy its texture folder too.
2. **Playable-character equipment** stores geometry and skinning in GLB files
   without an embedded material. Some source clients reuse one mesh with many texture
   variants, so the batch converters write loose PNG files plus slot tables
   that map each record to its texture. Select the desired PNG and material in
   Blender, Godot, or your own runtime.

All DDS-to-PNG paths preserve source RGBA by default. Transparent RGB is
corrected only for materials explicitly known to use alpha cutout, preventing
mipmap halos without corrupting glow masks or packed alpha data.

## Tool guide

The primary single-asset converters are:

- convert_character.py, convert_3do.py, convert_ani.py
- convert_smod.py, convert_vani.py, convert_mani.py
- convert_dg.py, convert_wld.py, convert_svmap.py
- convert_eft.py, convert_3de.py, and copy_texture.py

Batch and orchestration tools cover complete character catalogs, weapons,
mantles, mounts, map decoration, creature/NPC models, terrain fields, sounds,
and gameplay or map effect libraries. Their names describe their scope:
batch_convert_*.py, convert_map.py, convert_gameplay_effects.py,
convert_map_effect_catalogs.py, and prepare_wld_effects.py.

Shared readers and writers such as source_reader.py, gltf_writer.py,
texture_utils.py, and source_skeleton.py are implementation modules; other
scripts import them automatically.

See docs/TOOLS.md for a complete tool-by-tool reference.

## Notes and limitations

- These tools implement the binary layouts observed in supported source data.
  Unknown versions or malformed files may need additional research.
- A GLB can contain valid geometry even when a game-specific effect, shader,
  collision rule, or runtime behavior has no direct glTF equivalent.
- The repository deliberately excludes runtime code and original assets. Only
  converter source code and documentation are published.

## License

The converter source is available under the BSD 3-Clause License. Original
game data and third-party marks remain the property of their respective owners
and are not licensed by this repository.
