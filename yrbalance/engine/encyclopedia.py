"""
Mod encyclopedia: a searchable, unit-centric index of one or more mods.

For a mod folder it builds a UnitEntry per TechnoType with everything about it,
assembled from existing engine pieces: stats + weapon chain (rules), art/sound
entries (artmd/soundmd), the asset files each references and WHERE they live
(loose or which .mix, via the media catalog), whether a missing reference is a
vanilla asset or truly absent, and how the unit is obtained. Several mods can be
indexed at once so users can search/compare/export across a library of sources.

I/O-free except build_mod_index (reads the mod's files) and export_unit (writes).
Reuses the porter's resolution helpers so there is one source of truth.
"""

import os
from dataclasses import dataclass, field

from .ini_model import get_val, parse_ini_lines, build_section_map
from .config import ModConfig
from .typelists import parse_type_list
from .weapons import ALL_WEAPON_TAGS
from .obtain import classify_obtainability
from .mixlib import build_media_catalog, resolve_media, mix_id
from .portmod import _clean, _ordered_keys, _resolve_art, _resolve_sound

# Stat fields shown on a unit page, in order.
STAT_FIELDS = ("Cost", "Strength", "Armor", "TechLevel", "Speed", "Sight",
               "Owner", "Prerequisite", "BuildLimit", "Soylent", "Points")

_KIND_LIST = (("infantry", "InfantryTypes"), ("vehicle", "VehicleTypes"),
              ("aircraft", "AircraftTypes"), ("building", "BuildingTypes"))


@dataclass
class AssetRef:
    name: str          # e.g. GTNK01.VXL
    location: str      # human-readable: "loose", "mix: ecache7.mix", "vanilla", "—"
    status: str        # "present" | "vanilla" | "missing"


@dataclass
class UnitEntry:
    name: str
    mod: str
    kind: str
    obtain: str = ""
    obtain_reasons: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)          # field -> value (str)
    weapons: list = field(default_factory=list)         # [(slot, wpn, warhead, proj)]
    art_name: str = ""
    sound_names: list = field(default_factory=list)
    assets: list = field(default_factory=list)          # [AssetRef]
    warnings: list = field(default_factory=list)


@dataclass
class ModIndex:
    label: str
    folder: str
    rules_path: str
    units: dict = field(default_factory=dict)           # name_lower -> UnitEntry
    # kept for export:
    rules_map: dict = field(default_factory=dict)
    art_map: dict = field(default_factory=dict)
    sound_map: dict = field(default_factory=dict)
    catalog: dict = field(default_factory=dict)

    def unit_names(self):
        return sorted(u.name for u in self.units.values())


def _sibling(folder, *names):
    if not folder or not os.path.isdir(folder):
        return None
    low = {f.lower(): f for f in os.listdir(folder)}
    for n in names:
        if n.lower() in low:
            return os.path.join(folder, low[n.lower()])
    return None


def _asset_location(catalog, name, vanilla_ids, is_sound=False):
    loc = catalog.get(name.upper()) or catalog.get(mix_id(name))
    if loc is not None:
        if loc[0] == "loose":
            return AssetRef(name, "loose", "present")
        return AssetRef(name, f"mix: {os.path.basename(loc[1])}", "present")
    if vanilla_ids and mix_id(name) in vanilla_ids:
        return AssetRef(name, "vanilla (game)", "vanilla")
    if is_sound:
        # voice/sound samples live in audio.bag/.idx (not .mix), so "not in the
        # scanned mixes" is normal, not a missing-asset error.
        return AssetRef(name, "audio bag (not scanned)", "sound")
    return AssetRef(name, "— not found", "missing")


def build_mod_index(rules_path, label=None, config=None, vanilla_ids=None,
                    progress=None):
    """Index one mod. `vanilla_ids` (from vanilla_asset_ids) lets a missing asset
    be marked vanilla-provided vs truly missing."""
    config = config or ModConfig.default()
    folder = os.path.dirname(rules_path)
    label = label or os.path.basename(folder) or os.path.basename(rules_path)

    rules_map = build_section_map(parse_ini_lines(rules_path))[0]
    art_p = _sibling(folder, "artmd.ini", "art.ini")
    snd_p = _sibling(folder, "soundmd.ini", "sound.ini")
    art_map = build_section_map(parse_ini_lines(art_p))[0] if art_p else {}
    sound_map = build_section_map(parse_ini_lines(snd_p))[0] if snd_p else {}
    if progress:
        progress("cataloguing assets…")
    catalog = build_media_catalog([folder])
    obtain = classify_obtainability(rules_map, config, art_map=art_map or None)

    idx = ModIndex(label=label, folder=folder, rules_path=rules_path,
                   rules_map=rules_map, art_map=art_map, sound_map=sound_map,
                   catalog=catalog)

    for kind, section in _KIND_LIST:
        for name in parse_type_list(rules_map, section):
            if name.lower() in idx.units:
                continue
            e = UnitEntry(name=name, mod=label, kind=kind)
            ob = obtain.get(name.lower())
            if ob:
                e.obtain = ob.cls
                e.obtain_reasons = list(ob.reasons) + [
                    f"({a})" for a in ob.also]
            for stat in STAT_FIELDS:
                v = _clean(get_val(rules_map, name, stat))
                if v:
                    e.stats[stat] = v
            for tag in ALL_WEAPON_TAGS:
                w = _clean(get_val(rules_map, name, tag))
                if w and w.lower() not in ("none", ""):
                    wh = _clean(get_val(rules_map, w, "Warhead"))
                    pr = _clean(get_val(rules_map, w, "Projectile"))
                    e.weapons.append((tag, w, wh, pr))
            art_entry, art_files = _resolve_art(name, rules_map, art_map or {})
            snd_entries, snd_files = _resolve_sound(name, rules_map, sound_map or {})
            e.art_name = art_entry[0] if art_entry else ""
            e.sound_names = [n for n, _ in snd_entries]
            for fn in art_files:
                ref = _asset_location(catalog, fn, vanilla_ids)
                e.assets.append(ref)
                if ref.status == "missing":
                    e.warnings.append(f"missing asset: {fn}")
            for fn in snd_files:
                e.assets.append(_asset_location(catalog, fn, vanilla_ids,
                                                is_sound=True))
            idx.units[name.lower()] = e
    return idx


def search_indexes(indexes, query="", kind=None, mod=None):
    """Return UnitEntry list across indexes matching a name substring (and optional
    kind / mod label filters), sorted by name then mod."""
    q = query.lower().strip()
    out = []
    for idx in indexes:
        if mod and idx.label != mod:
            continue
        for u in idx.units.values():
            if q and q not in u.name.lower() and q not in u.obtain.lower():
                continue
            if kind and u.kind != kind:
                continue
            out.append(u)
    out.sort(key=lambda u: (u.name.lower(), u.mod.lower()))
    return out


def unit_report(entry):
    """Plain-text detail of a unit for display/clipboard."""
    L = [f"[{entry.name}]   {entry.kind}   —   {entry.mod}"]
    if entry.obtain:
        L.append(f"obtain: {entry.obtain}  ({'; '.join(entry.obtain_reasons)})")
    L.append("")
    L.append("stats:")
    for k, v in entry.stats.items():
        L.append(f"  {k} = {v}")
    if entry.weapons:
        L.append("\nweapons:")
        for slot, w, wh, pr in entry.weapons:
            extra = []
            if wh:
                extra.append(f"warhead {wh}")
            if pr:
                extra.append(f"projectile {pr}")
            L.append(f"  {slot} = {w}" + (f"  ({', '.join(extra)})" if extra else ""))
    if entry.art_name:
        L.append(f"\nart: [{entry.art_name}]")
    if entry.sound_names:
        L.append("sound: " + ", ".join(entry.sound_names))
    if entry.assets:
        L.append("\nassets:")
        for a in entry.assets:
            mark = {"present": " ", "vanilla": "·", "missing": "✗",
                    "sound": "♪"}.get(a.status, " ")
            L.append(f"  {mark} {a.name:22} {a.location}")
    return "\n".join(L)


def _section_block(section_map, name, ending="\n"):
    if name.lower() not in section_map:
        return []
    out = [f"[{name}]{ending}"]
    out += [f"{k}={_clean(v)}{ending}" for k, v in _ordered_keys(section_map, name)]
    out.append(ending)
    return out


def export_unit(index, unit_name, dest_folder):
    """Snag a unit out of a mod into dest_folder: write <unit>.ini (its rules
    section + weapon/warhead/projectile sections + art/sound entries) and extract
    its referenced media files. Returns a summary dict."""
    os.makedirs(dest_folder, exist_ok=True)
    e = index.units.get(unit_name.lower())
    if e is None:
        raise KeyError(unit_name)
    ending = "\r\n"

    # rules .ini: unit + its weapon chain
    lines = []
    lines += _section_block(index.rules_map, e.name, ending)
    for _slot, w, wh, pr in e.weapons:
        for sec in (w, wh, pr):
            if sec and sec.lower() in index.rules_map:
                lines += _section_block(index.rules_map, sec, ending)
    ini_path = os.path.join(dest_folder, f"{e.name}.ini")
    with open(ini_path, "w", encoding="latin-1", errors="replace") as f:
        f.writelines(lines)

    # art/sound entries → a sidecar .ini
    side = []
    if e.art_name:
        side += _section_block(index.art_map, e.art_name, ending)
    for sn in e.sound_names:
        side += _section_block(index.sound_map, sn, ending)
    if side:
        with open(os.path.join(dest_folder, f"{e.name}_artsound.ini"), "w",
                  encoding="latin-1", errors="replace") as f:
            f.writelines(side)

    # media files
    wanted = [a.name for a in e.assets if a.status in ("present",)]
    found, missing = resolve_media(index.catalog, wanted)
    for fn, data in found.items():
        with open(os.path.join(dest_folder, fn), "wb") as f:
            f.write(data)
    vanilla = [a.name for a in e.assets if a.status == "vanilla"]
    truly_missing = [a.name for a in e.assets if a.status == "missing"] + missing
    return {"ini": os.path.basename(ini_path),
            "media": sorted(found),
            "vanilla_assets": vanilla,
            "missing": truly_missing}
