"""
Change history: discover the tool's edits by scanning a rules file's inline
`; vanilla=` annotations, and revert individual ones.

Each edited line carries its original value (`; vanilla=` for most tags, the token
after the first `;` alongside `vanilla_cost=` for soylent). scan_changes turns
those into a list of change records classified by category; revert_change rewrites
one line back to its vanilla value.
"""

import re

from .config import ModConfig
from .selectors import build_category_lists
from .ini_model import (
    line_ending, parse_vanilla_from_line, get_current_field_value_from_line,
    _parse_number,
)

_CAT_LABEL = {"inf": "Infantry", "veh": "Vehicle", "nav": "Naval",
              "air": "Aircraft", "bld": "Structure", "def": "Defense"}


def _category_map(section_map, config):
    """section_name_lower -> display category label."""
    out = {}
    for key, names in build_category_lists(section_map, config).items():
        label = _CAT_LABEL.get(key, key)
        for n in names:
            out.setdefault(n.lower(), label)
    return out


def _classify(section, cat_map):
    cat = cat_map.get(section.lower())
    if cat:
        return cat
    low = section.lower()
    if low == "general":
        return "General"
    if low == "ai":
        return "AI"
    return "Other"


def scan_changes(lines, section_map, config=None):
    """
    Return a list of change records for every tool-annotated line:
      {line_idx, section, tag, current, vanilla, category}
    """
    if config is None:
        config = ModConfig.default()
    cat_map = _category_map(section_map, config)
    changes = []
    current = None
    for i, l in enumerate(lines):
        s = l.strip()
        m = re.match(r'^\[([^\]]+)\]', s)
        if m:
            current = m.group(1).strip()
            continue
        if current is None or not s or s.startswith(";"):
            continue
        van = parse_vanilla_from_line(l)
        if van is None and "vanilla_cost=" in l:
            mm = re.match(r'^\s*[^=]+=\s*[^;]*;\s*([0-9.]+)', l)
            if mm:
                van = _parse_number(mm.group(1))
        if van is None:
            continue
        km = re.match(r'^([^=;]+)=', s)
        if not km:
            continue
        changes.append({
            "line_idx": i,
            "section": current,
            "tag": km.group(1).strip(),
            "current": get_current_field_value_from_line(l),
            "vanilla": van,
            "category": _classify(current, cat_map),
        })
    return changes


def revert_change(lines, change):
    """Rewrite one change's line back to its vanilla value (stripping annotation)."""
    i = change["line_idx"]
    if not (0 <= i < len(lines)):
        return False
    m = re.match(r'^(\s*)([^=;]+)=', lines[i])
    if not m:
        return False
    ending = line_ending(lines[i]) or "\n"
    lines[i] = f"{m.group(1)}{m.group(2).strip()}={change['vanilla']}{ending}"
    return True
