"""
AI library: lift AITriggerType *bundles* (a trigger + its primary/backup teams +
those teams' TaskForces & Scripts) or individual blocks (TaskForce / Script /
TeamType) out of an aimd.ini into a reusable sidecar store, and push them back
into any aimd.ini.

Pull reuses portai's re-mint + reference-rewrite + Owner/House cleanup, and adds
a dedup/merge pass:
  - an incoming TaskForce/Script/TeamType that is IDENTICAL to one already in the
    target (ignoring its Name/id) is *reused* — references repoint to the existing
    id and nothing is appended;
  - a block that differs but is >= a similarity threshold (default 95%) to an
    existing one is still added as new, with a warning so the user can decide.

Referenced units (a TaskForce fields TNKDESO etc.) are reported; the caller may
leave-and-warn, remap each to an existing unit, or bring a saved stub section.

Storage is a sidecar JSON under ~/.yrbalancetool/ — never the mod's INI. I/O-free
except load/save of that one file.
"""

import json
import os
from dataclasses import dataclass, field, asdict

from .typelists import parse_type_list
from .ini_model import get_val
from .portmod import _clean, _ordered_keys, _register_type_list
from .portai import (
    _numbered, _minter, _existing_ids,
    _TRIG_TEAM1, _TRIG_OWNER, _TRIG_TEAM2, _INDEX_REF_ACTIONS,
)
from .sections import append_raw_section, set_key_in_appended_section

_KIND_LIST = {"taskforce": "TaskForces", "script": "ScriptTypes",
              "teamtype": "TeamTypes"}


# ── library data model ──────────────────────────────────────────────────────
@dataclass
class LibBlock:
    id: str
    kind: str                       # taskforce | script | teamtype
    keys: list = field(default_factory=list)      # [[k, v]]


@dataclass
class LibEntry:
    name: str                       # user label
    kind: str                       # bundle | taskforce | script | teamtype | trigger
    source: str = ""                # originating mod label
    blocks: list = field(default_factory=list)     # [LibBlock]
    trigger_id: str = ""            # for bundle/trigger
    trigger_def: str = ""
    units: list = field(default_factory=list)      # referenced unit ids
    unit_stubs: dict = field(default_factory=dict) # unit -> {"list":.., "keys":[[k,v]]}
    notes: list = field(default_factory=list)      # extraction-time (auto) notes
    user_notes: str = ""           # free-form private details the user writes
    credits: str = ""              # separate credits box (original author etc.)
    bookmarked: bool = False       # user flag — surfaced in the "★ Bookmarked" view

    def key(self):
        """Identity of an entry in the library (name + kind)."""
        return (self.name, self.kind)

    def to_json(self):
        d = asdict(self)
        return d

    @staticmethod
    def from_json(d):
        blocks = [LibBlock(**b) for b in d.get("blocks", [])]
        return LibEntry(
            name=d.get("name", ""), kind=d.get("kind", "bundle"),
            source=d.get("source", ""), blocks=blocks,
            trigger_id=d.get("trigger_id", ""), trigger_def=d.get("trigger_def", ""),
            units=list(d.get("units", [])), unit_stubs=dict(d.get("unit_stubs", {})),
            notes=list(d.get("notes", [])),
            user_notes=d.get("user_notes", ""), credits=d.get("credits", ""),
            bookmarked=bool(d.get("bookmarked", False)))


@dataclass
class AiCollection:
    """A named, ordered grouping of library entries, referenced by (name, kind)."""
    name: str
    items: list = field(default_factory=list)        # [[name, kind]]


def library_path():
    return os.path.join(os.path.expanduser("~"), ".yrbalancetool", "ailibrary.json")


def _load_doc(path=None):
    """Read the whole library document ({entries, collections}) as a dict."""
    path = path or library_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (FileNotFoundError, ValueError):
        return {"entries": [], "collections": []}
    raw.setdefault("entries", [])
    raw.setdefault("collections", [])
    return raw


def _save_doc(doc, path=None):
    path = path or library_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    os.replace(tmp, path)


def load_library(path=None):
    return [LibEntry.from_json(e) for e in _load_doc(path).get("entries", [])]


def save_library(entries, path=None):
    """Persist the entry list, preserving any collections already on disk."""
    doc = _load_doc(path)
    doc["entries"] = [e.to_json() for e in entries]
    _save_doc(doc, path)


def add_entry(entry, path=None):
    entries = load_library(path)
    entries = [e for e in entries if not (e.name == entry.name
                                          and e.kind == entry.kind)]
    entries.append(entry)
    save_library(entries, path)
    return entries


def remove_entry(name, kind, path=None):
    entries = [e for e in load_library(path)
               if not (e.name == name and e.kind == kind)]
    save_library(entries, path)
    # drop the removed entry from every collection too
    cols = load_collections(path)
    for c in cols:
        c.items = [it for it in c.items
                   if not (it[0] == name and it[1] == kind)]
    save_collections(cols, path)
    return entries


def set_entry_meta(name, kind, user_notes=None, credits=None, path=None):
    """Update the private user-notes and/or Credits box of a stored entry."""
    entries = load_library(path)
    for e in entries:
        if e.name == name and e.kind == kind:
            if user_notes is not None:
                e.user_notes = user_notes
            if credits is not None:
                e.credits = credits
    save_library(entries, path)
    return entries


def rename_entry(name, kind, new_name, path=None):
    """Rename a stored entry, keeping collection references in sync. Returns the
    updated entry list, or None if the new name clashes with another same-kind
    entry."""
    new_name = (new_name or "").strip()
    if not new_name or new_name == name:
        return load_library(path)
    entries = load_library(path)
    if any(e.name == new_name and e.kind == kind for e in entries):
        return None
    for e in entries:
        if e.name == name and e.kind == kind:
            e.name = new_name
    save_library(entries, path)
    cols = load_collections(path)
    for c in cols:
        for it in c.items:
            if it[0] == name and it[1] == kind:
                it[0] = new_name
    save_collections(cols, path)
    return entries


def set_bookmark(name, kind, on, path=None):
    entries = load_library(path)
    for e in entries:
        if e.name == name and e.kind == kind:
            e.bookmarked = bool(on)
    save_library(entries, path)
    return entries


def reorder_entries(ordered_keys, path=None):
    """Reorder the library to match `ordered_keys` ([(name, kind)]); any entries
    not named keep their relative order after the listed ones."""
    entries = load_library(path)
    index = {(e.name, e.kind): e for e in entries}
    out, seen = [], set()
    for k in ordered_keys:
        k = tuple(k)
        if k in index and k not in seen:
            out.append(index[k])
            seen.add(k)
    for e in entries:
        if (e.name, e.kind) not in seen:
            out.append(e)
    save_library(out, path)
    return out


# ── collections ─────────────────────────────────────────────────────────────
def load_collections(path=None):
    return [AiCollection(name=c.get("name", ""),
                         items=[list(it) for it in c.get("items", [])])
            for c in _load_doc(path).get("collections", [])]


def save_collections(collections, path=None):
    doc = _load_doc(path)
    doc["collections"] = [{"name": c.name, "items": [list(it) for it in c.items]}
                          for c in collections]
    _save_doc(doc, path)


def create_collection(name, path=None):
    name = (name or "").strip()
    cols = load_collections(path)
    if name and not any(c.name == name for c in cols):
        cols.append(AiCollection(name=name))
        save_collections(cols, path)
    return cols


def delete_collection(name, path=None):
    cols = [c for c in load_collections(path) if c.name != name]
    save_collections(cols, path)
    return cols


def rename_collection(old, new, path=None):
    new = (new or "").strip()
    cols = load_collections(path)
    if not new or any(c.name == new for c in cols):
        return cols
    for c in cols:
        if c.name == old:
            c.name = new
    save_collections(cols, path)
    return cols


def add_to_collection(name, item, path=None):
    """Add (entry_name, kind) to a collection (creating it if needed)."""
    item = list(item)
    cols = load_collections(path)
    col = next((c for c in cols if c.name == name), None)
    if col is None:
        col = AiCollection(name=name)
        cols.append(col)
    if item not in col.items:
        col.items.append(item)
    save_collections(cols, path)
    return cols


def remove_from_collection(name, item, path=None):
    item = list(item)
    cols = load_collections(path)
    for c in cols:
        if c.name == name:
            c.items = [it for it in c.items if it != item]
    save_collections(cols, path)
    return cols


# ── signatures / similarity (for dedup + near-dup warnings) ─────────────────
def _sig(keys):
    """Order-independent content signature of a block, ignoring its Name key."""
    return frozenset((k.lower(), _clean(v)) for k, v in keys
                     if k.lower() != "name")


def _similarity(a, b):
    """Jaccard overlap of two signatures (1.0 == identical content)."""
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 1.0


# ── extraction (aimd -> library) ────────────────────────────────────────────
def _block(src_map, kind, bid):
    return LibBlock(id=bid, kind=kind, keys=[list(kv)
                    for kv in _ordered_keys(src_map, bid)])


def _tf_units(src_map, tf):
    units = []
    for _idx, val in _numbered(src_map, tf):
        parts = [p.strip() for p in val.split(",")]
        if len(parts) >= 2 and parts[1]:
            units.append(parts[1])
    return units


def extract_block(src_map, kind, bid, source="", name=None):
    """Save a single TaskForce/Script/TeamType as its own library entry."""
    blk = _block(src_map, kind, bid)
    units = _tf_units(src_map, bid) if kind == "taskforce" else []
    return LibEntry(name=name or bid, kind=kind, source=source, blocks=[blk],
                    units=units)


def extract_bundle(src_map, trigger_id, source="", name=None, src_rules=None):
    """Save an AITrigger + its closure (both teams, their TaskForce & Script).
    If `src_rules` (a rules section map) is given, bundle stub TechnoType sections
    for the referenced units so a pull can offer to bring them."""
    trig = src_map.get("aitriggertypes")
    if not trig or trigger_id.lower() not in trig["keys"]:
        raise KeyError(f"trigger {trigger_id} not found")
    _li, kr, vr = trig["keys"][trigger_id.lower()]
    fields = [f.strip() for f in _clean(vr).split(",")]

    team_ids = []
    for pos in (_TRIG_TEAM1, _TRIG_TEAM2):
        if len(fields) > pos:
            t = fields[pos]
            if t and t.lower() not in ("0", "<none>", "none") \
                    and t.lower() in src_map and t not in team_ids:
                team_ids.append(t)

    blocks, seen = [], set()
    notes, units = [], []
    for tm in team_ids:
        blocks.append(_block(src_map, "teamtype", tm))
        seen.add(tm.lower())
        tf = _clean(get_val(src_map, tm, "TaskForce"))
        sc = _clean(get_val(src_map, tm, "Script"))
        if tf and tf.lower() in src_map and tf.lower() not in seen:
            blocks.append(_block(src_map, "taskforce", tf))
            seen.add(tf.lower())
            for u in _tf_units(src_map, tf):
                if u not in units:
                    units.append(u)
        if sc and sc.lower() in src_map and sc.lower() not in seen:
            blocks.append(_block(src_map, "script", sc))
            seen.add(sc.lower())
            for _idx, val in _numbered(src_map, sc):
                if val.split(",", 1)[0].strip() in _INDEX_REF_ACTIONS:
                    notes.append(f"script {sc} uses an index-ref action — review")

    stubs = {}
    if src_rules is not None:
        for u in units:
            if u.lower() in src_rules:
                lst = _unit_list_of(src_rules, u)
                stubs[u] = {"list": lst,
                            "keys": [list(kv) for kv in _ordered_keys(src_rules, u)]}

    return LibEntry(name=name or kr, kind="bundle", source=source, blocks=blocks,
                    trigger_id=kr, trigger_def=_clean(vr), units=units,
                    unit_stubs=stubs, notes=notes)


def _unit_list_of(rules, uid):
    for reg in ("InfantryTypes", "VehicleTypes", "AircraftTypes", "BuildingTypes"):
        if uid.lower() in {u.lower() for u in parse_type_list(rules, reg)}:
            return reg
    return ""


# ── pull planning (library -> aimd) ─────────────────────────────────────────
@dataclass
class PullItem:
    kind: str
    old_id: str
    new_id: str                     # minted, or an existing id when reused
    keys: list = field(default_factory=list)
    reused: bool = False
    note: str = ""


@dataclass
class PullPlan:
    items: list = field(default_factory=list)          # [PullItem]
    trigger_new_id: str = ""
    trigger_def: str = ""
    id_remap: dict = field(default_factory=dict)       # old_lower -> resolved id
    warnings: list = field(default_factory=list)
    missing_units: list = field(default_factory=list)  # referenced units absent in target
    stub_units: list = field(default_factory=list)     # units that WILL be appended


def _target_sigs(tgt_map):
    """{kind: [(id, sig, ordered_keys)]} for the target's existing AI blocks."""
    out = {}
    for kind, reg in _KIND_LIST.items():
        rows = []
        for bid in parse_type_list(tgt_map, reg):
            if bid.lower() in tgt_map:
                ks = _ordered_keys(tgt_map, bid)
                rows.append((bid, _sig(ks), ks))
        out[kind] = rows
    return out


def build_pull_plan(entry, tgt_map, target_countries, unit_remap=None,
                    bring_stubs=None, dedup=True, similarity_threshold=0.95):
    """Plan pushing `entry` into the target aimd `tgt_map`.

    unit_remap: {old_unit_lower: replacement_unit} applied to TaskForce unit fields.
    bring_stubs: iterable of unit ids (from entry.unit_stubs) to append as sections.
    Identical blocks are reused when `dedup`; near-identical (>= threshold) warned.
    """
    unit_remap = {k.lower(): v for k, v in (unit_remap or {}).items()}
    bring = {u for u in (bring_stubs or [])}
    countries = list(target_countries)
    cset = {c.lower() for c in countries}

    existing_ids = _existing_ids(tgt_map)
    mint = _minter(existing_ids)
    tgt_sigs = _target_sigs(tgt_map)
    plan = PullPlan()
    remap = plan.id_remap

    # units that exist in the target already (for missing-unit reporting)
    tgt_units = set()
    # rules-side lists aren't in an aimd map; callers pass a merged map that may
    # include them. Referenced units are checked against target type-lists if any.
    for reg in ("InfantryTypes", "VehicleTypes", "AircraftTypes", "BuildingTypes"):
        tgt_units.update(u.lower() for u in parse_type_list(tgt_map, reg))

    def resolve(idv):
        return remap.get(_clean(idv).lower(), _clean(idv))

    # process in dependency order: taskforce, script, then teamtype
    order = {"taskforce": 0, "script": 1, "teamtype": 2}
    blocks = sorted(entry.blocks, key=lambda b: order.get(b.kind, 9))

    for blk in blocks:
        keys = [list(kv) for kv in blk.keys]
        # rewrite a teamtype's TaskForce/Script to resolved ids; clean House;
        # apply unit remap to a taskforce's numbered unit fields
        if blk.kind == "teamtype":
            for kv in keys:
                kl = kv[0].lower()
                if kl in ("taskforce", "script"):
                    kv[1] = resolve(kv[1])
                elif kl == "house":
                    h = _clean(kv[1])
                    kv[1] = h if (h.lower() in ("<none>", "<all>", "")
                                  or h in countries) else "<none>"
        elif blk.kind == "taskforce" and unit_remap:
            for kv in keys:
                if kv[0].isdigit():
                    parts = [p.strip() for p in kv[1].split(",")]
                    if len(parts) >= 2 and parts[1].lower() in unit_remap:
                        parts[1] = unit_remap[parts[1].lower()]
                        kv[1] = ",".join(parts)

        sig = _sig(keys)
        reused_id = None
        if dedup:
            for (eid, esig, _ek) in tgt_sigs.get(blk.kind, []):
                if esig == sig:
                    reused_id = eid
                    break
        if reused_id:
            remap[blk.id.lower()] = reused_id
            plan.items.append(PullItem(blk.kind, blk.id, reused_id, keys,
                              reused=True,
                              note=f"identical to existing [{reused_id}] — reused"))
            continue

        # near-duplicate warning
        best_id, best = None, 0.0
        for (eid, esig, _ek) in tgt_sigs.get(blk.kind, []):
            s = _similarity(sig, esig)
            if s > best:
                best, best_id = s, eid
        new_id = mint()
        remap[blk.id.lower()] = new_id
        note = ""
        if best >= similarity_threshold and best < 1.0:
            note = (f"{int(round(best * 100))}% similar to existing [{best_id}] "
                    f"— added as new")
            plan.warnings.append(f"{blk.kind} '{blk.id}' {note}")
        # newly-added blocks become candidates for later blocks in THIS entry
        tgt_sigs.setdefault(blk.kind, []).append((new_id, sig, keys))
        plan.items.append(PullItem(blk.kind, blk.id, new_id, keys, note=note))

    # referenced units
    for u in entry.units:
        eff = unit_remap.get(u.lower(), u)
        if eff in bring:
            plan.stub_units.append(eff)
        elif eff.lower() not in tgt_units and u.lower() not in unit_remap:
            if tgt_units:                    # only meaningful if we know the lists
                plan.missing_units.append(u)

    # trigger (bundle/trigger entries)
    if entry.trigger_def:
        f = [x.strip() for x in entry.trigger_def.split(",")]
        if len(f) > _TRIG_TEAM1:
            f[_TRIG_TEAM1] = resolve(f[_TRIG_TEAM1])
        if len(f) > _TRIG_TEAM2:
            t2 = f[_TRIG_TEAM2]
            if t2.lower() in remap:
                f[_TRIG_TEAM2] = remap[t2.lower()]
            elif t2.lower() not in ("0", "<none>", "none", ""):
                f[_TRIG_TEAM2] = "0"
                plan.warnings.append(f"backup team [{t2}] not in entry → cleared")
        if len(f) > _TRIG_OWNER:
            ow = f[_TRIG_OWNER]
            if ow.lower() not in ("<all>", "") and ow.lower() not in cset:
                plan.warnings.append(f"owner '{ow}' unknown in target → set <all>")
                f[_TRIG_OWNER] = "<all>"
        plan.trigger_def = ",".join(f)
        plan.trigger_new_id = mint()

    if plan.missing_units:
        plan.warnings.append(
            "units not in target (teams won't build): "
            + ", ".join(sorted(set(plan.missing_units))))
    return plan


def apply_pull(lines, tgt_map, entry, plan):
    """Write a PullPlan into the target aimd lines (mutates lines/tgt_map).
    Reused blocks are skipped; new blocks are appended + registered; the trigger
    is appended to [AITriggerTypes]; requested unit stubs are appended too."""
    ending = "\r\n" if lines and lines[-1].endswith("\r\n") else "\n"
    summary = {"taskforce": 0, "script": 0, "teamtype": 0, "trigger": 0,
               "reused": 0, "unit_stub": 0}

    for it in plan.items:
        if it.reused:
            summary["reused"] += 1
            continue
        raw = [f"[{it.new_id}]{ending}"] + [f"{k}={v}{ending}" for k, v in it.keys]
        append_raw_section(lines, tgt_map, it.new_id, raw, ending)
        _register_type_list(lines, tgt_map, _KIND_LIST[it.kind], it.new_id, ending)
        summary[it.kind] += 1

    if plan.trigger_def and plan.trigger_new_id:
        if "aitriggertypes" not in tgt_map:
            append_raw_section(lines, tgt_map, "AITriggerTypes",
                               [f"[AITriggerTypes]{ending}"], ending)
        set_key_in_appended_section(lines, tgt_map, "AITriggerTypes",
                                    plan.trigger_new_id, plan.trigger_def)
        summary["trigger"] += 1

    for u in plan.stub_units:
        stub = entry.unit_stubs.get(u)
        if not stub or u.lower() in tgt_map:
            continue
        raw = [f"[{u}]{ending}"] + [f"{k}={v}{ending}" for k, v in stub["keys"]]
        append_raw_section(lines, tgt_map, u, raw, ending)
        if stub.get("list"):
            _register_type_list(lines, tgt_map, stub["list"], u, ending)
        summary["unit_stub"] += 1

    return summary
