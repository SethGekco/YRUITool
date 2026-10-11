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
(backslashes normalised). If a loose file isn't found, the mod's `.mix` archives
are searched by mix-id (deep / nested / encrypted via `find_in_mixes`); such files
get the provenance label ``mix:<name>``. Loose files are preferred; the mix search
is a fallback (and only runs when an include isn't present as a loose file).
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


def _norm(filename):
    """Normalise an include filename (Windows backslashes, quotes, whitespace)."""
    return filename.replace("\\", "/").strip().strip('"')


def _resolve_loose(filename, base_dir):
    """Absolute path of an include file relative to base_dir, or None."""
    cand = os.path.normpath(os.path.join(base_dir, _norm(filename)))
    return cand if os.path.isfile(cand) else None


def _mix_label(filename):
    """Provenance label / opt-in identifier for a file pulled from a .mix."""
    return "mix:" + os.path.basename(_norm(filename))


def _lines_from_mix(filename, mix_folders):
    """Bytes of an include file from the mod's .mix archives → text lines, or None.

    Only runs when the file isn't present loose. Decodes latin-1-safe (utf-8 with
    replacement) and keeps line endings so the combined output stays faithful."""
    if not mix_folders:
        return None
    from .mixlib import find_in_mixes
    try:
        data = find_in_mixes(mix_folders, os.path.basename(_norm(filename)))
    except Exception:
        return None
    if not data:
        return None
    return data.decode("utf-8", "replace").splitlines(keepends=True)


def _expand(lines, label, base_dir, mix_folders, allow, missing, seen):
    """Core: given already-read `lines` from source `label`, append the resolved
    content of every include it declares. base_dir resolves loose includes;
    mix_folders is the fallback .mix search path. Returns (out_lines, provenance).
    """
    out = list(lines)
    prov = [label] * len(lines)
    smap, _ = build_section_map(lines)

    for fn in _include_filenames(smap):
        loose = _resolve_loose(fn, base_dir)
        if loose is not None:
            key = os.path.realpath(loose)
            if key in seen:
                continue
            if allow is not None and not allow(loose):
                continue
            seen.add(key)
            try:
                c_lines = parse_ini_lines(loose)
            except OSError:
                if missing is not None:
                    missing.append((label, fn))
                continue
            c_out, c_prov = _expand(c_lines, loose, os.path.dirname(loose),
                                    mix_folders, allow, missing, seen)
            out.extend(c_out)
            prov.extend(c_prov)
            continue

        # Fallback: search the mod's .mix archives.
        mlabel = _mix_label(fn)
        if mlabel in seen:
            continue
        if allow is not None and not allow(mlabel):
            continue
        c_lines = _lines_from_mix(fn, mix_folders)
        if c_lines is None:
            if missing is not None:
                missing.append((label, fn))
            continue
        seen.add(mlabel)
        # Nested includes inside a mix-sourced file resolve against the mod folder
        # (loose first, then mixes again).
        nested_base = mix_folders[0] if mix_folders else base_dir
        c_out, c_prov = _expand(c_lines, mlabel, nested_base,
                                mix_folders, allow, missing, seen)
        out.extend(c_out)
        prov.extend(c_prov)
    return out, prov


def resolve_includes(filepath, allow=None, missing=None, mix_folders=None):
    """Return (combined_lines, provenance) for `filepath` with its include graph
    expanded.

    combined_lines: the main file's lines verbatim, then each included file's
        combined lines appended (recursively, numeric order). Main-file lines come
        first so first-occurrence-wins gives the main file precedence.
    provenance: a parallel list — provenance[i] is the source of line
        combined_lines[i]: an absolute path for a loose file, or ``mix:<name>``
        for a file pulled from a .mix archive.

    allow: optional predicate(id) -> bool, where id is the included file's absolute
        path (loose) or ``mix:<name>`` (mix). Returning False skips that file and
        its subtree.
    missing: optional list; each unresolved include is appended as
        (including_source, filename).
    mix_folders: folders whose .mix archives are searched when an include isn't
        loose. Defaults to the main file's own directory; pass [] to disable the
        mix fallback (loose files only).
    """
    base = os.path.dirname(filepath)
    if mix_folders is None:
        mix_folders = [base]
    lines = parse_ini_lines(filepath)
    seen = {os.path.realpath(filepath)}
    return _expand(lines, filepath, base, mix_folders, allow, missing, seen)


def load_with_includes(filepath, allow=None, missing=None, mix_folders=None):
    """Resolve the include graph and build the effective section map.

    Returns (combined_lines, section_map, section_order, section_source) where
    section_source maps section-name-lower -> the source of the file that first
    defined it (absolute path, or ``mix:<name>``).
    """
    lines, prov = resolve_includes(filepath, allow=allow, missing=missing,
                                   mix_folders=mix_folders)
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


def _graph_from_lines(lines, label, base_dir, mix_folders, allow, seen):
    """Build an include-graph node from already-read lines (handles loose + mix)."""
    node = {"file": label, "name": os.path.basename(label),
            "present": True, "children": []}
    smap, _ = build_section_map(lines)
    for fn in _include_filenames(smap):
        loose = _resolve_loose(fn, base_dir)
        if loose is not None:
            key = os.path.realpath(loose)
            child = {"file": key, "name": os.path.basename(loose),
                     "present": True, "children": []}
            if key in seen:
                child["cycle"] = True
                node["children"].append(child)
                continue
            if allow is not None and not allow(loose):
                continue
            seen.add(key)
            try:
                c_lines = parse_ini_lines(loose)
            except OSError:
                child["present"] = False
                node["children"].append(child)
                continue
            node["children"].append(_graph_from_lines(
                c_lines, loose, os.path.dirname(loose), mix_folders, allow, seen))
            continue

        mlabel = _mix_label(fn)
        if mlabel in seen:
            node["children"].append({"file": mlabel, "name": _norm(fn),
                                     "present": True, "children": [],
                                     "cycle": True, "in_mix": True})
            continue
        if allow is not None and not allow(mlabel):
            continue
        c_lines = _lines_from_mix(fn, mix_folders)
        if c_lines is None:
            node["children"].append(
                {"file": os.path.normpath(os.path.join(base_dir, _norm(fn))),
                 "name": _norm(fn), "present": False, "children": []})
            continue
        seen.add(mlabel)
        nested_base = mix_folders[0] if mix_folders else base_dir
        child = _graph_from_lines(c_lines, mlabel, nested_base,
                                  mix_folders, allow, seen)
        child["in_mix"] = True
        node["children"].append(child)
    return node


def include_graph(filepath, allow=None, mix_folders=None):
    """Describe the include tree for UI / porter opt-in.

    Returns a node dict: {"file", "name", "present", "children": [...]} per file.
    A missing include has present=False; a file re-reached on the current path has
    "cycle": True; a file found inside a .mix has "in_mix": True. `file` is an
    absolute path for loose files, or ``mix:<name>`` for mix-sourced ones.
    """
    base = os.path.dirname(filepath)
    if mix_folders is None:
        mix_folders = [base]
    try:
        lines = parse_ini_lines(filepath)
    except OSError:
        return {"file": os.path.realpath(filepath),
                "name": os.path.basename(filepath), "present": False,
                "children": []}
    seen = {os.path.realpath(filepath)}
    node = _graph_from_lines(lines, os.path.realpath(filepath), base,
                             mix_folders, allow, seen)
    return node


def flatten_graph(node):
    """Yield every present, non-cycle file id in an include graph, main first."""
    if node.get("present") and not node.get("cycle"):
        yield node["file"]
    for c in node.get("children", []):
        yield from flatten_graph(c)
