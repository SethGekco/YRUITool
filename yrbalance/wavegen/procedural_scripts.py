"""
procedural_scripts.py
=====================
Procedural script systems inspired by community AI mods:

1. BwP-encoded targeting
   Script actions 46 (Attack Enemy Structure), 47 (Move to Enemy Structure)
   and 58 (Move to Friendly Structure) take an argument encoded as:

       arg = 65536 * mode + building_index

   where building_index is the 0-based position of the BuildingType in the
   [BuildingTypes] list, and mode selects WHICH instance:
       0 = least threat     1 = highest threat
       2 = NEAREST          3 = FARTHEST

   This lets scripts say "attack the NEAREST war factory" or "patrol between
   my nearest refinery and my farthest refinery" — targeted, per-mod, and
   fully procedural (indexes are computed from the mod's own BuildingTypes).

2. Defensive patrols
   Small IsBaseDefense teams that walk between the AI's own key structures
   (refinery → war factory → loop) instead of standing still, giving bases a
   patrolled feel and intercepting harass earlier.

3. Neutral tech capture
   Engineer teams that capture the NEAREST instance of each capturable
   neutral tech structure (oil derricks, hospitals, machine shops...),
   gated by the AITriggerTypeExt DLL's RequiredNeutralBuildings condition so
   the trigger only fires while the neutral house still owns one.
"""

from __future__ import annotations

BWP_MIN_THREAT = 0
BWP_MAX_THREAT = 65536
BWP_NEAREST    = 131072   # 2 * 65536
BWP_FARTHEST   = 196608   # 3 * 65536

ACT_ATTACK_BUILDING  = 46
ACT_MOVE_ENEMY_ST    = 47   # BwP arg: move to nearest enemy instance
ACT_REGROUP_FRIENDLY = 54   # arg unused; regroup at closest friendly base
ACT_MOVE_FRIENDLY_ST = 58
ACT_GUARD_TICKS      = 5
ACT_JUMP             = 6
ACT_REGISTER_SUCCESS = 49
ACT_SET_MISSION      = 11
MISSION_AREA_GUARD   = 11
MISSION_HUNT         = 7    # roam and engage any enemy after primary target


class BwPResolver:
    """Maps BuildingType IDs to their [BuildingTypes] list index and encodes
    Building-with-Property arguments for script actions 46/47/58."""

    def __init__(self, ini):
        self.index: dict[str, int] = {}
        bt = ini.section('BuildingTypes') or {}
        # Game registers buildings in numeric key order of the list.
        items = []
        for k, v in bt.items():
            ks = str(k).strip()
            if not ks.isdigit():
                continue
            vid = (v or '').split(';')[0].strip()
            if vid:
                items.append((int(ks), vid))
        items.sort(key=lambda x: x[0])
        for i, (_, vid) in enumerate(items):
            if vid not in self.index:
                self.index[vid] = i

    def _encode(self, building_id: str, mode: int) -> int | None:
        i = self.index.get(building_id)
        if i is None:
            return None
        return mode + i

    def nearest(self, building_id: str)    -> int | None:
        return self._encode(building_id, BWP_NEAREST)

    def farthest(self, building_id: str)   -> int | None:
        return self._encode(building_id, BWP_FARTHEST)

    def max_threat(self, building_id: str) -> int | None:
        return self._encode(building_id, BWP_MAX_THREAT)


# ---------------------------------------------------------------------------
# Mod scanning helpers
# ---------------------------------------------------------------------------

def _tech_priority_score(sec) -> int:
    """Score a neutral tech building by tactical value, higher = better.

    Pattern-based, no unit-ID hardcoding. Reflects the ranking consistent
    across community strategy guides:
      1. Cash-producing (Oil Derrick-style): instant + ongoing income
      2. Vehicle repair (Machine Shop / Outpost-style): heals whole fleet
      3. Superweapon-granting: game-changing
      4. Infantry heal (Hospital-style): keeps meatgrinders alive
      5. Positive power (Power Plant-style): utility
      6. Anything else Capturable+neutral

    Scoring is additive — a building tagged both cash-producing AND
    vehicle-repair (unlikely but possible in mods) gets both bonuses.

    Airport-style free-unit-drops aren't tagged in vanilla YR at the
    rules level (they're map-scripted via trigger), so no reliable
    mechanical detection exists. Vanilla CAAIRP ends up scored equal to
    "nothing detected" and sorts among the least-valuable. If a mod adds
    a proper Airport rules tag, this scoring can be extended.
    """
    score = 0
    if not sec:
        return score

    # Cash — highest priority. ProduceCashStartup gives instant $1000+
    # in vanilla Oil Derrick; ProduceCashAmount is the ongoing drip.
    try:
        startup = int(sec.get('producecashstartup', '0') or '0')
    except ValueError:
        startup = 0
    try:
        amount = int(sec.get('producecashamount', '0') or '0')
    except ValueError:
        amount = 0
    if startup > 0 or amount > 0:
        score += 100
        # Bigger cash injections score higher
        score += min(50, startup // 20)
        score += min(30, amount * 3)

    # Vehicle repair — Machine Shop / Outpost. Repairs whole fleet as
    # long as vehicles come home.
    if sec.get('unitrepair', 'no').strip().lower() == 'yes':
        score += 60
    if sec.get('unitreload', 'no').strip().lower() == 'yes':
        score += 55   # ammo reload for aircraft — Outpost-like

    # Superweapon grant — powerful but rare and specific
    if sec.get('superweapon', '').strip():
        score += 50

    # Infantry heal — Hospital-style
    if sec.get('hospital', 'no').strip().lower() == 'yes':
        score += 40

    # Positive power — nice to have
    power_str = sec.get('power', '0').strip().lstrip('+')
    if power_str.isdigit():
        power = int(power_str)
        if power > 0:
            score += 10 + min(20, power // 25)

    return score


def find_capturable_tech_buildings(ini, limit: int = 8) -> list[str]:
    """Neutral capturable tech structures, ORDERED BY TACTICAL VALUE.

    Preferred source: NeutralTechBuildings= — YR's [AI] section explicitly
    lists them (CAAIRP, CATHOSP, CAOILD, CAOUTP, CAMACH, CAPOWR in vanilla).
    Some mods put the tag under [General] instead.

    Fallback: heuristic scan of [BuildingTypes] for TL<1, Capturable=yes,
    non-defense buildings with any obvious benefit tag. CATHOSP in vanilla
    has neither an explicit Hospital=yes flag nor any obvious benefit, so
    the explicit list must be honored first — otherwise CATHOSP is missed.

    Ordering: after collection, sort by `_tech_priority_score(sec)`
    descending. Downstream callers (AITrigger capture generation, overview
    display) then process the most tactically valuable targets first.
    Cash > Vehicle repair > Superweapon > Infantry heal > Power > Other.
    """
    collected: list[str] = []

    # 1) Explicit list — try [AI] first (vanilla), then [General] (some mods)
    listed = ''
    for section_name in ('AI', 'General'):
        sec = ini.section(section_name) or {}
        v = (sec.get('neutraltechbuildings', '') or '').strip()
        if v:
            listed = v
            break
    if listed:
        for tok in listed.split(','):
            bid = tok.split(';')[0].strip()
            if not bid or bid in collected:
                continue
            sec = ini.section(bid)
            if not sec:
                continue
            if sec.get('isbasedefense', 'no').strip().lower() == 'yes':
                continue
            collected.append(bid)

    # 2) Heuristic scan — additive, catches anything the explicit list missed
    bt = ini.section('BuildingTypes') or {}
    seen: set[str] = set(collected)
    for v in bt.values():
        bid = (v or '').split(';')[0].strip()
        if not bid or bid in seen:
            continue
        seen.add(bid)
        sec = ini.section(bid)
        if not sec:
            continue
        cap = sec.get('capturable', 'no').strip().lower()
        if cap not in ('yes', 'true', '1'):
            continue
        try:
            tl = int(sec.get('techlevel', '-1') or '-1')
        except ValueError:
            tl = -1
        if tl >= 1:
            continue   # player-buildable, not a map tech structure
        if sec.get('isbasedefense', 'no').strip().lower() == 'yes':
            continue
        beneficial = (
            sec.get('producecashamount', '').strip() or
            sec.get('producecashstartup', '').strip() or
            sec.get('hospital', 'no').strip().lower() == 'yes' or
            sec.get('unitreload', 'no').strip().lower() == 'yes' or
            sec.get('unitrepair', 'no').strip().lower() == 'yes' or
            sec.get('superweapon', '').strip() or
            (sec.get('power', '0').strip().lstrip('+').isdigit()
             and int(sec.get('power', '0').strip().lstrip('+') or 0) > 0)
        )
        if beneficial:
            collected.append(bid)

    # 3) Sort by tactical value, then cap at limit. Stable sort preserves
    # the original NeutralTechBuildings= ordering as a tiebreaker so mods
    # that curate their list get the intended order for equal-value ties.
    scored = [(bid, _tech_priority_score(ini.section(bid))) for bid in collected]
    scored.sort(key=lambda kv: -kv[1])
    return [bid for bid, _ in scored[:limit]]


def find_engineer(parser, faction: str) -> str | None:
    """Cheapest buildable Engineer=yes infantry for the faction."""
    best, best_cost = None, None
    for rec in parser.buildable:
        if rec.faction != faction or rec.category != 'Infantry':
            continue
        sec = parser.ini.section(rec.unit_id)
        if not sec or sec.get('engineer', 'no').strip().lower() != 'yes':
            continue
        cost = getattr(rec, 'cost', 0) or 0
        if best is None or cost < best_cost:
            best, best_cost = rec.unit_id, cost
    return best


def find_patrol_anchors(parser, faction: str) -> list[str]:
    """Key own-base structures to patrol between: refinery and war factory
    (falls back to construction yard). Returns building IDs in patrol order."""
    ini = parser.ini
    refinery = None
    warfact  = None
    conyard  = None
    countries = set()
    sides = getattr(parser, 'sides', None)
    if sides is not None:
        countries = {c.upper() for c in
                     getattr(sides, 'faction_countries', {}).get(faction, [])}

    def owned(sec) -> bool:
        if not countries:
            return True
        owners = {o.strip().upper() for o in sec.get('owner', '').split(',')
                  if o.strip()}
        return bool(owners & countries)

    bt = ini.section('BuildingTypes') or {}
    for v in bt.values():
        bid = (v or '').split(';')[0].strip()
        sec = ini.section(bid) if bid else None
        if not sec or not owned(sec):
            continue
        try:
            tl = int(sec.get('techlevel', '-1') or '-1')
        except ValueError:
            tl = -1
        if tl < 1 or tl >= 11:
            continue
        if refinery is None and sec.get('refinery', 'no').strip().lower() == 'yes':
            refinery = bid
        fac = sec.get('factory', '').strip().lower()
        if warfact is None and fac == 'unittype':
            warfact = bid
        if conyard is None and fac == 'buildingtype':
            conyard = bid
    anchors = [b for b in (refinery, warfact or conyard) if b]
    return anchors


def find_patrol_unit(parser, faction: str) -> str | None:
    """Cheap fast land combat unit for patrol duty."""
    best, best_score = None, None
    for rec in parser.buildable:
        if rec.faction != faction:
            continue
        if rec.category not in ('Vehicle', 'Infantry'):
            continue
        if getattr(rec, 'is_transport', False):
            continue
        if rec.mobility in ('Aircraft', 'Jumpjet', 'Naval'):
            continue
        cost = getattr(rec, 'cost', 0) or 0
        if cost <= 0:
            continue
        grade = getattr(rec, 'grade', 0) or 0
        if grade <= 0:
            continue   # non-combat
        score = cost   # cheapest combat unit wins
        if best is None or score < best_score:
            best, best_score = rec.unit_id, score
    return best


# ---------------------------------------------------------------------------
# Script builders
# ---------------------------------------------------------------------------

def build_patrol_actions(bwp: BwPResolver,
                          anchors: list[str],
                          guard_ticks: int = 15) -> list[tuple[int, int]]:
    """Walk between the nearest instances of each anchor building, guarding
    at each stop, then loop. Ends teams stay recruitable (Guard mission)."""
    actions: list[tuple[int, int]] = []
    for bid in anchors:
        arg = bwp.nearest(bid)
        if arg is None:
            continue
        actions.append((ACT_MOVE_FRIENDLY_ST, arg))
        actions.append((ACT_GUARD_TICKS, guard_ticks))
    if not actions:
        return []
    actions.append((ACT_JUMP, 1))   # loop to first action (1-indexed)
    return actions


def build_capture_actions(bwp: BwPResolver,
                           tech_building: str) -> list[tuple[int, int]]:
    """Engineer capture of the nearest neutral tech structure, register the
    success (feeds the adaptive-weight system), then area-guard the prize."""
    arg = bwp.nearest(tech_building)
    if arg is None:
        return []
    return [
        (ACT_ATTACK_BUILDING,  arg),               # engineers 'attack' = capture
        (ACT_REGISTER_SUCCESS, 0),                 # promote this trigger
        (ACT_SET_MISSION,      MISSION_AREA_GUARD) # hold the area
    ]


def build_destroy_tech_actions(bwp: BwPResolver,
                                tech_building: str) -> list[tuple[int, int]]:
    """Destroy a tech building — regroup at friendly base, move to the
    nearest enemy-owned or neutral instance, attack it, register success,
    then switch to hunt so the team continues engaging until wiped.

    Used by mid/late-game destroy-tech triggers gated on ElapsedTime.
    Rationale (Rex): in early game the AI prefers to capture tech
    buildings for the economic benefit, but after early game aggression
    is more valuable than economic infrastructure. This script realizes
    that intent — the team wipes the tech building rather than trying
    to hold it.
    """
    arg = bwp.nearest(tech_building)
    if arg is None:
        return []
    return [
        (ACT_REGROUP_FRIENDLY, 0),                 # form up first
        (ACT_MOVE_ENEMY_ST,    arg),               # move to the tech building
        (ACT_ATTACK_BUILDING,  arg),               # destroy it
        (ACT_REGISTER_SUCCESS, 0),                 # promote this trigger
        (ACT_SET_MISSION,      MISSION_HUNT),      # keep engaging afterward
    ]


def find_best_anti_building_composition(parser, faction: str,
                                          target_size: int = 5) -> list:
    """Return a composition (list of (count, uid) tuples) suited for
    destroying buildings. Pattern-based selection:

    Prefer: units tagged ARTILLERY or with strong DPS-vs-building.
    Fallback: any high-grade vehicle for the faction.

    `target_size` = approximate total unit count.
    Returns [] if the faction has no suitable units — caller should skip.
    """
    if not parser or not faction:
        return []

    ranked = []
    for rec in parser.buildable:
        if getattr(rec, 'faction', None) != faction:
            continue
        if getattr(rec, 'category', None) not in ('Vehicle', 'Infantry'):
            continue
        if getattr(rec, 'is_transport', False):
            continue
        if getattr(rec, 'is_no_combat', False):
            continue
        if getattr(rec, 'is_utility', False):
            continue

        # Anti-building fitness score. Uses grade_breakdown when available.
        bd = getattr(rec, 'grade_breakdown', None) or {}
        anti_bld = bd.get('dps_building', 0) or 0
        outrange = bd.get('outranges_def', 0) or 0
        base_grade = getattr(rec, 'grade', 0) or 0

        # Artillery gets a big weight because that's the canonical role
        roles = getattr(rec, 'roles', None) or []
        is_arty = any(r in ('ARTILLERY', 'AG-Artillery') for r in roles)

        fitness = anti_bld * 3 + outrange * 2 + base_grade
        if is_arty:
            fitness += 20
        if fitness <= 0:
            continue
        ranked.append((fitness, rec))

    if not ranked:
        return []
    ranked.sort(key=lambda kv: -kv[0])

    # Composition: 3 of the best, 2 of the second best if available
    top = ranked[0][1]
    composition = [(min(3, target_size), top.unit_id)]
    if len(ranked) >= 2 and target_size >= 5:
        second = ranked[1][1]
        # Don't duplicate — must be a different UID
        if second.unit_id != top.unit_id:
            composition.append((target_size - 3, second.unit_id))
    return composition

# ---------------------------------------------------------------------------
# Top-level integration
# ---------------------------------------------------------------------------

def generate_procedural_extras(wave_gen, parser,
                                 patrols: bool = True,
                                 capture: bool = True,
                                 destroy_tech: bool = True,
                                 destroy_tech_delay_seconds: int = 300) -> dict:
    """Generate patrol defense teams, neutral-tech capture teams, and
    mid/late-game destroy-tech teams for every faction, appending them to
    wave_gen's output lists.

    destroy_tech: also generate destroy-tech triggers gated on ElapsedTime.
        Once game time exceeds `destroy_tech_delay_seconds` (default 300s
        = 5 min), the AI shifts from capture-mode to destroy-mode for the
        same tech buildings. This tests the AITriggerTypeExt DLL's
        ElapsedTime feature and adds tactical bite in mid-game where
        vanilla AI stagnates.

    Returns counts.
    """
    from .taskforce_generator import TaskforceEntry
    from .script_generator import ScriptEntry
    from .team_generator import TeamEntry, FIXED_FLAGS

    bwp = BwPResolver(parser.ini)
    factions = sorted(getattr(parser.sides, 'faction_countries', {}).keys())
    counts = {'patrol_teams': 0, 'capture_teams': 0, 'destroy_tech_teams': 0}

    new_teams: list = []
    aiext_pairs: list = []   # (team_index_in_new_teams, cond) for capture gates

    for faction in factions:
        # ---------------- Patrols ----------------
        if patrols:
            anchors = find_patrol_anchors(parser, faction)
            unit    = find_patrol_unit(parser, faction)
            actions = build_patrol_actions(bwp, anchors)
            if unit and actions:
                units = [(4, unit)]
                # Duplicate detection: reuse an existing taskforce with the
                # same composition rather than creating a redundant section.
                # This is what causes two identical `0=4,E1` taskforces
                # (e.g. "4 Patrol E1" and "4 GI") when POOL and PATROL
                # generators both produce the same composition.
                existing_tf_id = wave_gen.tf_reg.find_duplicate(units)
                if existing_tf_id:
                    tf_id = existing_tf_id
                    # Merge our label tags into the existing taskforce's
                    # label so it captures both usages ([POOL][PATROL]).
                    existing_tf = next((t for t in wave_gen.taskforces
                                        if t.tf_id == tf_id), None)
                    new_tag = f"[{faction.upper()}][PATROL][DEFENSE]"
                    if existing_tf and new_tag not in existing_tf.label_comment:
                        merged = existing_tf.label_comment.rstrip() + new_tag
                        # Deduplicate any repeated bracket tags in the merged label
                        import re as _re
                        seen = []
                        for tag in _re.findall(r"\[[^\]]+\]", merged):
                            if tag not in seen:
                                seen.append(tag)
                        merged = "; " + "".join(seen)
                        existing_tf.label_comment = merged
                else:
                    tf_id = wave_gen.tf_reg.generate()
                    wave_gen.tf_reg.register_composition(tf_id, units)
                    label = f"; [{faction.upper()}][PATROL][DEFENSE]"
                    wave_gen.taskforces.append(TaskforceEntry(
                        tf_id=tf_id, name=f"4 Patrol {unit}",
                        label_comment=label, units=units, faction=faction,
                        phase="Early", role="PATROL", difficulty="all",
                        countries={faction}, forbidden=set(),
                        direct_attack=False))
                    wave_gen._appended_tf_ids.add(tf_id)

                sc_id = wave_gen.tf_reg.generate()
                wave_gen.scripts.append(ScriptEntry(
                    script_id=sc_id, name=f"{faction.title()} Base Patrol",
                    label_comment=f"; [SCRIPT][PATROL][{faction.upper()}]",
                    actions=actions))

                flags = dict(FIXED_FLAGS)
                flags.update({
                    "IsBaseDefense":             "yes",
                    "AreTeamMembersRecruitable": "yes",
                    "Aggressive":                "no",
                    "AvoidThreats":              "no",
                    "GuardSlower":               "no",
                    "Suicide":                   "no",
                    "Whiner":                    "yes",
                    "LooseRecruit":              "no",
                    "Annoyance":                 "no",
                    "Priority":                  "5",
                    "Max":                       "1",
                    "Stray":                     "20",
                    "RelaxedStray":              "25",
                })
                team_id = wave_gen.tf_reg.generate()
                new_teams.append(TeamEntry(
                    team_id=team_id,
                    name=f"{faction.upper()} Base Patrol",
                    label_comment=f"; [{faction.upper()}][PATROL][POOL]",
                    taskforce_id=tf_id, script_id=sc_id, flags=flags,
                    faction=faction, phase="Early", role="PATROL",
                    difficulty="all", is_pool=True))
                counts['patrol_teams'] += 1

        # ---------------- Tech capture ----------------
        if capture:
            engineer = find_engineer(parser, faction)
            if engineer:
                for bld in find_capturable_tech_buildings(parser.ini, limit=4):
                    actions = build_capture_actions(bwp, bld)
                    if not actions:
                        continue
                    tf_id = wave_gen.tf_reg.generate()
                    units = [(2, engineer)]
                    wave_gen.tf_reg.register_composition(tf_id, units)
                    label = f"; [{faction.upper()}][CAPTURE_TECH][{bld}]"
                    wave_gen.taskforces.append(TaskforceEntry(
                        tf_id=tf_id, name=f"2 Engineer capture {bld}",
                        label_comment=label, units=units, faction=faction,
                        phase="Mid", role="CAPTURE_TECH", difficulty="Medium",
                        countries={faction}, forbidden=set(),
                        direct_attack=False))
                    wave_gen._appended_tf_ids.add(tf_id)

                    sc_id = wave_gen.tf_reg.generate()
                    wave_gen.scripts.append(ScriptEntry(
                        script_id=sc_id,
                        name=f"Capture {bld} (nearest)",
                        label_comment=f"; [SCRIPT][CAPTURE_TECH][{bld}]",
                        actions=actions))

                    flags = dict(FIXED_FLAGS)
                    flags.update({
                        "Aggressive":   "no",
                        "AvoidThreats": "yes",
                        "Suicide":      "no",
                        "Whiner":       "no",
                        "GuardSlower":  "no",
                        "LooseRecruit": "no",
                        "Annoyance":    "no",
                        "AreTeamMembersRecruitable": "no",
                        "Priority":     "8",
                        "Max":          "1",
                    })
                    team_id = wave_gen.tf_reg.generate()
                    team = TeamEntry(
                        team_id=team_id,
                        name=f"{faction.upper()} Capture {bld}",
                        label_comment=label,
                        taskforce_id=tf_id, script_id=sc_id, flags=flags,
                        faction=faction, phase="Mid", role="CAPTURE_TECH",
                        difficulty="Medium", is_pool=False)
                    new_teams.append(team)

                    # AIExt gate: fire only while the NEUTRAL house still
                    # owns at least one of this tech structure.
                    try:
                        from .aiext_generator import AIExtConditions
                        cond = AIExtConditions()
                        cond.neutral_buildings     = [bld]
                        cond.neutral_buildings_min = [1]
                        cond.neutral_buildings_max = [-1]
                        aiext_pairs.append((len(new_teams) - 1, cond))
                    except Exception:
                        pass
                    counts['capture_teams'] += 1

        # ---------------- Destroy hostile tech (mid/late game) ----------------
        # Rex: "capturing is important, but after early game the AI should
        # just be more aggressive." Gated on ElapsedTime — tests the
        # AITriggerTypeExt DLL's elapsed-time feature.
        #
        # Composition: best anti-building units the faction has. Script:
        # regroup, move to tech building, attack, register success, hunt.
        #
        # Priority higher than capture so once elapsed time passes the
        # threshold, destroy triggers preempt fresh capture attempts.
        if destroy_tech:
            composition = find_best_anti_building_composition(
                parser, faction, target_size=5)
            if composition:
                for bld in find_capturable_tech_buildings(parser.ini, limit=4):
                    actions = build_destroy_tech_actions(bwp, bld)
                    if not actions:
                        continue
                    tf_id = wave_gen.tf_reg.generate()
                    existing = wave_gen.tf_reg.find_duplicate(composition)
                    if existing:
                        tf_id = existing
                    else:
                        wave_gen.tf_reg.register_composition(tf_id, composition)
                        label = (f"; [{faction.upper()}][DESTROY_TECH]"
                                 f"[{bld}]")
                        comp_name = ", ".join(
                            f"{c} {u}" for c, u in composition)
                        wave_gen.taskforces.append(TaskforceEntry(
                            tf_id=tf_id, name=f"{comp_name} destroy {bld}",
                            label_comment=label, units=composition,
                            faction=faction, phase="Mid", role="DESTROY_TECH",
                            difficulty="Medium", countries={faction},
                            forbidden=set(), direct_attack=False))
                        wave_gen._appended_tf_ids.add(tf_id)

                    sc_id = wave_gen.tf_reg.generate()
                    wave_gen.scripts.append(ScriptEntry(
                        script_id=sc_id,
                        name=f"Destroy {bld} (nearest)",
                        label_comment=f"; [SCRIPT][DESTROY_TECH][{bld}]",
                        actions=actions))

                    flags = dict(FIXED_FLAGS)
                    flags.update({
                        "Aggressive":                 "yes",
                        "AvoidThreats":               "no",
                        "Suicide":                    "no",
                        "Whiner":                     "no",
                        "GuardSlower":                "no",
                        "LooseRecruit":               "no",
                        "Annoyance":                  "no",
                        "AreTeamMembersRecruitable":  "no",
                        # Higher priority than capture (8) so once elapsed
                        # time gate opens, destroy preempts capture.
                        "Priority":                   "10",
                        "Max":                        "1",
                    })
                    team_id = wave_gen.tf_reg.generate()
                    team = TeamEntry(
                        team_id=team_id,
                        name=f"{faction.upper()} Destroy {bld}",
                        label_comment=f"; [{faction.upper()}][DESTROY_TECH][{bld}]",
                        taskforce_id=tf_id, script_id=sc_id, flags=flags,
                        faction=faction, phase="Mid", role="DESTROY_TECH",
                        difficulty="Medium", is_pool=False)
                    new_teams.append(team)

                    # AIExt gate: ElapsedTime >= threshold. Simplest form of
                    # the concept — no economy or existence conditions on the
                    # target, so the trigger fires reliably once game time
                    # passes the threshold. If the tech building doesn't
                    # exist at fire-time the script does nothing harmful.
                    try:
                        from .aiext_generator import AIExtConditions
                        cond = AIExtConditions()
                        cond.elapsed_seconds_min = destroy_tech_delay_seconds
                        aiext_pairs.append((len(new_teams) - 1, cond))
                    except Exception:
                        pass
                    counts['destroy_tech_teams'] += 1

    if new_teams:
        wave_gen.teams.extend(new_teams)
        triggers = wave_gen.trig_gen.generate_for_teams(new_teams)
        wave_gen.triggers.extend(triggers)
        for idx, cond in aiext_pairs:
            if idx < len(triggers) and not cond.is_empty():
                wave_gen.aiext_writer.collect(triggers[idx].trigger_id, cond, getattr(triggers[idx], 'name', None))

    return counts
