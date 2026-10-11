"""
script_generator.py
===================
Generates ScriptType entries for YR / YR-mod aimd.ini files.

A Script defines HOW units in a TeamType behave — movement, attack priority,
looping, retreating. Combined with a TaskForce (WHAT units) and a TeamType
(configuration), it forms a complete AI attack wave.

== SCRIPT STRUCTURE ==
Each script is a sequence of (action, argument) pairs written as:
    [SCRIPTID]
    Name=Human readable name
    0=action,argument
    1=action,argument
    ...

== VANILLA ACTIONS (skirmish-safe) ==
    0,X  — Attack target type X:
             1=enemy base (anything)  2=structures  3=harvesters
             4=infantry  5=vehicles  6=factories  7=base defenses
             8=house threats  9=power plants
    5,X  — Guard area for X time ticks (1 tick = 6 seconds)
    6,X  — Jump to script line X (loop control)
   11,X  — Set mission: 5=guard, 10=area guard, 14=hunt
   49,0  — Repeat until success (essential for persistent objectives)
   53,0  — Move to enemy base
   54,0  — Move to friendly base
   63,0  — Deploy/undeploy

== PHOBOS ACTIONS (requires Phobos extension) ==
10000,X  — Attack target type X (closer, repeating):
             1=anything  2=structures  3=miners  4=infantry
             5=vehicles  6=factories  7=defenses  8=threats
             9=power  12=refineries  13=mind controllers
            14=air  16=disruptors  17=ground vehicles
            18=economy  19=infantry factory  20=vehicle factory
            22=radar  25=super weapons  26=CY  36=non-defensive
10005,X  — Attack target type X (farther first, repeating)
10010,X  — Attack target type X (closer + higher threat, repeating)
10100,n  — Timed area guard for n ingame seconds
10102,n  — Regroup around team leader for n seconds
12000,n  — Wait if no target found (n=0 infinite retries)
16000,n  — Start timed jump to same line (n seconds)
16003,n  — Randomly skip next action (n=chance 0-100)
16004,n  — Pick random script from AIScriptsList index n

== DESIGN PRINCIPLES ==
1. Script library: fixed templates selected by taskforce labels.
   No procedural generation — the vocabulary is small and fixed.
2. Deduplication: if an identical action sequence already exists in
   aimd.ini, reuse that script's ID rather than writing a new one.
   Add new labels to existing scripts via comment update.
3. Phobos detection: auto-detect from aimd.ini action numbers >= 10000,
   but ask user to confirm since detection may be ambiguous.
4. Rush scripts: no retreat (no 54,0), straight to enemy, fast.
5. Direct attack scripts: no pre-gather, straight to target.
   - Anti-building → prioritize structures/factories
   - Anti-unit → hunt mode (11,14)
6. Label comments: same format as taskforces, above each section.

== TODO ==
- AIScriptsList + 16004 random script pool feature
- AI personality variants (RUSHER/STORMROLLER/TURTLE/GUERILLA)
- Transport/garrison scripts
- Superweapon coordination scripts
- Variable manipulation (18000-18999) for dynamic behavior
"""

import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Script action constants (for readability)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Vanilla script action constants — confirmed in vanilla aimd.ini
# ---------------------------------------------------------------------------
ACT_ATTACK           = 0    # arg = quarry type (see TGT_*)
ACT_GUARD_AREA_TIMED = 5    # arg = time ticks (1 tick = 6 sec at normal speed)
ACT_JUMP             = 6    # arg = line number (1-indexed; 6,1 = jump to first action)
ACT_ATTACK_UNLOAD    = 8    # arg = 0=keep all, 2=lose transport keep units
ACT_SET_MISSION      = 11   # arg = mission type (see MISSION_*)
ACT_LOAD_TRANSPORT   = 14   # arg = 0; load passengers into transport in same TF
ACT_SCATTER          = 21   # arg = 0; scatter formation
ACT_WAIT_LOADED      = 43   # arg = 0; wait until transport fully loaded
ACT_ATTACK_BUILDING  = 46   # arg = BwP building index; attack specific enemy structure
ACT_MOVE_TO_BUILDING = 47   # arg = BwP building index; move adjacent to enemy structure
ACT_REPEAT_UNTIL_OK  = 49   # arg = 0; retry previous action until success
ACT_GATHER_ENEMY     = 53   # arg = 0; gather at enemy base (DeeZire: gather, NOT move)
ACT_REGROUP_FRIENDLY = 54   # arg = 0; regroup at friendly base (assemble team)
ACT_IRON_CURTAIN     = 55   # arg = 0; activate Iron Curtain on task force (Soviet)
ACT_CHRONOSHIFT      = 56   # arg = BwP; chronoshift TF to specific enemy structure
ACT_CHRONOWARP       = 57   # arg = quarry type; chronowarp TF to target category
ACT_MOVE_FRIENDLY_ST = 58   # arg = BwP; move adjacent to friendly structure
ACT_DEPLOY           = 9    # arg = 0; deploy units with deploy state

# Vanilla quarry types for ACT_ATTACK (0) and ACT_CHRONOWARP (57)
TGT_CANCEL           = 0    # cancel attack mission
TGT_BASE             = 1    # anything (nearest enemy)
TGT_STRUCTURES       = 2    # enemy buildings
TGT_HARVESTERS       = 3    # enemy harvesters/miners
TGT_INFANTRY         = 4    # enemy infantry
TGT_VEHICLES         = 5    # enemy vehicles
TGT_FACTORIES        = 6    # enemy buildings with Factory= set
TGT_DEFENSES         = 7    # enemy buildings with IsBaseDefense=yes
TGT_THREATS          = 8    # enemy units approaching friendly base
TGT_POWER            = 9    # enemy buildings with positive Power=
TGT_OCCUPIABLE       = 10   # buildings with CanOccupyFire=yes (YR)
TGT_TECH             = 11   # buildings with NeedsEngineer=yes

# Vanilla mission types for ACT_SET_MISSION (11) — RA2/YR numbering
MISSION_GUARD        = 5    # guard: engage enemies in weapon range, don't pursue
MISSION_AREA_GUARD   = 11   # area guard: pursue enemies in Sight= range
                             # NOTE: area guard units NOT recruited from field for new teams
MISSION_HUNT         = 15   # hunt: scan and attack enemies anywhere on map

# Phobos actions
PHOBOS_ATTACK_CLOSER      = 10000   # arg = target type (extended list)
PHOBOS_ATTACK_FARTHER     = 10005
PHOBOS_ATTACK_THREAT      = 10010   # closer + higher threat
PHOBOS_LOAD_TRANSPORTS    = 10103  # Load Onto Transports (Phobos) — works with multiple vehicles
PHOBOS_TIMED_AREA_GUARD   = 10100
PHOBOS_REGROUP            = 10102
PHOBOS_WAIT_NO_TARGET     = 12000
PHOBOS_RANDOM_SKIP        = 16003
PHOBOS_RANDOM_SCRIPT      = 16004   # TODO: requires AIScriptsList

# Phobos attack target types (extended)
PTGT_ANYTHING        = 1
PTGT_STRUCTURES      = 2
PTGT_MINERS          = 3
PTGT_INFANTRY        = 4
PTGT_VEHICLES        = 5
PTGT_FACTORIES       = 6
PTGT_DEFENSES        = 7
PTGT_THREATS         = 8
PTGT_POWER           = 9
PTGT_REFINERIES      = 12
PTGT_MIND_CTRL       = 13
PTGT_ECONOMY         = 18
PTGT_VEH_FACTORY     = 20
PTGT_RADAR           = 22
PTGT_SUPER_WEAPONS   = 25
PTGT_CY              = 26
PTGT_NON_DEF_STRUCT  = 36


# ---------------------------------------------------------------------------
# Script template definition
# ---------------------------------------------------------------------------

@dataclass
class ScriptTemplate:
    """
    A named script template with a fixed action sequence.

    label_tags: set of taskforce label tags this script is appropriate for.
                Matching is done by checking which templates best fit the
                combination of labels on a taskforce.
    phobos:     True if this script requires Phobos.
    actions:    List of (action_id, argument) pairs.
    priority:   Higher = preferred when multiple templates match.
    """
    name:        str
    label_tags:  set[str]
    actions:     list[tuple[int, int]]
    phobos:      bool  = False
    priority:    int   = 0
    description: str   = ""


# ---------------------------------------------------------------------------
# Script template library
# ---------------------------------------------------------------------------

def _build_library(phobos: bool) -> list[ScriptTemplate]:
    """
    Full script template library — every template grounded in vanilla-confirmed
    actions from DeeZire AI.doc and aimd.ini analysis.

    Vanilla pattern (DeeZire): regroup first (54), gather at enemy (53), attack.
    Rush exception: no regroup — speed is the weapon.
    Large team exception: no gather — 10+ units block each other.
    """
    T = []

    # ================================================================
    # RUSH PHILOSOPHY
    # No regroup, no gather. Straight to enemy. Speed is the weapon.
    # Suicide=yes on TeamType handles the no-return-fire behavior.
    # ================================================================

    T.append(ScriptTemplate(
        name="Gunner Load Assault",
        description="Load infantry into Gunner=yes vehicles (IFVs), wait for "
                    "loading, gather at enemy, attack. Used for Hard-difficulty "
                    "late-game infantry waves paired with Gunner vehicles.",
        label_tags={"GUNNER"},
        priority=50,   # very high — only taskforces explicitly tagged GUNNER
        actions=[
            (ACT_LOAD_TRANSPORT,  0),          # 14,0: infantry board the IFVs
            (ACT_WAIT_LOADED,     0),          # 43,0: wait until boarding done
            (ACT_GATHER_ENEMY,    0),          # 53:   gather at enemy base
            (ACT_ATTACK,          TGT_BASE),   # 0,1:  attack anything
            (ACT_REPEAT_UNTIL_OK, 0),
            (ACT_JUMP,            3),          # loop back to gather
        ],
    ))

    # ------------------------------------------------------------------
    # Item 52 + 53: Mixed infantry+vehicle comps with loading-into-transport
    # ------------------------------------------------------------------
    # Vanilla 14,0 loads passengers into ONE transport per infantryman.
    # Phobos 10103,0 loads infantry across multiple transports intelligently
    # (see https://phobos.readthedocs.io/en/latest/AI-Scripting-and-Mapping.html#load-onto-transports).
    # Templates are emitted with both variants; the script generator picks
    # the Phobos version when phobos_enabled=True is passed, otherwise falls
    # back to vanilla 14,0.

    T.append(ScriptTemplate(
        name="Load Infantry (Vanilla)",
        description="Vanilla 14,0 load — one transport per infantryman. "
                    "Used when Phobos is not enabled.",
        label_tags={"LOAD_INFANTRY"},
        priority=45,
        phobos=False,
        actions=[
            (ACT_LOAD_TRANSPORT,  0),          # 14,0 vanilla
            (ACT_WAIT_LOADED,     0),
            (ACT_GATHER_ENEMY,    0),
            (ACT_ATTACK,          TGT_BASE),
            (ACT_REPEAT_UNTIL_OK, 0),
            (ACT_JUMP,            3),
        ],
    ))

    T.append(ScriptTemplate(
        name="Load Onto Transports (Phobos)",
        description="Phobos 10103,0 — infantry distribute across ALL available "
                    "transports in the taskforce, not just one per infantryman. "
                    "Works with mixed infantry+vehicle comps where the "
                    "vehicles have Gunner=yes or OpenTopped=yes passenger seats.",
        label_tags={"LOAD_INFANTRY"},
        priority=55,   # wins over vanilla when Phobos is enabled
        phobos=True,
        actions=[
            (PHOBOS_LOAD_TRANSPORTS, 0),       # 10103,0 Phobos multi-transport load
            (ACT_WAIT_LOADED,        0),
            (ACT_GATHER_ENEMY,       0),
            (ACT_ATTACK,             TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,    0),
            (ACT_JUMP,               3),
        ],
    ))

    T.append(ScriptTemplate(
        name="Rush Direct",
        description="No regroup. Straight to enemy base, attack anything, loop.",
        label_tags={"RUSH", "RUSH_WAVE", "RUSH_PHASE", "SUICIDE_RUSH",
                    "INFANTRY_RUSH_EARLY"},
        priority=5,
        actions=[
            (ACT_GATHER_ENEMY,    0),           # 53: move to enemy base fast
            (ACT_ATTACK,          TGT_BASE),    # 0,1: attack anything
            (ACT_REPEAT_UNTIL_OK, 0),
            (ACT_JUMP,            1),
        ],
    ))

    T.append(ScriptTemplate(
        name="Rush Kill Factories",
        description="No regroup. Rush and prioritize factories then base.",
        label_tags={"RUSH", "RUSH_WAVE", "FACTORY_KILLER"},
        priority=6,
        actions=[
            (ACT_GATHER_ENEMY,    0),
            (ACT_ATTACK,          TGT_FACTORIES),
            (ACT_REPEAT_UNTIL_OK, 0),
            (ACT_ATTACK,          TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK, 0),
            (ACT_ATTACK,          TGT_BASE),
            (ACT_REPEAT_UNTIL_OK, 0),
            (ACT_JUMP,            1),
        ],
    ))

    T.append(ScriptTemplate(
        name="Rush Kill Power",
        description="No regroup. Rush and kill power plants first.",
        label_tags={"RUSH", "RUSH_WAVE", "ANTI_BUILDING"},
        priority=4,
        actions=[
            (ACT_GATHER_ENEMY,    0),
            (ACT_ATTACK,          TGT_POWER),
            (ACT_REPEAT_UNTIL_OK, 0),
            (ACT_ATTACK,          TGT_BASE),
            (ACT_REPEAT_UNTIL_OK, 0),
            (ACT_JUMP,            1),
        ],
    ))

    # ================================================================
    # GENERAL ASSAULT PHILOSOPHY
    # Regroup first so team assembles, then gather at enemy, attack.
    # Vanilla pattern from DeeZire: 54 → 53 → attack → loop.
    # ================================================================

    T.append(ScriptTemplate(
        name="General Attack",
        description="Regroup, gather at enemy, attack anything, loop.",
        label_tags={"LAND", "GENERAL_ATTACK"},
        priority=3,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),          # 54: assemble team
            (ACT_GATHER_ENEMY,     0),          # 53: gather at enemy base
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    T.append(ScriptTemplate(
        name="Attack Structures Then Base",
        description="Regroup, gather, prioritize structures then anything.",
        label_tags={"ANTI_BUILDING", "GENERAL_ATTACK"},
        priority=4,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    T.append(ScriptTemplate(
        name="Hunt Vehicles Then Structures",
        description="Regroup, gather, hunt vehicles then hit base.",
        label_tags={"ANTI_VEHICLE"},
        priority=4,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_VEHICLES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    T.append(ScriptTemplate(
        name="Hunt Infantry Then Base",
        description="Regroup, gather, hunt infantry then hit base.",
        label_tags={"ANTI_INF"},
        priority=4,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_INFANTRY),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    # ================================================================
    # DEFENSE STRIP PHILOSOPHY
    # Clear defenses so follow-up waves can operate.
    # Best used as Team2 (support team) alongside a main assault.
    # ================================================================

    T.append(ScriptTemplate(
        name="Strip Defenses Then Base",
        description="Regroup, attack defenses, then factories, then base.",
        label_tags={"ANTI_DEFENSES"},
        priority=5,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_DEFENSES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_FACTORIES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    T.append(ScriptTemplate(
        name="Power Cut Then Defenses",
        description="Kill power (defenses offline), then strip defenses.",
        label_tags={"ANTI_DEFENSES", "ANTI_BUILDING"},
        priority=6,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_POWER),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_DEFENSES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    # ================================================================
    # FACTORY KILLER PHILOSOPHY
    # Destroy production capability — factories first, then CY.
    # ================================================================

    T.append(ScriptTemplate(
        name="Factory Priority Strike",
        description="Regroup, hit factories first, then remaining structures.",
        label_tags={"FACTORY_KILLER"},
        priority=6,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_FACTORIES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    T.append(ScriptTemplate(
        name="CY Priority Strike",
        description="Regroup, hit structures (ConYard) first, then factories.",
        label_tags={"CY_KILLER", "FACTORY_KILLER"},
        priority=7,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_FACTORIES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    # ================================================================
    # ARTILLERY / BOMBARDMENT PHILOSOPHY
    # Aggressive=no on TeamType — units hold range, don't chase.
    # ================================================================

    T.append(ScriptTemplate(
        name="Artillery Siege Defenses",
        description="Regroup, shell defenses from range, then structures.",
        label_tags={"ARTY", "ANTI_DEFENSES", "ARTILLERY"},
        priority=5,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_DEFENSES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    T.append(ScriptTemplate(
        name="Artillery Bombardment Structures",
        description="Regroup, shell structures from range.",
        label_tags={"ARTY", "ANTI_BUILDING", "ARTILLERY"},
        priority=5,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    T.append(ScriptTemplate(
        name="Artillery Standoff Bombardment",
        description="Artillery bombards from range without entering the "
                    "enemy base. Skips regroup (54) and gather at enemy (53) "
                    "since artillery has long weapon range and shouldn't "
                    "walk INTO the base.",
        label_tags={"ARTY", "ARTILLERY", "NO_GATHER"},
        priority=12,
        actions=[
            (ACT_ATTACK,           TGT_DEFENSES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    T.append(ScriptTemplate(
        name="Undefended Enemy Rush",
        description="Enemy has no defenses; skip gather to strike before "
                    "they can rebuild. The gather delay is only worthwhile "
                    "when defenses would pick off stragglers.",
        label_tags={"RUSH", "RUSH_WAVE", "SUICIDE_RUSH", "NO_DEFENSES"},
        priority=15,
        actions=[
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    # ================================================================
    # ECONOMY WARFARE PHILOSOPHY
    # Target income — harvesters, power, then factories.
    # ================================================================

    T.append(ScriptTemplate(
        name="Miner Hunt",
        description="Regroup, attack harvesters persistently, loop.",
        label_tags={"MINER_KILLER"},
        priority=6,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_HARVESTERS),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_FACTORIES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    T.append(ScriptTemplate(
        name="Power Plant Strike",
        description="Cut power first, then defenses go offline, then attack.",
        label_tags={"ANTI_BUILDING", "MINER_KILLER"},
        priority=4,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_POWER),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_DEFENSES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    # ================================================================
    # AERIAL PHILOSOPHIES — three bands
    # AERIAL_RUSH: no AA, hit immediately
    # AERIAL_WAVE: light AA, regroup then attack
    # AERIAL_ASSAULT: heavy AA, regroup + strip defenses first
    # ================================================================

    T.append(ScriptTemplate(
        name="Aerial Rush Strike",
        description="No AA — aerial straight to base, no regroup.",
        label_tags={"AERIAL_RUSH"},
        priority=5,
        actions=[
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    T.append(ScriptTemplate(
        name="Aerial Wave Attack",
        description="Light AA — regroup, gather, hit defenses then base.",
        label_tags={"AERIAL_WAVE"},
        priority=5,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_DEFENSES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    T.append(ScriptTemplate(
        name="Aerial Overwhelming Assault",
        description="Heavy AA — regroup tight, strip AA first then push.",
        label_tags={"AERIAL_ASSAULT"},
        priority=5,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_DEFENSES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_FACTORIES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    # ================================================================
    # INFILTRATION PHILOSOPHIES
    # Engineer/spy: regroup, move to enemy, attack structures.
    # Unit logic handles capture/infiltrate automatically on contact.
    # AvoidThreats=yes and Aggressive=yes on TeamType handle the sneak.
    # ================================================================

    T.append(ScriptTemplate(
        name="Engineer Capture",
        description="Regroup, move to enemy, attack structures "
                    "(Engineer=yes captures on contact).",
        label_tags={"ENGINEER"},
        priority=6,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
        ],
    ))

    T.append(ScriptTemplate(
        name="Spy Infiltration",
        description="Regroup, move to enemy, attack structures "
                    "(Agent=yes infiltrates on contact).",
        label_tags={"SPY"},
        priority=6,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
        ],
    ))

    # ================================================================
    # TRANSPORT DROP PHILOSOPHY
    # Load → wait fully loaded → move to enemy → unload → attack.
    # Vanilla pattern from Allied Transport scripts.
    # ================================================================

    T.append(ScriptTemplate(
        name="Transport Drop Attack",
        description="Load infantry, move to enemy, unload, attack factories.",
        label_tags={"TRANSPORT_DROP"},
        priority=5,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),          # 54: assemble at friendly base
            (ACT_LOAD_TRANSPORT,   0),          # 14: load passengers
            (ACT_WAIT_LOADED,      0),          # 43: wait until fully loaded
            (ACT_GATHER_ENEMY,     0),          # 53: move loaded to enemy
            (ACT_ATTACK_UNLOAD,    2),          # 8,2: unload (lose transport, keep units)
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_FACTORIES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
        ],
    ))

    # ================================================================
    # SUPERWEAPON COORDINATION
    # Iron Curtain: regroup tight (all units must be in IC range), activate, charge.
    # Chronosphere: regroup, chronowarp to power plants, guard while defenses offline.
    # Both depend on AIMinorSuperReadyPercent — AI uses them opportunistically.
    # ================================================================

    T.append(ScriptTemplate(
        name="Iron Curtain Assault",
        description="Regroup tight (all in IC range), activate Iron Curtain, "
                    "charge defenses. Soviet.",
        label_tags={"IRON_CURTAIN", "GENERAL_ATTACK"},
        priority=8,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),          # 54: all must be in IC range
            (ACT_IRON_CURTAIN,     0),          # 55: fire Iron Curtain on TF
            (ACT_GATHER_ENEMY,     0),          # 53: charge
            (ACT_ATTACK,           TGT_DEFENSES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
        ],
    ))

    T.append(ScriptTemplate(
        name="Chronosphere Strike",
        description="Regroup, chronowarp to power plants, guard 1 min while "
                    "defenses offline, then hit base. Allied.",
        label_tags={"CHRONOSPHERE", "ANTI_DEFENSES"},
        priority=8,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),          # 54: regroup
            (ACT_CHRONOWARP,       TGT_POWER),  # 57,9: warp to power plants
            (ACT_GUARD_AREA_TIMED, 10),         # 5,10: guard 1 min (defenses offline)
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_DEFENSES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_FACTORIES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
        ],
    ))

    # ================================================================
    # AREA CONTROL — AA and base guard
    # Area Guard intentionally used here: AI defends actively in its zone.
    # Note: Area Guard units NOT recruitable from field (DeeZire warning).
    # Pool teams use Guard (11,5) instead — see pool_script_actions.
    # ================================================================

    T.append(ScriptTemplate(
        name="AA Area Guard",
        description="Area guard: engage air threats in Sight= range.",
        label_tags={"AA"},
        priority=4,
        actions=[
            (ACT_SET_MISSION,     MISSION_AREA_GUARD),  # 11,11
            (ACT_REPEAT_UNTIL_OK, 0),
        ],
    ))

    T.append(ScriptTemplate(
        name="Hunt Anything",
        description="Hunt mission — seeks enemies anywhere on map.",
        label_tags={"HIGH_DPS", "GENERAL_ATTACK"},
        priority=3,
        actions=[
            (ACT_SET_MISSION,     MISSION_HUNT),  # 11,15
            (ACT_REPEAT_UNTIL_OK, 0),
        ],
    ))

    # ================================================================
    # HIGH DPS — elite strike force
    # ================================================================

    T.append(ScriptTemplate(
        name="High DPS Defense Strip",
        description="Elite units — regroup, strip defenses, hit factories.",
        label_tags={"HIGH_DPS", "ANTI_DEFENSES"},
        priority=5,
        actions=[
            (ACT_REGROUP_FRIENDLY, 0),
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_DEFENSES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_FACTORIES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    # ================================================================
    # NAVAL
    # ================================================================

    T.append(ScriptTemplate(
        name="Naval Attack",
        description="Naval: engage anything at sea then shore.",
        label_tags={"NAVAL"},
        priority=4,
        actions=[
            (ACT_ATTACK,          TGT_BASE),
            (ACT_REPEAT_UNTIL_OK, 0),
            (ACT_JUMP,            1),
        ],
    ))

    T.append(ScriptTemplate(
        name="Anti-Naval Hunt",
        description="Anti-naval: hunt enemy naval then base.",
        label_tags={"ANTI_NAVAL"},
        priority=4,
        actions=[
            (ACT_ATTACK,          TGT_BASE),
            (ACT_REPEAT_UNTIL_OK, 0),
            (ACT_JUMP,            1),
        ],
    ))

    # ================================================================
    # LARGE TEAM — skip gather (10+ units block each other)
    # ================================================================

    T.append(ScriptTemplate(
        name="Large Team Direct Assault",
        description="Large team: no gather, straight to enemy base.",
        label_tags={"LARGE_TEAM"},
        priority=10,
        actions=[
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    T.append(ScriptTemplate(
        name="Large Team Factory Strike",
        description="Large team: no gather, straight to factories.",
        label_tags={"LARGE_TEAM", "FACTORY_KILLER", "ANTI_BUILDING"},
        priority=12,
        actions=[
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_FACTORIES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_BASE),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    T.append(ScriptTemplate(
        name="Large Team Defense Strip",
        description="Large team: no gather, strip defenses then base.",
        label_tags={"LARGE_TEAM", "ANTI_DEFENSES"},
        priority=12,
        actions=[
            (ACT_GATHER_ENEMY,     0),
            (ACT_ATTACK,           TGT_DEFENSES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_ATTACK,           TGT_STRUCTURES),
            (ACT_REPEAT_UNTIL_OK,  0),
            (ACT_JUMP,             1),
        ],
    ))

    # ================================================================
    # PHOBOS TEMPLATES
    # ================================================================

    if phobos:

        T.append(ScriptTemplate(
            name="Threat Priority Attack",
            description="Attack closest high-threat targets, loop.",
            label_tags={"LAND", "GENERAL_ATTACK"},
            phobos=True, priority=4,
            actions=[
                (PHOBOS_ATTACK_THREAT,    PTGT_ANYTHING),
                (ACT_REPEAT_UNTIL_OK,     0),
                (ACT_JUMP,                1),
            ],
        ))

        T.append(ScriptTemplate(
            name="Regroup Then Threat Attack",
            description="Regroup 20s around leader then attack by threat.",
            label_tags={"LAND", "GENERAL_ATTACK", "HIGH_DPS"},
            phobos=True, priority=5,
            actions=[
                (PHOBOS_REGROUP,          20),
                (PHOBOS_ATTACK_THREAT,    PTGT_ANYTHING),
                (ACT_REPEAT_UNTIL_OK,     0),
                (ACT_JUMP,                2),
            ],
        ))

        T.append(ScriptTemplate(
            name="Economy Strike Phobos",
            description="Attack economy (miners + refineries) by threat.",
            label_tags={"MINER_KILLER"},
            phobos=True, priority=7,
            actions=[
                (PHOBOS_WAIT_NO_TARGET,   3),
                (PHOBOS_ATTACK_CLOSER,    PTGT_ECONOMY),
                (ACT_REPEAT_UNTIL_OK,     0),
                (ACT_JUMP,                1),
            ],
        ))

        T.append(ScriptTemplate(
            name="Factory CY Phobos",
            description="Attack vehicle factory → CY → structures by threat.",
            label_tags={"FACTORY_KILLER", "CY_KILLER"},
            phobos=True, priority=8,
            actions=[
                (PHOBOS_WAIT_NO_TARGET,   2),
                (PHOBOS_ATTACK_CLOSER,    PTGT_VEH_FACTORY),
                (ACT_REPEAT_UNTIL_OK,     0),
                (PHOBOS_ATTACK_CLOSER,    PTGT_CY),
                (ACT_REPEAT_UNTIL_OK,     0),
                (PHOBOS_ATTACK_THREAT,    PTGT_STRUCTURES),
                (ACT_REPEAT_UNTIL_OK,     0),
                (ACT_JUMP,                1),
            ],
        ))

        T.append(ScriptTemplate(
            name="Radar Strike Phobos",
            description="Radar first, then non-defense structures.",
            label_tags={"ANTI_BUILDING"},
            phobos=True, priority=5,
            actions=[
                (PHOBOS_WAIT_NO_TARGET,   2),
                (PHOBOS_ATTACK_CLOSER,    PTGT_RADAR),
                (ACT_REPEAT_UNTIL_OK,     0),
                (PHOBOS_ATTACK_THREAT,    PTGT_NON_DEF_STRUCT),
                (ACT_REPEAT_UNTIL_OK,     0),
                (ACT_JUMP,                1),
            ],
        ))

        T.append(ScriptTemplate(
            name="Super Weapon Priority Phobos",
            description="SW buildings first, then structures.",
            label_tags={"ANTI_BUILDING", "FACTORY_KILLER"},
            phobos=True, priority=5,
            actions=[
                (PHOBOS_WAIT_NO_TARGET,   2),
                (PHOBOS_ATTACK_CLOSER,    PTGT_SUPER_WEAPONS),
                (ACT_REPEAT_UNTIL_OK,     0),
                (PHOBOS_ATTACK_THREAT,    PTGT_STRUCTURES),
                (ACT_REPEAT_UNTIL_OK,     0),
                (ACT_JUMP,                1),
            ],
        ))

        T.append(ScriptTemplate(
            name="Mind Controller Hunt Phobos",
            description="Prioritize enemy mind controllers, then anything.",
            label_tags={"ANTI_INF", "ANTI_VEHICLE"},
            phobos=True, priority=5,
            actions=[
                (PHOBOS_WAIT_NO_TARGET,   2),
                (PHOBOS_ATTACK_CLOSER,    PTGT_MIND_CTRL),
                (ACT_REPEAT_UNTIL_OK,     0),
                (PHOBOS_ATTACK_THREAT,    PTGT_ANYTHING),
                (ACT_REPEAT_UNTIL_OK,     0),
                (ACT_JUMP,                1),
            ],
        ))

        T.append(ScriptTemplate(
            name="Timed Guard Then Assault Phobos",
            description="Area guard 30s, then assault defenses by threat.",
            label_tags={"ANTI_DEFENSES", "AA"},
            phobos=True, priority=6,
            actions=[
                (PHOBOS_TIMED_AREA_GUARD, 30),
                (PHOBOS_ATTACK_CLOSER,    PTGT_DEFENSES),
                (ACT_REPEAT_UNTIL_OK,     0),
                (PHOBOS_ATTACK_THREAT,    PTGT_STRUCTURES),
                (ACT_REPEAT_UNTIL_OK,     0),
                (ACT_JUMP,                2),
            ],
        ))

        T.append(ScriptTemplate(
            name="Random Power Cut Phobos",
            description="30% chance: power cut first, otherwise structures.",
            label_tags={"ANTI_BUILDING", "GENERAL_ATTACK"},
            phobos=True, priority=3,
            actions=[
                (PHOBOS_RANDOM_SKIP,      30),
                (PHOBOS_ATTACK_CLOSER,    PTGT_POWER),
                (PHOBOS_ATTACK_THREAT,    PTGT_STRUCTURES),
                (ACT_REPEAT_UNTIL_OK,     0),
                (ACT_JUMP,                1),
            ],
        ))

    # Filter Phobos-only templates when Phobos is not enabled. Templates
    # tagged phobos=True use action codes >= 10000 (Phobos extension actions)
    # which fail silently in vanilla YR + Ares. When phobos is False, they're
    # skipped so vanilla fallbacks (LOAD_INFANTRY vanilla vs Phobos, etc.)
    # get picked correctly.
    if not phobos:
        T = [t for t in T if not getattr(t, 'phobos', False)]

    return T



# ---------------------------------------------------------------------------
# Template matching
# ---------------------------------------------------------------------------

def select_template(taskforce_labels: set[str],
                    templates: list[ScriptTemplate],
                    rng) -> ScriptTemplate | None:
    """
    Select the best matching script template for a taskforce's label set.

    Scoring: count of overlapping tags * priority.
    Higher score = better match. Ties broken randomly.
    """
    best_score  = -1
    best_group: list[ScriptTemplate] = []

    for tmpl in templates:
        overlap = len(tmpl.label_tags & taskforce_labels)
        if overlap == 0:
            continue
        score = overlap * tmpl.priority
        if score > best_score:
            best_score  = score
            best_group  = [tmpl]
        elif score == best_score:
            best_group.append(tmpl)

    if not best_group:
        return None
    return rng.choice(best_group)


# ---------------------------------------------------------------------------
# Script ID registry and deduplication
# ---------------------------------------------------------------------------

class ScriptRegistry:
    """
    Tracks existing and newly generated script IDs.
    Supports deduplication by action sequence fingerprint.
    Also tracks existing Name= values to avoid duplicates.
    """

    _SECTION_PATTERN = re.compile(r"^\[([0-9A-Fa-f]{8}-G)\]")

    def __init__(self, global_registry,
                 existing_scripts: dict | None = None,
                 existing_names: set[str] | None = None):
        from .id_registry import GlobalIDRegistry
        self._global  = global_registry
        self._scripts: dict[str, list[tuple[int, int]]] = existing_scripts or {}
        self._names: set[str] = set(existing_names or [])

    @classmethod
    def from_aimd(cls, aimd_path: str | Path,
                  global_registry=None) -> "ScriptRegistry":
        """Load existing scripts and names from aimd.ini."""
        from .id_registry import GlobalIDRegistry as GIR
        if global_registry is None:
            global_registry = GIR.from_aimd(aimd_path)

        path = Path(aimd_path)
        if not path.exists():
            return cls(global_registry)

        with path.open(encoding="latin-1") as fh:
            lines = fh.readlines()

        scripts: dict[str, list[tuple[int, int]]] = {}
        existing_names: set[str] = set()
        current_id: str | None = None

        for line in lines:
            s = line.strip()
            m = cls._SECTION_PATTERN.match(s)
            if m:
                current_id = m.group(1)
                scripts[current_id] = []
                continue
            if current_id:
                if s.lower().startswith("name="):
                    name = s[5:].strip()
                    if name:
                        existing_names.add(name.lower())
                elif "=" in s and not s.startswith(";"):
                    key, _, val = s.partition("=")
                    try:
                        int(key.strip())
                        parts = val.strip().split(",")
                        if len(parts) == 2:
                            action = int(parts[0].strip())
                            arg    = int(parts[1].strip())
                            scripts[current_id].append((action, arg))
                    except ValueError:
                        pass

        return cls(global_registry, scripts, existing_names)

    def generate_id(self) -> str:
        return self._global.generate()

    def unique_name(self, name: str) -> str:
        """Return name, appending a suffix if already taken."""
        if name.lower() not in self._names:
            self._names.add(name.lower())
            return name
        i = 2
        while True:
            candidate = f"{name} {i}"
            if candidate.lower() not in self._names:
                self._names.add(candidate.lower())
                return candidate
            i += 1

    def find_duplicate(self,
                       actions: list[tuple[int, int]]) -> str | None:
        """Return existing script ID if an identical action sequence exists."""
        for sid, existing_actions in self._scripts.items():
            if existing_actions == actions:
                return sid
        return None

    def register(self, sid: str, actions: list[tuple[int, int]],
                 name: str = ""):
        self._scripts[sid] = actions
        self._global.register(sid)
        if name:
            self._names.add(name.lower())


# ---------------------------------------------------------------------------
# Script entry
# ---------------------------------------------------------------------------

@dataclass
class ScriptEntry:
    script_id:     str
    name:          str
    label_comment: str
    actions:       list[tuple[int, int]]
    phobos:        bool        = False
    is_duplicate:  bool        = False   # True if reusing existing ID
    taskforce_ids: list[str]   = field(default_factory=list)


def make_script_label_comment(template: ScriptTemplate,
                               taskforce_labels: set[str]) -> str:
    """
    Generate comment line above the script section.
    Scripts are faction-agnostic — only behavioral tags are included.
    Format: ; [SCRIPT][ROLE][PHASE][FLAGS][PHOBOS?]
    """
    # Tags that describe behavior, not faction/country/difficulty
    BEHAVIORAL = {
        "RUSH_WAVE", "RUSH_PHASE", "DIRECT_ATTACK",
        "ANTI_INF", "ANTI_VEHICLE", "ANTI_BUILDING", "ANTI_DEFENSES",
        "ARTY", "ARTILLERY", "MINER_KILLER", "FACTORY_KILLER",
        "CY_KILLER", "AA", "NAVAL", "ANTI_NAVAL",
        "GENERAL_ATTACK", "HIGH_DPS", "LAND", "AERIAL", "AMPHIBIOUS",
        "EARLY", "MID", "LATE", "ENDGAME",
    }
    parts = ["; [SCRIPT]"]
    for lbl in sorted(taskforce_labels & BEHAVIORAL):
        parts.append(f"[{lbl}]")
    if template.phobos:
        parts.append("[PHOBOS]")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Phobos detection
# ---------------------------------------------------------------------------

def detect_phobos(aimd_path: str | Path) -> bool:
    """
    Check if any existing script uses Phobos action numbers >= 10000.
    Returns True if Phobos scripts are detected.
    """
    path = Path(aimd_path)
    if not path.exists():
        return False
    with path.open(encoding="latin-1") as fh:
        for line in fh:
            if "=" in line and not line.strip().startswith(";"):
                _, _, val = line.partition("=")
                try:
                    parts = val.strip().split(",")
                    if len(parts) == 2:
                        action = int(parts[0].strip())
                        if action >= 10000:
                            return True
                except ValueError:
                    pass
    return False


# ---------------------------------------------------------------------------
# Script generator
# ---------------------------------------------------------------------------

class ScriptGenerator:
    """
    Generates ScriptType entries matched to TaskforceEntry objects.

    Usage:
        registry  = ScriptRegistry.from_aimd("aimd.ini")
        generator = ScriptGenerator(registry, phobos=True, seed=42)
        scripts   = generator.generate_for_taskforces(taskforces)
        generator.write_to_ini(scripts, "aimd.ini")
    """

    def __init__(self,
                 registry:  ScriptRegistry,
                 phobos:    bool = False,
                 seed:      int | None = None):
        import random
        self.registry  = registry
        self.phobos    = phobos
        self.rng       = random.Random(seed)
        self.templates = _build_library(phobos)

    def generate_for_taskforces(self,
                                 taskforces: list) -> list[ScriptEntry]:
        """
        Generate (or reuse) a script for each taskforce.
        Returns a list of ScriptEntry objects, deduplicated.
        Each taskforce gets the script that best fits its labels.
        """
        results: list[ScriptEntry]       = []
        seen_fingerprints: dict          = {}  # action_tuple -> ScriptEntry

        for tf in taskforces:
            # Parse labels from taskforce comment
            labels = set(re.findall(r"\[([^\]]+)\]", tf.label_comment))

            # Select best template
            template = select_template(labels, self.templates, self.rng)
            if template is None:
                # Fallback: standard ground attack
                template = self.templates[0]

            actions     = template.actions
            fingerprint = tuple(actions)

            # Check deduplication in new scripts first
            if fingerprint in seen_fingerprints:
                entry = seen_fingerprints[fingerprint]
                entry.taskforce_ids.append(tf.tf_id)
                tf.script_id = entry.script_id
                continue

            # Check deduplication against existing aimd.ini scripts
            existing_id = self.registry.find_duplicate(actions)
            if existing_id:
                entry = ScriptEntry(
                    script_id     = existing_id,
                    name          = template.name,
                    label_comment = make_script_label_comment(template, labels),
                    actions       = actions,
                    phobos        = template.phobos,
                    is_duplicate  = True,
                    taskforce_ids = [tf.tf_id],
                )
                seen_fingerprints[fingerprint] = entry
                results.append(entry)
                tf.script_id = existing_id
                continue

            # New script
            script_id = self.registry.generate_id()
            unique_name = self.registry.unique_name(template.name)
            self.registry.register(script_id, actions, unique_name)

            entry = ScriptEntry(
                script_id     = script_id,
                name          = unique_name,
                label_comment = make_script_label_comment(template, labels),
                actions       = actions,
                phobos        = template.phobos,
                is_duplicate  = False,
                taskforce_ids = [tf.tf_id],
            )
            seen_fingerprints[fingerprint] = entry
            results.append(entry)
            tf.script_id = script_id

        return results

    def write_to_ini(self,
                     scripts:      list[ScriptEntry],
                     aimd_path:    str | Path,
                     output_path:  str | Path | None = None) -> Path:
        """
        Write new (non-duplicate) scripts into aimd.ini.
        Duplicate scripts (already in file) are skipped.
        Appends new IDs to [ScriptTypes] list.
        """
        aimd_path   = Path(aimd_path)
        output_path = Path(output_path) if output_path else aimd_path

        lines = []
        if aimd_path.exists():
            with aimd_path.open(encoding="latin-1") as fh:
                lines = fh.readlines()

        # Strip trailing semicolons and blanks
        while lines and lines[-1].strip() in (";", ""):
            lines.pop()

        # Find or create [ScriptTypes] section
        st_idx = None
        for i, line in enumerate(lines):
            if line.strip() == "[ScriptTypes]":
                st_idx = i
                break
        if st_idx is None:
            lines.append("\n[ScriptTypes]\n")
            st_idx = len(lines) - 1

        # Find highest existing index
        highest = -1
        i = st_idx + 1
        while i < len(lines):
            s = lines[i].strip()
            if s.startswith("[") and s != "[ScriptTypes]":
                break
            if "=" in s and not s.startswith(";"):
                try:
                    highest = max(highest, int(s.split("=")[0].strip()))
                except ValueError:
                    pass
            i += 1
        insert_at = i

        # Only write genuinely new scripts
        new_scripts = [s for s in scripts if not s.is_duplicate]

        # Insert new IDs into [ScriptTypes] list with tool comment
        new_list = [f"{highest + 1 + j}={s.script_id}  ; YR-AI-Tool\r\n"
                    for j, s in enumerate(new_scripts)]
        lines[insert_at:insert_at] = new_list

        # Append script sections
        lines.append("\r\n")
        for s in new_scripts:
            lines.append(f"{s.label_comment}\r\n")
            lines.append(f"[{s.script_id}]\r\n")
            lines.append(f"Name={s.name}\r\n")
            for idx, (action, arg) in enumerate(s.actions):
                lines.append(f"{idx}={action},{arg}\r\n")
            lines.append("\r\n")

        lines.append(";\r\n")

        with output_path.open("w", encoding="latin-1") as fh:
            fh.writelines(lines)

        return output_path


# ---------------------------------------------------------------------------
# Interactive helpers
# ---------------------------------------------------------------------------

def ask_phobos(aimd_path: str | Path) -> bool:
    """Ask user if mod uses Phobos, defaulting to auto-detection result."""
    detected = detect_phobos(aimd_path)
    default  = "yes" if detected else "no"
    status   = "detected" if detected else "not detected"
    print(f"\nPhobos extension: {status} in {aimd_path}")
    print("  Phobos enables richer script actions (targeted attacks,")
    print("  timed guards, regrouping, random variants, and more).")
    while True:
        raw = input(f"Does this mod use Phobos? [{default}]: ").strip().lower()
        if not raw:
            return detected
        if raw in ("yes", "y", "1", "true"):
            return True
        if raw in ("no", "n", "0", "false"):
            return False
        print("  Please enter yes or no.")
