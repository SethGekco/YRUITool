"""
Obtainability classifier: for each TechnoType, how does a player actually get it?

Labels every unit/building as one of:
  * buildable  — appears in a build tree (TechLevel >= 0)
  * crate      — not buildable, but a crate can grant it (CrateGoodie=yes)
  * other      — not buildable, obtained another way (FreeUnit=, Spawns=,
                 DeploysInto/UndeploysInto, superweapon delivery)
  * dummy      — not buildable; only referenced by other code (a prerequisite-only
                 placeholder, or name-marked) — logic object, not a real unit
  * civilian   — not buildable, has art, and nothing references it (map/neutral obj)
  * unknown    — not buildable and no clear obtain path (flag for the user)

I/O-free; works on a parsed section_map (optionally an art section_map to judge
"has art"). Each result carries human-readable reasons so the UI can be honest
about uncertainty. Deliberately conservative: when in doubt it says `unknown`
rather than guess.
"""

from dataclasses import dataclass, field

from .ini_model import get_val
from .config import ModConfig
from .typelists import parse_type_list

OB_BUILDABLE = "buildable"
OB_CRATE = "crate"
OB_OTHER = "other"
OB_DUMMY = "dummy"
OB_CIVILIAN = "civilian"
OB_UNKNOWN = "unknown"

# Keys whose values reference a TechnoType by name → normalized obtain tag.
_OTHER_REF_KEYS = {
    "freeunit": "freeunit",
    "spawns": "spawn",
    "deploysinto": "deploy",
    "undeploysinto": "deploy",
    "payload": "superweapon",
    "unitdelivery.types": "superweapon",
    "deliver": "superweapon",
}
_OTHER_TAGS = ("freeunit", "spawn", "deploy", "superweapon")
_TAG_REASON = {
    "freeunit": "free from a building (FreeUnit=)",
    "spawn": "spawned by another unit (Spawns=)",
    "deploy": "deploy/undeploy of another object",
    "superweapon": "superweapon / delivery payload",
}


@dataclass
class Obtainability:
    name: str
    cls: str
    reasons: list = field(default_factory=list)
    also: list = field(default_factory=list)   # secondary flags (e.g. crate-eligible)


def _clean(v):
    return "" if v is None else v.split(";", 1)[0].strip()


def _num(v):
    s = _clean(v).rstrip("%")
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _techno_names(section_map):
    out = []
    for sec in ("InfantryTypes", "VehicleTypes", "AircraftTypes", "BuildingTypes"):
        out += parse_type_list(section_map, sec)
    return out


def _build_reference_index(section_map, techno_lower):
    """Map techno_lower → {ref_tag} for every place a techno is referenced in a
    way that affects obtainability. One pass over all section keys."""
    refs = {}
    for _low, s in section_map.items():
        for klow, (_li, _kr, vr) in s["keys"].items():
            if klow == "prerequisite" or klow.startswith("prerequisite"):
                tag = "prereq"
            else:
                tag = _OTHER_REF_KEYS.get(klow)
            if tag is None:
                continue
            for tok in _clean(vr).replace(",", " ").split():
                t = tok.lower().lstrip("-")
                if t in techno_lower:
                    refs.setdefault(t, set()).add(tag)
    return refs


def _has_art(section_map, art_map, name):
    image = _clean(get_val(section_map, name, "Image")) or name
    if art_map is not None:
        return image.lower() in art_map
    # No art map: fall back to whether an Image was explicitly set.
    return bool(_clean(get_val(section_map, name, "Image")))


def classify_obtainability(section_map, config=None, art_map=None):
    """Return {name_lower: Obtainability} for every declared TechnoType."""
    config = config or ModConfig.default()
    names = _techno_names(section_map)
    techno_lower = {n.lower() for n in names}
    refs = _build_reference_index(section_map, techno_lower)

    out = {}
    for name in names:
        low = name.lower()
        up = name.upper()
        tl = _num(get_val(section_map, name, "TechLevel"))   # engine default -1
        cost = _num(get_val(section_map, name, "Cost"))
        crate = _clean(get_val(section_map, name, "CrateGoodie")).lower() == "yes"
        rset = refs.get(low, set())
        has_art = _has_art(section_map, art_map, name)
        capturable = _clean(get_val(section_map, name, "Capturable")).lower() == "yes"
        reasons, also = [], []
        if crate:
            also.append("crate-eligible")

        if "DUMMY" in up:
            # A name-marked dummy is a dummy even if it carries a TechLevel.
            cls = OB_DUMMY
            reasons.append("name marks a dummy/placeholder")
            if "prereq" in rset:
                reasons.append("used only as a prerequisite")
        elif tl is not None and tl >= 0:
            cls = OB_BUILDABLE
            reasons.append(f"TechLevel {int(tl)} (in a build tree)")
        elif crate:
            cls = OB_CRATE
            reasons.append("not buildable; CrateGoodie=yes")
        elif rset & set(_OTHER_TAGS):
            cls = OB_OTHER
            reasons += [_TAG_REASON[t] for t in _OTHER_TAGS if t in rset]
        elif capturable:
            cls = OB_OTHER
            reasons.append("captured (Capturable=yes)")
        elif up.endswith("PRE") or (
                "prereq" in rset and not has_art and (cost is None or cost == 0)):
            cls = OB_DUMMY
            if up.endswith("PRE"):
                reasons.append("name marks a dummy/placeholder")
            if "prereq" in rset:
                reasons.append("used only as a prerequisite")
            if not reasons:
                reasons.append("not buildable, no art, free — logic placeholder")
        elif has_art and not rset:
            cls = OB_CIVILIAN
            reasons.append("not buildable, has art, unreferenced (civilian/map object)")
        else:
            cls = OB_UNKNOWN
            reasons.append("not buildable; no clear way to obtain it")

        out[low] = Obtainability(name, cls, reasons, also)
    return out
