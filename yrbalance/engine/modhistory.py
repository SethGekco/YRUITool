"""
Mod change history: per-rules-file snapshots + an append-only change log, kept
under ~/.yrbalancetool/history/. Two jobs:
  * scan-on-load — fingerprint every section; on the next load, diff against the
    stored snapshot to report what changed since last time (works even for mods
    not edited with this tool).
  * live log — append each section change as it's applied, so history survives
    restarts and can be queried per unit (for the encyclopedia).

I/O-free apart from the history file it reads/writes.
"""

import hashlib
import json
import os
import datetime


def _dir():
    d = os.path.join(os.path.expanduser("~"), ".yrbalancetool", "history")
    os.makedirs(d, exist_ok=True)
    return d


def _store_path(rules_path):
    key = hashlib.sha1(os.path.abspath(rules_path).encode("utf-8")).hexdigest()[:16]
    return os.path.join(_dir(), key + ".json")


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def section_fingerprints(lines, section_map):
    """{section_lower: short-hash of its body} for every section."""
    fps = {}
    names = sorted(section_map.values(), key=lambda s: s["start"])
    starts = [s["start"] for s in names]
    for i, s in enumerate(names):
        end = starts[i + 1] if i + 1 < len(starts) else len(lines)
        body = list(lines[s["start"]:end])
        while body and not body[-1].strip():          # drop trailing blank lines
            body.pop()
        norm = "\n".join(ln.rstrip("\r\n") for ln in body)  # line-ending agnostic
        fps[s["name_raw"].lower()] = hashlib.sha1(
            norm.encode("utf-8", "replace")).hexdigest()[:12]
    return fps


def diff_fingerprints(old, new):
    """Return {'added', 'removed', 'changed'} lists of section names (lower)."""
    o, n = set(old), set(new)
    changed = sorted(k for k in (o & n) if old[k] != new[k])
    return {"added": sorted(n - o), "removed": sorted(o - n), "changed": changed}


def load_history(rules_path):
    try:
        with open(_store_path(rules_path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"path": os.path.abspath(rules_path), "snapshot": {}, "log": []}


def _save(rules_path, data):
    tmp = _store_path(rules_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, _store_path(rules_path))


def record_load(rules_path, lines, section_map):
    """Snapshot on load; return the diff vs the previous snapshot (empty on first
    ever load). Updates the stored snapshot."""
    data = load_history(rules_path)
    new_fp = section_fingerprints(lines, section_map)
    old_fp = data.get("snapshot", {})
    diff = diff_fingerprints(old_fp, new_fp) if old_fp else {
        "added": [], "removed": [], "changed": []}
    if old_fp and (diff["added"] or diff["removed"] or diff["changed"]):
        data.setdefault("log", []).append({
            "time": _now(), "kind": "load-diff",
            "added": len(diff["added"]), "removed": len(diff["removed"]),
            "changed": diff["changed"][:200]})
    data["snapshot"] = new_fp
    data["path"] = os.path.abspath(rules_path)
    _save(rules_path, data)
    return diff


def update_snapshot(rules_path, lines, section_map):
    """Refresh the stored fingerprint snapshot without logging a diff (call after
    an apply so the next load doesn't re-report the same change)."""
    data = load_history(rules_path)
    data["snapshot"] = section_fingerprints(lines, section_map)
    data["path"] = os.path.abspath(rules_path)
    _save(rules_path, data)


def record_changes(rules_path, sections, note=""):
    """Append a change event for a set of section names (e.g. what an apply
    touched). `sections` is a list of names."""
    if not sections:
        return
    data = load_history(rules_path)
    data.setdefault("log", []).append({
        "time": _now(), "kind": "edit", "note": note,
        "sections": sorted({s for s in sections})[:500]})
    _save(rules_path, data)


def unit_history(rules_path, section_name):
    """Events that touched a given section, newest first — for the encyclopedia."""
    sl = section_name.lower()
    out = []
    for ev in reversed(load_history(rules_path).get("log", [])):
        if ev.get("kind") == "edit" and any(
                s.lower() == sl for s in ev.get("sections", [])):
            out.append(f"{ev['time']}: edited"
                       + (f" ({ev['note']})" if ev.get("note") else ""))
        elif ev.get("kind") == "load-diff" and sl in [
                c.lower() for c in ev.get("changed", [])]:
            out.append(f"{ev['time']}: changed since previous load")
    return out
