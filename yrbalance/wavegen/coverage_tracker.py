"""
coverage_tracker.py
===================
Two related concerns, one module:

  28. Full building-combination enumeration with silent dedup.
      For each faction, enumerate distinct prerequisite-building sets
      that produce meaningful taskforces. If two triggers would end up
      with the same composition, keep the one whose AIExt owner-building
      requirement list is SHORTER (fewer buildings required = easier for
      the AI to satisfy).  The dedup is silent — no warnings.

  29. Every legally-buildable unit used at least once.
      After the main wave generation finishes, scan every generated
      taskforce's unit list.  Any buildable unit that never appears
      gets one dedicated "utility filler" taskforce so the AI is at
      least aware of it.  Low-grade units (dogs, weak filler infantry)
      get sprinkled once minimum.

Both features are additive — they run AFTER the normal wave/pool/procedural
generation, augmenting rather than replacing anything.
"""
from __future__ import annotations
from typing import Iterable


# ---------------------------------------------------------------------------
# Coverage: unit usage tracking
# ---------------------------------------------------------------------------

def collect_used_unit_ids(wave_gen) -> set[str]:
    """Return every unit ID that appears in any generated taskforce."""
    used: set[str] = set()
    for tf in getattr(wave_gen, 'taskforces', []):
        for _, uid in (tf.units or []):
            if uid:
                used.add(uid)
    return used


def find_unused_units(parser, used: set[str]) -> list:
    """Return UnitRecords for every buildable unit not appearing in `used`.

    Naval and aerial units still count — they should surface in naval /
    aerial waves. Transports, no-combat, and utility units are excluded
    because they're not attack-composition candidates.
    """
    out = []
    for rec in parser.buildable:
        if rec.unit_id in used:
            continue
        if getattr(rec, 'is_no_combat', False):
            continue
        if getattr(rec, 'is_utility', False):
            continue
        if getattr(rec, 'is_transport', False) and not getattr(
                rec, 'is_combat_transport', False):
            continue
        out.append(rec)
    return out


def generate_coverage_fillers(wave_gen, parser) -> int:
    """Generate one 'filler' taskforce+team+trigger per unused unit.

    Returns the count of filler compositions created.

    Rationale: some low-grade units never win the composition selection
    contest but are still legal to build.  Giving each one a single
    minimum-priority trigger ensures the AI at least occasionally builds
    them, keeping the roster feeling complete rather than showcasing only
    the top-graded units.
    """
    from .taskforce_generator import TaskforceEntry
    from .script_generator  import ScriptEntry
    from .team_generator    import TeamEntry, FIXED_FLAGS

    used   = collect_used_unit_ids(wave_gen)
    unused = find_unused_units(parser, used)
    if not unused:
        return 0

    ini = parser.ini
    made = 0
    new_teams   = []

    # Grab (or generate) a very-simple attack script — same one whether
    # the unit is infantry or vehicle. Standard: gather at enemy, attack,
    # loop. Priority 3 so it loses to specific-role scripts.
    # (We use the existing script generator by faking a taskforce label
    # containing GENERAL_ATTACK below — it picks the right script for us.)

    for rec in unused:
        faction = getattr(rec, 'faction', None) or 'gdi'

        # Composition: 3 of the unit, capped by BuildLimit if set.
        sec = ini.section(rec.unit_id)
        try:
            bl = int(sec.get('buildlimit', '0') or '0') if sec else 0
        except ValueError:
            bl = 0
        count = 3
        if bl > 0:
            count = min(count, bl)
        units_list = [(count, rec.unit_id)]

        existing = wave_gen.tf_reg.find_duplicate(units_list)
        if existing:
            tf_id = existing
        else:
            tf_id = wave_gen.tf_reg.generate()
            wave_gen.tf_reg.register_composition(tf_id, units_list)

        label = (f"; [{faction.upper()}][COVERAGE_FILLER]"
                 f"[{rec.category.upper() if getattr(rec, 'category', None) else 'UNIT'}]")

        tf = TaskforceEntry(
            tf_id         = tf_id,
            name          = f"{count} {rec.unit_id}",
            label_comment = label,
            units         = units_list,
            faction       = faction,
            phase         = "Mid",
            role          = "GENERAL_ATTACK",
            difficulty    = "Medium",
            countries     = {faction},
            forbidden     = set(),
            direct_attack = False,
        )
        if tf_id not in wave_gen._appended_tf_ids:
            wave_gen._appended_tf_ids.add(tf_id)
            wave_gen.taskforces.append(tf)

        # Reuse the script selector — it'll pick a general-attack script.
        script = wave_gen._get_script_for_tf(tf)
        if script is None:
            continue

        flags = dict(FIXED_FLAGS)
        flags.update({
            "Priority":    "1",   # lowest priority — sprinkled, not spammed
            "Max":         "1",
            "IsBaseDefense": "no",
        })
        team = TeamEntry(
            team_id       = wave_gen.tf_reg.generate(),
            name          = f"{faction.upper()} Coverage {rec.unit_id}",
            label_comment = label,
            taskforce_id  = tf_id,
            script_id     = script.script_id,
            flags         = flags,
            faction       = faction,
            phase         = "Mid",
            role          = "GENERAL_ATTACK",
            difficulty    = "Medium",
            is_pool       = False,
        )
        new_teams.append(team)
        made += 1

    if new_teams:
        wave_gen.teams.extend(new_teams)
        triggers = wave_gen.trig_gen.generate_for_teams(new_teams)
        wave_gen.triggers.extend(triggers)

    return made


# ---------------------------------------------------------------------------
# Building-combination silent dedup
# ---------------------------------------------------------------------------

def _prereq_key(entry) -> tuple:
    """Frozenset of owner-building requirements from a trigger's AIExt.

    Triggers with no attached AIExt are treated as "no requirements" and
    all cluster together under the same key.
    """
    cond = getattr(entry, 'aiext_conditions', None)
    if cond is None:
        return frozenset()
    return frozenset(getattr(cond, 'owner_buildings', []) or [])


def _tf_composition_key(entry, wave_gen) -> tuple:
    """Sorted tuple of (count, uid) pairs — identifies "same wave"."""
    # entry is a TriggerEntry pointing at a team pointing at a taskforce
    team_id = getattr(entry, 'team1_id', None)
    if not team_id:
        return ()
    team = next((t for t in wave_gen.teams if t.team_id == team_id), None)
    if team is None:
        return ()
    tf = next((f for f in wave_gen.taskforces
               if f.tf_id == team.taskforce_id), None)
    if tf is None:
        return ()
    return tuple(sorted(tf.units or []))


def silent_dedup_by_min_prereq(wave_gen) -> int:
    """When two triggers produce the same composition but differ in the
    number of prerequisite buildings they require, silently drop the one
    with more requirements. Returns the number dropped.
    """
    if not getattr(wave_gen, 'triggers', None):
        return 0

    # Group triggers by composition key
    groups: dict[tuple, list] = {}
    for trig in wave_gen.triggers:
        key = _tf_composition_key(trig, wave_gen)
        if not key:
            continue
        groups.setdefault(key, []).append(trig)

    to_drop_ids: set[str] = set()
    for key, trigs in groups.items():
        if len(trigs) < 2:
            continue
        # Keep the one with the SHORTEST owner_buildings requirement.
        # Tiebreak by fewest total conditions (owner + enemy + neutral).
        def score(t):
            cond = getattr(t, 'aiext_conditions', None)
            if cond is None:
                return (0, 0)
            return (
                len(getattr(cond, 'owner_buildings', []) or []),
                len(getattr(cond, 'owner_buildings', []) or []) +
                len(getattr(cond, 'enemy_buildings', []) or []) +
                len(getattr(cond, 'neutral_buildings', []) or []),
            )
        trigs.sort(key=score)
        keeper = trigs[0]
        for t in trigs[1:]:
            to_drop_ids.add(t.trigger_id)

    if not to_drop_ids:
        return 0

    wave_gen.triggers = [t for t in wave_gen.triggers
                         if t.trigger_id not in to_drop_ids]
    # Also drop the associated AIExt sidecars
    writer = getattr(wave_gen, 'aiext_writer', None)
    if writer is not None and hasattr(writer, '_conds'):
        # writer._conds is the collect() storage — filter by trigger id
        try:
            writer._conds = {tid: c for tid, c in writer._conds.items()
                             if tid not in to_drop_ids}
        except Exception:
            pass

    return len(to_drop_ids)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def apply_coverage(wave_gen, parser) -> dict:
    """Run both coverage passes and return counts for reporting."""
    dropped = silent_dedup_by_min_prereq(wave_gen)
    filled  = generate_coverage_fillers(wave_gen, parser)
    return {'silent_dedup': dropped, 'coverage_fillers': filled}
