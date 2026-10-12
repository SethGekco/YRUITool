"""
Mod-content porter: extract new content from a source mod and prepare it to drop
into a target mod, rescaling balance to the target's curve.

This module is the I/O-free analysis core. It does NOT edit files; it produces a
`PortPlan` (what to move, the proposed balanced stats with a confidence per stat,
and the Owner/Prerequisite cleanup) that a UI reviews and an applier/blueprint
writes out. Art/sound (.ini in other files) plug in as extra section maps later;
for now it handles the rules-side object graph.

Design intent (per the feature spec):
  * "new" = a section present in the source mod but not the target.
  * balance = for every object the two mods SHARE, ratio = target/source; take the
    modal cluster, drop extreme outliers, and expose a confidence. If a vanilla
    reference is supplied, cross-check against each mod's drift from vanilla.
  * be loud when unsure — low-confidence factors are flagged so the user can type
    the ratio by hand.
  * strip Owner countries / Prerequisite tokens the target can't honor; deduce
    conservatively and flag. RequiredHouses is left for the user.
"""

from dataclasses import dataclass, field
import statistics

from .ini_model import get_val, clean_value as _clean, num_value as _num
from .config import ModConfig
from .typelists import parse_type_list
from .weapons import ALL_WEAPON_TAGS
from .sections import append_raw_section, set_key_in_appended_section
from .obtain import classify_obtainability, OB_DUMMY

# Stats the tool actively balances, and which section each applies to.
TECHNO_STATS = ("Cost", "Strength", "Speed", "Sight")
WEAPON_STATS = ("Damage", "ROF", "Range")
BALANCE_STATS = TECHNO_STATS + WEAPON_STATS

# Weapon sub-reference tags that pull warheads/projectiles/anims into the closure.
_WEAPON_REF_TAGS = ("Warhead", "Projectile", "AmbientDamage")
# Tags that reference assets living in OTHER files (reported, not moved here).
_ART_REF_TAGS = ("Image", "Cameo", "AltCameo", "Sequence")
_SOUND_REF_TAGS = ("Report", "VoiceSelect", "VoiceMove", "VoiceAttack", "DieSound",
                   "EnterWater", "LeaveWater", "CrushSound")

_DEFAULT_GENERIC_PREREQS = {
    "POWER", "FACTORY", "BARRACKS", "RADAR", "TECH", "PROC",
}


# ── small helpers ───────────────────────────────────────────────────────────
def _stat(section_map, name, stat):
    return _num(get_val(section_map, name, stat))


def _ordered_keys(section_map, name):
    """[(key_raw, val_raw)] for a section, in original line order."""
    s = section_map.get(name.lower())
    if not s:
        return []
    items = sorted(s["keys"].items(), key=lambda kv: kv[1][0])
    return [(kr, vr) for _klow, (_li, kr, vr) in items]


def _exists(section_map, name):
    return name.lower() in section_map


# ── data model ──────────────────────────────────────────────────────────────
@dataclass
class StatFactor:
    stat: str
    factor: float
    confidence: str          # "high" | "medium" | "low" | "none"
    samples: int
    spread: float            # relative spread of the kept cluster (0 = identical)
    note: str = ""


@dataclass
class StatChange:
    stat: str
    old: float
    new: float
    factor: float
    confidence: str


@dataclass
class PortObject:
    name: str
    kind: str
    keys: list = field(default_factory=list)       # [(key_raw, val_raw)] from source
    stat_changes: list = field(default_factory=list)  # [StatChange]
    owner_before: str = ""
    owner_after: str = ""
    owner_dropped: list = field(default_factory=list)
    prereq_before: str = ""
    prereq_after: str = ""
    prereq_dropped: list = field(default_factory=list)
    asset_refs: list = field(default_factory=list)   # [(tag, value, where)]
    art_entry: tuple = None                          # (art_name, [(k, v)]) or None
    sound_entries: list = field(default_factory=list)  # [(name, [(k, v)])]
    media_files: list = field(default_factory=list)  # best-effort filenames to copy
    obtainability: object = None                     # Obtainability or None
    deps: list = field(default_factory=list)         # built-in part units (InitialPayload.Types) ported with it
    warnings: list = field(default_factory=list)


@dataclass
class PortPlan:
    objects: list = field(default_factory=list)          # [PortObject]
    factors: dict = field(default_factory=dict)          # stat -> StatFactor
    target_countries: list = field(default_factory=list)
    global_warnings: list = field(default_factory=list)

    def by_kind(self):
        out = {}
        for o in self.objects:
            out.setdefault(o.kind, []).append(o)
        return out

    def kinds(self):
        return sorted({o.kind for o in self.objects})


# ── classification ──────────────────────────────────────────────────────────
def _is_dummy_building(section_map, name):
    """Heuristic: a placeholder/logic building, not a real buildable structure.
    Deliberately high-precision so real civilian/defensive buildings (which can
    also be TechLevel -1) aren't swept in: a true dummy is named *DUMMY/*PRE, or
    is both un-buildable (TechLevel<0) AND free (Cost 0/absent). Reclassifiable
    in review."""
    up = name.upper()
    if "DUMMY" in up or up.endswith("PRE"):
        return True
    tl = _stat(section_map, name, "TechLevel")
    cost = _stat(section_map, name, "Cost")
    return tl is not None and tl < 0 and (cost is None or cost == 0)


def classify_new_objects(src_map, tgt_map, config=None, obtain=None):
    """Return {kind: [names]} for sections in src not in tgt, by object kind.

    TechnoTypes come from the source's type-list registries; weapons/warheads/
    projectiles are discovered by reference closure over the new technos.
    Buildings are split real vs dummy using the obtainability classifier when
    `obtain` is provided, else a quick heuristic."""
    config = config or ModConfig.default()
    kinds = {}

    def add(kind, name):
        if not _exists(tgt_map, name):
            kinds.setdefault(kind, [])
            if name not in kinds[kind]:
                kinds[kind].append(name)

    def is_dummy(name):
        if obtain is not None:
            o = obtain.get(name.lower())
            return o is not None and o.cls == OB_DUMMY
        return _is_dummy_building(src_map, name)

    reg = {
        "infantry": "InfantryTypes",
        "vehicle": "VehicleTypes",
        "aircraft": "AircraftTypes",
    }
    for kind, section in reg.items():
        for name in parse_type_list(src_map, section):
            add(kind, name)
    for name in parse_type_list(src_map, "BuildingTypes"):
        if not _exists(tgt_map, name):
            add("building_dummy" if is_dummy(name) else "building", name)

    # Reference closure: weapons → warheads/projectiles, over the new technos.
    seeds = [n for names in kinds.values() for n in names]
    weapons, warheads, projectiles = _closure(src_map, seeds)
    for w in weapons:
        add("weapon", w)
    for wh in warheads:
        add("warhead", wh)
    for p in projectiles:
        add("projectile", p)
    return kinds


def _closure(src_map, techno_names):
    """Follow weapon references from technos → weapons → warheads/projectiles.
    Only returns names that are source sections (so they can be moved)."""
    weapons, warheads, projectiles = [], [], []

    def ref(name, tag):
        return _clean(get_val(src_map, name, tag))

    for t in techno_names:
        for wt in ALL_WEAPON_TAGS:
            w = ref(t, wt)
            if w and w.lower() not in ("none", "") and _exists(src_map, w) \
                    and w not in weapons:
                weapons.append(w)
    for w in weapons:
        wh = ref(w, "Warhead")
        if wh and _exists(src_map, wh) and wh not in warheads:
            warheads.append(wh)
        pr = ref(w, "Projectile")
        if pr and _exists(src_map, pr) and pr not in projectiles:
            projectiles.append(pr)
    return weapons, warheads, projectiles


# ── balance factors ─────────────────────────────────────────────────────────
def _robust_factor(ratios):
    """From per-object target/source ratios, return (factor, spread, kept_count).
    Drops extreme outliers (outside [0.5·median, 2·median]) and reports the
    relative spread of what remains."""
    ratios = [r for r in ratios if r and r > 0]
    if not ratios:
        return 1.0, 0.0, 0
    med = statistics.median(ratios)
    kept = [r for r in ratios if 0.5 * med <= r <= 2.0 * med] or ratios
    factor = statistics.median(kept)
    if len(kept) >= 2 and factor:
        try:
            q = statistics.quantiles(kept, n=4)
            spread = (q[2] - q[0]) / factor
        except statistics.StatisticsError:
            spread = (max(kept) - min(kept)) / factor
    else:
        spread = 0.0
    return factor, spread, len(kept)


def _confidence(samples, spread):
    if samples < 2:
        return "none"
    if samples >= 8 and spread < 0.25:
        return "high"
    if samples >= 4 and spread < 0.5:
        return "medium"
    return "low"


# Which registry defines each factor-kind (shared-object classification).
_FACTOR_KIND_REGISTRY = (
    ("infantry", "InfantryTypes"),
    ("vehicle", "VehicleTypes"),      # vehicles + naval share VehicleTypes
    ("aircraft", "AircraftTypes"),
    ("building", "BuildingTypes"),
)
# Object kind → the factor-kind whose shared pool rescales it.
_OBJ_TO_FACTOR_KIND = {
    "infantry": "infantry", "vehicle": "vehicle", "naval": "vehicle",
    "aircraft": "aircraft", "building": "building", "building_dummy": "building",
    "weapon": "weapon",
}


def _one_factor(stat, names, src_map, tgt_map, vanilla_map):
    ratios = []
    for name in names:
        s = _stat(src_map, name, stat)
        t = _stat(tgt_map, name, stat)
        if s and t and s > 0:
            ratios.append(t / s)
    factor, spread, kept = _robust_factor(ratios)
    conf = _confidence(kept, spread)
    note = f"{kept} shared object(s)"
    if vanilla_map is not None:
        d_ratios = []
        for name in names:
            van = _stat(vanilla_map, name, stat)
            s = _stat(src_map, name, stat)
            t = _stat(tgt_map, name, stat)
            if van and s and t and van > 0 and s > 0:
                d_ratios.append((t / van) / (s / van))
        dfac, _dspread, dkept = _robust_factor(d_ratios)
        if dkept >= 2 and factor:
            if abs(dfac - factor) / factor < 0.2:
                if conf in ("low", "medium"):
                    conf = {"low": "medium", "medium": "high"}[conf]
                    note += "; agrees with vanilla drift"
            else:
                note += (f"; DISAGREES with vanilla drift "
                         f"({dfac:.2f} vs {factor:.2f}) — verify")
                if conf == "high":
                    conf = "medium"
    return StatFactor(stat, round(factor, 4), conf, kept, round(spread, 3), note)


def compute_factors(src_map, tgt_map, vanilla_map=None, config=None):
    """Per-kind rescale factors: {factor_kind: {stat: StatFactor}}.

    Factors are computed WITHIN each object kind, because mods routinely rebalance
    categories differently (e.g. infantry ×0.25 while vehicles ×4) — a single
    global factor would mis-scale imports across categories. Shared objects are
    classified by the target's type-list registries; weapons by having a numeric
    Damage. Vanilla drift is cross-checked per kind when available."""
    config = config or ModConfig.default()
    shared = set(src_map) & set(tgt_map)

    reg_sets = {kind: {n.lower() for n in parse_type_list(tgt_map, sec)}
                for kind, sec in _FACTOR_KIND_REGISTRY}
    buckets = {k: [] for k in list(reg_sets) + ["weapon"]}
    for low in shared:
        name = tgt_map[low]["name_raw"]
        placed = False
        for kind in reg_sets:
            if low in reg_sets[kind]:
                buckets[kind].append(name)
                placed = True
                break
        if not placed and _stat(tgt_map, name, "Damage") is not None:
            buckets["weapon"].append(name)

    out = {}
    for kind, names in buckets.items():
        stats = WEAPON_STATS if kind == "weapon" else TECHNO_STATS
        out[kind] = {stat: _one_factor(stat, names, src_map, tgt_map, vanilla_map)
                     for stat in stats}
    return out


# ── owner / prerequisite cleanup ────────────────────────────────────────────
def target_countries(tgt_map):
    """Country names declared in the target's [Countries] list (upper-cased set
    returned alongside the display list)."""
    names = parse_type_list(tgt_map, "Countries")
    # [Countries] entries occasionally carry inline comments.
    return [_clean(n) for n in names if _clean(n)]


def _generic_prereqs(tgt_map):
    s = tgt_map.get("genericprerequisites")
    if not s:
        return set(_DEFAULT_GENERIC_PREREQS)
    return {kr.upper() for _klow, (_li, kr, _vr) in s["keys"].items()}


def clean_owner(owner_raw, countries):
    cset = {c.lower() for c in countries}
    kept, dropped = [], []
    for tok in [t.strip() for t in _clean(owner_raw).split(",") if t.strip()]:
        (kept if tok.lower() in cset else dropped).append(tok)
    return kept, dropped


def clean_prereq(prereq_raw, tgt_map, moved_lower, generics):
    kept, dropped = [], []
    for tok in [t.strip() for t in _clean(prereq_raw).split(",") if t.strip()]:
        up = tok.upper().lstrip("-")   # leading '-' is a "not" marker in some mods
        if up in generics or tok.lower() in moved_lower or _exists(tgt_map, tok):
            kept.append(tok)
        else:
            dropped.append(tok)
    return kept, dropped


# ── art / sound resolution (multi-file) ─────────────────────────────────────
def _resolve_art(obj_name, src_map, src_art_map):
    """Find the object's art entry in the source artmd and the media files it
    pulls in (voxel/shp image + cameo/altcameo SHPs). Best-effort."""
    image = _clean(get_val(src_map, obj_name, "Image")) or obj_name
    if not _exists(src_art_map, image):
        return None, []
    keys = _ordered_keys(src_art_map, image)
    files = []
    cameo = _clean(get_val(src_art_map, image, "Cameo"))
    if cameo and cameo.lower() != "none":
        files.append(cameo.upper() + ".SHP")
    alt = _clean(get_val(src_art_map, image, "AltCameo"))
    if alt and alt.lower() != "none":
        files.append(alt.upper() + ".SHP")
    if _clean(get_val(src_art_map, image, "Voxel")).lower() == "yes":
        files += [image.upper() + ".VXL", image.upper() + ".HVA"]
    else:
        files.append(image.upper() + ".SHP")
    return (image, keys), files


def _resolve_sound(obj_name, src_map, src_sound_map):
    """Find sound entries the object references (Report/voices) in the source
    soundmd, plus the .wav tokens they list. Best-effort."""
    entries, files, seen = [], [], set()
    for tag in _SOUND_REF_TAGS:
        name = _clean(get_val(src_map, obj_name, tag))
        if not name or name.lower() in ("none", "") or name.lower() in seen:
            continue
        if _exists(src_sound_map, name):
            seen.add(name.lower())
            keys = _ordered_keys(src_sound_map, name)
            entries.append((name, keys))
            for k, v in keys:
                if k.lower() == "sounds":
                    # soundmd Sounds= is space-separated (sometimes comma); tokens
                    # may be bag sample ids (leading '$'). Best-effort filenames.
                    for t in _clean(v).replace(",", " ").split():
                        files.append(t.lstrip("$").upper() + ".WAV")
    return entries, files


# ── plan assembly ───────────────────────────────────────────────────────────
def build_port_plan(src_map, tgt_map, vanilla_map=None, config=None,
                    include_kinds=None, src_art_map=None, src_sound_map=None,
                    factors=None):
    """Produce a PortPlan. include_kinds limits which object kinds to port
    (default: all discovered). src_art_map/src_sound_map enable art/sound entry
    resolution (the port then carries those entries, not just flags them).
    `factors` ({factor_kind: {stat: StatFactor}}) overrides the computed
    shared-unit factors — e.g. balance-culture conversion factors."""
    config = config or ModConfig.default()
    plan = PortPlan()
    plan.target_countries = target_countries(tgt_map)
    cset = plan.target_countries
    generics = _generic_prereqs(tgt_map)
    plan.factors = (factors if factors is not None
                    else compute_factors(src_map, tgt_map, vanilla_map))
    obtain = classify_obtainability(src_map, config, art_map=src_art_map)

    kinds = classify_new_objects(src_map, tgt_map, config, obtain=obtain)
    moved_lower = {n.lower() for names in kinds.values() for n in names}

    for kind, names in kinds.items():
        if include_kinds is not None and kind not in include_kinds:
            continue
        stats = (WEAPON_STATS if kind == "weapon"
                 else TECHNO_STATS if kind in
                 ("infantry", "vehicle", "aircraft", "building", "building_dummy")
                 else ())
        fkind = _OBJ_TO_FACTOR_KIND.get(kind)
        kind_factors = plan.factors.get(fkind, {})
        for name in names:
            obj = PortObject(name=name, kind=kind,
                             keys=_ordered_keys(src_map, name),
                             obtainability=obtain.get(name.lower()))
            # rescale balance stats using THIS kind's factor
            for stat in stats:
                old = _stat(src_map, name, stat)
                if old is None:
                    continue
                sf = kind_factors.get(stat)
                if sf is None or sf.confidence == "none":
                    obj.warnings.append(
                        f"no reliable {kind} {stat} factor — left unchanged, "
                        "set by hand")
                    continue
                new = round(old * sf.factor, 2)
                obj.stat_changes.append(
                    StatChange(stat, old, new, sf.factor, sf.confidence))
                if sf.confidence == "low":
                    obj.warnings.append(
                        f"{stat} factor low-confidence ({sf.factor:.2f}) — review")
            # owner / prereq cleanup (technos & buildings)
            if kind in ("infantry", "vehicle", "aircraft", "building",
                        "building_dummy"):
                ow = get_val(src_map, name, "Owner")
                if ow is not None:
                    obj.owner_before = _clean(ow)
                    kept, dropped = clean_owner(ow, cset)
                    obj.owner_after = ",".join(kept)
                    obj.owner_dropped = dropped
                    if dropped and not kept:
                        obj.warnings.append(
                            f"Owner emptied (dropped {', '.join(dropped)}) — set "
                            "the intended countries manually")
                    elif dropped:
                        obj.warnings.append(
                            f"dropped unknown Owner: {', '.join(dropped)}")
                pr = get_val(src_map, name, "Prerequisite")
                if pr is not None:
                    obj.prereq_before = _clean(pr)
                    kept, dropped = clean_prereq(pr, tgt_map, moved_lower, generics)
                    obj.prereq_after = ",".join(kept)
                    obj.prereq_dropped = dropped
                    if dropped:
                        obj.warnings.append(
                            f"dropped unknown Prerequisite: {', '.join(dropped)}")
                # Ares InitialPayload: built-in (often open-topped) passengers are
                # part of the unit — port them with it. Skip Prerequisite dummies
                # (handled separately); these are real built-in units.
                payload = _clean(get_val(src_map, name, "InitialPayload.Types"))
                if payload:
                    deps = [t.strip() for t in payload.split(",") if t.strip()]
                    obj.deps = deps
                    if deps:
                        miss = [d for d in deps if not _exists(src_map, d)]
                        note = ("built-in units ported with it "
                                "(InitialPayload): " + ", ".join(deps))
                        if miss:
                            note += f"  [missing in source: {', '.join(miss)}]"
                        obj.warnings.append(note)
            # asset references living in other files (report only)
            for tag in _ART_REF_TAGS:
                v = _clean(get_val(src_map, name, tag))
                if v and v.lower() not in ("none", ""):
                    obj.asset_refs.append((tag, v, "artmd.ini / assets"))
            for tag in _SOUND_REF_TAGS:
                v = _clean(get_val(src_map, name, tag))
                if v and v.lower() not in ("none", ""):
                    obj.asset_refs.append((tag, v, "soundmd.ini / assets"))
            # multi-file: resolve the actual art/sound entries + media files
            is_techno = kind in ("infantry", "vehicle", "aircraft", "building",
                                 "building_dummy")
            if is_techno and src_art_map is not None:
                obj.art_entry, art_files = _resolve_art(name, src_map, src_art_map)
                obj.media_files += art_files
                if obj.art_entry is None:
                    obj.warnings.append("no art entry found in source artmd")
            if is_techno and src_sound_map is not None:
                obj.sound_entries, snd_files = _resolve_sound(
                    name, src_map, src_sound_map)
                obj.media_files += snd_files
            plan.objects.append(obj)

    if not plan.objects:
        plan.global_warnings.append("No new content found to port.")
    return plan


# ── blueprint / preview text ────────────────────────────────────────────────
def _rebuilt_section(obj):
    """The [Name] + key=value lines for an object, with balanced stats applied."""
    newvals = {c.stat.lower(): c.new for c in obj.stat_changes}
    out = [f"[{obj.name}]"]
    for kr, vr in obj.keys:
        kl = kr.lower()
        if kl in newvals:
            out.append(f"{kr}={_fmt(newvals[kl])}")
        elif kl == "owner" and obj.owner_before:
            out.append(f"{kr}={obj.owner_after}")
        elif kl == "prerequisite" and obj.prereq_before:
            out.append(f"{kr}={obj.prereq_after}")
        else:
            out.append(f"{kr}={_clean(vr)}")
    return out


def _fmt(n):
    return str(int(n)) if float(n).is_integer() else str(n)


_LIST_FOR_KIND = {
    "infantry": "InfantryTypes", "vehicle": "VehicleTypes",
    "naval": "VehicleTypes", "aircraft": "AircraftTypes",
    "building": "BuildingTypes", "building_dummy": "BuildingTypes",
}


def _ending(lines):
    return "\r\n" if lines and lines[-1].endswith("\r\n") else "\n"


def _expand_selection(plan, selected):
    """Grow a selection to include each selected unit's built-in InitialPayload
    part units (transitively) and the weapon chain of each auto-added part unit —
    a transport's dummy passengers (and a dummy gunner's weapon) are never left
    behind. User-picked units keep their own weapon selection unchanged. Returns a
    new set."""
    selected = set(selected)
    by_name = {o.name.lower(): o for o in plan.objects}

    def _kv(obj, tag):
        for kr, vr in obj.keys:
            if kr.lower() == tag:
                return _clean(vr)
        return ""

    weap_tags = {t.lower() for t in ALL_WEAPON_TAGS}
    frontier = list(selected)
    newly = []
    while frontier:
        o = by_name.get(frontier.pop().lower())
        if not o:
            continue
        for d in getattr(o, "deps", []):
            if d not in selected:
                selected.add(d)
                frontier.append(d)
                newly.append(d)
    for nm in newly:                       # weapon chain of each auto-added dep
        o = by_name.get(nm.lower())
        if not o:
            continue
        for kr, vr in o.keys:
            if kr.lower() not in weap_tags:
                continue
            w = _clean(vr)
            wobj = by_name.get(w.lower()) if w else None
            if wobj and w not in selected:
                selected.add(w)
                for x in (_kv(wobj, "warhead"), _kv(wobj, "projectile")):
                    if x and x.lower() in by_name and x not in selected:
                        selected.add(x)
    return selected


def plan_to_files(plan, selected=None, overrides=None):
    """Raw INI text per associated file for the selected objects (InitialPayload
    deps expanded, overrides applied) — exactly what each file receives on apply.
    Returns an ordered {filename: text} dict, only for files with content, so the
    UI can show rulesmd.ini / artmd.ini / soundmd.ini side by side."""
    sel = _expand_selection(
        plan, selected if selected is not None else [o.name for o in plan.objects])
    objs = [o for o in plan.objects if o.name in sel]
    files = {}

    rules = []
    bk = plan.by_kind()
    for kind in sorted(bk):
        kobjs = [o for o in bk[kind] if o.name in sel]
        if not kobjs:
            continue
        rules.append(f"; --- {kind} ---")
        for o in kobjs:
            rules.append("")
            rules += [ln.rstrip("\n") for ln in _section_raw(o, overrides, "\n")]
    if rules:
        files["rulesmd.ini"] = "\n".join(rules).strip() + "\n"

    art, seen = [], set()
    for o in objs:
        if o.art_entry and o.art_entry[0].lower() not in seen:
            seen.add(o.art_entry[0].lower())
            art.append(f"[{o.art_entry[0]}]")
            art += [f"{k}={v}" for k, v in o.art_entry[1]]
            art.append("")
    if art:
        files["artmd.ini"] = "\n".join(art).strip() + "\n"

    snd, seen = [], set()
    for o in objs:
        for name, kvs in o.sound_entries:
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            snd.append(f"[{name}]")
            snd += [f"{k}={v}" for k, v in kvs]
            snd.append("")
    if snd:
        files["soundmd.ini"] = "\n".join(snd).strip() + "\n"

    return files


def _section_raw(obj, overrides, ending):
    """[Name] + key=value lines with balanced (or overridden) stats and cleaned
    Owner/Prerequisite applied."""
    fkind = _OBJ_TO_FACTOR_KIND.get(obj.kind)
    newvals = {}
    for c in obj.stat_changes:
        ov = overrides.get((fkind, c.stat)) if overrides else None
        newvals[c.stat.lower()] = round(c.old * ov, 2) if ov else c.new
    raw = [f"[{obj.name}]{ending}"]
    for kr, vr in obj.keys:
        kl = kr.lower()
        if kl in newvals:
            raw.append(f"{kr}={_fmt(newvals[kl])}{ending}")
        elif kl == "owner" and obj.owner_before:
            raw.append(f"{kr}={obj.owner_after}{ending}")
        elif kl == "prerequisite" and obj.prereq_before:
            raw.append(f"{kr}={obj.prereq_after}{ending}")
        else:
            raw.append(f"{kr}={_clean(vr)}{ending}")
    return raw


def _register_type_list(lines, section_map, list_section, value, ending):
    """Append `<nextIndex>=value` to a numeric index list, creating it if absent."""
    s = section_map.get(list_section.lower())
    if s is None:
        append_raw_section(lines, section_map, list_section,
                           [f"[{list_section}]{ending}"], ending)
        s = section_map[list_section.lower()]
    idxs = [int(k) for k in s["keys"] if k.isdigit()]
    nxt = (max(idxs) + 1) if idxs else 0
    set_key_in_appended_section(lines, section_map, list_section, str(nxt), value)


def _append_entry(lines, section_map, name, keys, ending):
    """Append a plain [name] section from [(k, v)] pairs if it isn't already there."""
    if _exists(section_map, name):
        return False
    raw = [f"[{name}]{ending}"] + [f"{k}={_clean(v)}{ending}" for k, v in keys]
    append_raw_section(lines, section_map, name, raw, ending)
    return True


def apply_port_plan(lines, section_map, plan, selected=None, overrides=None,
                    art=None, sound=None):
    """Write selected ported objects into the target rules (mutates lines/
    section_map in place). Appends each object's section (balanced/overridden +
    cleaned Owner/Prereq), registers technos in their type-list, and — when
    `art`/`sound` (each an (lines, section_map) tuple for the target files) are
    given — appends the resolved art/sound entries. Returns a summary dict.

    `selected`: object names to port (default all). `overrides`: {(factor_kind,
    stat): factor}. Existing sections in the target are skipped (never clobbered)."""
    selected = _expand_selection(
        plan, selected if selected is not None else [o.name for o in plan.objects])
    ending = _ending(lines)
    summary = {"sections": 0, "registered": 0, "art": 0, "sound": 0,
               "skipped_existing": 0, "media": []}
    for obj in plan.objects:
        if obj.name not in selected:
            continue
        if _exists(section_map, obj.name):
            summary["skipped_existing"] += 1
            continue
        append_raw_section(lines, section_map, obj.name,
                           _section_raw(obj, overrides, ending), ending)
        summary["sections"] += 1
        ls = _LIST_FOR_KIND.get(obj.kind)
        if ls:
            _register_type_list(lines, section_map, ls, obj.name, ending)
            summary["registered"] += 1
        if art is not None and obj.art_entry:
            al, am = art
            if _append_entry(al, am, obj.art_entry[0], obj.art_entry[1], _ending(al)):
                summary["art"] += 1
        if sound is not None and obj.sound_entries:
            sl, smap = sound
            for sname, skeys in obj.sound_entries:
                if _append_entry(sl, smap, sname, skeys, _ending(sl)):
                    summary["sound"] += 1
        summary["media"] += obj.media_files
    return summary


def plan_to_blueprint(plan):
    """Render the plan as INI text: new sections (balanced) grouped by kind, plus
    a header noting factors, warnings, and asset references to copy by hand."""
    lines = ["; ===== Ported mod content ====="]
    lines.append("; Balance factors (source→target), per object kind:")
    for fkind in sorted(plan.factors):
        parts = [f"{s}:x{sf.factor}[{sf.confidence}]"
                 for s, sf in plan.factors[fkind].items() if sf.samples]
        if parts:
            lines.append(f";   {fkind}: " + "  ".join(parts))
    lines.append(";")
    for obj in plan.objects:
        if obj.obtainability is not None:
            ob = obj.obtainability
            extra = (" + " + ", ".join(ob.also)) if ob.also else ""
            lines.append(f"; [{obj.name}] obtain: {ob.cls}{extra} "
                         f"({'; '.join(ob.reasons)})")
        if obj.warnings:
            lines.append(f"; [{obj.name}] NOTE: " + " | ".join(obj.warnings))
        if obj.asset_refs:
            refs = ", ".join(f"{t}={v} ({w})" for t, v, w in obj.asset_refs)
            lines.append(f"; [{obj.name}] assets to copy: {refs}")
    for kind in sorted(plan.by_kind()):
        lines.append("")
        lines.append(f"; --- {kind} ---")
        for obj in plan.by_kind()[kind]:
            lines.append("")
            lines.extend(_rebuilt_section(obj))
    return "\n".join(lines)
