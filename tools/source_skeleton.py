'Canonical character skeleton: hierarchy, resting poses and TRS.\n\nModule shared by convert_character.py (meshes + skin) and convert_ani.py\n(animations). It exists because the skeleton lives divided into two formats and\nneither of them has it in its entirety:\n\n  - .3dc brings the bind matrices per bone, but NOT the hierarchy;\n  - the .ani brings the hierarchy (parentBoneIndex), but its matrix per bone\n    is the resting pose OF THAT CLIP, not the bind of the model.\n\nCONVENTION OF MATRICES (the easy part to get wrong)\n----------------------------------------------------\nThe original transposes twice and the transpositions cancel:\ncharacter_loader.cpp:47-70 (read_matrix, "column-first") and then\ncharacter_system.cpp:204-212 (mat4_from_source_transposed). Net: Mat4\nof the engine is the float[16] of the row-major file read as is, in\nvector-ROW convention (translation in the last row).\n\nread_matrix() of convert_character.py/convert_ani.py replicates only the\nfirst transposition, so the matrices that we handle in Python are the\nTRANSPOSE of the motor: standard vector-COLUMN convention, the same\nwhat glTF. All the mathematics down here is in that convention, and that\'s why\nThe order of the products appears reversed with respect to C++.\n\nTranslated, the skinning of character_system.cpp:2640 (mat4_multiply(meshBone,\nclientFinals[bone]), applied in :2646) is v_out = F * B * v: B is applied\nfirst, that is, the matrix of the .3dc is the INVERSE-BIND and it goes directly to\ninverseBindGlTF arrays. Also verified by measuring where B sends a\nthe vertices that each bone influences: |B*v| ~0.06-0.15 (falls on the joint),\nvs |B^-1*v| ~1.3-2.3 (it goes to the height of the model).\n\nThe .ani quaternion goes to glTF WITHOUT conjugation: mat4_from_rotation_translation\n(character_system.cpp:141-162) assemble the transpose of the standard R(q) with\nthe same (x,y,z,w), so when we go to the column convention the R(q) remains\nstandard with the same q.'

import glob
import os
from pathlib import Path

import numpy as np


def as_matrix(flat16) -> np.ndarray:
    'The float[16] already reordered by read_matrix -> 4x4 vector-column.'
    return np.array(flat16, dtype=np.float64).reshape(4, 4)


def bone_name(index: int) -> str:
    'Stable name by index. The .3dc/.ani do not have names: the index\n    bone ES identity (character_system.cpp:2640 indexes clientFinals\n    with the same boneIndex that comes from the .3dc weights). It has to be\n    identical between the mesh GLB and the animation one so that Godot can\n    re-point the tracks to the same Skeleton3D.'
    return "bone_%03d" % index


def decompose(m: np.ndarray) -> tuple:
    '4x4 -> (translation, xyzw quaternion, scale). The matrices of\n    dataset are rigid (checked: det(R)=1.0000, |R*Rt-I|=0), but the\n    scale is extracted the same so as not to break if a rare asset appears.'
    t = m[:3, 3].tolist()
    r = m[:3, :3].copy()
    scale = np.linalg.norm(r, axis=0)
    scale[scale < 1e-12] = 1.0
    r = r / scale

    # Shepperd: Choosing the largest pivot avoids division because ~0
    # It has the direct form when the trace is negative.
    trace = r[0, 0] + r[1, 1] + r[2, 2]
    if trace > 0.0:
        s = np.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (r[2, 1] - r[1, 2]) / s
        y = (r[0, 2] - r[2, 0]) / s
        z = (r[1, 0] - r[0, 1]) / s
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = np.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2.0
        w = (r[2, 1] - r[1, 2]) / s
        x = 0.25 * s
        y = (r[0, 1] + r[1, 0]) / s
        z = (r[0, 2] + r[2, 0]) / s
    elif r[1, 1] > r[2, 2]:
        s = np.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2.0
        w = (r[0, 2] - r[2, 0]) / s
        x = (r[0, 1] + r[1, 0]) / s
        y = 0.25 * s
        z = (r[1, 2] + r[2, 1]) / s
    else:
        s = np.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2.0
        w = (r[1, 0] - r[0, 1]) / s
        x = (r[0, 2] + r[2, 0]) / s
        y = (r[1, 2] + r[2, 1]) / s
        z = 0.25 * s

    q = np.array([x, y, z, w], dtype=np.float64)
    n = np.linalg.norm(q)
    q = q / n if n > 1e-12 else np.array([0.0, 0.0, 0.0, 1.0])
    return t, q.tolist(), scale.tolist()


def globals_to_locals(globals_: list, parents: list) -> list:
    """local_i = inv(global_padre) * global_i.

    Es compute_client_finals (character_system.cpp:315) pasado a convencion
    columna: alla es mat4_multiply(raw_i, inv(raw_padre)) en vector-fila.
    """
    out = []
    for i, g in enumerate(globals_):
        p = parents[i]
        if 0 <= p < len(globals_):
            out.append(np.linalg.inv(globals_[p]) @ g)
        else:
            out.append(g.copy())
    return out


class Skeleton:
    "Canonical skeleton of a race/gender prefix (humf, elmr, ...).\n\n    It provides the HIERARCHY and the names, which are the only thing really shared\n    between all the parts and all the clips: it is what the original does,\n    where an .ani moves the entire skeleton and all equipped parts are\n    warp with the same clientFinals (character_system.cpp:2615-2653\n    loops through ALL sourceVertices of the composite character with a single\n    array of endings).\n\n    The resting pose is NOT shared on purpose. The inverse-bind is for\n    part in the original (meshBones[meshBoneBase + boneIndex], indexed by\n    part, against clientFinals[boneIndex], shared), precisely so that\n    each mesh can be modeled in its own pose. Force a rest\n    only deforms the parts that do not match: with humf_hand007 of\n    reference, humf_lower001 was going 0.49 and humf_boots007 0.96. That's why every\n    .glb uses its own inv(B) as rest, and rest_globals from here only covers\n    the bones that that part does not have."

    def __init__(self, parents: list, rest_globals: list, prefix: str = ""):
        self.parents = parents
        self.rest_globals = rest_globals
        self.prefix = prefix
        self.names = [bone_name(i) for i in range(len(parents))]

    def __len__(self) -> int:
        return len(self.parents)

    def trs_for(self, globals_: list | None = None) -> list:
        'Local TRS by bone. globals_ (optional) overrides the rest of\n        consensus for the bones it covers -- there goes the inv(B) of the part\n        that is being exported.'
        merged = list(self.rest_globals)
        for i, g in enumerate(globals_ or []):
            if i < len(merged):
                merged[i] = g
        return [decompose(m) for m in globals_to_locals(merged, self.parents)]


def hierarchy_from_files(ani_paths: list) -> tuple:
    "Returns (parents, conflicts) reading an explicit list of .ani.\n\n    You can take the hierarchy from any file because it is stable:\n    out of humf's 86 .anis there is only ONE variant of parentBoneIndex, and\n    about humm's 69 too. It is also verified and notified if any\n    file is exited, instead of assumed.\n\n    WHAT ARE VEHICLE CLIPS WITH TWICE THE BONES?\n    ----------------------------------------------------\n    5 files on the 1192 of the dataset (humf x2, humm x3) bring a\n    SECOND entire copy of a 36-bone humanoid skeleton glued to the\n    final: humf_020_veh_run has 72 = 36 + 36, and humm_020_veh_run has 92 =\n    56 + 36. Measured: the parents of the added block are those of the\n    36, displaced; and in both cases NO bone from the added block\n    has keyframes (the highest index anime is the last one in the\n    real skeleton). He is the second horseman's placeholder -- the same one as in\n    data/vehicle/*.csv appears as the Bone2 column, always -1.\n\n    Those files are left out of the consensus and nothing happens: the consensus leaves\n    identical without them, and the clip itself converts well because\n    convert_ani.build_channels loops range(len(skeleton)) -- so\n    truncates the canonical skeleton and discards the single copy."
    from convert_ani import parse_ani

    best: list = []
    conflicts = []
    for path in sorted(ani_paths):
        try:
            ani = parse_ani(Path(path))
        except Exception:
            continue
        parents = [b["parentBoneIndex"] for b in ani["bones"]]
        n = min(len(parents), len(best))
        if parents[:n] != best[:n]:
            # Distinguish the inert queue from a really different hierarchy:
            # If the mismatched indices are not animated by this clip, the
            # warning, it is not worth going to look at the file.
            animated = {i for i, b in enumerate(ani["bones"])
                        if b["rotationFrames"] or b["translationFrames"]}
            inert = not any(i in animated for i in range(n) if parents[i] != best[i])
            conflicts.append((os.path.basename(path), inert))
            continue
        if len(parents) > len(best):
            best = parents

    if not best:
        raise ValueError('Could not read any .ani from the list')
    return best, conflicts


def load_hierarchy(ani_dir: Path, prefix: str) -> tuple:
    'hierarchy_from_files over all .anis of a character prefix.'
    paths = glob.glob(str(ani_dir / ("%s_*.[aA][nN][iI]" % prefix)))
    if not paths:
        raise ValueError("%s: Could not read any .ani of '%s'" % (ani_dir, prefix))
    return hierarchy_from_files(paths)


def describe_conflicts(conflicts: list) -> list:
    'Readable warnings for what hierarchy_from_files returns.'
    inert = [name for name, is_inert in conflicts if is_inert]
    real = [name for name, is_inert in conflicts if not is_inert]
    out = []
    if inert:
        out.append("%d .ani con huesos de mas sin animar al final, fuera del "
                   "consenso (inofensivo, ver hierarchy_from_files): %s"
                   % (len(inert), ", ".join(inert[:4])))
    if real:
        out.append("%d .ani con jerarquia REALMENTE distinta (ignorados): %s"
                   % (len(real), ", ".join(real[:4])))
    return out


_SKELETON_CACHE: dict = {}


def build_skeleton_cached(race_dir: Path, prefix: str) -> tuple:
    'build_skeleton scans all .3dc and .ani prefix, so\n    calling it by converted part would be quadratic (52 parts x 52 files\n    by race). The skeleton is the same for all, it is calculated once.'
    key = (str(race_dir).lower(), prefix.lower())
    if key not in _SKELETON_CACHE:
        _SKELETON_CACHE[key] = build_skeleton(race_dir, prefix)
    return _SKELETON_CACHE[key]


def build_skeleton_from_files(mesh_paths: list, ani_paths: list,
                              prefix: str = "", part_bone_counts: dict | None = None) -> tuple:
    'build_skeleton over EXPLICIT lists of .3dc and .ani.\n\n    It exists apart from the glob for vehicles: a vehicle is not a prefix\n    with its own directory but a RECORD of data/vehicle/*.csv that\n    names your meshes and your 5 clips one by one (and there are shared meshes\n    between records, such as vehicle_Hu_01.3DC between records 0 and 2). The\n    file list comes out of the CSV, which is the same authority used by the\n    original (character_system.cpp:1670-1720).'
    from convert_character import parse_3dc

    warnings = []
    parents, conflicts = hierarchy_from_files(ani_paths)
    warnings.extend(describe_conflicts(conflicts))

    # The resting pose comes from .3dc, not from .ani: the one from .ani is the one from
    # clip and switch between files (29 of 30 differ, up to 1.89).
    #

    # CONSENSUS is taken by bone between all parties, not a part of
    # reference: choose "the one with the most bones" grabbed humf_hand007,
    # which is an outlier (differs 1.06 from the rest) and deformed the others.
    # Consensus is only used for bones that the exported part does not
    # has -- see Skeleton.trs_for.
    per_bone: dict = {}
    max_part_bones = 0
    for path in sorted(mesh_paths):
        try:
            model = parse_3dc(Path(path))
        except Exception:
            continue
        max_part_bones = max(max_part_bones, len(model["bones"]))
        for i, flat in enumerate(model["bones"]):
            key = tuple(np.round(np.asarray(flat, dtype=np.float64), 4))
            per_bone.setdefault(i, {})
            per_bone[i][key] = per_bone[i].get(key, 0) + 1
    if not per_bone:
        raise ValueError("could not read any .3dc of '%s'" % (prefix or "?"))

    bone_count = max(len(parents), max_part_bones)
    if part_bone_counts:
        bone_count = max(bone_count, max(part_bone_counts.values()))

    if bone_count > len(parents):
        extra = bone_count - len(parents)
        warnings.append(
            "%d hueso(s) mas alla de los %d que trae el .ani: se cuelgan de la raiz y "
            "quedan en bind, sin animacion" % (extra, len(parents)))
        parents = list(parents) + [0] * extra

    # inv(B) = global bind of the bone. The bones that nowhere covers
    # They remain in identity: they have no known bind and no clip moves them.
    rest_globals = []
    for i in range(bone_count):
        votes = per_bone.get(i)
        if votes:
            winner = max(votes.items(), key=lambda kv: kv[1])[0]
            rest_globals.append(np.linalg.inv(as_matrix(list(winner))))
        else:
            rest_globals.append(np.eye(4))

    return Skeleton(parents[:bone_count], rest_globals, prefix), warnings


def build_skeleton(race_dir: Path, prefix: str, part_bone_counts: dict | None = None) -> tuple:
    'Build the canonical skeleton of a character prefix.\n\n    part_bone_counts: {nombre_parte: n_huesos} of the .3dc already read. It is used\n    to size the skeleton, because there are parts with MORE bones than\n    any .ani of your race (viwr 61 vs 44, viwm 61 vs 55, vimm 55 vs 51,\n    humf_hand007 42 vs 38 -- ~20 files out of 7320). Those extra bones\n    They hang from the root and remain in bind: no clip touches them, so\n    encouraging them is not an option, but throwing away the entire conversion for them\n    neither.\n\n    Returns (Skeleton, notices).'
    ani_paths = glob.glob(str(race_dir / "ani" / ("%s_*.[aA][nN][iI]" % prefix)))
    if not ani_paths:
        raise ValueError("%s: Could not read any .ani of '%s'" % (race_dir, prefix))
    mesh_paths = glob.glob(str(race_dir / "3dc" / ("%s_*.3dc" % prefix)))
    return build_skeleton_from_files(mesh_paths, ani_paths, prefix, part_bone_counts)
