"""
Weapon editing: vanilla snapshots, the edit registry, conflict-resolving clones,
and building elite-slot injection.

Weapons are shared between units, so editing the same weapon with two different
ratio sets is a conflict. The registry tracks every edit; on conflict the weapon
is cloned, the requesting unit's tag redirected to the clone, and the clone gets
the new ratios (applied from the vanilla snapshot). Ported from the monolith;
`WeaponRegistry.print_summary` became `format_summary()` (returns text, no I/O).
"""

from .ini_model import (
    scale_int, scale_from_vanilla, format_edited_value,
    modify_line, append_line_after_section_header, get_val,
)


# ── Weapon tag sets ──────────────────────────────────────────────────────────

REGULAR_WEAPON_TAGS = ["Primary", "Secondary"] + [f"Weapon{i}" for i in range(1, 18)]
ELITE_WEAPON_TAGS   = ["ElitePrimary", "EliteSecondary"] + [f"EliteWeapon{i}" for i in range(1, 18)]
ALL_WEAPON_TAGS     = REGULAR_WEAPON_TAGS + ELITE_WEAPON_TAGS

REG_TO_ELITE_TAG = {"Primary": "ElitePrimary", "Secondary": "EliteSecondary"}
REG_TO_ELITE_TAG.update({f"Weapon{i}": f"EliteWeapon{i}" for i in range(1, 18)})


# ── Vanilla snapshots (captured before any edits) ────────────────────────────

def snapshot_weapon(section_map, weapon_name):
    snap = {}
    s = section_map.get(weapon_name.lower())
    if s is None:
        return snap
    for field in ("damage", "range", "rof"):
        entry = s["keys"].get(field)
        if entry is None:
            continue
        raw = entry[2].split(";")[0].strip()
        try:
            snap[field] = int(raw)
        except ValueError:
            try:
                snap[field] = float(raw)
            except ValueError:
                pass
    return snap


def build_weapon_snapshots(section_map, all_type_lists):
    snapshots = {}
    for type_list in all_type_lists:
        for t in type_list:
            for tag in ALL_WEAPON_TAGS:
                wname = get_val(section_map, t, tag)
                if wname and wname.lower() not in ("none", "") and wname not in snapshots:
                    snapshots[wname] = snapshot_weapon(section_map, wname)
    return snapshots


# ── Edit registry ────────────────────────────────────────────────────────────

class WeaponRegistry:
    """
    Tracks every weapon edit so conflicts can be detected.
    edited[weapon_name] = {'dmg', 'rng', 'rof': ratios, 'pass': 'reg'|'eli'}
    """
    def __init__(self):
        self.edited = {}
        self.conflicts = []
        self.redirects = {}

    def record(self, weapon_name, dmg_r, rng_r, rof_r, weapon_pass):
        self.edited[weapon_name] = {
            "dmg": dmg_r, "rng": rng_r, "rof": rof_r, "pass": weapon_pass
        }

    def already_edited(self, weapon_name):
        return weapon_name in self.edited

    def ratios_match(self, weapon_name, dmg_r, rng_r, rof_r):
        prev = self.edited.get(weapon_name)
        if prev is None:
            return True
        return prev["dmg"] == dmg_r and prev["rng"] == rng_r and prev["rof"] == rof_r

    def add_conflict(self, weapon_name, unit_name, tag, prev_ratios, new_ratios,
                     clone_name):
        self.conflicts.append({
            "weapon": weapon_name, "unit": unit_name, "tag": tag,
            "prev_ratios": prev_ratios, "new_ratios": new_ratios,
            "clone": clone_name,
        })

    def format_summary(self):
        """Render the conflict report to a string (no I/O)."""
        if not self.conflicts:
            return "\n  No cross-category weapon conflicts detected.\n"
        out = []
        out.append(f"\n  {'='*64}\n")
        out.append(f"  WEAPON CONFLICT REPORT  "
                   f"({len(self.conflicts)} conflict(s) resolved by cloning)\n")
        out.append(f"  {'='*64}\n")
        for c in self.conflicts:
            prev, new = c["prev_ratios"], c["new_ratios"]
            out.append(f"\n  Weapon : {c['weapon']}\n")
            out.append(f"  Unit   : {c['unit']}  (tag: {c['tag']})\n")
            out.append(f"  Previous ratios -> dmg={prev['dmg']}  rng={prev['rng']}  rof={prev['rof']}\n")
            out.append(f"  Requested ratios-> dmg={new['dmg']}   rng={new['rng']}   rof={new['rof']}\n")
            out.append(f"  Resolution      : cloned to [{c['clone']}], tag redirected in unit section\n")
        return "".join(out)


# ── Cloning ──────────────────────────────────────────────────────────────────

def make_safe_weapon_name(base, suffix, section_map, max_len=24):
    """
    Build a safe weapon clone name `base_suffix`, guaranteed <= max_len chars and
    unique in section_map. Truncates the base to fit; appends a disambiguator on
    collision.
    """
    sep = "_"
    available = max_len - len(sep) - len(suffix)
    if available < 1:
        candidate = suffix[:max_len]
    else:
        candidate = f"{base[:available]}{sep}{suffix}"
    counter = 1
    name = candidate
    while name.lower() in section_map:
        tail = str(counter)
        name = candidate[: max_len - len(tail)] + tail
        counter += 1
    return name


def clone_weapon_section(lines, section_map, orig_name, clone_name,
                         snapshots, dmg_ratio, rng_ratio, rof_ratio,
                         clone_label="Cloned"):
    """
    Append a new [clone_name] section at end of file. All fields copied from the
    original; Damage/Range/ROF computed from the vanilla snapshot. Returns
    clone_name (or None on failure).
    """
    if clone_name.lower() in section_map:
        return clone_name

    orig_s = section_map.get(orig_name.lower())
    if orig_s is None:
        return None

    snap = snapshots.get(orig_name, {})
    ending = "\r\n" if lines and lines[-1].endswith("\r\n") else "\n"

    kv_lines = []
    for k_lower, (li, kr, vr) in sorted(orig_s["keys"].items(),
                                        key=lambda x: x[1][0]):
        field = k_lower
        ratio = {"damage": dmg_ratio, "range": rng_ratio, "rof": rof_ratio}.get(field)
        if ratio is not None and ratio != 1.0 and field in snap:
            orig_v = snap[field]
            new_str, _ = scale_from_vanilla(orig_v, ratio)
            val_str = format_edited_value(new_str, orig_v,
                                          f"{clone_label} Edit by AI script")
        else:
            val_str = vr
        kv_lines.append((kr, val_str))

    new_sec_line_idx = len(lines)
    lines.append(f"{ending}")
    lines.append(f"[{clone_name}]{ending}")
    lines.append(f"; {clone_label} from [{orig_name}] - AI script{ending}")
    key_start = len(lines)
    for kr, val_str in kv_lines:
        lines.append(f"{kr}={val_str}{ending}")

    new_keys = {}
    for offset, (kr, val_str) in enumerate(kv_lines):
        new_keys[kr.lower()] = (key_start + offset, kr, val_str)

    section_map[clone_name.lower()] = {
        "name_raw": clone_name,
        "start": new_sec_line_idx + 1,
        "keys": new_keys,
    }
    snapshots[clone_name] = snap.copy()
    return clone_name


def redirect_unit_weapon_tag(lines, section_map, unit_name, tag, new_weapon_name):
    """Point a unit's weapon tag at new_weapon_name and update the cache."""
    s = section_map.get(unit_name.lower())
    if s is None:
        return
    entry = s["keys"].get(tag.lower())
    if entry is None:
        return
    li, kr, vr = entry
    modify_line(lines, li, kr,
                f"{new_weapon_name} ;Redirected from {vr.split(';')[0].strip()} - AI script")
    s["keys"][tag.lower()] = (li, kr,
                              f"{new_weapon_name} ;Redirected from {vr.split(';')[0].strip()}")


# ── Core weapon editing (with conflict detection) ────────────────────────────

def apply_weapon_fields(lines, section_map, weapon_name,
                        dmg_ratio, rng_ratio, rof_ratio,
                        registry, snapshots, weapon_pass,
                        unit_name=None, tag=None, range_threshold=None):
    """
    Edit Damage=/Range=/ROF= on weapon_name from the vanilla snapshot.

    First edit: apply and record. Second edit, same ratios: skip. Second edit,
    different ratios: clone, redirect the unit tag, apply new ratios to the clone,
    report the conflict. weapon_pass is 'reg' or 'eli' (tracked separately).

    range_threshold: if set, weapons whose vanilla Range is below it are left
    unscaled on Range (protects melee / very short-range weapons like attack dogs).
    """
    if range_threshold is not None:
        vr = snapshots.get(weapon_name, {}).get("range")
        if vr is not None and vr < range_threshold:
            rng_ratio = 1.0

    registry_key = f"{weapon_name}::{weapon_pass}"

    if registry.already_edited(registry_key):
        if registry.ratios_match(registry_key, dmg_ratio, rng_ratio, rof_ratio):
            return weapon_name
        suffix = (unit_name or "CLN").replace(" ", "")[:8]
        clone_name = make_safe_weapon_name(weapon_name, suffix, section_map)
        clone_weapon_section(
            lines, section_map, weapon_name, clone_name,
            snapshots, dmg_ratio, rng_ratio, rof_ratio,
            clone_label=f"ConflictClone({suffix})")
        if unit_name and tag:
            redirect_unit_weapon_tag(lines, section_map, unit_name, tag, clone_name)
        prev = registry.edited[registry_key]
        registry.add_conflict(
            weapon_name, unit_name or "?", tag or "?",
            {"dmg": prev["dmg"], "rng": prev["rng"], "rof": prev["rof"]},
            {"dmg": dmg_ratio, "rng": rng_ratio, "rof": rof_ratio},
            clone_name)
        registry.record(f"{clone_name}::{weapon_pass}",
                        dmg_ratio, rng_ratio, rof_ratio, weapon_pass)
        return clone_name

    # First time editing this weapon — apply from vanilla snapshot.
    registry.record(registry_key, dmg_ratio, rng_ratio, rof_ratio, weapon_pass)
    s = section_map.get(weapon_name.lower())
    if s is None:
        return weapon_name
    snap = snapshots.get(weapon_name, {})

    for field, ratio in (("damage", dmg_ratio), ("range", rng_ratio), ("rof", rof_ratio)):
        if ratio == 1.0:
            continue
        entry = s["keys"].get(field)
        if entry is None:
            continue
        li, kr, vr = entry
        if field in snap:
            orig = snap[field]
            new_str, _ = scale_from_vanilla(orig, ratio)
            modify_line(lines, li, kr, format_edited_value(new_str, orig))
        else:
            raw = vr.split(";")[0].strip()
            try:
                orig_i = int(raw)
                new_v = scale_int(orig_i, ratio)
                modify_line(lines, li, kr, format_edited_value(str(new_v), orig_i))
            except ValueError:
                pass
    return weapon_name


# ── Building elite weapon injection ──────────────────────────────────────────

def inject_elite_slots_for_building(lines, section_map, building_name,
                                    snapshots, registry,
                                    dmg_ratio, rng_ratio, rof_ratio,
                                    range_threshold=None):
    s = section_map.get(building_name.lower())
    if s is None:
        return

    for reg_tag in REGULAR_WEAPON_TAGS:
        elite_tag = REG_TO_ELITE_TAG.get(reg_tag)
        if elite_tag is None:
            continue
        if elite_tag.lower() in s["keys"]:
            continue
        reg_entry = s["keys"].get(reg_tag.lower())
        if reg_entry is None:
            continue
        _, _, reg_wname_raw = reg_entry
        reg_wname = reg_wname_raw.split(";")[0].strip()
        if not reg_wname or reg_wname.lower() in ("none", ""):
            continue

        eff_rng = rng_ratio
        if range_threshold is not None:
            vr = snapshots.get(reg_wname, {}).get("range")
            if vr is not None and vr < range_threshold:
                eff_rng = 1.0

        elite_wname = make_safe_weapon_name(reg_wname, "Elite", section_map)
        if elite_wname.lower() not in section_map:
            clone_weapon_section(
                lines, section_map, reg_wname, elite_wname,
                snapshots, dmg_ratio, eff_rng, rof_ratio,
                clone_label="BuildingElite")
            registry.record(f"{elite_wname}::eli",
                            dmg_ratio, eff_rng, rof_ratio, "eli")

        append_line_after_section_header(
            lines, section_map, building_name,
            f"{elite_tag}={elite_wname} ;Added by AI script")

        s2 = section_map.get(building_name.lower())
        if s2 and elite_tag.lower() not in s2["keys"]:
            start = s2["start"]
            for li2 in range(start + 1, min(start + 60, len(lines))):
                if lines[li2].strip().lower().startswith(elite_tag.lower() + "="):
                    s2["keys"][elite_tag.lower()] = (
                        li2, elite_tag, f"{elite_wname} ;Added by AI script")
                    break
