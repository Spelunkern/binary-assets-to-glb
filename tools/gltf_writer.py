'Shared GLB writer for tools/ mesh converters.\n\nReplaces the OBJ+MTL pipeline (Phase 1/2): OBJ has no skeleton or form\nto declare transparency by clipping, so .vani cannot animate and the\nfoliage was left with poorly resolved blend. GLB (binary glTF) does support everything\nthat -- this module is the common base; the real skeleton/skinning support\nIt is added when there is a converter that needs it (see convert_character.py).\n\nUse pygltflib for the container and Pillow to decode the textures\n.dds (Pillow reads native DXT1/3/5) and re-encode them as PNG -- glTF does not have\na standard DDS image type.\n\nThe textures are NOT embedded in the .glb: they go to data/textures/ with the\nname derived from its content and are referenced by `uri`. See add_texture,\nwhich explains why (embedded cost 1,177 MB of duplicate disk and 8\nminutes of each reimport).'

import hashlib
import io
import sys
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pygltflib as gltf

sys.path.insert(0, str(Path(__file__).parent))
from texture_utils import load_rgba_debled  # noqa: E402

## Single folder of shared textures. Only one for the entire project and not
## one per section: the largest duplicates cross folders (559 images
## between entity/building and entity/shape, 411 between building and world/dungeon),
## so separating them would lose just the bulk of the deduplication.
SHARED_TEXTURES = Path(__file__).resolve().parent.parent / "data" / "textures"


class GlbBuilder:
    def __init__(self):
        self.buffer_blob = bytearray()
        self.buffer_views = []
        self.accessors = []
        self.meshes = []
        self.materials = []
        self.textures = []
        self.images = []
        self.samplers = []
        self.nodes = []
        self.skins = []
        self.animations = []
        self._texture_cache: dict[str, int] = {}
        ## Indexes of images whose uri is still just the file name:
        ## It is completed in save(), when you know where the .glb is going to be.
        self._pending_uris: list[int] = []
        self._material_cache: dict[tuple, int] = {}

    def _add_buffer_view(self, data: bytes, target: int | None = None, byte_stride: int | None = None) -> int:
        offset = len(self.buffer_blob)
        self.buffer_blob += data
        pad = (-len(data)) % 4
        if pad:
            self.buffer_blob += b"\x00" * pad
        bv = gltf.BufferView(buffer=0, byteOffset=offset, byteLength=len(data))
        if target is not None:
            bv.target = target
        # Without this Godot throws "Buffer view byte stride should be declared
        # for vertex attributes" when importing -- harmless (assumes data
        # packaged, which is exactly what it is) but with thousands of
        # .glb converted thousands of warnings accumulate in the editor.
        # byteStride is only valid in bufferViews target=ARRAY_BUFFER
        # (vertex attributes), not in indices or images.
        if target == gltf.ARRAY_BUFFER and byte_stride is not None:
            bv.byteStride = byte_stride
        idx = len(self.buffer_views)
        self.buffer_views.append(bv)
        return idx

    _COMPONENT_BYTES = {
        gltf.UNSIGNED_BYTE: 1, gltf.UNSIGNED_SHORT: 2,
        gltf.UNSIGNED_INT: 4, gltf.FLOAT: 4,
    }
    _TYPE_COMPONENT_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}

    def _add_accessor(self, arr: np.ndarray, type_str: str, component_type: int,
                       target: int | None = None, minmax: bool = False) -> int:
        byte_stride = None
        if target == gltf.ARRAY_BUFFER:
            byte_stride = self._COMPONENT_BYTES[component_type] * self._TYPE_COMPONENT_COUNT[type_str]
        bv_idx = self._add_buffer_view(arr.tobytes(), target=target, byte_stride=byte_stride)
        acc = gltf.Accessor(bufferView=bv_idx, componentType=component_type,
                             count=int(arr.shape[0]), type=type_str)
        if minmax:
            flat = arr.reshape(arr.shape[0], -1) if arr.ndim > 1 else arr.reshape(-1, 1)
            acc.min = flat.min(axis=0).tolist()
            acc.max = flat.max(axis=0).tolist()
        idx = len(self.accessors)
        self.accessors.append(acc)
        return idx

    def add_texture(self, dds_path: Path, *, debleed: bool = False) -> tuple[int, bool]:
        'Returns (texture_index, has_alpha_cutout).\n\n\t\tThe texture is NOT embedded: it is written to data/textures/ with the\n        name derived from its CONTENT and referenced by `uri`.\n\n        Embedded was the natural --un .glb self-contained-- but it is very expensive\n        here The Godot importer comes with `gltf/embedded_image_handling`\n        in Extract Textures: for each embedded image write a PNG next to it\n        from the .glb and imports it as a loose texture. Like the same texture\n        They share dozens of assets, that gave 11,760 PNG of which 7,730 were\n        copy byte by byte from another -- 1,177 MB of disk, 8 minutes each\n        reimport, and a different resource in VRAM for each copy.\n\n        Shared, the same image is a file, an import and a resource, the\n        use one asset or one hundred. See tools/externalize_glb_textures.py, which did\n        this same transformation on the .glb already generated.'
        # The same DDS can be emitted with distinct alpha semantics, so cache
        # entries include the requested conversion mode.
        key = (str(dds_path), debleed)
        if key in self._texture_cache:
            entry = self._texture_cache[key]
            return entry

        img = load_rgba_debled(dds_path, debleed=debleed)
        has_alpha = False
        alpha_band = img.getchannel("A")
        lo, hi = alpha_band.getextrema()
        if lo < 250:
            has_alpha = True

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        datos = buf.getvalue()
        # The name comes from the content and not from the source .dds: two paths
        # different ones with the same image have to fall into the same file.
        nombre = hashlib.md5(datos).hexdigest()[:16] + ".png"
        SHARED_TEXTURES.mkdir(parents=True, exist_ok=True)
        destino = SHARED_TEXTURES / nombre
        if not destino.exists():
            destino.write_bytes(datos)

        image_idx = len(self.images)
        # The final uri is assembled in save(), which is where you know WHERE it will go.
        # the .glb remains and therefore how to get from there to the shared one.
        self.images.append(gltf.Image(uri=nombre))
        self._pending_uris.append(image_idx)

        sampler_idx = len(self.samplers)
        self.samplers.append(gltf.Sampler(
            magFilter=gltf.LINEAR, minFilter=gltf.LINEAR_MIPMAP_LINEAR,
            wrapS=gltf.REPEAT, wrapT=gltf.REPEAT,
        ))

        tex_idx = len(self.textures)
        self.textures.append(gltf.Texture(source=image_idx, sampler=sampler_idx))
        self._texture_cache[key] = (tex_idx, has_alpha)
        return tex_idx, has_alpha

    def add_material(self, texture_path: Path | None, lightmap_path: Path | None = None,
                     alpha_cutout: bool | None = None) -> int | None:
        'lightmap_path (optional) – maps to the occlusionTexture of the glTF\n        (standard core spec field, not an extension) with texCoord=1 --\n        Godot imports it directly as AO multiplied by the albedo,\n        using UV2 (see _build_primitive/uvs2). It is the same role as\n        lightmapTexture in the original shader (color *= lm), so\n        reusing it as "lightmap" is a reasonable translation without having to\n        write your own shader.\n\n        alpha_cutout: Force whether the texture is cut by alpha or not. In None\n        It is decided by looking at the alpha channel, which is the only thing that can be done\n        when the asset is not accompanied by that information. but when\n        IF it comes you have to pass it: several textures in the game use alpha\n        for something else. Character armor ones (AlphaBlendingMode\n        "Glow" in team CSVs) have ~0 alpha on almost the entire map\n        -- humf_torso001.dds gives 0.0% de texels >=128 -- so guessing\n        The entire mesh was cut out and four loose triangles were left.\n        The original decides by the CSV, not by the texture:\n        entry.alphaCutout = (alphaMode == "Alpha" || alphaMode ==\n        "Visibility"), character_system.cpp:594.'
        if texture_path is None or not texture_path.exists():
            return None
        cache_key = (str(texture_path), str(lightmap_path) if lightmap_path else None,
                     alpha_cutout)
        if cache_key in self._material_cache:
            return self._material_cache[cache_key]

        # Never guess that alpha may alter hidden RGB. Only a converter with
        # explicit cutout metadata may request the fringe-safe variant.
        tex_idx, sniffed_alpha = self.add_texture(
            texture_path, debleed=alpha_cutout is True)
        has_alpha = sniffed_alpha if alpha_cutout is None else alpha_cutout
        pbr = gltf.PbrMetallicRoughness(
            baseColorTexture=gltf.TextureInfo(index=tex_idx),
            metallicFactor=0.0, roughnessFactor=1.0,
        )
        mat = gltf.Material(pbrMetallicRoughness=pbr)
        if has_alpha:
            mat.alphaMode = "MASK"
            mat.alphaCutoff = 0.5
            mat.doubleSided = True
        else:
            mat.alphaMode = "OPAQUE"

        if lightmap_path is not None and lightmap_path.exists():
            lm_idx, _ = self.add_texture(lightmap_path, debleed=False)
            mat.occlusionTexture = gltf.TextureInfo(index=lm_idx, texCoord=1)

        idx = len(self.materials)
        self.materials.append(mat)
        self._material_cache[cache_key] = idx
        return idx

    @staticmethod
    def _drop_degenerate_faces(positions, indices) -> list:
        'Triangles with area ~0 (2 repeated vertices, or 3 vertices\n        collinear/coincident) cause Godot to be unable to calculate a\n        normal -- fall into the fallback (0,0,0) and throw "Vector3 cannot be\n        normalized" when generating LODs. It happens in some assets of the dataset\n        original (real degenerate geometry, not a conversion bug) --\n        they are filtered here, in the writer, to cover any .smod/.vani/\n        etc. without having to fix it separately on each converter.'
        clean = []
        for i in range(0, len(indices), 3):
            a, b, c = indices[i], indices[i + 1], indices[i + 2]
            if a == b or b == c or a == c:
                continue
            pa, pb, pc = positions[a], positions[b], positions[c]
            ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
            vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
            cx = uy * vz - uz * vy
            cy = uz * vx - ux * vz
            cz = ux * vy - uy * vx
            if (cx * cx + cy * cy + cz * cz) < 1e-12:
                continue
            clean.extend((a, b, c))
        return clean

    ## Returns None if the part is left WITHOUT FACES after filtering the
    ## degenerated. That would produce an index accessor with count=0, and Godot
    ## rejects the ENTIRE .glb with "Invalid accessor count 0" -- not the
    ## single surface, the file. Step with F_B1_Dun2_1F.dg, whose dungeon
    ## complete was left regardless of a mesh of 4 vertices whose two
    ## faces were degenerate. Callers filter out Nones.
    def _build_primitive(self, positions, normals, uvs, indices,
                          material_idx: int | None, joints=None, weights=None, uvs2=None):
        indices = self._drop_degenerate_faces(positions, indices)
        if not indices or not positions:
            return None
        pos_arr = np.array(positions, dtype=np.float32)
        attrs = gltf.Attributes()
        attrs.POSITION = self._add_accessor(pos_arr, "VEC3", gltf.FLOAT,
                                             target=gltf.ARRAY_BUFFER, minmax=True)
        if normals:
            attrs.NORMAL = self._add_accessor(
                np.array(normals, dtype=np.float32), "VEC3", gltf.FLOAT, target=gltf.ARRAY_BUFFER)
        if uvs:
            attrs.TEXCOORD_0 = self._add_accessor(
                np.array(uvs, dtype=np.float32), "VEC2", gltf.FLOAT, target=gltf.ARRAY_BUFFER)
        if uvs2:
            attrs.TEXCOORD_1 = self._add_accessor(
                np.array(uvs2, dtype=np.float32), "VEC2", gltf.FLOAT, target=gltf.ARRAY_BUFFER)
        if joints is not None:
            attrs.JOINTS_0 = self._add_accessor(
                np.array(joints, dtype=np.uint8), "VEC4", gltf.UNSIGNED_BYTE, target=gltf.ARRAY_BUFFER)
        if weights is not None:
            attrs.WEIGHTS_0 = self._add_accessor(
                np.array(weights, dtype=np.float32), "VEC4", gltf.FLOAT, target=gltf.ARRAY_BUFFER)

        vertex_count = pos_arr.shape[0]
        idx_dtype = np.uint32 if vertex_count > 65535 else np.uint16
        idx_arr = np.array(indices, dtype=idx_dtype).reshape(-1)
        comp_type = gltf.UNSIGNED_INT if idx_dtype == np.uint32 else gltf.UNSIGNED_SHORT
        indices_acc = self._add_accessor(idx_arr, "SCALAR", comp_type, target=gltf.ELEMENT_ARRAY_BUFFER)

        return gltf.Primitive(attributes=attrs, indices=indices_acc, material=material_idx)

    def add_mesh(self, name: str, positions, normals, uvs, indices,
                 material_idx: int | None, joints=None, weights=None, uvs2=None) -> int:
        'Mesh of a single primitive/surface (characters, loose items).'
        primitive = self._build_primitive(positions, normals, uvs, indices, material_idx, joints, weights, uvs2)
        if primitive is None:
            raise ValueError('mesh without valid faces after filtering out the degenerate ones')
        mesh_idx = len(self.meshes)
        self.meshes.append(gltf.Mesh(name=name, primitives=[primitive]))
        return mesh_idx

    # ArrayMesh of Godot does not accept more than 256 surfaces (MAX_MESH_SURFACES).
    # Large assets (a .dg dungeon can contain hundreds of sub-meshes)
    # They overcome that easily even if the number of UNIQUE materials is small --
    # see add_multi_part_mesh.
    MAX_SURFACES = 256

    def add_multi_part_mesh(self, name: str, parts: list[dict]) -> int:
        'Mesh with a unique primitive/surface per MATERIAL (not per\n        original part/sub-mesh -- parts that share material are\n        merge into a single surface, both to avoid stepping on the limit of\n        256 Godot surfaces so that MultiMeshInstance3D can\n        instantiate the entire asset of a).'
        by_material: dict = {}
        order: list = []
        for p in parts:
            # A part without vertices or without faces produces an accessor with
            # count=0, and Godot rejects the WHOLE .glb with "Invalid accessor
            # count 0" -- not the surface alone, the file. step in
            # f_b1_dun2_1f.dg, which brings a mesh with 4 vertices and 0 faces:
            # the entire dungeon remained unimportant.
            if not p.get("positions") or not p.get("indices"):
                continue
            key = p.get("material_idx")
            if key not in by_material:
                by_material[key] = {"positions": [], "normals": [], "uvs": [], "uvs2": [],
                                     "indices": [], "material_idx": key}
                order.append(key)
            group = by_material[key]
            offset = len(group["positions"])
            group["positions"].extend(p["positions"])
            group["normals"].extend(p.get("normals") or [])
            group["uvs"].extend(p.get("uvs") or [])
            group["uvs2"].extend(p.get("uvs2") or [])
            group["indices"].extend(i + offset for i in p["indices"])

        merged_parts = [by_material[k] for k in order]
        if len(merged_parts) > self.MAX_SURFACES:
            raise ValueError(
                f'{name}: {len(merged_parts)}unique materials exceeds the limit of{self.MAX_SURFACES}surfaces per mesh of Godot')

        primitives = [
            prim for prim in (
                self._build_primitive(p["positions"], p["normals"] or None, p["uvs"] or None,
                                       p["indices"], p["material_idx"], uvs2=p["uvs2"] or None)
                for p in merged_parts)
            if prim is not None
        ]
        if not primitives:
            raise ValueError(f'{name}: Nowhere was left with valid faces')
        mesh_idx = len(self.meshes)
        self.meshes.append(gltf.Mesh(name=name, primitives=primitives))
        return mesh_idx

    def add_mesh_multi_primitive(self, name: str, parts: list[dict]) -> int:
        'Like add_multi_part_mesh, but WITHOUT merging parts that share\n        material -- one surface per part, in the same order as the\n        input list. For small assets (VANI) where the caller\n        needs that exact 1:1 correspondence (e.g. re-mapping vertices\n        animated per frame against the already exported mesh) and the limit of 256\n        surfaces is never a real problem.'
        if len(parts) > self.MAX_SURFACES:
            raise ValueError(
                f'{name}: {len(parts)}parts exceeds the limit of{self.MAX_SURFACES}surfaces per mesh of Godot')
        # None are NOT filtered here: this variant exists precisely for
        # maintain 1:1 correspondence with the input list (see the
        # docstring), so an empty part is a caller error and not
        # something that can be skipped quietly.
        primitives = [
            self._build_primitive(p["positions"], p.get("normals") or None, p.get("uvs") or None,
                                   p["indices"], p["material_idx"], uvs2=p.get("uvs2") or None)
            for p in parts
        ]
        mesh_idx = len(self.meshes)
        self.meshes.append(gltf.Mesh(name=name, primitives=primitives))
        return mesh_idx

    def add_node(self, mesh_idx: int | None = None, name: str = "", skin_idx: int | None = None,
                 translation=None, rotation=None, scale=None, children=None) -> int:
        node = gltf.Node(name=name)
        if mesh_idx is not None:
            node.mesh = mesh_idx
        if skin_idx is not None:
            node.skin = skin_idx
        if translation is not None:
            node.translation = [float(v) for v in translation]
        if rotation is not None:
            node.rotation = [float(v) for v in rotation]
        if scale is not None:
            node.scale = [float(v) for v in scale]
        if children:
            node.children = list(children)
        idx = len(self.nodes)
        self.nodes.append(node)
        return idx

    # ---- Esqueleto / skinning ----

    def add_bone_nodes(self, parents: list, trs: list, names: list) -> list:
        'Creates a node per bone with its LOCAL TRS and returns the indices.\n\n        Local and not global because that is what Godot needs to animate and\n        mix: the AnimationTree interpolates local TRS, and bake to space\n        world would make a blend idle->walk stretch the limbs instead of\n        rotate them. The original itself mixes on premises\n        (character_system.cpp:2581).'
        if not (len(parents) == len(trs) == len(names)):
            raise ValueError('parents/trs/names must be the same length')

        base = len(self.nodes)
        for i, (translation, rotation, scale) in enumerate(trs):
            self.add_node(name=names[i], translation=translation, rotation=rotation, scale=scale)

        roots = []
        for i, parent in enumerate(parents):
            if 0 <= parent < len(parents) and parent != i:
                parent_node = self.nodes[base + parent]
                if parent_node.children is None:
                    parent_node.children = []
                parent_node.children.append(base + i)
            else:
                roots.append(base + i)
        return [base + i for i in range(len(parents))], roots

    def add_skin(self, joint_node_indices: list, inverse_bind_matrices, skeleton_root: int | None = None) -> int:
        "joints + inverseBindMatrices. Without this Godot does not build the Skeleton3D\n        although the .glb already has JOINTS_0/WEIGHTS_0: the mesh is imported\n        static and the weights are silently discarded.\n\n        inverse_bind_matrices arrives as (N,4,4) in vector-column convention\n        (see source_skeleton). glTF wants them column-major, which is the\n        transposed from numpy's row-major -- hence the transpose."
        ibm = np.asarray(inverse_bind_matrices, dtype=np.float32)
        if ibm.shape != (len(joint_node_indices), 4, 4):
            raise ValueError('%d 4x4 matrices were expected, %r arrived'
                             % (len(joint_node_indices), ibm.shape))
        flat = np.ascontiguousarray(ibm.transpose(0, 2, 1)).reshape(-1, 16)
        acc = self._add_accessor(flat, "MAT4", gltf.FLOAT)

        skin = gltf.Skin(joints=list(joint_node_indices), inverseBindMatrices=acc)
        if skeleton_root is not None:
            skin.skeleton = skeleton_root
        idx = len(self.skins)
        self.skins.append(skin)
        return idx

    # ---- Animacion ----

    def add_animation(self, name: str, channels: list) -> int:
        'channels: list of dicts {node, path, times, values}.\n\n        path is "translation" | "rotation" | "scale." times in seconds.\n        LINEAR interpolation, which for rotation is the slerp that does\n        sample_rotation in the original (character_system.cpp:247-266).'
        gltf_channels, gltf_samplers = [], []
        for ch in channels:
            times = np.asarray(ch["times"], dtype=np.float32).reshape(-1)
            values = np.asarray(ch["values"], dtype=np.float32)
            if times.shape[0] != values.shape[0]:
                raise ValueError('%s/%s: %d times vs %d values'
                                 % (name, ch["path"], times.shape[0], values.shape[0]))
            # min/max in the input accessor is required by spec; without
            # The Godot imports the animation with duration 0.
            input_acc = self._add_accessor(times, "SCALAR", gltf.FLOAT, minmax=True)
            type_str = "VEC4" if ch["path"] == "rotation" else "VEC3"
            output_acc = self._add_accessor(values, type_str, gltf.FLOAT)

            sampler_idx = len(gltf_samplers)
            gltf_samplers.append(gltf.AnimationSampler(
                input=input_acc, output=output_acc, interpolation="LINEAR"))
            gltf_channels.append(gltf.AnimationChannel(
                sampler=sampler_idx,
                target=gltf.AnimationChannelTarget(node=ch["node"], path=ch["path"]),
            ))

        idx = len(self.animations)
        self.animations.append(gltf.Animation(name=name, channels=gltf_channels, samplers=gltf_samplers))
        return idx

    def save(self, dst_path: Path, root_node_indices: list[int] | None = None) -> None:
        dst_path.parent.mkdir(parents=True, exist_ok=True)

        doc = gltf.GLTF2()
        doc.asset = gltf.Asset(generator="Research/tools", version="2.0")
        doc.meshes = self.meshes
        doc.materials = self.materials
        doc.textures = self.textures
        doc.images = self.images
        doc.samplers = self.samplers
        doc.nodes = self.nodes
        doc.bufferViews = self.buffer_views
        doc.accessors = self.accessors
        doc.skins = self.skins
        doc.animations = self.animations

        if root_node_indices is None:
            # Only nodes that are not children of anyone. List all (as
            # before, when the tree was flat) would duplicate each bone: a
            # once as the root of the scene and another time hanging from his father, and
            # Godot would import the entire flattened skeleton.
            child_nodes = set()
            for node in self.nodes:
                if node.children:
                    child_nodes.update(node.children)
            root_node_indices = [i for i in range(len(self.nodes)) if i not in child_nodes]
        doc.scenes = [gltf.Scene(nodes=root_node_indices)]
        doc.scene = 0
        doc.buffers = [gltf.Buffer(byteLength=len(self.buffer_blob))]
        doc.set_binary_blob(bytes(self.buffer_blob))

        # Shared textures are referenced with a path RELATIVE to the .glb,
        # which is the only thing that the Godot importer resolves (and what makes it
        # the project remains mobile). walk_up allows the ".." that do
		# missing to exit data/entity/<seccion>/ to data/textures/.
        for i in self._pending_uris:
            destino = SHARED_TEXTURES / doc.images[i].uri
            relativa = destino.relative_to(Path(dst_path).resolve().parent, walk_up=True)
            doc.images[i].uri = quote(str(relativa).replace("\\", "/"))
        doc.save(str(dst_path))
