"""
Section manipulation infrastructure: safe clone naming, appending whole sections,
setting/removing keys in appended sections, and registering entries into index
list sections (e.g. [InfantryTypes]) with prefixed keys.

This is the shared plumbing every clone/generator feature sits on. All inserts fix
up the cached `section_map` indices so downstream sections don't drift.

IMPORTANT — corruption fix: `set_key_in_appended_section` here inserts a new key at
the END of the section body (after the last real content line, stopping before the
blank/comment boundary that separates sections), NOT right after the header. The
original tool inserted after the header, which — combined with hundreds of calls —
let tags bleed into the next section's inter-section comment block and eventually
overwrote section headers. It also scans the live body first (so a stale cached
index can't create a duplicate) and de-dupes.
"""

import re

from .ini_model import line_ending, modify_line, pop_eof_guard


def _shift_indices(section_map, insert_idx, by=1):
    """Shift every cached start/key index at or after insert_idx by `by`."""
    for sec_data in section_map.values():
        if sec_data["start"] >= insert_idx:
            sec_data["start"] += by
        new_keys = {}
        for k, (li, kr, vr) in sec_data["keys"].items():
            new_keys[k] = (li + by if li >= insert_idx else li, kr, vr)
        sec_data["keys"] = new_keys


def make_safe_clone_name(base, suffix, section_map, max_len=24):
    """Build a safe section name `base+suffix` <= max_len and unique in section_map."""
    available = max_len - len(suffix)
    if available < 1:
        candidate = suffix[:max_len]
    else:
        candidate = f"{base[:available]}{suffix}"
    counter = 1
    name = candidate
    while name.lower() in section_map:
        tail = str(counter)
        name = candidate[: max_len - len(tail)] + tail
        counter += 1
    return name


def get_section_raw_lines(lines, section_map, section_name):
    """
    Return the raw lines of a section — from its [Header] to just before the next
    section header in file order.
    """
    s = section_map.get(section_name.lower())
    if s is None:
        return []
    start = s["start"]
    next_start = len(lines)
    for other_name, other_data in section_map.items():
        if other_name == section_name.lower():
            continue
        if start < other_data["start"] < next_start:
            next_start = other_data["start"]
    return lines[start:next_start]


def append_raw_section(lines, section_map, section_name, raw_lines, ending="\n"):
    """
    Append a new section built from raw_lines (raw_lines[0] must be the header),
    registering it in section_map. Returns the section name (unchanged if it
    already exists).
    """
    if section_name.lower() in section_map:
        return section_name

    # New content goes in front of a trailing ';' guard, never after it —
    # otherwise this section's final key becomes the file's last line, which
    # the engine does not read. The guard is put back below.
    guard = pop_eof_guard(lines)

    if lines and lines[-1].strip() != "":
        lines.append(ending)

    new_start = len(lines)
    for raw_line in raw_lines:
        lines.append(raw_line.rstrip("\r\n") + ending)

    new_keys = {}
    for offset, raw_line in enumerate(raw_lines):
        if offset == 0:
            continue  # header
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        m = re.match(r'^([^=;]+)=(.*)$', stripped)
        if m:
            kr = m.group(1).strip()
            if kr.lower() not in new_keys:
                new_keys[kr.lower()] = (new_start + offset, kr, m.group(2))

    section_map[section_name.lower()] = {
        "name_raw": section_name, "start": new_start, "keys": new_keys}
    lines.extend(guard)          # exactly what trailed the file, now after us
    return section_name


def _section_body_bounds(lines, start):
    """
    Given a section header index, return (last_content_idx, found_key_scan_range).
    Scans the body, stopping at the next header or at a blank line that begins the
    inter-section boundary (blank followed by comment/header/EOF). Returns the index
    of the last real key/content line (>= start).
    """
    last_content_idx = start
    i = start + 1
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            break  # next section header
        if stripped == "":
            # A blank line whose following non-blank content is a comment, a
            # header, or EOF marks the section boundary — stop here so we don't
            # reach into the next section's inter-section comment block.
            j = i + 1
            while j < n and lines[j].strip() == "":
                j += 1
            if j >= n:
                break
            nxt = lines[j].strip()
            if nxt.startswith(";") or (nxt.startswith("[") and nxt.endswith("]")):
                break
            i += 1
            continue
        # Comment or key line inside the body counts as content we insert after.
        last_content_idx = i
        i += 1
    return last_content_idx


def set_key_in_appended_section(lines, section_map, section_name, key_raw, new_val_str):
    """
    Set (overwrite) or add a key in a section. If the key exists anywhere in the
    live body it is overwritten in place; otherwise the new key is inserted at the
    END of the section body (before the section boundary), never after the header.
    Cached indices are fixed up on insert.
    """
    s = section_map.get(section_name.lower())
    if s is None:
        return
    start = s["start"]
    ending = line_ending(lines[start]) or "\n"

    # Scan the live body for an existing occurrence and the last content line.
    last_content_idx = start
    found_idx = None
    i = start + 1
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            break
        if stripped == "":
            j = i + 1
            while j < n and lines[j].strip() == "":
                j += 1
            if j >= n:
                break
            nxt = lines[j].strip()
            if nxt.startswith(";") or (nxt.startswith("[") and nxt.endswith("]")):
                break
            i += 1
            continue
        if not stripped.startswith(";"):
            m = re.match(r'^([^=;]+)=', stripped)
            if m and m.group(1).strip().lower() == key_raw.lower():
                found_idx = i  # keep last occurrence; de-dupe below
            last_content_idx = i
        i += 1

    if found_idx is not None:
        modify_line(lines, found_idx, key_raw, new_val_str)
        s["keys"][key_raw.lower()] = (found_idx, key_raw, new_val_str)
        return

    insert_idx = last_content_idx + 1
    lines.insert(insert_idx, f"{key_raw}={new_val_str}{ending}")
    _shift_indices(section_map, insert_idx, +1)
    section_map[section_name.lower()]["keys"][key_raw.lower()] = (
        insert_idx, key_raw, new_val_str)


def remove_key_from_section(lines, section_map, section_name, key_raw):
    """Blank a key=value line in a section (replace with an empty line)."""
    s = section_map.get(section_name.lower())
    if s is None:
        return
    entry = s["keys"].get(key_raw.lower())
    if entry is None:
        return
    li, _, _ = entry
    lines[li] = line_ending(lines[li]) or "\n"
    del s["keys"][key_raw.lower()]


# ── Index-list registration ──────────────────────────────────────────────────

def new_registration_state():
    """
    Create fresh per-run registration state. Tracks, per key-prefix, the next
    index and whether the blank separator has been inserted. Pass the same state
    object across all registrations in one run.
    """
    return {}


def register_in_list(lines, section_map, section_name, state, key_prefix, value):
    """
    Append `value` to an index list section as `key_prefix####=value` (e.g.
    DEGB0003=NAME), creating the section if absent and inserting a blank separator
    before the first prefixed entry. Uses/updates `state[key_prefix]`.
    """
    counter = state.setdefault(key_prefix, {"next": 0, "separator_inserted": False})

    if section_name.lower() not in section_map:
        ending = "\r\n" if lines and lines[-1].endswith("\r\n") else "\n"
        guard = pop_eof_guard(lines)       # keep a trailing ';' guard last
        if lines and lines[-1].strip():
            lines.append(ending)
        new_start = len(lines)
        lines.append(f"[{section_name}]{ending}")
        section_map[section_name.lower()] = {
            "name_raw": section_name, "start": new_start, "keys": {}}
        lines.extend(guard)

    s = section_map[section_name.lower()]
    ending = line_ending(lines[s["start"]]) or "\n"

    last_line_idx = s["start"]
    for _, (li, _, _) in s["keys"].items():
        if li > last_line_idx:
            last_line_idx = li

    if not counter["separator_inserted"]:
        sep_idx = last_line_idx + 1
        lines.insert(sep_idx, ending)
        _shift_indices(section_map, sep_idx, +1)
        last_line_idx += 1
        counter["separator_inserted"] = True

    de_key = f"{key_prefix}{counter['next']:04d}"
    counter["next"] += 1
    insert_idx = last_line_idx + 1
    lines.insert(insert_idx, f"{de_key}={value}{ending}")
    _shift_indices(section_map, insert_idx, +1)
    section_map[section_name.lower()]["keys"][de_key.lower()] = (insert_idx, de_key, value)
    return de_key
