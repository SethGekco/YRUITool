"""Detect which engine-extension DLLs a mod actually ships.

Used to gate reference-feed knowledge so Eva only talks about features whose DLL
is present (e.g. don't explain TechnoAttachmentExt tags to someone not using it).

Syringe-loaded extension DLLs live next to the game .exe (or in the mod folder);
we scan the rules folder and its parent for *.dll and return their base names,
lowercased. Matching against a feed entry's declared DLL is case-insensitive and
ignores a trailing '.dll'.
"""

import os


def _dll_names_in(folder):
    out = set()
    if not folder or not os.path.isdir(folder):
        return out
    try:
        for fn in os.listdir(folder):
            if fn.lower().endswith(".dll") and os.path.isfile(
                    os.path.join(folder, fn)):
                out.add(fn[:-4].lower())   # basename without .dll
    except OSError:
        pass
    return out


def detect_dlls(folder, extra_folders=None):
    """Set of extension-DLL base names (lowercased, no .dll) found beside a mod.

    Scans `folder` and its parent (the game root is usually the parent of a mod
    subfolder, and vice versa), plus any `extra_folders`."""
    found = set()
    seen_dirs = set()
    cands = [folder, os.path.dirname(folder) if folder else None]
    cands += list(extra_folders or [])
    for d in cands:
        if not d:
            continue
        real = os.path.realpath(d)
        if real in seen_dirs:
            continue
        seen_dirs.add(real)
        found |= _dll_names_in(d)
    return found


def normalize_dll(name):
    """Normalize a DLL identifier (strip path, '.dll', case) for comparison."""
    if not name:
        return ""
    base = os.path.basename(str(name)).strip()
    if base.lower().endswith(".dll"):
        base = base[:-4]
    return base.lower()
