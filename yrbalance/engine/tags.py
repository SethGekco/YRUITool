"""
Unit tags — user-defined labels for organizing units, stored OUTSIDE the mod
(a sidecar at ~/.yrbalancetool/tags.json, keyed by the rules file's path). Lets
users group by provenance, role, review-state, etc., and tag many at once.

Automatic tags (source mod / kind / obtain class) are derived at display time
from a UnitEntry and merged with the stored custom tags — only custom tags are
persisted here. I/O-free apart from the sidecar it reads/writes.
"""

import json
import os


def _path():
    base = os.path.join(os.path.expanduser("~"), ".yrbalancetool")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "tags.json")


def _load_all():
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_all(data):
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, _path())


def _norm(tag):
    return " ".join(tag.strip().split()).lower()


def load_tags(rules_path):
    """{unit_lower: [tags]} of stored custom tags for a mod."""
    return _load_all().get(os.path.abspath(rules_path), {})


def set_unit_tags(rules_path, unit, tags):
    data = _load_all()
    key = os.path.abspath(rules_path)
    mod = data.setdefault(key, {})
    clean = sorted({_norm(t) for t in tags if _norm(t)})
    if clean:
        mod[unit.lower()] = clean
    else:
        mod.pop(unit.lower(), None)
    if not mod:
        data.pop(key, None)
    _save_all(data)


def add_unit_tag(rules_path, unit, tag):
    cur = set(load_tags(rules_path).get(unit.lower(), []))
    cur.add(_norm(tag))
    set_unit_tags(rules_path, unit, cur)


def remove_unit_tag(rules_path, unit, tag):
    cur = set(load_tags(rules_path).get(unit.lower(), []))
    cur.discard(_norm(tag))
    set_unit_tags(rules_path, unit, cur)


def bulk_add_tag(rules_path, units, tag):
    """Tag many units at once. Returns the number changed."""
    data = _load_all()
    key = os.path.abspath(rules_path)
    mod = data.setdefault(key, {})
    t = _norm(tag)
    n = 0
    for u in units:
        cur = set(mod.get(u.lower(), []))
        if t not in cur:
            cur.add(t)
            mod[u.lower()] = sorted(cur)
            n += 1
    _save_all(data)
    return n


def bulk_remove_tag(rules_path, units, tag):
    data = _load_all()
    key = os.path.abspath(rules_path)
    mod = data.get(key, {})
    t = _norm(tag)
    n = 0
    for u in units:
        cur = set(mod.get(u.lower(), []))
        if t in cur:
            cur.discard(t)
            n += 1
            if cur:
                mod[u.lower()] = sorted(cur)
            else:
                mod.pop(u.lower(), None)
    if not mod:
        data.pop(key, None)
    _save_all(data)
    return n


def all_tags(rules_path):
    """Every custom tag in use for a mod, sorted."""
    tags = set()
    for ts in load_tags(rules_path).values():
        tags.update(ts)
    return sorted(tags)
