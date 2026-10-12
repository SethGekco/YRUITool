"""
AI import: transplant an old mod's existing aimd.ini AI (TaskForces / ScriptTypes
/ TeamTypes / AITriggerTypes) for a set of ported units into a target mod — the AI
the modder already tuned, not generated.

Closure graph: a ported unit is used by TaskForces → TaskForces are used by
TeamTypes (TaskForce=) which also name a Script= → TeamTypes are referenced by
AITriggerTypes (primary + backup team). Each brought entry gets a freshly-minted,
collision-free id, and every id-STRING reference between brought entries is
rewritten. Owner/House values are cleaned against the target's countries.

Honest limitation: some script actions (e.g. "Join Team") reference a TeamType by
its registry INDEX, not its id — those can't be remapped reliably here, so scripts
containing them are flagged for manual review rather than silently broken.

I/O-free: build_ai_import_plan works on parsed section maps; apply_ai_import mutates
the target aimd lines in place.
"""

from dataclasses import dataclass, field

from .ini_model import get_val, numbered_entries as _numbered
from .typelists import parse_type_list
from .sections import append_raw_section, set_key_in_appended_section
from .portmod import _clean, _ordered_keys, clean_owner, _register_type_list

# AITrigger comma fields: 0 Name,1 Team1,2 Owner,3 TechLevel,…,14 Team2,…
_TRIG_TEAM1, _TRIG_OWNER, _TRIG_TEAM2 = 1, 2, 14
# Script action numbers whose parameter is a registry INDEX (not remappable here).
_INDEX_REF_ACTIONS = {"18"}   # 18 = Join Team (teamtype index)


@dataclass
class AIEntry:
    kind: str                 # taskforce | script | teamtype | trigger
    old_id: str
    new_id: str
    keys: list = field(default_factory=list)       # [(k, v)] for sections
    trigger_def: str = ""     # cleaned comma def (triggers only)
    warnings: list = field(default_factory=list)


@dataclass
class AIImportPlan:
    entries: list = field(default_factory=list)
    id_remap: dict = field(default_factory=dict)   # old_id_lower -> new_id
    warnings: list = field(default_factory=list)

    def by_kind(self):
        out = {}
        for e in self.entries:
            out.setdefault(e.kind, []).append(e)
        return out


def _existing_ids(tgt):
    ids = set()
    for reg in ("TaskForces", "ScriptTypes", "TeamTypes"):
        for v in parse_type_list(tgt, reg):
            ids.add(_clean(v).lower())
    trig = tgt.get("aitriggertypes")
    if trig:
        ids.update(k for k in trig["keys"])
    ids.update(tgt.keys())
    return ids


def _minter(existing):
    counter = [0]
    taken = set(existing)

    def mint():
        while True:
            cand = f"{0xF0000000 + counter[0]:08X}-G"
            counter[0] += 1
            if cand.lower() not in taken:
                taken.add(cand.lower())
                return cand
    return mint


def build_ai_import_plan(src, tgt, ported_units, available_units,
                         target_countries):
    """Plan the AI entries to bring for `ported_units`. `available_units` is every
    unit that will exist in the target (ported ∪ target's own) — used to flag
    taskforce units that would dangle. Returns an AIImportPlan."""
    ported = {u.lower() for u in ported_units}
    avail = {u.lower() for u in available_units}
    countries = list(target_countries)

    # 1. taskforces that field a ported unit
    tf_ids, tf_units = [], {}
    for tf in parse_type_list(src, "TaskForces"):
        units = []
        hit = False
        for _idx, val in _numbered(src, tf):
            parts = [p.strip() for p in val.split(",")]
            if len(parts) >= 2:
                units.append(parts[1])
                if parts[1].lower() in ported:
                    hit = True
        if hit:
            tf_ids.append(tf)
            tf_units[tf.lower()] = units
    tf_set = {t.lower() for t in tf_ids}

    # 2. teamtypes using those taskforces (+ their scripts)
    team_ids, script_ids = [], []
    for tm in parse_type_list(src, "TeamTypes"):
        tf = _clean(get_val(src, tm, "TaskForce"))
        if tf and tf.lower() in tf_set:
            team_ids.append(tm)
            sc = _clean(get_val(src, tm, "Script"))
            if sc and sc.lower() not in {s.lower() for s in script_ids}:
                script_ids.append(sc)
    team_set = {t.lower() for t in team_ids}

    # 3. triggers referencing those teamtypes
    trig = src.get("aitriggertypes")
    trig_items = []
    if trig:
        for klow, (_li, kr, vr) in trig["keys"].items():
            fields = [f.strip() for f in _clean(vr).split(",")]
            t1 = fields[_TRIG_TEAM1] if len(fields) > _TRIG_TEAM1 else ""
            # include a trigger only when its PRIMARY team is brought, so Team1
            # always remaps; a non-brought backup (Team2) is neutralized below.
            if t1.lower() in team_set:
                trig_items.append((kr, fields))

    plan = AIImportPlan()
    mint = _minter(_existing_ids(tgt))
    remap = plan.id_remap
    for i in tf_ids + script_ids + team_ids:
        remap[i.lower()] = mint()
    trig_remap = {}
    for tid, _f in trig_items:
        trig_remap[tid.lower()] = mint()

    def remapped(idv):
        return remap.get(_clean(idv).lower(), _clean(idv))

    # taskforce entries
    for tf in tf_ids:
        e = AIEntry("taskforce", tf, remap[tf.lower()], keys=_ordered_keys(src, tf))
        for u in tf_units.get(tf.lower(), []):
            if u.lower() not in ("", "none") and u.lower() not in avail:
                e.warnings.append(f"unit [{u}] not in target — team may not build")
        plan.entries.append(e)

    # script entries (flag index-ref actions)
    for sc in script_ids:
        e = AIEntry("script", sc, remap[sc.lower()], keys=_ordered_keys(src, sc))
        for _idx, val in _numbered(src, sc):
            act = val.split(",", 1)[0].strip()
            if act in _INDEX_REF_ACTIONS:
                e.warnings.append(
                    f"action {act} references a TeamType by index — review by hand")
        plan.entries.append(e)

    # teamtype entries (rewrite TaskForce/Script ids, clean House)
    for tm in team_ids:
        keys = []
        for kr, vr in _ordered_keys(src, tm):
            kl = kr.lower()
            if kl in ("taskforce", "script"):
                keys.append((kr, remapped(vr)))
            elif kl == "house":
                h = _clean(vr)
                if h.lower() in ("<none>", "<all>", "") or h in countries:
                    keys.append((kr, h))
                else:
                    keys.append((kr, "<none>"))
            else:
                keys.append((kr, _clean(vr)))
        e = AIEntry("teamtype", tm, remap[tm.lower()], keys=keys)
        plan.entries.append(e)

    # trigger entries (rewrite Team1/Team2, clean Owner)
    cset = {c.lower() for c in countries}
    for tid, fields in trig_items:
        f = list(fields)
        e = AIEntry("trigger", tid, trig_remap[tid.lower()])
        if len(f) > _TRIG_TEAM1:
            f[_TRIG_TEAM1] = remapped(f[_TRIG_TEAM1])      # primary always brought
        if len(f) > _TRIG_TEAM2:
            t2 = f[_TRIG_TEAM2]
            if t2.lower() in remap:
                f[_TRIG_TEAM2] = remap[t2.lower()]
            elif t2.lower() not in ("0", "<none>", "none", ""):
                f[_TRIG_TEAM2] = "0"                       # backup not imported
                e.warnings.append(f"backup team [{t2}] not imported → cleared")
        if len(f) > _TRIG_OWNER:
            ow = f[_TRIG_OWNER]
            if ow.lower() not in ("<all>", "") and ow.lower() not in cset:
                e.warnings.append(f"owner '{ow}' unknown in target → set <all>")
                f[_TRIG_OWNER] = "<all>"
        e.trigger_def = ",".join(f)
        plan.entries.append(e)

    if not plan.entries:
        plan.warnings.append("No AI entries reference the ported units.")
    return plan


_LIST_FOR = {"taskforce": "TaskForces", "script": "ScriptTypes",
             "teamtype": "TeamTypes"}


def apply_ai_import(lines, section_map, plan):
    """Write the plan's AI entries into the target aimd (mutates lines/section_map).
    Sections are appended with their minted ids and registered in the index lists;
    triggers are appended to [AITriggerTypes]. Returns a summary dict."""
    ending = "\r\n" if lines and lines[-1].endswith("\r\n") else "\n"
    summary = {"taskforce": 0, "script": 0, "teamtype": 0, "trigger": 0,
               "skipped": 0}
    for e in plan.entries:
        if e.new_id.lower() in section_map or (
                e.kind == "trigger" and section_map.get("aitriggertypes", {})
                .get("keys", {}).get(e.new_id.lower())):
            summary["skipped"] += 1
            continue
        if e.kind == "trigger":
            if "aitriggertypes" not in section_map:
                append_raw_section(lines, section_map, "AITriggerTypes",
                                   [f"[AITriggerTypes]{ending}"], ending)
            set_key_in_appended_section(lines, section_map, "AITriggerTypes",
                                        e.new_id, e.trigger_def)
            summary["trigger"] += 1
        else:
            raw = [f"[{e.new_id}]{ending}"] + [f"{k}={v}{ending}" for k, v in e.keys]
            append_raw_section(lines, section_map, e.new_id, raw, ending)
            _register_type_list(lines, section_map, _LIST_FOR[e.kind], e.new_id,
                                ending)
            summary[e.kind] += 1
    return summary
