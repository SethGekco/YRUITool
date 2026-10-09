"""
Bookmarked mod sources — a small persistent library of rules files the user wants
quick access to (for the encyclopedia and the porter). Plain JSON at
~/.yrbalancetool/bookmarks.json; I/O-free apart from that file.
"""

import json
import os


def _path():
    base = os.path.join(os.path.expanduser("~"), ".yrbalancetool")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "bookmarks.json")


def _label_for(rules_path):
    folder = os.path.dirname(rules_path)
    return os.path.basename(folder) or os.path.basename(rules_path)


def load_bookmarks():
    """Return [{'label', 'rules_path', 'exists'}], newest first."""
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = []
    out = []
    for b in data:
        p = b.get("rules_path", "")
        out.append({"label": b.get("label") or _label_for(p),
                    "rules_path": p, "exists": os.path.isfile(p)})
    return out


def _save(items):
    with open(_path(), "w", encoding="utf-8") as f:
        json.dump([{"label": b["label"], "rules_path": b["rules_path"]}
                   for b in items], f, indent=2)


def add_bookmark(rules_path, label=None):
    """Add (or move-to-front) a bookmark. Returns the updated list."""
    rules_path = os.path.abspath(rules_path)
    items = [b for b in load_bookmarks()
             if os.path.abspath(b["rules_path"]) != rules_path]
    items.insert(0, {"label": label or _label_for(rules_path),
                     "rules_path": rules_path, "exists": os.path.isfile(rules_path)})
    _save(items)
    return items


def remove_bookmark(rules_path):
    rules_path = os.path.abspath(rules_path)
    items = [b for b in load_bookmarks()
             if os.path.abspath(b["rules_path"]) != rules_path]
    _save(items)
    return items


def is_bookmarked(rules_path):
    rules_path = os.path.abspath(rules_path)
    return any(os.path.abspath(b["rules_path"]) == rules_path
               for b in load_bookmarks())
