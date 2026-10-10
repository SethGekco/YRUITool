"""
Asset/AI library catalog — a cross-mod shelf of TechnoTypes and AITriggerTypes.

A *source* is a mod added to the library, either by REFERENCE (read live from its
folder) or as a SNAPSHOT (its rules/aimd/art/sound copied into a private dir under
~/.yrbalancetool/library/<id>/ so it survives the original being moved). Both are
read the same way afterwards — only where `rules_path` points differs.

Items are scanned on demand (reused from encyclopedia.build_mod_index for the
TechnoTypes, plus the sibling aimd's AITriggerTypes) and grouped by KIND for the
tab views. Collections are user lists that cut across kinds/sources; one auto
collection is created per source (the mod's folder). Each source carries an
estimated "balance culture" — per-kind factors vs vanilla, plus a plain-English
summary (deterministic, or Eva-written).

Storage is a sidecar JSON (~/.yrbalancetool/library.json) — never the mod's INI.
"""

import json
import os
import re
import shutil
from dataclasses import dataclass, field, asdict

from .ini_model import parse_ini_lines, build_section_map
from .typelists import parse_type_list
from .portmod import compute_factors

_KIND_TABS = ("infantry", "vehicle", "aircraft", "building", "ai", "other")
_SNAPSHOT_SIBLINGS = ("aimd.ini", "ai.ini", "artmd.ini", "art.ini",
                      "soundmd.ini", "sound.ini")


# ── data model ──────────────────────────────────────────────────────────────
@dataclass
class LibrarySource:
    id: str
    label: str
    mode: str                    # "reference" | "snapshot"
    rules_path: str = ""         # where items are read from (private copy if snapshot)
    orig_path: str = ""          # original rules path (provenance)
    added_at: str = ""
    balance: dict = field(default_factory=dict)      # {kind: {stat: {...}}}
    balance_summary: str = ""


@dataclass
class Collection:
    name: str
    auto_source: str = ""        # set => auto per-mod folder for this source id
    items: list = field(default_factory=list)        # [[source_id, kind, name]]


@dataclass
class LibraryItem:
    source_id: str
    name: str
    kind: str                    # infantry|vehicle|aircraft|building|ai|other
    obtain: str = ""
    detail: str = ""             # short descriptor (id / trigger name)


def library_catalog_path():
    return os.path.join(os.path.expanduser("~"), ".yrbalancetool", "library.json")


def _snapshot_dir(source_id):
    return os.path.join(os.path.expanduser("~"), ".yrbalancetool", "library",
                        source_id)


# ── persistence ─────────────────────────────────────────────────────────────
def load_catalog(path=None):
    path = path or library_catalog_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (FileNotFoundError, ValueError):
        return [], []
    sources = [LibrarySource(**s) for s in raw.get("sources", [])]
    cols = [Collection(**c) for c in raw.get("collections", [])]
    return sources, cols


def save_catalog(sources, collections, path=None):
    path = path or library_catalog_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"sources": [asdict(s) for s in sources],
                   "collections": [asdict(c) for c in collections]},
                  fh, indent=1)
    os.replace(tmp, path)


def _slug(label):
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return s or "src"


def _unique_id(base, taken):
    cand, n = base, 1
    while cand in taken:
        n += 1
        cand = f"{base}-{n}"
    return cand


# ── add / remove sources ────────────────────────────────────────────────────
def add_source(rules_path, label=None, mode="reference", added_at="",
               vanilla_path=None, path=None):
    """Add a mod to the library. mode='snapshot' copies rules + sibling aimd/art/
    sound into a private dir first. Computes the balance culture vs vanilla."""
    sources, cols = load_catalog(path)
    label = label or os.path.basename(os.path.dirname(rules_path)) or \
        os.path.basename(rules_path)
    sid = _unique_id(_slug(label), {s.id for s in sources})
    orig = rules_path

    if mode == "snapshot":
        dst = _snapshot_dir(sid)
        os.makedirs(dst, exist_ok=True)
        base = os.path.basename(rules_path)
        shutil.copy2(rules_path, os.path.join(dst, base))
        folder = os.path.dirname(rules_path)
        for sib in _SNAPSHOT_SIBLINGS:
            sp = os.path.join(folder, sib)
            if os.path.isfile(sp):
                shutil.copy2(sp, os.path.join(dst, sib))
        rules_path = os.path.join(dst, base)

    balance = balance_culture(rules_path, vanilla_path)
    src = LibrarySource(id=sid, label=label, mode=mode, rules_path=rules_path,
                        orig_path=orig, added_at=added_at, balance=balance,
                        balance_summary=describe_culture(balance))
    sources.append(src)
    cols.append(Collection(name=label, auto_source=sid))
    save_catalog(sources, cols, path)
    return src


def remove_source(source_id, path=None):
    sources, cols = load_catalog(path)
    sources = [s for s in sources if s.id != source_id]
    # drop the auto collection and prune manual references
    cols = [c for c in cols if c.auto_source != source_id]
    for c in cols:
        c.items = [it for it in c.items if it[0] != source_id]
    save_catalog(sources, cols, path)
    # best-effort remove snapshot files
    d = _snapshot_dir(source_id)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    return sources, cols


def set_source_summary(source_id, summary, path=None):
    sources, cols = load_catalog(path)
    for s in sources:
        if s.id == source_id:
            s.balance_summary = summary
    save_catalog(sources, cols, path)
    return sources


# ── collections ─────────────────────────────────────────────────────────────
def create_collection(name, path=None):
    sources, cols = load_catalog(path)
    if not any(c.name == name for c in cols):
        cols.append(Collection(name=name))
        save_catalog(sources, cols, path)
    return cols


def delete_collection(name, path=None):
    sources, cols = load_catalog(path)
    cols = [c for c in cols if not (c.name == name and not c.auto_source)]
    save_catalog(sources, cols, path)
    return cols


def _item_key(item):
    return [item.source_id, item.kind, item.name]


def add_to_collection(name, item, path=None):
    sources, cols = load_catalog(path)
    key = _item_key(item) if isinstance(item, LibraryItem) else list(item)
    for c in cols:
        if c.name == name and not c.auto_source:
            if key not in c.items:
                c.items.append(key)
    save_catalog(sources, cols, path)
    return cols


def remove_from_collection(name, item, path=None):
    sources, cols = load_catalog(path)
    key = _item_key(item) if isinstance(item, LibraryItem) else list(item)
    for c in cols:
        if c.name == name and not c.auto_source:
            c.items = [it for it in c.items if it != key]
    save_catalog(sources, cols, path)
    return cols


def collection_keys(collection):
    """Set of (source_id, kind, name_lower) a collection matches. Auto collections
    match every item of their source (resolved by the caller against scanned items);
    for those this returns an empty set and `collection.auto_source` is used."""
    return {(k[0], k[1], k[2].lower()) for k in collection.items}


# ── item scanning ───────────────────────────────────────────────────────────
def scan_items(source, config=None):
    """All library items for a source, grouped later by kind. Reuses the
    encyclopedia index for TechnoTypes and the sibling aimd for AITriggerTypes."""
    from .encyclopedia import build_mod_index   # local: avoids import cycle
    items = []
    try:
        idx = build_mod_index(source.rules_path, label=source.label, config=config)
    except Exception:
        idx = None
    if idx is not None:
        for u in idx.units.values():
            kind = u.kind if u.kind in _KIND_TABS else "other"
            items.append(LibraryItem(source.id, u.name, kind,
                                     obtain=getattr(u, "obtain", ""),
                                     detail=getattr(u, "obtain", "")))
    # AI triggers from the sibling aimd
    for aimd in _siblings(source.rules_path, ("aimd.ini", "ai.ini")):
        amap = build_section_map(parse_ini_lines(aimd))[0]
        trig = amap.get("aitriggertypes")
        if trig:
            for _klow, (_li, kr, vr) in trig["keys"].items():
                nm = vr.split(",", 1)[0].strip()
                items.append(LibraryItem(source.id, kr, "ai",
                                         detail=nm or kr))
        break
    return items


def _siblings(rules_path, names):
    folder = os.path.dirname(rules_path)
    low = {f.lower(): f for f in os.listdir(folder)} if os.path.isdir(folder) else {}
    return [os.path.join(folder, low[n]) for n in names if n in low]


# ── balance culture ─────────────────────────────────────────────────────────
_CULTURE_KINDS = ("infantry", "vehicle", "aircraft", "building")
_CULTURE_STATS = ("Cost", "Strength", "Speed")


def balance_culture(rules_path, vanilla_path=None):
    """Per-kind factors of this mod vs vanilla (factor = mod / vanilla). Returns
    {kind: {stat: {factor, confidence, samples, note}}}. Empty if no vanilla."""
    vanilla_path = vanilla_path or _default_vanilla()
    if not vanilla_path or not os.path.isfile(vanilla_path):
        return {}
    try:
        mod = build_section_map(parse_ini_lines(rules_path))[0]
        van = build_section_map(parse_ini_lines(vanilla_path))[0]
    except Exception:
        return {}
    # factor = tgt/src, so src=vanilla, tgt=mod gives mod/vanilla
    factors = compute_factors(van, mod)
    out = {}
    for kind, stats in factors.items():
        kd = {}
        for stat, sf in stats.items():
            if sf.confidence != "none" and sf.samples >= 2:
                kd[stat] = {"factor": sf.factor, "confidence": sf.confidence,
                            "samples": sf.samples, "note": sf.note}
        if kd:
            out[kind] = kd
    return out


def _default_vanilla():
    p = os.path.expanduser(
        "~/.yrbalancetool/knowledge/read/vanilla_yr_rulesmd.ini")
    return p if os.path.isfile(p) else None


def _phrase(stat, factor):
    if stat == "Cost":
        if factor < 0.9:
            return f"{int(round((1 - factor) * 100))}% cheaper"
        if factor > 1.1:
            return f"{factor:.1f}× pricier"
        return "vanilla-priced"
    word = {"Strength": "tough", "Speed": "fast"}.get(stat, stat.lower())
    if factor > 1.1:
        return f"{factor:.1f}× {word}er"
    if factor < 0.9:
        return f"{int(round((1 - factor) * 100))}% less {word}"
    return f"vanilla {word}ness"


def describe_culture(balance):
    """Deterministic plain-English summary of a balance-culture dict."""
    if not balance:
        return "No vanilla baseline available — balance culture unknown."
    parts = []
    for kind in _CULTURE_KINDS:
        kd = balance.get(kind)
        if not kd:
            continue
        bits = [_phrase(stat, kd[stat]["factor"])
                for stat in _CULTURE_STATS if stat in kd]
        if bits:
            parts.append(f"{kind}: " + ", ".join(bits))
    return "; ".join(parts) if parts else \
        "Too few shared units with vanilla to estimate."


def conversion_factors(from_culture, to_culture):
    """Per-kind/stat multiplier to move a value tuned for `from` into `to`'s scale
    (to/from). Only where both sides have a factor. Serves 'convert mod A → B'."""
    out = {}
    for kind in set(from_culture) & set(to_culture):
        kd = {}
        for stat in set(from_culture[kind]) & set(to_culture[kind]):
            f = from_culture[kind][stat]["factor"]
            t = to_culture[kind][stat]["factor"]
            if f:
                kd[stat] = round(t / f, 4)
        if kd:
            out[kind] = kd
    return out
