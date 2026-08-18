'Weapon/shield anchor bones per character.\n\nPort of src/character/weapon_bone_map.cpp::apply_default_attach_bones. The\nvalues are recovered from the original skeletons, not calculated: each\nprefix has its own bone numbering (humf 42, huwm 65, vimr 36), so\nthat "the right wrist" is a different index in each one and there is no way to\ndeduce it.\n\nThe weapon does not deform: it hangs entirely from the bone and transforms with\nclientFinals[bone] WITHOUT inverse-bind (character_system.cpp:2683), which in Godot\nis exactly a BoneAttachment3D.'

## prefix -> (weapon bone, shield bone).
BASE_BONES = {
    # Human
    "humf": (24, 10), "huwf": (27, 13),
    "humm": (24, 10), "huwm": (27, 13),
    # Elf
    "elmm": (25, 13), "elwm": (27, 14),
    "elmr": (23, 10), "elwr": (27, 14),
    # Deatheater
    "demf": (25, 10), "dewf": (28, 14),
    "demr": (24, 10), "dewr": (28, 14),
    # Vile
    "vimm": (22, 10), "viwm": (28, 16),
    "vimr": (24, 10), "viwr": (30, 16),
}

## Secondary hand of the dual wield: own bone plus a fine adjustment in the
## local space of that bone -- position (X, Y, Z) and rotation in degrees
## (X, Y, Z), applied BEFORE the bone matrix.
## The prefixes that are not here use the shield bone.
DUAL_BONES = {
    # Espadas dobles (guerreros)
    "humf": (15, (0.0, 0.0, 0.0), (180.0, 0.0, 0.0)),
    "huwf": (17, (0.0, 0.0, 0.0), (180.0, 0.0, 0.0)),
    "demf": (15, (0.0, 0.0, 0.0), (180.0, 180.0, 0.0)),
    "dewf": (18, (0.030, 0.0, 0.0), (180.0, 180.0, 0.0)),
    # Garras (rangers)
    "elmr": (14, (0.325, 0.0, 0.0), (0.0, -100.0, 0.500)),
    "elwr": (14, (0.325, 0.0, 0.0), (0.0, -100.0, 0.500)),
    "vimr": (14, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
    "viwr": (20, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
}

## Ranged classes route the weapon to a dedicated bone. The order of
## original is an if/else-if: the remote ones are tested first and then
## then the claw, so an elmr with a bow goes to 14 and with a claw to 22.
RANGED_OVERRIDES = {
    "elmr": (("bow", "crossbow"), 14),
    "elwr": (("bow", "crossbow"), 18),
    "demr": (("bow", "javelin"), 14),
    "dewr": (("bow", "javelin"), 18),
}

CLAW_OVERRIDES = {"elmr": 22, "elwr": 26}

## Only these two types draw a second copy of the same mesh on the
## other hand (character_system.h:69-72).
DUAL_WIELD_TYPES = ("dualsword", "claw")


def attach_for_prefix(prefix: str) -> dict | None:
    '"attach" block that is saved in character.txt. Returns None if the\n    prefix is not in the table (the original leaves any defaults).'
    prefix = prefix.lower()
    if prefix not in BASE_BONES:
        return None

    weapon_bone, shield_bone = BASE_BONES[prefix]

    overrides: dict = {}
    if prefix in RANGED_OVERRIDES:
        types, bone = RANGED_OVERRIDES[prefix]
        for weapon_type in types:
            overrides[weapon_type] = bone
    if prefix in CLAW_OVERRIDES:
        overrides.setdefault("claw", CLAW_OVERRIDES[prefix])

    # By default the secondary hand mounts on the shield bone.
    dual = {"bone": shield_bone, "offsetPos": [0.0, 0.0, 0.0],
            "offsetRotDeg": [0.0, 0.0, 0.0]}
    if prefix in DUAL_BONES:
        bone, pos, rot = DUAL_BONES[prefix]
        dual = {"bone": bone, "offsetPos": list(pos), "offsetRotDeg": list(rot)}

    return {
        "weaponBone": weapon_bone,
        "shieldBone": shield_bone,
        "weaponBoneOverrides": overrides,
        "dual": dual,
        "dualWieldTypes": list(DUAL_WIELD_TYPES),
    }
