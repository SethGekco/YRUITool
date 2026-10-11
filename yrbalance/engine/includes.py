"""
Resolve Ares `[#include]` / Phobos `[$include]` directives into one effective INI.

Mods split rules across included files — classic Ares `[#include]` (a numbered
list of filenames) and the Phobos `[$include]` variant — for country files,
superweapon files, and (as of this tool) the `ai_prereq_*` dummy files it writes
itself. The game splices each included file in where the directive sits; we build
one combined line list so analysis/port see every section, not just the main file.

Design:
- **Read-only.** This is for analysis/port. The editable file the UI loads stays
  single-file, so saving never collapses the split back into one file.
- **Main file wins, includes fill gaps.** The main file's lines are kept verbatim,
  then each included file's content is appended (recursively, numeric-key order).
  Combined with the tool's first-occurrence-wins section map, the main file's own
  sections take precedence — matching how mods use includes to carry *disjoint*
  content (countries, SWs, dummies) rather than to override the main file.
- **Cycle-safe** (a file can't include itself transitively), tracks **provenance**
  (which source file each line/section came from), and supports a per-file
  **opt-in** `allow` predicate so the porter can let the user choose which
  includes to pull in.

Include filenames are resolved relative to the including file's own directory
(backslashes normalised). Files inside .mix archives are not resolved here (loose
files only) — that's a documented follow-up.
"""

import os

from .ini_model import parse_ini_lines, build_section_map

# Section names that carry include directives (Ares and Phobos spellings).
INCLUDE_SECTIONS = ("#include", "$include")


def _include_filenames(section_map):
    """Ordered [filename] from the [#include]/[$include] sections of one map.

    Numeric keys (the usual `1=foo.ini`) sort ascending; any non-numeric keys
    keep their insertion order after the numeric ones. `#include` is processed
    before `$include` when a file somehow has both.
    """
    out = []
    for sec in INCLUDE_SECTIONS:
        s = section_map.get(sec)
        if not s:
            continue
        numeric, other = [], []
        for _klow, (_idx, kraw, vraw) in s["keys"].items():
            fn = vraw.split(";")[0].strip()
            if not fn:
                continue
            try:
                numeric.append((int(kraw), fn))
            except ValueError:
                other.append(fn)
        numeric.sort(key=lambda t: t[0])
        out.extend(fn for _k, fn in numeric)
        out.extend(other)
    return out


def _resolve_path(filename, base_dir):
    """Resolve an include filename (relative to base_dir) to an absolute path, or
    None if it doesn't exist as a loose file. Normalises Windows backslashes."""
    fn = filename.replace("\\", "/").strip().strip('"')
    cand = os.path.normpath(os.path.join(base_dir, fn))
    return cand if os.path.isfile(cand) else None


def resolve_includes(filepath, allow=None, missing=None, _seen=None):
    """Return (combined_lines, provenance) for `filepath` with its include graph
    expanded.

    combined_lines: the main file's lines verbatim, then each included file's
        combined lines appended (recursively, numeric order). Main-file lines come
        first so first-occurrence-wins gives the main file precedence.
    provenance: a parallel list — provenance[i] is the absolute path of the file
        that line combined_lines[i] came from.

    allow: optional predicate(abs_path) -> bool. When it returns False for an
        included file, that file (and its subtree) is skipped.
    missing: optional list; each unresolved include filename is appended as
        (including_file, filename).
    """
    _seen = _seen if _seen is not None else set()
    real = os.path.realpath(filepath)
    if real in _seen:
        return [], []                       # cycle — already expanded
    _seen = _seen | {real}

    lines = parse_ini_lines(filepath)
    out = list(lines)
    prov = [filepath] * len(lines)

    smap, _ = build_section_map(lines)
    base = os.path.dirname(filepath)
    for fn in _include_filenames(smap):
        child = _resolve_path(fn, base)
        if child is None:
            if missing is not None:
                missing.append((filepath, fn))
            continue
        if allow is not None and not allow(child):
            continue
        c_lines, c_prov = resolve_includes(child, allow, missing, _seen)
        out.extend(c_lines)
        prov.extend(c_prov)
    return out, prov


def load_with_includes(filepath, allow=None, missing=None):
    """Resolve the include graph and build the effective section map.

    Returns (combined_lines, section_map, section_order, section_source) where
    section_source maps section-name-lower -> the absolute path of the file that
    first defined it (so a consumer can show where a section lives).
    """
    lines, prov = resolve_includes(filepath, allow=allow, missing=missing)
    section_map, section_order = build_section_map(lines)
    section_source = {}
    for name_raw, idx in section_order:
        low = name_raw.lower()
        if low not in section_source:
            section_source[low] = prov[idx] if idx < len(prov) else filepath
    return lines, section_map, section_order, section_source


def has_includes(section_map):
    """True if a section map carries any [#include]/[$include] directive."""
    return any(sec in section_map for sec in INCLUDE_SECTIONS)


def include_graph(filepath, allow=None, _seen=None):
    """Describe the include tree for UI / porter opt-in.

    Returns a node dict: {"file": abs_path, "name": basename, "present": True,
    "children": [node | {"file","name","present":False} for missing]}. A file
    already visited on the current path (cycle) gets present=True, children=[]
    and "cycle": True.
    """
    _seen = _seen if _seen is not None else set()
    real = os.path.realpath(filepath)
    node = {"file": real, "name": os.path.basename(filepath),
            "present": True, "children": []}
    if real in _seen:
        node["cycle"] = True
        return node
    _seen = _seen | {real}

    try:
        smap, _ = build_section_map(parse_ini_lines(filepath))
    except OSError:
        node["present"] = False
        return node
    base = os.path.dirname(filepath)
    for fn in _include_filenames(smap):
        child = _resolve_path(fn, base)
        if child is None:
            node["children"].append(
                {"file": os.path.normpath(os.path.join(base, fn)),
                 "name": fn, "present": False, "children": []})
            continue
        if allow is not None and not allow(child):
            continue
        node["children"].append(include_graph(child, allow, _seen))
    return node


def flatten_graph(node):
    """Yield every present, non-cycle file path in an include graph, main first."""
    if node.get("present") and not node.get("cycle"):
        yield node["file"]
    for c in node.get("children", []):
        yield from flatten_graph(c)
