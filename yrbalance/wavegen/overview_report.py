"""
overview_report.py
==================
Human-readable overview of the AI unit ecosystem the tool detected in the
mod. Written to overview.txt in the output folder after generation.

Features:
- Per-unit trigger count (item 42)
- Spawner sub-category under low/no-AA aerial section (item 43)
- Two-axis grade display: GLOBAL + ROLE (item 44)
- Direct-scan Spy/Engineer from InfantryTypes (items 45, 46)
"""
from __future__ import annotations
from pathlib import Path


def _grade_breakdown(rec, role_grade: float | None = None) -> str:
    parts = []
    for attr, label in (('cost', 'cost'), ('speed', 'speed'),
                        ('strength', 'HP')):
        v = getattr(rec, attr, None)
        if v is not None:
            parts.append(f"{label}={v}")
    g = getattr(rec, 'grade', None)
    if g is not None:
        if role_grade is not None:
            parts.append(f"GLOBAL={g}")
            parts.append(f"ROLE={role_grade:.1f}")
        else:
            parts.append(f"GRADE={g}")
    roles = getattr(rec, 'roles', None) or []
    if roles:
        parts.append(f"roles=[{', '.join(sorted(roles))}]")
    return "  ".join(parts)


def _line(rec, extra: str = '', trigger_count: int | None = None,
          role_grade: float | None = None) -> str:
    name = getattr(rec, 'name', '?')
    uid  = getattr(rec, 'unit_id', '?')
    fac  = getattr(rec, 'faction', '?')
    head = f"  [{uid}] {name}  ({fac})"
    if trigger_count is not None:
        head += f"  [used in {trigger_count} trigger(s)]"
    if extra:
        head += f"  — {extra}"
    return head + f"\n      {_grade_breakdown(rec, role_grade=role_grade)}"


def _has_role(rec, *roles) -> bool:
    r = getattr(rec, 'roles', None) or set()
    return any(x in r for x in roles)


def _section(title: str, count: int) -> list[str]:
    return [f"{title}  ({count})", "-" * 72]


def _role_grade(rec, section_key: str) -> float | None:
    bd = getattr(rec, 'grade_breakdown', None)
    if not bd:
        return None
    weights = {
        'aerial':      ['dps_vehicle', 'dps_building', 'range', 'health'],
        'aa':          ['aa_capability_bonus', 'range', 'health'],
        'meatgrinder': ['dps_infantry', 'surv_per_cost', 'cost_spam'],
        'artillery':   ['dps_building', 'outranges_def', 'range'],
        'spy':         ['surv_per_cost', 'speed'],
        'engineer':    ['surv_per_cost', 'speed'],
        'spawner':     ['dps_vehicle', 'dps_building', 'range'],
        'gunner':      ['dps_infantry', 'dps_vehicle', 'health'],
        'transport':   ['health', 'speed', 'dps_infantry'],
    }.get(section_key, [])
    if not weights:
        return None
    return sum(bd.get(k, 0) or 0 for k in weights)


def collect_trigger_counts(wave_gen) -> dict[str, int]:
    """Count how many AITriggerTypes reference each unit."""
    counts: dict[str, int] = {}
    if wave_gen is None:
        return counts
    teams = {t.team_id: t for t in getattr(wave_gen, 'teams', [])}
    tfs   = {t.tf_id:   t for t in getattr(wave_gen, 'taskforces', [])}
    for trig in getattr(wave_gen, 'triggers', []):
        team = teams.get(getattr(trig, 'team1_id', None))
        if team is None:
            continue
        tf = tfs.get(getattr(team, 'taskforce_id', None))
        if tf is None:
            continue
        seen: set[str] = set()
        for _, uid in (tf.units or []):
            if uid and uid not in seen:
                seen.add(uid)
                counts[uid] = counts.get(uid, 0) + 1
    return counts


def _stub_for(uid: str, ini, role_tag: str):
    """Minimal record for a unit not in parser.buildable."""
    sec = ini.section(uid)
    if not sec:
        return None
    try:
        tl = int(sec.get('techlevel', '-1') or '-1')
    except ValueError:
        tl = -1
    if tl < 1 or tl >= 11:
        return None
    class _Stub: pass
    rec = _Stub()
    rec.unit_id  = uid
    rec.name     = sec.get('name', uid)
    rec.faction  = sec.get('owner', '?').split(',')[0].strip()
    for attr, default in (('cost', 0), ('speed', 0), ('strength', 0)):
        try:
            setattr(rec, attr, int(sec.get(attr, str(default)) or default))
        except ValueError:
            setattr(rec, attr, default)
    rec.grade = None
    rec.roles = [role_tag]
    return rec


def _direct_scan_infantry(parser, ini_flag: str, role_tag: str) -> list:
    """Scan [InfantryTypes] for units with ini_flag=yes; returns UnitRecord
    if available or a stub otherwise."""
    ini = parser.ini
    hits: list = []
    seen: set[str] = set()
    for uid in ini.list_from_section('InfantryTypes'):
        if not uid or uid.startswith(';') or uid in seen:
            continue
        seen.add(uid)
        sec = ini.section(uid)
        if not sec:
            continue
        if sec.get(ini_flag, 'no').strip().lower() != 'yes':
            continue
        rec = next((r for r in parser.buildable if r.unit_id == uid), None)
        if rec is None:
            rec = _stub_for(uid, ini, role_tag)
        if rec is not None:
            hits.append(rec)
    hits.sort(key=lambda r: r.unit_id)
    return hits


def _find_spawners(parser, aerial_uids: set[str]) -> list:
    """Ground units whose primary weapon spawns aircraft or missiles."""
    ini = parser.ini
    spawners: list = []
    aircraft_list = set(u for u in ini.list_from_section('AircraftTypes')
                        if u and not u.startswith(';'))
    for rec in parser.buildable:
        if rec.unit_id in aerial_uids:
            continue
        sec = ini.section(rec.unit_id)
        if not sec:
            continue
        primary = (sec.get('primary', '') or '').split(';')[0].strip()
        if not primary:
            continue
        wsec = ini.section(primary)
        if not wsec:
            continue
        spawns = (wsec.get('spawns', '') or '').split(';')[0].strip()
        if not spawns:
            continue
        spawn_sec = ini.section(spawns)
        if not spawn_sec:
            continue
        is_aircraft = spawns in aircraft_list
        is_missile  = (spawn_sec.get('missilespawn', 'no').lower() == 'yes'
                       or spawn_sec.get('type', '').strip().lower() == 'ballistic')
        if is_aircraft or is_missile:
            spawners.append(rec)
    spawners.sort(key=lambda r: -(getattr(r, 'grade', 0) or 0))
    return spawners


def _script_action_report(wave_gen) -> list[str]:
    """Items 54, 55: list every action used across generated scripts, count
    how many scripts use each, and enumerate all scripts by name.

    Returns a list of overview lines. Empty when no wave_gen or no scripts.
    """
    if wave_gen is None:
        return []
    scripts = list(getattr(wave_gen, 'scripts', []))
    if not scripts:
        return []

    # Map action code → short human name so the counts are readable
    ACTION_NAMES: dict[int, str] = {
        0:  "Attack quarry",
        5:  "Guard area timed",
        6:  "Jump",
        8:  "Attack + unload",
        9:  "Deploy",
        11: "Set mission",
        14: "Load transport (vanilla)",
        21: "Scatter",
        43: "Wait until loaded",
        46: "Attack enemy structure (BwP)",
        47: "Move to enemy structure (BwP)",
        49: "Repeat until success",
        53: "Gather at enemy",
        54: "Regroup at friendly base",
        55: "Iron Curtain",
        56: "Chronoshift",
        57: "Chronowarp",
        58: "Move to friendly structure (BwP)",
        10000: "Attack closer (Phobos)",
        10005: "Attack farther (Phobos)",
        10010: "Attack closer+higher threat (Phobos)",
        10100: "Timed area guard (Phobos)",
        10102: "Regroup (Phobos)",
        10103: "Load onto transports (Phobos)",
        12000: "Wait until no target (Phobos)",
    }

    from collections import Counter
    action_usage: Counter = Counter()
    for sc in scripts:
        for step in getattr(sc, 'actions', []) or []:
            if isinstance(step, tuple) and len(step) >= 1:
                action_usage[step[0]] += 1

    L: list[str] = []
    L += _section("SCRIPT ACTION USAGE ACROSS GENERATED SCRIPTS",
                  sum(action_usage.values()))
    L += [f"  {len(scripts)} scripts generated, using the following action codes:",
          ""]
    for action_id, count in sorted(action_usage.items(),
                                    key=lambda kv: -kv[1]):
        name = ACTION_NAMES.get(action_id, f"unknown ({action_id})")
        L.append(f"  {action_id:>5}  {name:35s}  used {count:>4} time(s)")
    L.append("")

    # List of scripts by name with step count
    L += _section("SCRIPTS BY NAME (with step count)", len(scripts))
    for sc in sorted(scripts, key=lambda s: getattr(s, 'name', '')):
        name  = getattr(sc, 'name', '?')
        steps = getattr(sc, 'actions', []) or []
        L.append(f"  {name:45s}  {len(steps)} step(s)")
    L.append("")
    return L


def _team_feature_report(wave_gen) -> list[str]:
    """Item 55: feature-frequency counts across generated TeamTypes and
    TaskForces."""
    if wave_gen is None:
        return []
    teams = list(getattr(wave_gen, 'teams', []))
    tfs   = list(getattr(wave_gen, 'taskforces', []))
    if not teams and not tfs:
        return []

    from collections import Counter
    team_flag_usage: Counter = Counter()
    for t in teams:
        flags = getattr(t, 'flags', None) or {}
        for k, v in flags.items():
            if str(v).strip().lower() in ('yes', 'true'):
                team_flag_usage[k] += 1

    tf_unit_usage: Counter = Counter()
    for tf in tfs:
        for _, uid in (getattr(tf, 'units', None) or []):
            if uid:
                tf_unit_usage[uid] += 1

    L: list[str] = []
    L += _section("TEAMTYPE FLAG USAGE (yes/true flags across all teams)",
                  sum(team_flag_usage.values()))
    L += [f"  {len(teams)} teams generated. Boolean flags set to yes/true:", ""]
    for flag, count in sorted(team_flag_usage.items(),
                               key=lambda kv: -kv[1])[:25]:
        L.append(f"  {flag:35s}  {count:>4} team(s)")
    L.append("")

    L += _section("TASKFORCE UNIT USAGE (unique units by frequency)",
                  len(tf_unit_usage))
    L += [f"  {len(tfs)} taskforces generated. Top 25 most-used unit IDs:", ""]
    for uid, count in tf_unit_usage.most_common(25):
        L.append(f"  {uid:15s}  in {count:>4} taskforce(s)")
    L.append("")
    return L


def build_overview(parser, scan_result=None,
                    gunner_hard_infantry: bool = True,
                    trigger_counts: dict[str, int] | None = None,
                    wave_gen=None) -> str:
    tc = trigger_counts or {}
    L: list[str] = []
    L += ["=" * 72,
          "  YR AI WAVE GENERATOR — MOD OVERVIEW",
          "=" * 72, ""]
    L += ["This document lists the AI-relevant units the tool detected in your",
          "mod and explains where each shows up in the generated waves. Each",
          "unit line shows the trigger count and both GLOBAL grade (unit's",
          "overall combat value) and ROLE grade (fit for the section's job).",
          ""]
    ini = parser.ini

    # Factions
    fc = getattr(parser.sides, 'faction_countries', {}) if getattr(
        parser, 'sides', None) else {}
    if fc:
        L += ["FACTIONS", "-" * 72]
        for fac, countries in sorted(fc.items()):
            L.append(f"  {fac:12s}  countries: {', '.join(sorted(countries))}")
        L.append("")

    # Aerial
    aerial = [r for r in parser.buildable
              if getattr(r, 'mobility', '') in
                  ('Aircraft', 'Helicopter', 'Jumpjet')
              and not getattr(r, 'is_transport', False)]
    aerial.sort(key=lambda r: -(getattr(r, 'grade', 0) or 0))
    L += _section("AERIAL UNITS SPAMMED WHEN ENEMY HAS LOW/NO AA", len(aerial))
    L += ["  Fire when the AIExt condition detects enemy has zero AA (rush),",
          "  few AA (wave), or the AI accepts losses (assault).", ""]
    for r in aerial[:25]:
        L.append(_line(r, trigger_count=tc.get(r.unit_id, 0),
                        role_grade=_role_grade(r, "aerial")))
    if not aerial:
        L.append("  (no buildable aerial units detected)")
    L.append("")

    # Spawner sub-category
    spawners = _find_spawners(parser, {r.unit_id for r in aerial})
    L += [f"  → SPAWNER UNITS (missile/aircraft carriers)  ({len(spawners)})",
          "  " + "-" * 68]
    L += ["    Ground units whose primary weapon spawns aircraft or missiles.",
          "    Fill the same 'enemy has no AA' role as aerial units.", ""]
    for r in spawners[:15]:
        L.append(_line(r, trigger_count=tc.get(r.unit_id, 0),
                        role_grade=_role_grade(r, "spawner")))
    if not spawners:
        L.append("    (no spawner units detected)")
    L.append("")

    # AA units
    aa_units = [r for r in parser.buildable
                if _has_role(r, 'AA', 'AA_Secondary')
                and not getattr(r, 'is_transport', False)]
    aa_units.sort(key=lambda r: -(getattr(r, 'grade', 0) or 0))
    L += _section("AA UNITS SPAMMED WHEN ENEMY HAS MANY AIRCRAFT",
                  len(aa_units))
    for r in aa_units[:25]:
        extra = ('AA_primary'
                 if 'AA' in (getattr(r, 'roles', None) or set())
                 else 'AA_secondary')
        L.append(_line(r, extra, trigger_count=tc.get(r.unit_id, 0),
                        role_grade=_role_grade(r, "aa")))
    if not aa_units:
        L.append("  (no buildable AA units detected)")
    L.append("")

    # AA structures
    if scan_result is not None and getattr(scan_result, 'aa', None):
        aa_bldgs = getattr(scan_result.aa, 'aa_structure_ids', [])
        L += _section("AA STRUCTURES DETECTED IN MOD", len(aa_bldgs))
        for bid in aa_bldgs[:20]:
            sec = ini.section(bid)
            name = sec.get('name', bid) if sec else bid
            L.append(f"  [{bid}] {name}")
        L.append("")

    # Meatgrinders
    grinders = [r for r in parser.buildable
                if getattr(r, 'category', '') == 'Infantry'
                and (getattr(r, 'cost', 999) or 999) <= 200
                and (getattr(r, 'grade', 0) or 0) >= 4]
    grinders.sort(key=lambda r: (getattr(r, 'cost', 999) or 999))
    L += _section("MEATGRINDER INFANTRY (cheap, spammable)", len(grinders))
    for r in grinders[:15]:
        L.append(_line(r, trigger_count=tc.get(r.unit_id, 0),
                        role_grade=_role_grade(r, "meatgrinder")))
    L.append("")

    # Artillery
    arty = [r for r in parser.buildable
            if _has_role(r, 'ARTILLERY', 'AG-Artillery')]
    arty.sort(key=lambda r: -(getattr(r, 'grade', 0) or 0))
    L += _section("ARTILLERY / BOMBARDMENT UNITS", len(arty))
    for r in arty[:15]:
        L.append(_line(r, trigger_count=tc.get(r.unit_id, 0),
                        role_grade=_role_grade(r, "artillery")))
    L.append("")

    # Spies (item 45)
    spies = _direct_scan_infantry(parser, 'agent', 'SPY')
    L += _section("SPY / INFILTRATION UNITS", len(spies))
    for r in spies[:10]:
        L.append(_line(r, trigger_count=tc.get(r.unit_id, 0),
                        role_grade=_role_grade(r, "spy")))
    if not spies:
        L.append("  (no spy/infiltration units detected)")
    L.append("")

    # Engineers (item 46)
    engs = _direct_scan_infantry(parser, 'engineer', 'ENGINEER')
    L += _section("ENGINEER UNITS", len(engs))
    for r in engs[:10]:
        L.append(_line(r, trigger_count=tc.get(r.unit_id, 0),
                        role_grade=_role_grade(r, "engineer")))
    L.append("")

    # Gunner vehicles
    if gunner_hard_infantry:
        gunners = [r for r in parser.buildable
                   if ini.section(r.unit_id)
                   and ini.section(r.unit_id).get('gunner', 'no').strip().lower() == 'yes']
        L += _section("GUNNER=YES VEHICLES (IFV mechanic)", len(gunners))
        for r in gunners[:10]:
            L.append(_line(r, trigger_count=tc.get(r.unit_id, 0),
                            role_grade=_role_grade(r, "gunner")))
        if not gunners:
            L.append("  (no Gunner=yes vehicles detected)")
        L.append("")

    # Neutral tech
    try:
        from .procedural_scripts import find_capturable_tech_buildings
        tech = find_capturable_tech_buildings(ini, limit=20)
    except Exception:
        tech = []
    L += _section("NEUTRAL TECH BUILDINGS (capture targets)", len(tech))
    for bid in tech[:15]:
        sec = ini.section(bid)
        name = sec.get('name', bid) if sec else bid
        L.append(f"  [{bid}] {name}")
    L.append("")

    # Full grade summary — ALL scored units, highest to lowest
    scored = [r for r in parser.buildable
              if (getattr(r, 'grade', None) or 0) > 0]
    scored.sort(key=lambda r: -r.grade)
    L += _section("ALL GRADED UNITS (highest to lowest)", len(scored))
    L += ["  Every buildable unit with a positive grade, ordered by grade.",
          "  Grade combines role fit, DPS, survivability, and mobility.", ""]
    for r in scored:
        L.append(_line(r, trigger_count=tc.get(r.unit_id, 0)))
    L.append("")

    # Combat transports
    combat_txp = [r for r in parser.buildable
                  if getattr(r, 'is_combat_transport', False)]
    L += _section("COMBAT-VIABLE TRANSPORTS", len(combat_txp))
    for r in combat_txp[:10]:
        L.append(_line(r, trigger_count=tc.get(r.unit_id, 0),
                        role_grade=_role_grade(r, "transport")))
    if not combat_txp:
        L.append("  (no combat-viable transports detected)")
    L.append("")

    # Items 54, 55: script action usage + team/taskforce feature counts
    L += _script_action_report(wave_gen)
    L += _team_feature_report(wave_gen)

    L += ["=" * 72, "  End of overview", "=" * 72]
    return "\n".join(L) + "\n"


def write_overview(path, parser, scan_result=None,
                    gunner_hard_infantry: bool = True,
                    trigger_counts: dict[str, int] | None = None,
                    wave_gen=None):
    path = Path(path)
    text = build_overview(parser, scan_result=scan_result,
                          gunner_hard_infantry=gunner_hard_infantry,
                          trigger_counts=trigger_counts,
                          wave_gen=wave_gen)
    path.write_text(text, encoding='utf-8')
    return path
