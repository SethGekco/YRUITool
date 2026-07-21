"""
Scalar balance operations: scaling individual tags, speed (with cap), build time,
debris, adjacency, soylent, and the sight pass.

These are the low-level "apply a ratio to a field" primitives. On a re-run they
delegate to `smart_apply_tag` for vanilla/current-mode logic and flag drift to an
observation report.

Re-run context and per-session dedup state live at module level (as in the
original tool) but are now driven through explicit setter/reset functions so a GUI
can control them deterministically instead of relying on import-time globals.
A future refactor could fold these into a Session object.
"""

import re

from .ini_model import (
    scale_int, scale_from_vanilla, format_edited_value,
    parse_vanilla_from_line, get_current_field_value_from_line,
    line_ending, modify_line, append_line_after_section_header, get_val,
)
from .report import smart_apply_tag


# ── Re-run context ───────────────────────────────────────────────────────────
# When `report` is None the operations run in first-run mode. Set it (via
# set_rerun_context) to enable smart re-run handling.
_rerun_ctx = {
    "ticket":           None,
    "rerun_mode":       "vanilla",
    "uncommented_mode": "vanilla",
    "report":           None,
    "category":         "unknown",
}

# Units that have had sight applied this session — prevents duplicates when the
# user runs multiple individual category operations.
_sight_done = set()


def set_rerun_context(ticket=None, rerun_mode="vanilla",
                      uncommented_mode="vanilla", report=None,
                      category="unknown"):
    """Enable/adjust re-run mode. Pass report=None (default) for first-run mode."""
    _rerun_ctx.update({
        "ticket": ticket,
        "rerun_mode": rerun_mode,
        "uncommented_mode": uncommented_mode,
        "report": report,
        "category": category,
    })


def set_category(category):
    """Update just the category label used in re-run flagging."""
    _rerun_ctx["category"] = category


def reset_session_state():
    """Clear per-session state (re-run context + sight dedup). Call before a run."""
    _rerun_ctx.update({
        "ticket": None, "rerun_mode": "vanilla",
        "uncommented_mode": "vanilla", "report": None, "category": "unknown",
    })
    _sight_done.clear()


# ── Tag scaling ──────────────────────────────────────────────────────────────

def apply_tag_ratio(lines, section_map, section_name, tag, ratio,
                    suffix="Edit by AI script"):
    """
    Scale an existing numeric tag. On re-runs, delegates to smart_apply_tag; on
    first runs, writes a plain value with vanilla and last_ratio embedded.
    """
    ctx = _rerun_ctx
    if ctx["report"] is not None:
        return smart_apply_tag(
            lines, section_map, section_name, tag, ratio,
            ctx["category"], ctx["ticket"],
            ctx["rerun_mode"], ctx["uncommented_mode"],
            ctx["report"], suffix)

    s = section_map.get(section_name.lower())
    if s is None:
        return False
    entry = s["keys"].get(tag.lower())
    if entry is None:
        return False
    li, kr, vr = entry
    # Rescan from section start in case insertions shifted the cached index.
    start = s["start"]
    for _si in range(start + 1, min(start + 300, len(lines))):
        _raw = lines[_si].strip()
        if _raw.startswith("[") and _raw.endswith("]"):
            break
        if re.match(rf"^{re.escape(tag)}\s*=", _raw, re.IGNORECASE):
            _vr_candidate = _raw.split("=", 1)[1] if "=" in _raw else ""
            _val_part = _vr_candidate.split(";")[0].strip()
            if "; vanilla=0" in _raw or _val_part == "0":
                continue  # skip artifact lines; keep scanning for the real one
            li = _si
            vr = _vr_candidate
            break
    raw = vr.split(";")[0].strip()
    ending = line_ending(lines[start]) or "\n"

    def _dedup_tag(written_li):
        """Blank any duplicate tag lines in the section (keep only written_li)."""
        for _di in range(start + 1, min(start + 300, len(lines))):
            if _di == written_li:
                continue
            _dr = lines[_di].strip()
            if _dr.startswith("[") and _dr.endswith("]"):
                break
            if re.match(rf"^{re.escape(tag)}\s*=", _dr, re.IGNORECASE):
                lines[_di] = ending

    try:
        orig_i = int(raw)
        new_v = scale_int(orig_i, ratio)
        modify_line(lines, li, kr,
                    f"{new_v}; last_value={orig_i} ; last_ratio={ratio} ; vanilla={orig_i}")
        _dedup_tag(li)
    except ValueError:
        try:
            orig_f = float(raw)
            new_v = orig_f * ratio
            if new_v == int(new_v):
                new_v = int(new_v)
            modify_line(lines, li, kr,
                        f"{new_v}; last_value={orig_f} ; last_ratio={ratio} ; vanilla={orig_f}")
            _dedup_tag(li)
        except ValueError:
            pass
    return True


def apply_speed_capped(lines, section_map, section_name, ratio, speed_max=None):
    """Scale Speed= by ratio, then clamp to speed_max if provided."""
    ctx = _rerun_ctx
    if ctx["report"] is not None:
        smart_apply_tag(
            lines, section_map, section_name, "Speed", ratio,
            ctx["category"], ctx["ticket"],
            ctx["rerun_mode"], ctx["uncommented_mode"],
            ctx["report"], "Edit by AI script")
        if speed_max is not None:
            s = section_map.get(section_name.lower())
            if s:
                entry = s["keys"].get("speed")
                if entry:
                    li, kr, _ = entry
                    current = get_current_field_value_from_line(lines[li])
                    vanilla_tag = parse_vanilla_from_line(lines[li])
                    if current is not None and current > speed_max:
                        ending = line_ending(lines[li]) or "\n"
                        ws = lines[li][: len(lines[li]) - len(lines[li].lstrip())]
                        lines[li] = (f"{ws}{kr}={speed_max};{vanilla_tag or current} "
                                     f"Edit by AI script (capped at {speed_max})"
                                     f" ; vanilla={vanilla_tag or current}"
                                     f" ; last_ratio={ratio}{ending}")
        return True

    s = section_map.get(section_name.lower())
    if s is None:
        return False
    entry = s["keys"].get("speed")
    if entry is None:
        return False
    li, kr, vr = entry
    raw = vr.split(";")[0].strip()
    try:
        orig = int(raw)
    except ValueError:
        try:
            orig = float(raw)
        except ValueError:
            return False
    new_v = scale_int(orig, ratio) if isinstance(orig, int) else orig * ratio
    capped = False
    if speed_max is not None and new_v > speed_max:
        new_v = speed_max
        capped = True
    suffix = (f"Edit by AI script (capped at {speed_max})"
              if capped else "Edit by AI script")
    modify_line(lines, li, kr,
                f"{new_v};{orig} {suffix} ; vanilla={orig} ; last_ratio={ratio}")
    return True


def apply_buildtime(lines, section_map, section_name, ratio):
    """Scale BuildTimeMultiplier= (adding it if absent)."""
    s = section_map.get(section_name.lower())
    if s is None:
        return
    entry = s["keys"].get("buildtimemultiplier")
    if entry is not None:
        li, kr, vr = entry
        raw = vr.split(";")[0].strip()
        try:
            orig = float(raw)
            new_v = orig * ratio
            if new_v == int(new_v):
                new_v = int(new_v)
            modify_line(lines, li, kr,
                        f"{new_v};{orig} Edit by AI script ; vanilla={orig} ; last_ratio={ratio}")
        except ValueError:
            pass
    else:
        new_v = ratio
        if new_v == int(new_v):
            new_v = int(new_v)
        new_line = (f"BuildTimeMultiplier={new_v};1.0 "
                    f"Added+scaled by AI script ; vanilla=1.0")
        append_line_after_section_header(lines, section_map, section_name, new_line)


# ── Debris / adjacent / soylent ──────────────────────────────────────────────

def apply_debris(lines, section_map, type_name, ratio):
    for tag in ("MaxDebris", "MinDebris"):
        s = section_map.get(type_name.lower())
        if s is None:
            continue
        entry = s["keys"].get(tag.lower())
        if entry is None:
            continue
        li, kr, vr = entry
        raw = vr.split(";")[0].strip()
        try:
            orig = int(raw)
        except ValueError:
            continue
        new_v = scale_int(orig, ratio)
        modify_line(lines, li, kr,
                    f"{new_v};{orig} Edit by AI script ; vanilla={orig} ; last_ratio={ratio}")


def apply_adjacent(lines, section_map, type_name, ratio):
    s = section_map.get(type_name.lower())
    if s is None:
        return
    entry = s["keys"].get("adjacent")
    if entry is None:
        return
    li, kr, vr = entry
    raw = vr.split(";")[0].strip()
    try:
        orig = int(raw)
    except ValueError:
        return
    new_v = scale_int(orig, ratio)
    modify_line(lines, li, kr,
                f"{new_v};{orig} Edit by AI script ; vanilla={orig}")


def apply_soylent(lines, section_map, type_name, vanilla_cost, cost_ratio, soylent_ratio):
    """
    Set Soylent= to round(vanilla_cost * cost_ratio * soylent_ratio). vanilla_cost
    is the original unedited cost so the soylent value is always derived cleanly.
    """
    s = section_map.get(type_name.lower())
    if s is None:
        return
    entry = s["keys"].get("soylent")
    if entry is None:
        return  # Unit has no Soylent tag — skip
    li, kr, vr = entry
    orig_soylent = vr.split(";")[0].strip()
    try:
        new_val = scale_int(int(float(vanilla_cost)), cost_ratio * soylent_ratio)
    except (ValueError, TypeError):
        return
    modify_line(lines, li, kr,
                f"{new_val};{orig_soylent} Edit by AI script ; vanilla_cost={vanilla_cost}")


# ── Sight pass ───────────────────────────────────────────────────────────────

def get_current_range_from_lines(lines, section_map, weapon_name):
    s = section_map.get(weapon_name.lower())
    if s is None:
        return None
    entry = s["keys"].get("range")
    if entry is None:
        return None
    li = entry[0]
    m = re.match(r'^[^=]+=([^;]+)', lines[li].strip())
    if not m:
        return None
    val_part = m.group(1).strip()
    try:
        return int(val_part)
    except ValueError:
        try:
            return int(float(val_part))
        except ValueError:
            return None


def apply_sight_equal_to_range(lines, section_map, type_name, weapon_name):
    weapon_range = get_current_range_from_lines(lines, section_map, weapon_name)
    if weapon_range is None:
        return False
    s = section_map.get(type_name.lower())
    if s is None:
        return False
    entry = s["keys"].get("sight")
    if entry is None:
        return False
    li, kr, vr = entry
    orig_sight = vr.split(";")[0].strip()
    modify_line(lines, li, kr,
                f"{weapon_range}; last_value={orig_sight} ; method=sight_match"
                f" ; vanilla={orig_sight}")
    return True


def _first_weapon(section_map, type_name):
    """Return the unit's first usable weapon name (Primary preferred), or None."""
    from .weapons import REGULAR_WEAPON_TAGS
    primary = get_val(section_map, type_name, "Primary")
    if primary and primary.lower() not in ("none", ""):
        return primary
    for tag in REGULAR_WEAPON_TAGS:
        wname = get_val(section_map, type_name, tag)
        if wname and wname.lower() not in ("none", ""):
            return wname
    return None


def _apply_sight_no_weapon(lines, section_map, type_name, mode, value):
    """Fallback Sight for a weapon-less unit in sight-equal mode."""
    if mode == "multiplier":
        if value != 1.0:
            apply_tag_ratio(lines, section_map, type_name, "Sight", value)
        return
    if mode == "minimum":
        s = section_map.get(type_name.lower())
        entry = s["keys"].get("sight") if s else None
        if entry is None:
            return
        li, kr, vr = entry
        orig = vr.split(";")[0].strip()
        try:
            cur = float(orig)
        except ValueError:
            return
        if cur < value:
            new_v = int(value) if value == int(value) else value
            modify_line(lines, li, kr,
                        f"{new_v}; last_value={orig} ; method=sight_min ; vanilla={orig}")


def do_sight_pass(lines, section_map, type_list, sight_equal, sight_ratio,
                  no_weapon_mode="leave", no_weapon_value=1.0):
    """
    Second-pass sight handling. In sight_equal mode, Sight is set to the unit's
    weapon range; weapon-less units use no_weapon_mode ('leave'|'multiplier'|
    'minimum') with no_weapon_value. Otherwise Sight is scaled by sight_ratio.
    """
    for t in type_list:
        if t in _sight_done:
            continue
        if sight_equal:
            wname = _first_weapon(section_map, t)
            applied = False
            if wname:
                applied = apply_sight_equal_to_range(lines, section_map, t, wname)
            if not applied:
                _apply_sight_no_weapon(lines, section_map, t,
                                       no_weapon_mode, no_weapon_value)
        elif sight_ratio != 1.0:
            apply_tag_ratio(lines, section_map, t, "Sight", sight_ratio)
        _sight_done.add(t)


def apply_soylent_fraction(lines, section_map, type_name, factor):
    """Set Soylent = round(current Cost * factor). Skips units with no Soylent tag."""
    s = section_map.get(type_name.lower())
    if s is None:
        return
    soy = s["keys"].get("soylent")
    if soy is None:
        return
    # Read the CURRENT written cost (the cache's value string is the pre-edit one,
    # so read the live line via the robust rescanning reader).
    from .costscaling import get_three_costs
    _vanilla, _pre, current = get_three_costs(lines, section_map, type_name)
    if current is None:
        return
    li, kr, vr = soy
    orig = vr.split(";")[0].strip()
    new_val = scale_int(int(current), factor)
    modify_line(lines, li, kr,
                f"{new_val};{orig} Edit by AI script ; vanilla_cost={current}")
