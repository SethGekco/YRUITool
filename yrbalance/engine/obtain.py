"""
Obtainability classifier: for each TechnoType, how does a player actually get it,
and if not, what is it?

Classes:
  * normal    — player-buildable (TechLevel >= 0, in a build tree)
  * special   — not buildable, but a player can still get it indirectly (super-
                weapon delivery, FreeUnit, spawn, deploy, crew ejected on sell,
                paradrop, capture) AND it has an image
  * crate     — not buildable; a crate can grant it (CrateGoodie=yes)
  * ai-only   — not buildable, not player-obtainable, has an image, and appears in
                aimd.ini TaskForces (the AI builds it; the player can't)
  * civilian  — not buildable, not obtainable, has an image, and nothing in the AI
                or logic references it (map / neutral / prop object)
  * dummy     — a logic object, not a real unit: no image (nothing to see), or an
                indirect-only object with no image, or a superweapon weapon-holder,
                or a name-marked / prerequisite-only placeholder
  * unknown   — residual: no class could be decided (should be rare)

Decision order (first match wins), mirroring the intended taxonomy:
  name-marked DUMMY/PRE → dummy
  TechLevel >= 0 → normal
  CrateGoodie=yes → crate
  indirect obtain (SW / FreeUnit / spawn / deploy / [General] crew-paradrop /
      capturable):  has image → special ; no image → dummy
  otherwise unobtainable:
      has image → superweapon weapon-holder → dummy
                  prerequisite-only placeholder → dummy
                  appears in aimd → ai-only
                  else → civilian
      no image → dummy

I/O-free; works on a parsed rules section_map, optionally an art section_map (to
judge "has art") and an aimd section_map (to spot AI-only units). Each result
carries human-readable reasons so the UI can be honest about uncertainty.
"""

from dataclasses import dataclass, field

from .ini_model import get_val
from .config import ModConfig
from .typelists import parse_type_list
from .weapons import ALL_WEAPON_TAGS

OB_NORMAL = "normal"
OB_SPECIAL = "special"
OB_CRATE = "crate"
OB_AI_ONLY = "ai-only"
OB_CIVILIAN = "civilian"
OB_DUMMY = "dummy"
OB_UNKNOWN = "unknown"

ALL_OBTAIN_CLASSES = (OB_NORMAL, OB_SPECIAL, OB_CRATE, OB_AI_ONLY,
                      OB_CIVILIAN, OB_DUMMY, OB_UNKNOWN)

# Keys whose values reference a TechnoType by name → normalized indirect-obtain tag.
_OTHER_REF_KEYS = {
    "freeunit": "freeunit",
    "spawns": "spawn",
    "deploysinto": "deploy",
    "undeploysinto": "deploy",
    "payload": "superweapon",
    "unitdelivery.types": "superweapon",
    "deliver": "superweapon",
}
# Indirect-obtain tags (a player CAN end up with the unit, just not by building it).
_OTHER_TAGS = ("freeunit", "spawn", "deploy", "superweapon", "general")
_TAG_REASON = {
    "freeunit": "free from a building (FreeUnit=)",
    "spawn": "spawned by another unit (Spawns=)",
    "deploy": "deploy/undeploy of another object",
    "superweapon": "superweapon / delivery payload",
    "general": "referenced in [General] (crew / paradrop / base / free unit)",
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


# The techno list sections name every unit; an entry there is registration, not
# a "reference", so they're skipped when judging whether anything references a unit.
_LIST_SECTIONS = {"infantrytypes", "vehicletypes", "aircrafttypes",
                  "buildingtypes"}


def _build_reference_index(section_map, techno_lower):
    """Map techno_lower → {ref_tag} for every place a techno is referenced in a
    way that affects obtainability. One pass over all section keys, plus a scan of
    [General] (crew / paradrop / base / free units are all referenced there).
    The techno type-list sections are skipped (listing is not a reference)."""
    refs = {}
    for low, s in section_map.items():
        if low in _LIST_SECTIONS:
            continue
        general = (low == "general")
        for klow, (_li, _kr, vr) in s["keys"].items():
            if klow == "prerequisite" or klow.startswith("prerequisite"):
                tag = "prereq"
            else:
                tag = _OTHER_REF_KEYS.get(klow)
            for tok in _clean(vr).replace(",", " ").split():
                t = tok.lower().lstrip("-")
                if t not in techno_lower or t == low:
                    continue      # unknown token, or a section referencing itself
                if tag is not None:
                    refs.setdefault(t, set()).add(tag)
                # Any techno named anywhere in [General] is obtainable somehow
                # (base unit, paradrop, crew, free unit, …).
                if general:
                    refs.setdefault(t, set()).add("general")
                # Record a bare "referenced" marker so we can tell civilian
                # (unreferenced) from logic objects.
                refs.setdefault(t, set()).add("@ref")
    return refs


def _sw_weapon_set(section_map):
    """Weapons named by SuperWeapon sections (keys ending in 'weapon'). Conservative:
    only fires on an explicit Weapon= reference, so there are no false positives on
    mods that don't use that pattern."""
    weapons = set()
    for sw in parse_type_list(section_map, "SuperWeaponTypes"):
        s = section_map.get(sw.lower())
        if not s:
            continue
        for klow, (_li, _kr, vr) in s["keys"].items():
            if klow == "weapon" or klow.endswith("weapon"):
                w = _clean(vr).lower()
                if w and w not in ("none", ""):
                    weapons.add(w)
    return weapons


def _unit_weapons(section_map, name):
    """Lowercased weapon section names a techno carries (primary/secondary/…)."""
    out = set()
    for tag in ALL_WEAPON_TAGS:
        w = _clean(get_val(section_map, name, tag))
        if w and w.lower() not in ("none", ""):
            out.add(w.lower())
    return out


def ai_unit_ids(aimd_map):
    """Lowercased unit IDs that appear in any aimd.ini TaskForce (count,UNITID)."""
    ids = set()
    if not aimd_map:
        return ids
    for tf in parse_type_list(aimd_map, "TaskForces"):
        s = aimd_map.get(tf.lower())
        if not s:
            continue
        for klow, (_li, _kr, vr) in s["keys"].items():
            if not klow.isdigit():
                continue
            parts = _clean(vr).split(",")
            if len(parts) >= 2 and parts[1].strip():
                ids.add(parts[1].strip().lower())
    return ids


def _art_state(section_map, art_map, name):
    """'yes' / 'no' / 'unknown' — whether the techno has an image.

    With an art map we can decide (image entry present or not). Without one we
    genuinely can't tell, so we return 'unknown' rather than guess — the caller
    then avoids image-dependent verdicts (civilian vs dummy) and stays honest."""
    if art_map is None:
        return "unknown"
    image = _clean(get_val(section_map, name, "Image")) or name
    return "yes" if image.lower() in art_map else "no"


def classify_obtainability(section_map, config=None, art_map=None, aimd_map=None):
    """Return {name_lower: Obtainability} for every declared TechnoType.

    art_map  — artmd section map, to judge "has an image".
    aimd_map — aimd section map, to spot AI-only units (built by AI TaskForces).
    """
    config = config or ModConfig.default()
    names = _techno_names(section_map)
    techno_lower = {n.lower() for n in names}
    refs = _build_reference_index(section_map, techno_lower)
    sw_weapons = _sw_weapon_set(section_map)
    ai_units = ai_unit_ids(aimd_map)

    out = {}
    for name in names:
        low = name.lower()
        up = name.upper()
        tl = _num(get_val(section_map, name, "TechLevel"))   # engine default -1
        cost = _num(get_val(section_map, name, "Cost"))
        crate = _clean(get_val(section_map, name, "CrateGoodie")).lower() == "yes"
        rset = refs.get(low, set())
        art = _art_state(section_map, art_map, name)
        capturable = _clean(get_val(section_map, name, "Capturable")).lower() == "yes"
        indirect = (rset & set(_OTHER_TAGS)) or capturable
        in_aimd = low in ai_units
        referenced = bool(rset)   # named in any key value anywhere (incl. @ref)
        # Art-independent "logic object" signals.
        sw_holder = bool(sw_weapons
                         and (_unit_weapons(section_map, name) & sw_weapons))
        prereq_dummy = up.endswith("PRE") or (
            "prereq" in rset and (cost is None or cost == 0))
        reasons, also = [], []
        if crate:
            also.append("crate-eligible")
        if in_aimd:
            also.append("in aimd TaskForces")

        if "DUMMY" in up:
            cls = OB_DUMMY
            reasons.append("name marks a dummy/placeholder")
            if "prereq" in rset:
                reasons.append("used only as a prerequisite")

        elif tl is not None and tl >= 0:
            cls = OB_NORMAL
            reasons.append(f"TechLevel {int(tl)} (player-buildable)")

        elif crate:
            cls = OB_CRATE
            reasons.append("not buildable; CrateGoodie=yes")

        elif indirect:
            # Obtainable indirectly. With no art it's a logic object delivering an
            # effect (dummy); otherwise a real "special" unit.
            ind_reasons = [_TAG_REASON[t] for t in _OTHER_TAGS if t in rset]
            if capturable:
                ind_reasons.append("captured (Capturable=yes)")
            if art == "no":
                cls = OB_DUMMY
                reasons.append("indirect-obtain object with no art — logic object")
            else:
                cls = OB_SPECIAL
            reasons += ind_reasons

        elif sw_holder:
            cls = OB_DUMMY
            reasons.append("holds a weapon used by a superweapon — logic object")

        elif prereq_dummy:
            cls = OB_DUMMY
            if up.endswith("PRE"):
                reasons.append("name marks a dummy/placeholder")
            if "prereq" in rset:
                reasons.append("used only as a prerequisite")

        elif art == "yes":
            # Unobtainable but has an image: AI-only or civilian prop.
            if in_aimd:
                cls = OB_AI_ONLY
                reasons.append("not player-obtainable; built by AI TaskForces (aimd)")
            elif not referenced:
                cls = OB_CIVILIAN
                reasons.append(
                    "not buildable, has art, unreferenced (civilian / map object)")
            else:
                cls = OB_CIVILIAN
                reasons.append(
                    "not buildable, has art, not AI-used (likely civilian / prop)")

        elif art == "no":
            # Unobtainable and definitely no image → logic object.
            cls = OB_DUMMY
            reasons.append("not buildable, no art — logic object / dummy")

        else:
            # Art can't be determined (no art map). Use art-independent signals.
            if in_aimd:
                cls = OB_AI_ONLY
                reasons.append("not player-obtainable; built by AI TaskForces (aimd)")
            else:
                cls = OB_UNKNOWN
                reasons.append("not buildable; no clear obtain path (art unknown)")

        out[low] = Obtainability(name, cls, reasons, also)
    return out
