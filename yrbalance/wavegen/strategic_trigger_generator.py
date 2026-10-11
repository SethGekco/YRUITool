"""
strategic_trigger_generator.py
================================
Generates strategic AI triggers that use dummy prerequisite buildings
to detect tactical situations and respond intelligently.

Extends the base trigger generator with condition-based triggers that
reference DMPRNODEF / DMPRNODEFAA / mixed dummy buildings.

== SCENARIOS HANDLED ==

ENEMY-BASED (CondType=0, enemy owns dummy):
  1. Enemy No Defenses (owns DMPRNODEF)
     → Rush with direct attack, no gather
  2. Enemy No AA (owns DMPRNODEFAA)
     → Aerial spam waves

SELF-BASED (CondType=1, AI owns dummy):
  3. Self No Defenses (owns DMPRNODEF)
     → Small emergency guard teams (IsBaseDefense=no)
     → Patrol between faction buildings
  4. Self No AA (owns DMPRNODEFAA)
     → Small emergency AA guard teams
     → Patrol between faction buildings

MIXED CONDITION (requires mixed dummy prereqs):
  5. Self: No CY + Has Service Depot + Has Warfactory
     → Build MCV team (reuse vanilla 043F23CC-G deploy script)
  6. Enemy: No CY + Has Warfactory
     → Immediate factory kill attack

== PATROL SCRIPT STRUCTURE ==
  54,0           ; regroup at friendly base
  58,131072+idx  ; move to building A
  5,N            ; guard N ticks (default 5 = 30 seconds)
  58,131072+idx  ; move to building B
  5,N
  58,131072+idx  ; move to building C
  5,N
  6,1            ; loop from step 1 (skip initial regroup)

== TRIGGER WEIGHTS ==
  Enemy no defenses:   StartW=500, MinW=100, MaxW=500
  Enemy no AA:         StartW=500, MinW=100, MaxW=500
  Self no defenses:    StartW=1000, MinW=500, MaxW=1000
  Self no AA:          StartW=1000, MinW=500, MaxW=1000
  MCV rebuild:         StartW=5000, MinW=5000, MaxW=5000
  Enemy no CY attack:  StartW=500, MinW=200, MaxW=500
"""

import re
import random
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from .rulesmd_parser import RulesMdParser, UnitRecord
from .taskforce_generator import (
    TaskforceGenerator, IDRegistry, TaskforceSpec,
    GeneratorConfig, TaskforceEntry, make_vanilla_name,
)
from .script_generator import ScriptGenerator, ScriptRegistry, ScriptEntry
from .team_generator import TeamGenerator, TeamRegistry, TeamEntry, POOL_FLAGS
from .trigger_generator import (
    TriggerGenerator, TriggerRegistry, TriggerEntry,
    COND_ENEMY_OWNS, COND_AI_OWNS, COND_ENEMY_CREDITS,
    ALWAYS_TRUE, OWN_AT_LEAST_1, make_comparator, OP_GTE,
    build_side_map,
)


# ---------------------------------------------------------------------------
# Patrol ticks (user-configurable)
# ---------------------------------------------------------------------------

DEFAULT_PATROL_TICKS = 5   # 30 seconds per stop


# ---------------------------------------------------------------------------
# Patrol script builder
# ---------------------------------------------------------------------------

def make_patrol_script_actions(building_indices: list[int],
                                patrol_ticks: int = DEFAULT_PATROL_TICKS,
                                regroup_first: bool = True) -> list[tuple[int,int]]:
    """
    Build patrol script actions for a list of building type indices.
    Uses 131072+index formula for structure numbers.

    regroup_first: if True, add 54,0 (move to friendly base) as first action
                   and loop back to action 1 (skip regroup on subsequent loops)
    """
    actions = []
    if regroup_first:
        actions.append((54, 0))   # move to friendly base

    loop_target = len(actions)   # loop back here after regroup

    for idx in building_indices:
        actions.append((58, 131072 + idx))   # move to friendly structure
        actions.append((5,  patrol_ticks))    # guard N ticks

    actions.append((6, loop_target))  # loop (skip initial regroup)
    return actions


def make_area_guard_actions() -> list[tuple[int,int]]:
    """Simple area guard script — units stay near spawn and guard."""
    return [(11, 5)]   # Guard mission (recruitable)


def make_direct_attack_actions() -> list[tuple[int,int]]:
    """Direct attack — straight to enemy base, attack anything."""
    return [
        (53, 0),   # move to enemy base
        (0,  1),   # attack anything
        (49, 0),   # repeat until success
        (6,  0),   # loop
    ]


def make_naval_bombard_actions() -> list[tuple[int,int]]:
    """
    Naval long-range bombardment script.
    Ships with long-range weapons (CARRIER, DRED, BSUB) should:
      1. Hunt enemy targets (ships fire at range, engine finds targets)
      2. Attack structures within range
      3. Loop — they stay at sea and bombard from distance
    Unlike ground attacks, ships cannot move to enemy base on land,
    so we use 'hunt' (0,1) which lets the AI drive targeting naturally.
    """
    return [
        (0,  2),   # attack structures (long-range ships engage base)
        (49, 0),   # repeat until success
        (0,  1),   # attack anything
        (49, 0),
        (6,  0),   # loop
    ]


def make_naval_hunt_actions() -> list[tuple[int,int]]:
    """
    Naval unit hunt script — hunt enemy naval units.
    Used for the 'fight for dominance' team that patrols for enemy ships.
    """
    return [
        (0,  4),   # attack vehicles (naval units are vehicles)
        (49, 0),
        (0,  1),   # attack anything
        (49, 0),
        (6,  0),
    ]


def make_naval_shipyard_attack_actions() -> list[tuple[int,int]]:
    """
    Naval shipyard attack — target enemy naval production.
    Priority: factories (shipyards) → structures → anything.
    """
    return [
        (0,  6),   # attack factories (shipyards)
        (49, 0),
        (0,  2),   # attack structures
        (49, 0),
        (0,  1),
        (49, 0),
        (6,  0),
    ]
    """Direct attack — straight to enemy base, attack anything."""
    return [
        (53, 0),   # move to enemy base
        (0,  1),   # attack anything
        (49, 0),   # repeat until success
        (6,  0),   # loop
    ]


def make_factory_attack_actions() -> list[tuple[int,int]]:
    """Factory priority attack — factories then structures."""
    return [
        (53, 0),
        (0,  6),   # attack factories
        (49, 0),
        (0,  2),   # attack structures
        (49, 0),
        (0,  1),
        (49, 0),
    ]


def make_aerial_rush_actions() -> list[tuple[int,int]]:
    """Aerial rush — move to enemy base and attack immediately."""
    return [
        (53, 0),
        (0,  1),
        (49, 0),
        (6,  0),
    ]


def make_transport_drop_actions(phobos: bool = True,
                                anchor_arg: int | None = None
                                ) -> list[tuple[int,int]]:
    """
    Aerial transport drop script.
    Phobos: uses 10103,0 (load ALL transports simultaneously).
    Vanilla: uses 14,0 (single transport only).

    anchor_arg: BwP-encoded FRIENDLY building for a 58 (MoveToFriendlyBldg)
    rendezvous BEFORE loading — required for flying transports: everyone
    (flyer included) paths to the static building, the flyer lands there,
    and the payload walks in. Without it the payload chases the airborne
    transport, blocks its landing cell, and the team softlocks (this is
    how vanilla's own Blackhawk scripts board — they open with 58,GAWEAP).

    Weight adaptation via Phobos 14001/14002:
      - 14000,100  Reward +100 weight when team kills a target (success signal)
      - 14002,200  Decrease weight by 200 at start (penalize if team dies quickly
                   without killing anything — net effect: 200 penalty unless
                   at least 2 successful kills)
    """
    load = [(10103, 0)] if phobos else [(14, 0)]
    pre  = [(14002, 50)]                       # pre-penalize: cost of attempt
    if anchor_arg is not None:
        pre = pre + [(58, anchor_arg)]         # rendezvous at own factory
    return pre + load + [
        (43,    0),     # wait until fully loaded
        (53,    0),     # move to enemy base
        (8,     2),     # unload (lose transport, keep infantry)
        (14000, 100),   # reward +100 weight per kill after drop
        (0,     1),     # attack anything
        (49,    0),     # repeat until success
    ]


def make_massive_aerial_actions() -> list[tuple[int,int]]:
    """Massive aerial wave — straight to enemy base."""
    return [
        (53, 0),
        (0,  1),
        (49, 0),
        (6,  0),
    ]


# ---------------------------------------------------------------------------
# MCV vanilla script ID
# ---------------------------------------------------------------------------

VANILLA_MCV_SCRIPT_ID = "043F23CC-G"   # General MCV Deploy

# Vanilla MCV team IDs
VANILLA_MCV_TEAMS = {
    'gdi':       "043F29AC-G",   # Allied MCV
    'nod':       "043F83EC-G",   # Soviet MCV
    'thirdside': "043F89AC-G",   # Yuri MCV
}

# MCV unit IDs per faction
MCV_UNITS = {
    'gdi':       'AMCV',
    'nod':       'SMCV',
    'thirdside': 'PCV',
}


# ---------------------------------------------------------------------------
# Strategic scenario definitions
# ---------------------------------------------------------------------------

@dataclass
class StrategicScenario:
    """
    One strategic scenario — condition + response team + trigger.
    """
    name:          str
    faction:       str
    cond_type:     int        # 0=enemy owns, 1=AI owns
    cond_obj:      str        # dummy building ID to check
    comparator:    str        # hex comparator string
    weights:       tuple      # (start, min, max)
    difficulty:    str        # Easy/Medium/Hard/all
    taskforce_id:  str | None = None
    script_id:     str | None = None
    team_id:       str | None = None
    trigger_id:    str | None = None
    team2_id:      str        = '<none>'
    is_base_def:   bool       = False
    notes:         str        = ""


# ---------------------------------------------------------------------------
# Strategic trigger generator
# ---------------------------------------------------------------------------

class StrategicTriggerGenerator:
    """
    Generates strategic TeamTypes, Scripts, and AITriggerTypes that
    respond to dummy prerequisite building conditions.

    Usage:
        gen = StrategicTriggerGenerator(
            parser, global_reg, tf_reg, sc_reg, team_reg, trig_reg,
            side_map, dummy_gen, defense_gen,
            patrol_ticks=5, seed=42)
        gen.generate(factions=['gdi','nod','thirdside'])
        gen.write_all(aimd_path, output_path)
    """

    def __init__(self, parser: RulesMdParser,
                 global_reg, tf_reg: IDRegistry,
                 sc_reg: ScriptRegistry, team_reg: TeamRegistry,
                 trig_reg: TriggerRegistry,
                 side_map: dict[str, int],
                 dummy_gen=None,
                 defense_gen=None,
                 patrol_ticks: int = DEFAULT_PATROL_TICKS,
                 seed: int | None = None,
                 ares: bool = False):
        self.parser      = parser
        self.global_reg  = global_reg
        self.tf_reg      = tf_reg
        self.sc_reg      = sc_reg
        self.team_reg    = team_reg
        self.trig_reg    = trig_reg
        self.side_map    = side_map
        self.dummy_gen   = dummy_gen    # FullDummyPrereqGenerator instance
        self.defense_gen = defense_gen  # DefenseDummyGenerator instance
        self.patrol_ticks = patrol_ticks
        self.rng         = random.Random(seed)
        self.ares        = ares         # Ares engine detected

        self.tf_gen   = TaskforceGenerator(parser, tf_reg, seed=seed)
        self.sc_gen   = ScriptGenerator(sc_reg, seed=seed)
        self.team_gen = TeamGenerator(parser, team_reg, seed=seed)
        self.trig_gen = TriggerGenerator(trig_reg, side_map, seed=seed)

        # All generated entries
        self.taskforces:  list[TaskforceEntry]  = []
        self.scripts:     list[ScriptEntry]     = []
        self.teams:       list[TeamEntry]       = []
        self.triggers:    list[TriggerEntry]    = []
        self.scenarios:   list[StrategicScenario] = []

        # Registered script IDs by action fingerprint
        self._script_cache: dict[tuple, str] = {}

        # Naval dummy ID maps (bid → absence/presence dummy section ID)
        # Populated when dummy_gen has generated absence dummies for shipyards
        self._shipyard_absence: dict[str, str] = {}   # GAYARD → DMPRX023
        self._shipyard_presence: dict[str, str] = {}  # GAYARD → DMPR023

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def generate(self, factions: list[str],
                 difficulties: list[str] | None = None,
                 phobos: bool = True):
        """Generate all strategic triggers for the given factions.
        phobos: whether to use Phobos script actions (10103,0 for transport drop).
                Dummy prereqs already require Phobos so this defaults True.
        """
        if difficulties is None:
            difficulties = ['Easy', 'Medium', 'Hard']

        self._phobos = phobos

        # Build shipyard dummy ID maps from dummy_gen if available
        if self.dummy_gen:
            for ad in self.dummy_gen.absence_dummies:
                if len(ad.bld_ids) == 1:
                    self._shipyard_absence[ad.bld_ids[0]] = ad.section_id
            for pd in self.dummy_gen.dummy_blds:
                if len(pd.bld_ids) == 1:
                    self._shipyard_presence[pd.bld_ids[0]] = pd.section_id

        buildings = self.parser.ini.list_from_section('BuildingTypes')

        for faction in factions:
            for difficulty in difficulties:
                self._gen_enemy_no_defenses(faction, difficulty, buildings)
                self._gen_enemy_no_aa(faction, difficulty, buildings)
                self._gen_enemy_aa_massive_aerial(faction, difficulty, buildings)
                self._gen_aerial_transport_drop(faction, difficulty, buildings)
                self._gen_self_no_defenses(faction, difficulty, buildings)
                self._gen_self_no_aa(faction, difficulty, buildings)
                self._gen_mcv_rebuild(faction, difficulty, buildings)
                self._gen_enemy_no_cy_attack(faction, difficulty, buildings)
                self._gen_enemy_no_power(faction, difficulty)
                # Naval strategic triggers
                self._gen_naval_no_enemy_shipyard(faction, difficulty)
                self._gen_naval_fight_for_dominance(faction, difficulty)
                self._gen_naval_dominance_pressure(faction, difficulty)

    # ------------------------------------------------------------------
    # Scenario generators
    # ------------------------------------------------------------------

    def _gen_enemy_no_defenses(self, faction: str, difficulty: str,
                                buildings: list):
        """
        Enemy has no defenses → aggressive direct rush.
        Uses the master DMPRNODEF dummy — faction-neutral, fires when
        ANY enemy player has no defenses regardless of building faction.
        (e.g. NOD player with captured GDI Pillboxes still HAS defenses)
        """
        if not self.defense_gen:
            return
        cond_obj = 'DMPRNODEF'
        if not self._dummy_exists(cond_obj):
            return

        tf = self._get_or_create_taskforce(
            faction, 'Rush', 'RUSH', difficulty,
            label=f"[{faction.upper()}][RUSH][RUSH_WAVE][DIRECT_ATTACK]"
            f"[LAND][{difficulty.upper()}]")
        if not tf:
            return

        script_id = self._get_or_create_script(
            'direct_rush', make_direct_attack_actions())
        team = self._create_team(
            tf, script_id, faction, 'Rush', 'RUSH', difficulty,
            overrides={
                'Priority':                  '5',
                'Max':                       '2',
                'Suicide':                   'yes',
                'Aggressive':                'yes',
                'AvoidThreats':              'no',
                'AreTeamMembersRecruitable': 'no',
            })
        trigger = self._create_trigger(
            name       = f"{faction.upper()} Rush No Def {difficulty}",
            team1_id   = team.team_id,
            faction    = faction,
            cond_type  = COND_ENEMY_OWNS,
            cond_obj   = cond_obj,
            comparator = OWN_AT_LEAST_1,
            weights    = (500.0, 100.0, 500.0),
            difficulty = difficulty,
        )
        self.taskforces.append(tf)
        self.teams.append(team)
        self.triggers.append(trigger)
        self.scenarios.append(StrategicScenario(
            name='enemy_no_defenses', faction=faction,
            cond_type=COND_ENEMY_OWNS, cond_obj=cond_obj,
            comparator=OWN_AT_LEAST_1, weights=(500,100,500),
            difficulty=difficulty, taskforce_id=tf.tf_id,
            script_id=script_id, team_id=team.team_id,
            trigger_id=trigger.trigger_id,
            notes="Enemy has no defenses (any faction) — rush"))

    def _gen_enemy_no_aa(self, faction: str, difficulty: str,
                          buildings: list):
        """
        Enemy has no AA → aerial spam.
        Uses master DMPRNODEFAA — faction-neutral, fires when any enemy
        player has no AA defenses regardless of building faction.
        """
        if not self.defense_gen:
            return
        cond_obj = 'DMPRNODEFAA'
        if not self._dummy_exists(cond_obj):
            return

        from .taskforce_generator import MOBILITY_AERIAL, MOBILITY_AIRCRAFT
        AERIAL_MOBILITIES = {MOBILITY_AERIAL, MOBILITY_AIRCRAFT,
                             'Jumpjet', 'Aircraft', 'Helicopter'}

        aerial_units = [
            r for r in self.parser.buildable
            if r.faction == faction
            and r.mobility in AERIAL_MOBILITIES
            and r.grade >= 0
            and not r.is_utility
        ]
        if not aerial_units:
            return

        tf = self._get_or_create_taskforce(
            faction, 'Late', 'AERIAL', difficulty,
            label=f"[{faction.upper()}][LATE][AERIAL][DIRECT_ATTACK]"
            f"[{difficulty.upper()}]")
        if not tf:
            return

        script_id = self._get_or_create_script(
            'aerial_rush', make_aerial_rush_actions())
        team = self._create_team(
            tf, script_id, faction, 'Mid', 'AERIAL', difficulty,
            overrides={
                'Priority':   '5',
                'Max':        '2',
                'Aggressive': 'yes',
                'Suicide':    'no',
                'AreTeamMembersRecruitable': 'no',
            })
        trigger = self._create_trigger(
            name       = f"{faction.upper()} Aerial No AA {difficulty}",
            team1_id   = team.team_id,
            faction    = faction,
            cond_type  = COND_ENEMY_OWNS,
            cond_obj   = cond_obj,
            comparator = OWN_AT_LEAST_1,
            weights    = (500.0, 100.0, 500.0),
            difficulty = difficulty,
        )
        self.taskforces.append(tf)
        self.teams.append(team)
        self.triggers.append(trigger)
        self.scenarios.append(StrategicScenario(
            name='enemy_no_aa', faction=faction,
            cond_type=COND_ENEMY_OWNS, cond_obj=cond_obj,
            comparator=OWN_AT_LEAST_1, weights=(500,100,500),
            difficulty=difficulty, taskforce_id=tf.tf_id,
            script_id=script_id, team_id=team.team_id,
            trigger_id=trigger.trigger_id,
            notes="Enemy has no AA (any faction) — aerial spam"))

    def _gen_self_no_defenses(self, faction: str, difficulty: str,
                               buildings: list):
        """AI has no defenses → small emergency patrol guard teams."""
        cond_obj = 'DMPRNODEF'
        if not self._dummy_exists(cond_obj):
            return

        # Build patrol route between faction's key buildings
        patrol_stops = self._faction_patrol_stops(faction, buildings)
        if not patrol_stops:
            return

        # Small generalist taskforce (3-4 units)
        tf = self._get_or_create_small_guard_tf(faction, difficulty)
        if not tf:
            return

        # Patrol script — faction-specific because building indices differ
        patrol_actions = make_patrol_script_actions(
            patrol_stops, self.patrol_ticks)
        script_key    = f"patrol_{faction}"
        script_id     = self._get_or_create_script(
            script_key, patrol_actions,
            name=f"{faction.upper()} Emergency Patrol")

        team = self._create_team(
            tf, script_id, faction, 'Early', 'GUARD', difficulty,
            overrides={
                'Priority':                  '10',
                'Max':                       '1',
                'Suicide':                   'no',
                'Aggressive':                'yes',
                'Whiner':                    'yes',
                'IsBaseDefense':             'no',  # don't fill defense slots
                'AreTeamMembersRecruitable': 'yes',
                'AvoidThreats':              'no',
            })

        trigger = self._create_trigger(
            name       = f"{faction.upper()} Self No Def Guard {difficulty}",
            team1_id   = team.team_id,
            faction    = faction,
            cond_type  = COND_AI_OWNS,         # CondType=1: AI itself owns
            cond_obj   = cond_obj,
            comparator = OWN_AT_LEAST_1,
            weights    = (1000.0, 500.0, 1000.0),
            difficulty = difficulty,
            is_base_def = True,
        )
        self.scenarios.append(StrategicScenario(
            name='self_no_defenses', faction=faction,
            cond_type=COND_AI_OWNS, cond_obj=cond_obj,
            comparator=OWN_AT_LEAST_1, weights=(1000,500,1000),
            difficulty=difficulty, taskforce_id=tf.tf_id,
            script_id=script_id, team_id=team.team_id,
            trigger_id=trigger.trigger_id, is_base_def=True,
            notes="AI has no defenses — emergency patrol guard"))

    def _gen_self_no_aa(self, faction: str, difficulty: str,
                         buildings: list):
        """AI has no AA → small emergency AA patrol teams."""
        cond_obj = 'DMPRNODEFAA'
        if not self._dummy_exists(cond_obj):
            return

        # Primary AA units first (dedicated AA role)
        aa_units = [
            r for r in self.parser.buildable
            if r.faction == faction and 'AA' in r.roles
            and not r.is_utility and r.grade >= 0
        ]
        # Fall back to units with secondary AA capability (e.g. HTK)
        if not aa_units:
            aa_units = [
                r for r in self.parser.buildable
                if r.faction == faction and 'AA_Secondary' in r.roles
                and not r.is_utility and r.grade >= 0
            ]
        if not aa_units:
            return

        # Small AA taskforce
        tf = self._get_or_create_small_guard_tf(
            faction, difficulty, aa_only=True)
        if not tf:
            return

        patrol_stops = self._faction_patrol_stops(faction, buildings)
        patrol_actions = make_patrol_script_actions(
            patrol_stops, self.patrol_ticks)
        script_key = f"patrol_aa_{faction}"
        script_id  = self._get_or_create_script(
            script_key, patrol_actions,
            name=f"{faction.upper()} Emergency AA Patrol")

        team = self._create_team(
            tf, script_id, faction, 'Early', 'GUARD_AA', difficulty,
            overrides={
                'Priority':                  '10',
                'Max':                       '1',
                'Suicide':                   'no',
                'Aggressive':                'yes',
                'Whiner':                    'yes',
                'IsBaseDefense':             'no',
                'AreTeamMembersRecruitable': 'yes',
            })

        trigger = self._create_trigger(
            name       = f"{faction.upper()} Self No AA Guard {difficulty}",
            team1_id   = team.team_id,
            faction    = faction,
            cond_type  = COND_AI_OWNS,
            cond_obj   = cond_obj,
            comparator = OWN_AT_LEAST_1,
            weights    = (1000.0, 500.0, 1000.0),
            difficulty = difficulty,
            is_base_def = True,
        )
        self.scenarios.append(StrategicScenario(
            name='self_no_aa', faction=faction,
            cond_type=COND_AI_OWNS, cond_obj=cond_obj,
            comparator=OWN_AT_LEAST_1, weights=(1000,500,1000),
            difficulty=difficulty, taskforce_id=tf.tf_id,
            script_id=script_id, team_id=team.team_id,
            trigger_id=trigger.trigger_id, is_base_def=True,
            notes="AI has no AA — emergency AA patrol"))

    def _gen_mcv_rebuild(self, faction: str, difficulty: str,
                          buildings: list):
        """AI has no CY but has service depot and warfactory → build MCV."""
        if not self.dummy_gen:
            return

        # Need mixed dummy: CY absent + depot present + warfactory present
        bld_list = self.parser.ini.list_from_section('BuildingTypes')
        cy_id     = self._faction_building(faction, 'cy',     bld_list)
        depot_id  = self._faction_building(faction, 'depot',  bld_list)
        wfac_id   = self._faction_building(faction, 'wfac',   bld_list)
        if not all([cy_id, depot_id, wfac_id]):
            return

        # Request mixed dummy from dummy_gen: depot+warfactory present, CY absent
        self.dummy_gen.request_mixed_combo(
            present_ids=[depot_id, wfac_id],
            absent_ids =[cy_id])
        cond_obj = self.dummy_gen._mixed_section_id(
            tuple(sorted([bld_list.index(depot_id),
                           bld_list.index(wfac_id)])),
            (bld_list.index(cy_id),))

        mcv_uid = MCV_UNITS.get(faction)
        if not mcv_uid or mcv_uid not in self.parser.units:
            return

        # Check if vanilla MCV team exists; if so reuse it
        from .rulesmd_parser import IniParser
        aimd_ini   = IniParser(str(Path(self.parser.ini.path)))
        vanilla_id = VANILLA_MCV_TEAMS.get(faction)
        team_id    = vanilla_id  # reuse vanilla if it exists

        if not vanilla_id:
            # Generate new MCV team
            tf = self._make_single_unit_tf(mcv_uid, faction)
            if not tf:
                return
            team = self._create_team(
                tf, VANILLA_MCV_SCRIPT_ID, faction,
                'Late', 'MCV', difficulty,
                overrides={'Priority': '5', 'Max': '1',
                           'Suicide': 'no', 'Autocreate': 'yes'})
            team_id = team.team_id

        mcv_rec = self.parser.units.get(mcv_uid)
        mcv_tl  = 1
        if mcv_rec:
            s = self.parser.ini.section(mcv_uid)
            if s:
                try: mcv_tl = max(1, int(s.get('techlevel','1') or '1'))
                except ValueError: mcv_tl = 1

        trigger = self._create_trigger(
            name       = f"{faction.upper()} MCV Rebuild {difficulty}",
            team1_id   = team_id,
            faction    = faction,
            cond_type  = COND_AI_OWNS,
            cond_obj   = cond_obj,
            comparator = OWN_AT_LEAST_1,
            weights    = (5000.0, 5000.0, 5000.0),
            difficulty = difficulty,
            tech_level = mcv_tl,
        )
        self.scenarios.append(StrategicScenario(
            name='mcv_rebuild', faction=faction,
            cond_type=COND_AI_OWNS, cond_obj=cond_obj,
            comparator=OWN_AT_LEAST_1, weights=(5000,5000,5000),
            difficulty=difficulty, team_id=team_id,
            trigger_id=trigger.trigger_id,
            notes="AI has no CY but has depot+warfactory — build MCV"))

    def _gen_enemy_aa_massive_aerial(self, faction: str, difficulty: str,
                                      buildings: list):
        """Enemy HAS AA defenses → massive aerial waves to overwhelm.
        Condition: enemy owns DMPRNODEFAA_*_A (batch dummy alive = AA exists).
        Uses EndGame phase for ~30 ZEP / ~96 JUMPJET at Hard difficulty.
        """
        if not self.defense_gen:
            return

        # Find the AA batch dummy for this faction's defenses
        # Format: DMPRNODEFAA_{FACTION_SHORT}_A
        cond_obj = None
        for bld in self.defense_gen.buildings:
            sid = bld['section_id']
            # Match DMPRNODEFAA_XXX_A (any faction's batch A)
            if (sid.startswith('DMPRNODEFAA_') and
                    sid.endswith('_A') and
                    'SW' not in sid and 'KSW' not in sid):
                cond_obj = sid
                break
        if not cond_obj:
            return

        from .taskforce_generator import MOBILITY_AERIAL, MOBILITY_AIRCRAFT
        AERIAL_MOBS = {MOBILITY_AERIAL, MOBILITY_AIRCRAFT,
                       'Jumpjet', 'Aircraft', 'Helicopter'}
        aerial_units = [
            r for r in self.parser.buildable
            if r.faction == faction
            and r.mobility in AERIAL_MOBS
            and r.grade >= 0
            and not r.is_utility
            and not r.is_transport
        ]
        if not aerial_units:
            return

        # EndGame phase = massive wave (~30 ZEP / ~96 JUMPJET at Hard)
        tf = self._get_or_create_taskforce(
            faction, 'EndGame', 'AERIAL', difficulty,
            label=f"[{faction.upper()}][ENDGAME][AERIAL][MASSIVE_WAVE]"
            f"[{difficulty.upper()}]")
        if not tf:
            return

        script_id = self._get_or_create_script(
            'massive_aerial', make_massive_aerial_actions())

        team = self._create_team(
            tf, script_id, faction, 'EndGame', 'AERIAL_MASSIVE', difficulty,
            overrides={
                'Priority':                  '7',
                'Max':                       '1',   # one massive wave at a time
                'Aggressive':                'yes',
                'Suicide':                   'no',
                'AreTeamMembersRecruitable': 'no',
                'AvoidThreats':              'no',
            })

        trigger = self._create_trigger(
            name       = f"{faction.upper()} Massive Aerial Has AA {difficulty}",
            team1_id   = team.team_id,
            faction    = faction,
            cond_type  = COND_ENEMY_OWNS,
            cond_obj   = cond_obj,
            comparator = OWN_AT_LEAST_1,
            weights    = (300.0, 50.0, 300.0),
            difficulty = difficulty,
        )
        self.scenarios.append(StrategicScenario(
            name='enemy_aa_massive_aerial', faction=faction,
            cond_type=COND_ENEMY_OWNS, cond_obj=cond_obj,
            comparator=OWN_AT_LEAST_1, weights=(300, 50, 300),
            difficulty=difficulty, taskforce_id=tf.tf_id,
            script_id=script_id, team_id=team.team_id,
            trigger_id=trigger.trigger_id,
            notes=f"Enemy has AA ({cond_obj}) — send massive aerial wave"))

    def _gen_aerial_transport_drop(self, faction: str, difficulty: str,
                                    buildings: list):
        """Enemy has no AA → aerial transport drop.
        Uses Phobos 10103,0 (multi-transport) if available, else 14,0 fallback.
        Only generates for factions that have aerial transports.

        Payload selection (anti-building priority):
          1. E1 (GI) — best building DPS, fill most slots
          2. 1x TANY — C4 bonus (BuildLimit=1 so only one)
          JUMPJET excluded: Jumpjet mobility cannot board transports.
          Only Land/Amphibious/Hover/Tunnel infantry can board.

        Weight adaptation (Phobos only):
          14002,50  pre-penalize each attempt
          14000,100 reward per kill after landing
          Net: +50 per kill, -50 baseline → AI backs off if drops fail repeatedly.
        """
        # Boarding-capable mobility types
        BOARDING_OK = {'Land', 'Amphibious', 'Tunnel', 'Hover'}

        from .taskforce_generator import MOBILITY_AERIAL, MOBILITY_AIRCRAFT
        AERIAL_MOBS = {MOBILITY_AERIAL, MOBILITY_AIRCRAFT,
                       'Jumpjet', 'Aircraft', 'Helicopter'}

        # Find aerial transports for this faction
        transports = [
            r for r in self.parser.buildable
            if r.faction == faction
            and r.mobility in AERIAL_MOBS
            and r.is_transport
        ]
        if not transports:
            return

        if not self._dummy_exists('DMPRNODEFAA'):
            return
        cond_obj = 'DMPRNODEFAA'

        ini        = self.parser.ini
        transport  = max(transports, key=lambda r: r.grade)
        passengers = int(ini.section(transport.unit_id).get('passengers', '0'))
        size_limit = int(ini.section(transport.unit_id).get('sizelimit', '99'))

        # Rank boarding infantry by anti-building score
        # Score = dps_building + c4*0.5 (C4 bonus weighted lower than raw DPS)
        payload_pool = [
            r for r in self.parser.buildable
            if r.faction == faction
            and r.category == 'Infantry'
            and r.mobility in BOARDING_OK          # must be able to board
            and not r.is_utility
            and r.grade >= 0
            and int(ini.section(r.unit_id).get('size', '1')) <= size_limit
        ]
        if not payload_pool:
            return

        def anti_building_score(rec):
            """
            Score infantry for anti-building effectiveness.
            C4=yes is dominant — instant kill on any building.
            Raw building DPS is secondary.
            BuildLimit=1 units (TANY) handled separately.
            """
            gb       = rec.grade_breakdown or {}
            c4_bonus = 10.0 if rec.has_c4 else 0.0   # C4 dominates
            bld_dps  = gb.get('dps_building', 0)
            return c4_bonus + bld_dps

        payload_pool.sort(key=anti_building_score, reverse=True)

        # Build payload: fill with best unit, respect BuildLimit=1
        payload_units: list[tuple[int, str]] = []
        slots_remaining = passengers
        for r in payload_pool:
            if slots_remaining <= 0:
                break
            build_limit = int(ini.section(r.unit_id).get('buildlimit', '0'))
            count       = 1 if build_limit == 1 else slots_remaining
            payload_units.append((count, r.unit_id))
            slots_remaining -= count

        if not payload_units:
            return

        # Build TF: 1 transport + payload infantry
        tf_units = [(1, transport.unit_id)] + payload_units
        tf = self._make_multi_unit_tf(tf_units, faction, 'Rush')
        if not tf:
            return

        # 58-anchor at the transport's own factory — mandatory for flyers
        # (see make_transport_drop_actions docstring). No anchor building →
        # no safe boarding → skip the wave.
        fac = getattr(transport, 'factory_anchor', None)
        fac_idx = -1
        if fac:
            try:
                fac_idx = list(ini.list_from_section('BuildingTypes')).index(fac)
            except ValueError:
                fac_idx = -1
        if fac_idx < 0:
            return
        from .procedural_scripts import BWP_NEAREST
        anchor_arg = BWP_NEAREST + fac_idx

        phobos     = getattr(self, '_phobos', True)
        script_key = (f"transport_drop_{'phobos' if phobos else 'vanilla'}"
                      f"_a{fac_idx}")
        script_id  = self._get_or_create_script(
            script_key,
            make_transport_drop_actions(phobos, anchor_arg),
            name=f"Aerial Transport Drop {'Phobos' if phobos else 'Vanilla'}")

        payload_desc = ', '.join(
            f"{c}x {self.parser.units[uid].name if uid in self.parser.units else uid}"
            for c, uid in payload_units)

        self.taskforces.append(tf)

        team = self._create_team(
            tf, script_id, faction, 'Rush', 'AERIAL_TRANSPORT', difficulty,
            overrides={
                'Priority':                  '6',
                'Max':                       '3',
                'Aggressive':                'yes',
                'Suicide':                   'no',
                'AreTeamMembersRecruitable': 'no',
                # pairs with the 8,2 unload: the flyer goes home
                'TransportsReturnOnUnload':  'yes',
            })
        trigger = self._create_trigger(
            name       = f"{faction.upper()} Transport Drop No AA {difficulty}",
            team1_id   = team.team_id,
            faction    = faction,
            cond_type  = COND_ENEMY_OWNS,
            cond_obj   = cond_obj,
            comparator = OWN_AT_LEAST_1,
            weights    = (500.0, 100.0, 500.0),
            difficulty = difficulty,
        )
        self.teams.append(team)
        self.triggers.append(trigger)
        self.scenarios.append(StrategicScenario(
            name='aerial_transport_drop', faction=faction,
            cond_type=COND_ENEMY_OWNS, cond_obj=cond_obj,
            comparator=OWN_AT_LEAST_1, weights=(500, 100, 500),
            difficulty=difficulty, taskforce_id=tf.tf_id,
            script_id=script_id, team_id=team.team_id,
            trigger_id=trigger.trigger_id,
            notes=f"No AA — {transport.name} drops [{payload_desc}]"))

    def _make_multi_unit_tf(self, units: list[tuple[int, str]],
                             faction: str,
                             phase: str) -> 'TaskforceEntry | None':
        """Create a taskforce with specific units (count, unit_id) pairs."""
        from .taskforce_generator import TaskforceEntry as TFE, make_vanilla_name
        existing = self.tf_reg.find_duplicate(units)
        if existing:
            return TFE(tf_id=existing,
                       name=f"Transport Drop",
                       label_comment='',
                       units=units,
                       faction=faction, phase=phase,
                       role='AERIAL_TRANSPORT', difficulty='all')
        tf_id = self.tf_reg.generate()
        self.tf_reg.register_composition(tf_id, units)
        name  = make_vanilla_name(units, self.parser, self.tf_reg)
        entry = TFE(tf_id=tf_id, name=name,
                    label_comment=f"; [{faction.upper()}][{phase.upper()}][AERIAL_TRANSPORT]",
                    units=units, faction=faction,
                    phase=phase, role='AERIAL_TRANSPORT', difficulty='all')
        self.taskforces.append(entry)
        return entry

    def _gen_enemy_no_cy_attack(self, faction: str, difficulty: str,
                                 buildings: list):
        """
        Enemy has warfactory but no CY → attack warfactory NOW.
        Category-neutral: uses all-CY-absent combo as condition.
        Generates 3 variants — one per possible warfactory type — since
        a NOD player with a captured GDI warfactory still has production.

        CondObj = DMPR[wfac]nX[CY0]nX[CY1]nX[CY2]
        e.g. DMPR007nX002nX025nX301 = GAWEAP present + no CY at all
        """
        if not self.dummy_gen:
            return

        bld_list = self.parser.ini.list_from_section('BuildingTypes')
        cy_ids   = [b for b in ['GACNST','NACNST','YACNST'] if b in bld_list]
        wfac_ids = [b for b in ['GAWEAP','NAWEAP','YAWEAP'] if b in bld_list]
        if not cy_ids or not wfac_ids:
            return

        tf = self._get_or_create_taskforce(
            faction, 'Late', 'FACTORY_KILLER', difficulty,
            label=f"[{faction.upper()}][LATE][FACTORY_KILLER]"
            f"[DIRECT_ATTACK][LAND][{difficulty.upper()}]")
        if not tf:
            return

        script_id = self._get_or_create_script(
            'factory_attack', make_factory_attack_actions())

        for wfac_id in wfac_ids:
            # Mixed dummy: this warfactory present + ALL CYs absent
            self.dummy_gen.request_mixed_combo(
                present_ids=[wfac_id],
                absent_ids=cy_ids)
            cond_obj = self.dummy_gen._mixed_section_id(
                (bld_list.index(wfac_id),),
                tuple(sorted(bld_list.index(c) for c in cy_ids)))

            team = self._create_team(
                tf, script_id, faction, 'Late', 'FACTORY_KILLER', difficulty,
                overrides={
                    'Priority':   '5',
                    'Max':        '1',
                    'Aggressive': 'yes',
                    'Suicide':    'no',
                })
            trigger = self._create_trigger(
                name       = f"{faction.upper()} No CY Has {wfac_id} {difficulty}",
                team1_id   = team.team_id,
                faction    = faction,
                cond_type  = COND_ENEMY_OWNS,
                cond_obj   = cond_obj,
                comparator = OWN_AT_LEAST_1,
                weights    = (500.0, 200.0, 500.0),
                difficulty = difficulty,
            )
            self.taskforces.append(tf)
            self.teams.append(team)
            self.triggers.append(trigger)
            self.scenarios.append(StrategicScenario(
                name=f'enemy_no_cy_{wfac_id}', faction=faction,
                cond_type=COND_ENEMY_OWNS, cond_obj=cond_obj,
                comparator=OWN_AT_LEAST_1, weights=(500,200,500),
                difficulty=difficulty, taskforce_id=tf.tf_id,
                script_id=script_id, team_id=team.team_id,
                trigger_id=trigger.trigger_id,
                notes=f"Enemy has {wfac_id} but no CY (any faction)"))

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _dummy_exists(self, dummy_id: str) -> bool:
        """
        Check if a dummy building ID has been or will be generated.
        Defense gen dummies are checked against generated buildings.
        Regular dummy gen checks are optimistic — if the gen is configured
        to produce them (correct mode), assume they'll exist after generate_all().
        """
        if self.defense_gen:
            if any(b['section_id'] == dummy_id
                   for b in self.defense_gen.buildings):
                return True
        if self.dummy_gen:
            # Check already-generated entries
            if any(d.section_id == dummy_id
                   for d in self.dummy_gen.absence_dummies):
                return True
            if any(d.section_id == dummy_id
                   for d in self.dummy_gen.dummy_blds):
                return True
            # For mixed dummies, they'll be generated on demand
            if any(m.get('section_id') == dummy_id
                   for m in self.dummy_gen._mixed_dummies):
                return True
        return False

    def _faction_patrol_stops(self, faction: str,
                               buildings: list) -> list[int]:
        """Return building type indices for patrol route."""
        keys = {
            'gdi':       ['GAWEAP', 'GAPILE', 'GACNST'],
            'nod':       ['NAWEAP', 'NAHAND', 'NACNST'],
            'thirdside': ['YAWEAP', 'YABRCK', 'YACNST'],
        }
        stops = []
        for bid in keys.get(faction, []):
            if bid in buildings:
                stops.append(buildings.index(bid))
        return stops

    def _faction_building(self, faction: str, role: str,
                           buildings: list) -> str | None:
        """Get a specific building ID for a faction by role."""
        mapping = {
            'gdi':       {'cy':'GACNST','depot':'GADEPT','wfac':'GAWEAP'},
            'nod':       {'cy':'NACNST','depot':'NADEPT','wfac':'NAWEAP'},
            'thirdside': {'cy':'YACNST','depot':'YADEPT','wfac':'YAWEAP'},
        }
        bid = mapping.get(faction, {}).get(role)
        return bid if bid and bid in buildings else None

    def _all_enemy_faction_buildings(self, role: str,
                                      buildings: list) -> list[tuple[str, str]]:
        """
        Return (enemy_faction, building_id) pairs for all enemy factions
        that have a building of the given role.
        Used to generate one trigger variant per enemy faction.
        Covers mirror matches — GDI vs GDI needs GACNST variant too.
        """
        all_factions = ['gdi', 'nod', 'thirdside']
        result = []
        for enemy_fac in all_factions:
            bid = self._faction_building(enemy_fac, role, buildings)
            if bid:
                result.append((enemy_fac, bid))
        return result

    def _category_absence_id(self, category: str) -> str | None:
        """
        Return the section ID for the category-level absence dummy —
        present when ALL buildings of this category are absent simultaneously.
        Faction-neutral: fires regardless of which faction owns what.
        Cached to avoid repeated BuildingCategorizer scans.
        """
        if not self.dummy_gen:
            return None
        cache_key = f'_cat_abs_{category}'
        if hasattr(self, cache_key):
            return getattr(self, cache_key)
        from .building_categorizer import BuildingCategorizer
        bc = BuildingCategorizer(str(self.parser.ini.path))
        buildings = bc.scan()
        cat_bids = [b.bid for b in buildings if b.category == category]
        result = None
        if cat_bids:
            for ad in self.dummy_gen.absence_dummies:
                if set(ad.bld_ids) == set(cat_bids):
                    result = ad.section_id
                    break
        setattr(self, cache_key, result)
        return result

    def _category_presence_id(self, category: str) -> str | None:
        """
        Return section ID for the category-level presence dummy.
        Cached to avoid repeated BuildingCategorizer scans.
        """
        if not self.dummy_gen:
            return None
        cache_key = f'_cat_pres_{category}'
        if hasattr(self, cache_key):
            return getattr(self, cache_key)
        from .building_categorizer import BuildingCategorizer
        bc = BuildingCategorizer(str(self.parser.ini.path))
        buildings = bc.scan()
        cat_bids = [b.bid for b in buildings if b.category == category]
        result = None
        if cat_bids:
            for pd in self.dummy_gen.dummy_blds:
                if set(pd.bld_ids) == set(cat_bids):
                    result = pd.section_id
                    break
        setattr(self, cache_key, result)
        return result

    def _enemy_cy_ids(self, buildings: list) -> list[str]:
        """Return all CY building IDs present in this mod."""
        return [bid for bid in ['GACNST', 'NACNST', 'YACNST']
                if bid in buildings]

    def _enemy_cy_id(self, buildings: list) -> str | None:
        """Get any construction yard ID (for backward compat)."""
        ids = self._enemy_cy_ids(buildings)
        return ids[0] if ids else None

    def _get_or_create_taskforce(self, faction: str, phase: str,
                                  role: str, difficulty: str,
                                  label: str) -> TaskforceEntry | None:
        """Get existing or generate new taskforce for this role.
        Each difficulty generates a separate taskforce (different size/composition).
        """
        specs = [TaskforceSpec(
            faction=faction, phase=phase, role=role,
            difficulty=difficulty, count=1)]
        batch = self.tf_gen.generate_batch(GeneratorConfig(specs))
        if batch:
            self.taskforces.extend(batch)
            return batch[0]
        # If deduped (same composition exists), try generating a variant
        # by requesting a small batch and taking any result
        specs_multi = [TaskforceSpec(
            faction=faction, phase=phase, role=role,
            difficulty=difficulty, count=3)]
        batch2 = self.tf_gen.generate_batch(GeneratorConfig(specs_multi))
        new_ones = [tf for tf in batch2 if tf not in self.taskforces]
        if new_ones:
            self.taskforces.extend(new_ones)
            return new_ones[0]
        return None

    def _get_or_create_small_guard_tf(
            self, faction: str, difficulty: str,
            aa_only: bool = False) -> TaskforceEntry | None:
        """Create a small guard taskforce."""
        if aa_only:
            # Use Mid phase — covers more AA units than Early
            # (SCHP is Late but HTK is Mid; Mid includes Early units too)
            for role, phase in [('AA', 'Mid'), ('AA', 'Late'), ('GENERAL_ATTACK', 'Mid')]:
                specs = [TaskforceSpec(
                    faction=faction, phase=phase, role=role,
                    difficulty=difficulty, count=1)]
                batch = self.tf_gen.generate_batch(GeneratorConfig(specs))
                if batch:
                    self.taskforces.extend(batch)
                    return batch[0]
            return None
        else:
            specs = [TaskforceSpec(
                faction=faction, phase='Early', role='GENERAL_ATTACK',
                difficulty=difficulty, count=1)]
            batch = self.tf_gen.generate_batch(GeneratorConfig(specs))
            if batch:
                self.taskforces.extend(batch)
                return batch[0]
            return None

    def _unit_tech_level(self, *unit_ids: str) -> int:
        """Return max TechLevel across given units, clamped to at least 1."""
        max_tl = 0
        for uid in unit_ids:
            if not uid: continue
            s = self.parser.ini.section(uid)
            if not s: continue
            try: tl = int(s.get('techlevel','1') or '1')
            except ValueError: tl = 1
            if tl > max_tl: max_tl = tl
        return max(max_tl, 1)

    def _make_single_unit_tf(self, unit_id: str,
                              faction: str) -> TaskforceEntry | None:
        """Create a single-unit taskforce (e.g. MCV)."""
        units_list = [(1, unit_id)]
        existing   = self.tf_reg.find_duplicate(units_list)
        if existing:
            # Return a stub entry referencing existing TF
            from .taskforce_generator import TaskforceEntry as TFE
            return TFE(tf_id=existing, name=unit_id,
                       label_comment='', units=units_list,
                       faction=faction, phase='Late',
                       role='MCV', difficulty='all')
        tf_id = self.tf_reg.generate()
        self.tf_reg.register_composition(tf_id, units_list)
        name  = make_vanilla_name(units_list, self.parser, self.tf_reg)
        from .taskforce_generator import TaskforceEntry as TFE
        entry = TFE(tf_id=tf_id, name=name,
                    label_comment=f"; [{faction.upper()}][LATE][MCV]",
                    units=units_list, faction=faction,
                    phase='Late', role='MCV', difficulty='all')
        self.taskforces.append(entry)
        return entry

    def _get_or_create_script(self, key: str,
                               actions: list[tuple[int,int]],
                               name: str | None = None) -> str:
        """Get or create script by action fingerprint."""
        fp = tuple(actions)
        if fp in self._script_cache:
            return self._script_cache[fp]

        # Check existing aimd scripts
        existing = self.sc_reg.find_duplicate(list(actions))
        if existing:
            self._script_cache[fp] = existing
            return existing

        # Create new
        script_id = self.sc_reg.generate_id()
        sc_name   = self.sc_reg.unique_name(
            name or key.replace('_', ' ').title())
        self.sc_reg.register(script_id, list(actions), sc_name)

        entry = ScriptEntry(
            script_id     = script_id,
            name          = sc_name,
            label_comment = f"; [SCRIPT][STRATEGIC][{key.upper()}]",
            actions       = list(actions),
            phobos        = False,
            is_duplicate  = False,
            taskforce_ids = [],
        )
        self.scripts.append(entry)
        self._script_cache[fp] = script_id
        return script_id

    def _create_team(self, tf: TaskforceEntry, script_id: str,
                      faction: str, phase: str, role: str,
                      difficulty: str,
                      overrides: dict | None = None) -> TeamEntry:
        """Create a TeamType entry."""
        from .team_generator import build_team_flags, FIXED_FLAGS
        flags = dict(FIXED_FLAGS)
        flags.update(build_team_flags(role, self.rng))
        if overrides:
            flags.update(overrides)

        faction_name = {'gdi':'GDI','nod':'Soviet',
                        'thirdside':'Yuri'}.get(faction, faction.upper())
        raw_name = f"{faction_name} {phase} {role.replace('_',' ').title()} {difficulty}"
        name     = self.team_reg.unique_name(raw_name)
        team_id  = self.team_reg.generate()
        label    = f"; [{faction.upper()}][{phase.upper()}][{role}][{difficulty.upper()}][STRATEGIC]"

        entry = TeamEntry(
            team_id       = team_id,
            name          = name,
            label_comment = label,
            taskforce_id  = tf.tf_id,
            script_id     = script_id,
            flags         = flags,
            faction       = faction,
            phase         = phase,
            role          = role,
            difficulty    = difficulty,
        )
        self.teams.append(entry)
        return entry

    def _create_trigger(self, name: str, team1_id: str,
                         faction: str, cond_type: int,
                         cond_obj: str, comparator: str,
                         weights: tuple, difficulty: str,
                         team2_id: str = '<none>',
                         is_base_def: bool = False,
                         tech_level: int = 1) -> TriggerEntry:
        """Create an AITriggerType entry."""
        easy, normal, hard = {
            'Easy':   (1,0,0), 'Medium': (0,1,0),
            'Hard':   (0,0,1), 'all':    (1,1,1),
        }.get(difficulty, (0,0,1))

        side       = self.side_map.get(faction, 0)
        start_w, min_w, max_w = weights
        trig_name  = self.trig_reg.unique_name(name)
        trigger_id = self.trig_reg.generate()

        entry = TriggerEntry(
            trigger_id = trigger_id,
            name       = trig_name,
            team1_id   = team1_id,
            owner      = '<all>',
            tech_level = max(tech_level, 1),
            cond_type  = cond_type,
            cond_obj   = cond_obj,
            comparator = comparator,
            start_w    = float(start_w),
            min_w      = float(min_w),
            max_w      = float(max_w),
            skirmish   = 1,
            side       = side,
            team2_id   = team2_id,
            easy       = easy,
            normal     = normal,
            hard       = hard,
        )
        self.triggers.append(entry)
        return entry

    # ------------------------------------------------------------------
    # Write output
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Power infrastructure triggers
    # ------------------------------------------------------------------

    def _category_power_absence_id(self) -> str | None:
        """Return the all-power-plants-absent dummy section ID."""
        return self._category_absence_id('Power Plants')

    def _gen_enemy_no_power(self, faction: str, difficulty: str):
        """
        Enemy has no power plants → all Powered=yes defenses are offline.
        This includes Tesla Coils, Prism Towers, SAM sites, Flak Cannons,
        Grand Cannons, Sentry Guns, and Gattling Cannons.

        When this fires: the enemy's most dangerous defenses are dark.
        Use an aggressive rush — all static defenses that require power
        are essentially removed from combat.

        Condition: enemy owns DMPRX[pp0]nX[pp1]nX[pp2]...
        (all power plants absent = no power on the map for this faction)
        """
        absence_id = self._category_power_absence_id()
        if not absence_id:
            return  # requires Phobos dummies

        tf = self._get_or_create_taskforce(
            faction, 'Rush', 'RUSH', difficulty,
            label=f"[{faction.upper()}][RUSH][RUSH_WAVE][NO_POWER_RUSH]"
            f"[LAND][{difficulty.upper()}]")
        if not tf:
            return

        script_id = self._get_or_create_script(
            'direct_rush', make_direct_attack_actions())
        team = self._create_team(
            tf, script_id, faction, 'Rush', 'RUSH', difficulty,
            overrides={
                'Priority':   '7',   # high — enemy defenses are blind
                'Max':        '3',
                'Suicide':    'yes',
                'Aggressive': 'yes',
                'AvoidThreats': 'no',
                'AreTeamMembersRecruitable': 'no',
            })
        trigger = self._create_trigger(
            name       = f"{faction.upper()} Rush No Power {difficulty}",
            team1_id   = team.team_id,
            faction    = faction,
            cond_type  = COND_ENEMY_OWNS,
            cond_obj   = absence_id,
            comparator = OWN_AT_LEAST_1,
            weights    = (600.0, 150.0, 600.0),   # very high — golden window
            difficulty = difficulty,
        )
        self.taskforces.append(tf)
        self.teams.append(team)
        self.triggers.append(trigger)
        self.scenarios.append(StrategicScenario(
            name='enemy_no_power', faction=faction,
            cond_type=COND_ENEMY_OWNS, cond_obj=absence_id,
            comparator=OWN_AT_LEAST_1, weights=(600, 150, 600),
            difficulty=difficulty, taskforce_id=tf.tf_id,
            script_id=script_id, team_id=team.team_id,
            trigger_id=trigger.trigger_id,
            notes="Enemy has no power → all Powered=yes defenses offline"))

    # ------------------------------------------------------------------
    # Naval strategic scenarios
    # ------------------------------------------------------------------

    def _naval_bombard_units(self, faction: str) -> list:
        """Return naval bombarders for a faction, ranked by score."""
        return [r for r in self.parser.naval_bombarders
                if r.faction == faction]

    def _naval_units_for_faction(self, faction: str,
                                  role_filter=None) -> list:
        """Return naval buildable units for faction, optionally filtered."""
        units = [r for r in self.parser.buildable
                 if r.faction == faction and r.is_naval_unit
                 and r.grade >= 0 and not r.is_utility]
        if role_filter:
            units = [r for r in units
                     if any(role in r.roles for role in role_filter)]
        return units

    def _shipyard_ids_for_enemy(self, faction: str) -> list[str]:
        """
        Return ALL shipyard building IDs that could be an enemy's shipyard.
        Includes same-faction yards for mirror matches.
        """
        ini = self.parser.ini
        ai_sec = ini.section('AI')
        yard_str = ai_sec.get('buildnavalyard', '')
        return [x.strip() for x in yard_str.split(',') if x.strip()]

    def _any_shipyard_absence_id(self) -> str | None:
        """
        Return the section ID for the category-level shipyard absence dummy:
        present when ALL shipyards are absent simultaneously.
        This is faction-neutral — if a NOD player captures GAYARD, they
        still have a shipyard and this dummy will be killed.
        """
        return self._category_absence_id('Shipyards')

    def _any_shipyard_presence_ids(self) -> list[str]:
        """
        Return per-shipyard presence dummy IDs. Used for 'enemy HAS a shipyard'
        condition — need one trigger per shipyard type since any one being
        present means the enemy has naval production.
        """
        if not self.dummy_gen:
            return []
        yards = self._shipyard_ids_for_enemy('')
        return [self._shipyard_presence.get(y) for y in yards
                if y in self._shipyard_presence]

    def _gen_naval_no_enemy_shipyard(self, faction: str, difficulty: str):
        """
        Enemy has NO shipyard (any faction) -> bombard land targets.
        Uses category-level absence dummy: DMPRX[yard0]nX[yard1]nX[yard2]
        which fires when ALL shipyards are gone, regardless of faction.
        A NOD player with a captured GAYARD still has a shipyard.
        """
        bombarders = self._naval_bombard_units(faction)
        if not bombarders:
            return

        absence_id = self._any_shipyard_absence_id()
        if not absence_id:
            return  # requires Phobos dummies

        top   = bombarders[0]
        units = [(2, top.unit_id)]
        tf    = self._make_multi_unit_tf(units, faction, 'Late')
        if not tf:
            return
        self.taskforces.append(tf)

        actions   = make_naval_bombard_actions()
        script_id = self._get_or_create_script('naval_bombard', actions, 'Naval Bombard')

        team = self._create_team(tf, script_id, faction, 'Late', 'NAVAL_BOMBARD',
                                  difficulty, overrides={'Aggressive': 'yes'})
        trigger = self._create_trigger(
            name       = f"{faction.upper()} Naval Bombard No Shipyard {difficulty}",
            team1_id   = team.team_id,
            faction    = faction,
            cond_type  = COND_ENEMY_OWNS,
            cond_obj   = absence_id,
            comparator = OWN_AT_LEAST_1,
            weights    = (400.0, 100.0, 400.0),
            difficulty = difficulty,
        )
        self.teams.append(team)
        self.triggers.append(trigger)
        self.scenarios.append(StrategicScenario(
            name='naval_no_enemy_shipyard', faction=faction,
            cond_type=COND_ENEMY_OWNS, cond_obj=absence_id,
            comparator=OWN_AT_LEAST_1, weights=(400,100,400),
            difficulty=difficulty, taskforce_id=tf.tf_id,
            script_id=script_id, team_id=team.team_id,
            trigger_id=trigger.trigger_id,
            notes="Enemy has no shipyard (any faction) -> bombard land"))

    def _gen_naval_fight_for_dominance(self, faction: str, difficulty: str):
        """
        Scenario: Enemy HAS a shipyard -> fight for naval dominance.
        One trigger per possible enemy shipyard (covers mirror matches).
        Two simultaneous teams per trigger: one attacks the yard, one hunts naval.
        """
        naval_units = self._naval_units_for_faction(faction)
        if not naval_units:
            return

        surface = [r for r in naval_units if 'AN-Surface' in r.roles] or naval_units
        hunters = [r for r in naval_units if 'AN-Submerged' in r.roles] or naval_units
        all_yards = self._shipyard_ids_for_enemy(faction)

        # Build shared taskforces
        tf1 = self._make_multi_unit_tf([(2, surface[0].unit_id)], faction, 'Mid')
        tf2 = self._make_multi_unit_tf([(2, hunters[0].unit_id)], faction, 'Mid')
        if not tf1 or not tf2:
            return
        self.taskforces.extend([tf1, tf2])

        attack_sc_id = self._get_or_create_script('naval_yard_attack',
                                                   make_naval_shipyard_attack_actions(),
                                                   'Naval Shipyard Attack')
        hunt_sc_id   = self._get_or_create_script('naval_hunt',
                                                   make_naval_hunt_actions(),
                                                   'Naval Hunt')

        for yard_id in all_yards:
            presence_id = self._shipyard_presence.get(yard_id)
            if presence_id:
                cond_obj = presence_id
                weights  = (350.0, 100.0, 400.0)
                notes    = f"Enemy has {yard_id} -> fight for dominance"
            else:
                cond_obj = 'GACNST'
                weights  = (200.0, 75.0, 250.0)
                notes    = f"No Phobos: always-on fallback for {yard_id}"

            team1 = self._create_team(tf1, attack_sc_id, faction, 'Mid',
                                      'NAVAL_SHIPYARD_ATTACK', difficulty,
                                      overrides={'Aggressive': 'yes'})
            team2 = self._create_team(tf2, hunt_sc_id, faction, 'Mid',
                                      'NAVAL_HUNT', difficulty,
                                      overrides={'Aggressive': 'yes'})
            trigger = self._create_trigger(
                name       = f"{faction.upper()} Naval Fight {yard_id} {difficulty}",
                team1_id   = team1.team_id,
                faction    = faction,
                cond_type  = COND_ENEMY_OWNS,
                cond_obj   = cond_obj,
                comparator = OWN_AT_LEAST_1,
                weights    = weights,
                difficulty = difficulty,
                team2_id   = team2.team_id,
            )
            self.teams.extend([team1, team2])
            self.triggers.append(trigger)
            self.scenarios.append(StrategicScenario(
                name=f'naval_fight_for_dominance_{yard_id}', faction=faction,
                cond_type=COND_ENEMY_OWNS, cond_obj=cond_obj,
                comparator=OWN_AT_LEAST_1, weights=weights,
                difficulty=difficulty, taskforce_id=tf1.tf_id,
                script_id=attack_sc_id, team_id=team1.team_id,
                trigger_id=trigger.trigger_id, notes=notes))

    def _gen_naval_dominance_pressure(self, faction: str, difficulty: str):
        """
        Scenario: AI has naval dominance (ALL enemy shipyards gone) ->
        coastal pressure. Uses category-level absence dummy (neutral).
        A NOD player capturing GAYARD prevents this dummy from firing
        until that yard is also destroyed.
        """
        bombarders = self._naval_bombard_units(faction)
        surface    = self._naval_units_for_faction(faction, ['AN-Surface'])
        if not bombarders and not surface:
            return

        wave_units = []
        if bombarders:
            wave_units.append((2, bombarders[0].unit_id))
        if surface and (not bombarders or surface[0].unit_id != bombarders[0].unit_id):
            wave_units.append((1, surface[0].unit_id))
        if not wave_units:
            return

        absence_id = self._any_shipyard_absence_id()
        if not absence_id:
            return  # requires Phobos dummies

        tf = self._make_multi_unit_tf(wave_units, faction, 'Late')
        if not tf:
            return
        self.taskforces.append(tf)

        actions   = make_naval_bombard_actions()
        script_id = self._get_or_create_script('naval_coastal_pressure', actions,
                                               'Naval Coastal Pressure')
        team    = self._create_team(tf, script_id, faction, 'Late',
                                     'NAVAL_PRESSURE', difficulty,
                                     overrides={'Aggressive': 'yes'})
        trigger = self._create_trigger(
            name       = f"{faction.upper()} Naval Pressure {difficulty}",
            team1_id   = team.team_id,
            faction    = faction,
            cond_type  = COND_ENEMY_OWNS,
            cond_obj   = absence_id,
            comparator = OWN_AT_LEAST_1,
            weights    = (500.0, 150.0, 600.0),
            difficulty = difficulty,
        )
        self.teams.append(team)
        self.triggers.append(trigger)
        self.scenarios.append(StrategicScenario(
            name='naval_dominance_pressure', faction=faction,
            cond_type=COND_ENEMY_OWNS, cond_obj=absence_id,
            comparator=OWN_AT_LEAST_1, weights=(500,150,600),
            difficulty=difficulty, taskforce_id=tf.tf_id,
            script_id=script_id, team_id=team.team_id,
            trigger_id=trigger.trigger_id,
            notes="Naval dominance (all shipyards gone) -> coastal pressure"))

    def write_all(self, aimd_path: str | Path,
                   output_path: str | Path | None = None):
        """Write all generated entries to aimd.ini."""
        from pathlib import Path as P
        aimd_path   = P(aimd_path)
        output_path = P(output_path) if output_path else aimd_path

        # Write scripts
        new_scripts = [s for s in self.scripts if not s.is_duplicate]
        if new_scripts:
            self.sc_gen.write_to_ini(new_scripts, output_path, output_path)

        # Write taskforces
        if self.taskforces:
            self.tf_gen.write_to_ini(self.taskforces, output_path, output_path)

        # Write teams
        if self.teams:
            self.team_gen.write_to_ini(self.teams, output_path, output_path)

        # Write triggers
        if self.triggers:
            self.trig_gen.write_to_ini(self.triggers, output_path, output_path)

    def summary(self) -> str:
        by_name = {}
        for s in self.scenarios:
            by_name.setdefault(s.name, 0)
            by_name[s.name] += 1
        lines = ["Strategic trigger summary:"]
        for name, count in sorted(by_name.items()):
            lines.append(f"  {name:<30} {count} triggers")
        lines.append(f"  {'TOTAL':<30} {len(self.scenarios)}")
        lines.append(f"Taskforces: {len(self.taskforces)}")
        lines.append(f"Scripts:    {len(self.scripts)}")
        lines.append(f"Teams:      {len(self.teams)}")
        lines.append(f"Triggers:   {len(self.triggers)}")
        return "\n".join(lines)
