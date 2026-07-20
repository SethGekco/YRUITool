"""
INI model: parsing, section mapping, and the mutate-lines-in-place edit engine.

This is the I/O-free foundation of YRBalanceTool. It contains *no* prompts and
*no* application logic beyond reading, indexing, and rewriting rulesmd/aimd-style
INI files. Ported faithfully from the original dying_earth_balance_tool.py so the
battle-tested behaviour (section-drift-safe inserts, vanilla comment tracking) is
preserved exactly.

The core model:
  * `lines`       - the raw file as a list of strings (with line endings intact).
  * `section_map` - {name_lower: {"name_raw": str, "start": int,
                                  "keys": {key_lower: (line_idx, key_raw, val_raw)}}}
  * `section_order` - [(name_raw, line_idx), ...] in file order (duplicates kept).

All edits mutate `lines` in place. `section_map` caches go stale after any insert;
callers that insert must rescan or use the index-fixup helpers here.
"""

import re
import math


# =============================================================================
# VALUE / COMMENT HELPERS
# =============================================================================

def scale_int(value, ratio):
    """Round-half-up integer scaling (matches the original tool)."""
    return int(math.floor(value * ratio + 0.5))


def scale_from_vanilla(vanilla_val, ratio):
    """Apply ratio to a vanilla numeric value. Returns (new_value_str, new_numeric)."""
    if isinstance(vanilla_val, int):
        new_v = scale_int(vanilla_val, ratio)
        return str(new_v), new_v
    else:
        new_v = vanilla_val * ratio
        if new_v == int(new_v):
            new_v = int(new_v)
        return str(new_v), new_v


def format_edited_value(new_val_str, vanilla_val, suffix="Edit by AI script"):
    """Format as: new_value;old_value suffix ; vanilla=X"""
    return f"{new_val_str};{vanilla_val} {suffix} ; vanilla={vanilla_val}"


def _parse_number(raw):
    """Parse a string as int if possible, else float, else None."""
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return None


def parse_vanilla_from_line(line_str):
    """
    Extract the vanilla=X tag from an already-edited line's comment, e.g.:
      'Cost=3200; last_value=1600 ; last_ratio=2.0 ; vanilla=800'
    Returns the numeric vanilla value or None if not present / not yet edited.
    """
    m = re.search(r';\s*vanilla=([0-9.]+)', line_str)
    return _parse_number(m.group(1)) if m else None


def parse_last_value_from_line(line_str):
    """
    Extract the last_value=X tag from a tool-edited line. This is the value the
    line held at the START of the last session. Returns numeric or None.
    """
    m = re.search(r';\s*last_value=([0-9.]+)', line_str)
    return _parse_number(m.group(1)) if m else None


def parse_last_ratio_from_line(line_str):
    """Extract the last_ratio=X tag from a tool-edited line. Returns float or None."""
    m = re.search(r';\s*last_ratio=([0-9.]+)', line_str)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def get_current_field_value_from_line(line_str):
    """
    Read the CURRENT (already-scaled) numeric value from a key=value line.
    Returns the value before the first ';', i.e. the active in-game value.
    """
    m = re.match(r'^[^=]+=([^;]+)', line_str.strip())
    if not m:
        return None
    return _parse_number(m.group(1).strip())


# =============================================================================
# INI PARSER
# =============================================================================

def parse_ini_lines(filepath):
    """Read a file into a list of lines, preserving original line endings."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        return f.readlines()


def build_section_map(lines):
    """
    Build (section_map, section_order) from a list of lines.

    Only the FIRST occurrence of each section name is registered in section_map;
    duplicate headers (e.g. [Virus] as both a unit and a warhead) must not
    overwrite the already-collected keys from the first block. section_order keeps
    every header occurrence in file order.
    """
    section_map = {}
    section_order = []
    current_section = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        m = re.match(r'^\[([^\]]+)\]', stripped)
        if m:
            sec = m.group(1).strip()
            sec_lower = sec.lower()
            current_section = sec_lower
            if sec_lower not in section_map:
                section_map[sec_lower] = {"name_raw": sec, "start": i, "keys": {}}
            section_order.append((sec, i))
            continue
        if current_section is None:
            continue
        if not stripped or stripped.startswith(";"):
            continue
        m = re.match(r'^([^=;]+)=(.*)$', stripped)
        if m:
            key_raw = m.group(1).strip()
            val_raw = m.group(2)
            key_lower = key_raw.lower()
            if key_lower not in section_map[current_section]["keys"]:
                section_map[current_section]["keys"][key_lower] = (i, key_raw, val_raw)
    return section_map, section_order


def get_val(section_map, section, key, default=None):
    """Return the active (pre-semicolon) value of a key, or default."""
    s = section_map.get(section.lower())
    if s is None:
        return default
    entry = s["keys"].get(key.lower())
    if entry is None:
        return default
    return entry[2].split(";")[0].strip()


# =============================================================================
# LINE MODIFICATION ENGINE
# =============================================================================

def line_ending(line):
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    if line.endswith("\r"):
        return "\r"
    return ""


def modify_line(lines, line_idx, key_raw, new_value_str):
    """Rewrite a key=value line in place, preserving leading whitespace/ending."""
    line = lines[line_idx]
    ws = line[: len(line) - len(line.lstrip())]
    ending = line_ending(line)
    lines[line_idx] = f"{ws}{key_raw}={new_value_str}{ending}"


def append_line_after_section_header(lines, section_map, section_name, new_line_text):
    """Insert a line right after [SectionHeader], updating all cached indices."""
    s = section_map.get(section_name.lower())
    if s is None:
        return -1
    insert_idx = s["start"] + 1
    ending = line_ending(lines[s["start"]]) or "\n"
    lines.insert(insert_idx, f"{new_line_text}{ending}")
    for sec_data in section_map.values():
        if sec_data["start"] >= insert_idx:
            sec_data["start"] += 1
        new_keys = {}
        for k, (li, kr, vr) in sec_data["keys"].items():
            new_keys[k] = (li + 1 if li >= insert_idx else li, kr, vr)
        sec_data["keys"] = new_keys
    return insert_idx
