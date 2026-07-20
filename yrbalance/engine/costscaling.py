"""
Cost scaling: flat, by TechLevel tier, or by building depth (flat-per-tier or
tiered rates).

`get_three_costs` reads the three reference points off a Cost= line — vanilla
(from the ; vanilla= comment), pre_f (vanilla × last_ratio), and current — so a
scale can be applied from any of them. It rescans from the section start to
survive stale cached indices and skips Cost=0 corruption artifacts. Ported from
the monolith; the interactive base/rate prompts stay in the UI layer.
"""

import re

from .ini_model import (
    scale_int, line_ending, modify_line, get_val,
    parse_vanilla_from_line, get_current_field_value_from_line,
)
from .techlevel import valid_tl, get_effective_tl, get_tl_store


def get_three_costs(lines, section_map, name):
    """
    Return (vanilla, pre_f, current) for a unit's Cost= line.
      vanilla -- from '; vanilla=X' comment, or current if no comment.
      pre_f   -- round(vanilla * last_ratio) from '; last_ratio=R', or current.
      current -- the actual number on the line right now.
    Rescans from the section start to handle stale indices and skips Cost=0
    artifacts left by bad prior writes.
    """
    s = section_map.get(name.lower())
    if s is None:
        return None, None, None

    start = s["start"]
    line_str = None
    for i in range(start + 1, min(start + 200, len(lines))):
        raw = lines[i].strip()
        if raw.startswith("[") and raw.endswith("]"):
            break
        if raw.lower().startswith("cost=") or raw.lower().startswith("cost ="):
            if "; vanilla=0 ;" in raw or "; vanilla=0" in raw:
                continue
            if raw.lower().startswith("cost=0;") or raw.lower().startswith("cost=0 ;"):
                continue
            line_str = lines[i]
            entry = s["keys"].get("cost")
            if entry is not None:
                _, kr, _ = entry
                s["keys"]["cost"] = (i, kr, lines[i].split("=", 1)[1].rstrip())
            # Blank any orphaned Cost=0 artifact lines we skipped above.
            _ending_r = line_ending(lines[start]) or "\n"
            for _oi in range(start + 1, i):
                _or = lines[_oi].strip()
                if _or.lower().startswith("cost=") and (
                        "; vanilla=0 ;" in _or or _or.lower().startswith("cost=0;")):
                    lines[_oi] = _ending_r
            break

    if line_str is None:
        entry = s["keys"].get("cost")
        if entry is None:
            return None, None, None
        li, _, _ = entry
        if li < len(lines) and "cost" in lines[li].lower():
            line_str = lines[li]
        else:
            return None, None, None

    current = get_current_field_value_from_line(line_str)
    vanilla = parse_vanilla_from_line(line_str)

    lr_m = re.search(r'; last_ratio=([0-9.eE+-]+)', line_str)
    if lr_m and vanilla is not None:
        try:
            pre_f = scale_int(int(vanilla), float(lr_m.group(1)))
        except (ValueError, TypeError):
            pre_f = current
    else:
        pre_f = current

    if vanilla is None:
        vanilla = current
    return vanilla, pre_f, current


def write_cost(lines, section_map, name, new_cost, base_label, base_value):
    """
    Write a new Cost= with vanilla/last_ratio annotation, and blank any duplicate
    *tool-written* Cost= lines in the section (plain vanilla Cost= lines are left).
    """
    s = section_map.get(name.lower())
    if s is None:
        return
    entry = s["keys"].get("cost")
    if entry is None:
        return
    li, kr, _ = entry
    vanilla = parse_vanilla_from_line(lines[li])
    if vanilla is None:
        vanilla = base_value
    ratio = (new_cost / base_value) if base_value else 1.0
    modify_line(lines, li, kr,
                f"{new_cost}; last_value={base_value}"
                f" ; last_ratio={ratio:.4f} ; vanilla={vanilla}")

    start = s["start"]
    ending = line_ending(lines[start]) or "\n"
    for i in range(start + 1, min(start + 300, len(lines))):
        if i == li:
            continue
        raw = lines[i].strip()
        if raw.startswith("[") and raw.endswith("]"):
            break
        if raw.lower().startswith("cost="):
            if "AI script" in raw or "; vanilla=" in raw or "; last_ratio=" in raw:
                lines[i] = ending


def _resolve_base(vanilla, pre_f, current, base_choice):
    base = {"vanilla": vanilla, "pre_f": pre_f, "current": current}.get(base_choice, current)
    return current if base is None else base


def apply_flat_cost_scaling(lines, section_map, type_list, ratio, base_choice):
    """Apply a flat ratio to Cost for all units in type_list. Returns count."""
    count = 0
    for name in type_list:
        vanilla, pre_f, current = get_three_costs(lines, section_map, name)
        if current is None:
            continue
        base = _resolve_base(vanilla, pre_f, current, base_choice)
        write_cost(lines, section_map, name, scale_int(int(base), ratio),
                   base_choice, base)
        count += 1
    return count


def apply_tl_cost_scaling(lines, section_map, type_list, ratio, base_choice,
                          tl_source):
    """
    Scale cost by TechLevel × ratio. tl_source: 'vanilla' | 'effective' | 'custom'.
    Returns count.
    """
    store = get_tl_store()
    count = 0
    for name in type_list:
        vanilla, pre_f, current = get_three_costs(lines, section_map, name)
        if current is None:
            continue
        base = _resolve_base(vanilla, pre_f, current, base_choice)

        if tl_source == "effective":
            tl_str = get_effective_tl(section_map, name)
        elif tl_source == "custom":
            entry = store.get(name)
            tl_str = str(entry["new"]) if entry else \
                get_effective_tl(section_map, name)
        else:
            tl_str = get_val(section_map, name, "TechLevel", "1")

        try:
            tl = max(1, int(tl_str))
        except ValueError:
            tl = 1

        write_cost(lines, section_map, name,
                   scale_int(int(base), tl * ratio), base_choice, base)
        count += 1
    return count


def apply_depth_cost_scaling(lines, section_map, building_list, depths,
                             ratio, base_choice, min_depth, tiered_rates):
    """
    Scale building cost by (depth+1)×ratio, or by tiered_rates {depth: mult}.
    Skips buildings with TechLevel outside 1-10 and those below min_depth.
    Returns count.
    """
    count = 0
    for name in building_list:
        if valid_tl(get_val(section_map, name, "TechLevel", "")) is None:
            continue
        depth = depths.get(name.lower(), 0)
        if depth < min_depth:
            continue

        vanilla, pre_f, current = get_three_costs(lines, section_map, name)
        if current is None:
            continue
        base = _resolve_base(vanilla, pre_f, current, base_choice)

        if tiered_rates is not None:
            mult = tiered_rates.get(depth, tiered_rates.get(
                max(tiered_rates.keys()), 1.0))
        else:
            mult = (depth + 1) * ratio  # depth+1 so depth-1 blds get ratio×2

        write_cost(lines, section_map, name,
                   scale_int(int(base), mult), base_choice, base)
        count += 1
    return count
