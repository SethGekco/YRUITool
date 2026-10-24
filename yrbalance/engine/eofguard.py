"""
The engine never reads an INI's last line, so every INI the game reads must
end with a comment. The tool guards every INI it WRITES (guard_ini_text);
this module covers the ones it only READS — the loaded mod's files and an old
mod being ported from — which may end on a real key the game silently skips.

Finding them is read-only. Fixing one appends a ';' line as raw bytes and
nothing else: no re-encoding, no line-ending normalisation, so no other byte
of the file can change.
"""

import os

from .fsutil import sibling_file
from .ini_model import last_line_unguarded, guard_ini_bytes
from .includes import include_graph, flatten_graph

# Game-read INIs that sit beside a rules file, by role (first match wins).
_SIBLINGS = (("artmd.ini", "art.ini"), ("soundmd.ini", "sound.ini"),
             ("aimd.ini", "ai.ini"))


def mod_ini_files(rules_path):
    """The loose INIs the game reads for a mod: its rules file, every file in
    its [#include]/[$include] graph, and the art/sound/AI files beside it
    (plus their includes). Files packed in .mix archives are left out — they
    can't be appended to in place."""
    out, seen = [], set()

    def add_tree(path):
        try:
            files = flatten_graph(include_graph(path, mix_folders=[]))
        except Exception:
            files = [path]
        for f in files:
            if f.startswith("mix:") or not os.path.isfile(f):
                continue
            key = os.path.realpath(f)
            if key not in seen:
                seen.add(key)
                out.append(f)

    if rules_path and os.path.isfile(rules_path):
        add_tree(rules_path)
        folder = os.path.dirname(rules_path)
        for names in _SIBLINGS:
            p = sibling_file(folder, *names)
            if p:
                add_tree(p)
    return out


def scan_eof_guards(paths):
    """[(path, last_line)] for the files whose last line the game skips."""
    out = []
    for p in paths:
        try:
            with open(p, "rb") as fh:
                last = last_line_unguarded(fh.read())
        except OSError:
            continue
        if last is not None:
            out.append((p, last))
    return out


def add_eof_guard(path):
    """Append the ';' guard to a file in place (bytes only). Returns True if
    the file was changed."""
    with open(path, "rb") as fh:
        data = fh.read()
    new = guard_ini_bytes(data)
    if new == data:
        return False
    with open(path, "ab") as fh:
        fh.write(new[len(data):])
    return True
