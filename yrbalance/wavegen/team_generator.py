"""
team_generator.py
=================
Generates TeamType entries for YR / YR-mod aimd.ini files.

A TeamType binds a TaskForce (WHAT) + Script (HOW) together with
behavioural flags that control how the AI builds and deploys the team.

== TEAMTYPE FORMAT ==
[TEAMID]
Name=Human readable name
Script=SCRIPTID
TaskForce=TASKFORCEID
Priority=N          ; lower = higher priority
Max=N               ; max simultaneous instances; also auto-triggers at 1
Autocreate=yes/no   ; created automatically by AI on timer
Prebuild=yes/no     ; pre-build backup copy
Reinforce=yes/no    ; map-only: arrives as reinforcement
Full=yes/no         ; map-only: pre-fill transport
Suicide=yes/no      ; no retaliation, ignored by threat scans
Aggressive=yes/no   ; executes orders without stopping for combat
AvoidThreats=yes/no ; evades enemy GuardRange
Whiner=yes/no       ; auto-replace destroyed members (pool teams)
AreTeamMembersRecruitable=yes/no  ; freed units joinable by other teams
IsBaseDefense=yes/no ; assumes defensive posture while waiting
Annoyance=yes/no    ; destroyed members auto-replaced persistently
GuardSlower=yes/no  ; fast units slow to match slow ones
LooseRecruit=yes/no ; dissolves after successful mission
Loadable=yes/no     ; reload ammo at structure (legacy)
Recruiter=yes/no    ; can recruit existing map units
IonImmune=yes/no    ; immune to weather storms (TS legacy)
OnlyTargetHouseEnemy=no ; also target neutrals if no
MindControlDecision=0   ; what to do with mind-controlled units
OnTransOnly=no          ; only execute if transport action succeeds
VeteranLevel=1          ; 1=novice, 2=veteran, 3=elite
Group=-1                ; unit grouping after mission (-1=lose transports)

== ATTACK TEAMTYPE FLAGS BY ROLE ==
Rush:
  Priority=5, Max=2, Suicide=yes, Aggressive=yes, Whiner=no
  AvoidThreats=no, AreTeamMembersRecruitable=no

General Attack:
  Priority=5-7, Max=1, Suicide=no, Aggressive=yes, Whiner=no
  AvoidThreats=30% chance, AreTeamMembersRecruitable=yes

Miner Killer:
  Priority=15, Max=1, Suicide=no, Aggressive=no, Whiner=no
  AvoidThreats=no, AreTeamMembersRecruitable=yes, Annoyance=yes

Arty / Anti-Defenses:
  Priority=10, Max=1, Suicide=no, Aggressive=no, Whiner=no
  AvoidThreats=yes, GuardSlower=yes, AreTeamMembersRecruitable=no

== POOL TEAMTYPE FLAGS ==
Dedicated defender pools (infantry and vehicle, separate):
  Priority=5, Max=1, Autocreate=yes
  IsBaseDefense=yes, Whiner=yes, Aggressive=yes
  AreTeamMembersRecruitable=yes   (Yuri vanilla style, better for AI)
  Suicide=no
  Script: 11,5 (Guard) — skirmish safe, units remain recruitable

== NAME FORMAT ==
Attack: "[FACTION] [PHASE] [ROLE] [DIFFICULTY]"
         e.g. "GDI Mid General Attack Hard"
Pool:   "[FACTION] [TIER] Pool [UNIT_TYPE]"
         e.g. "GDI Early Pool Infantry"

== LABEL COMMENT FORMAT ==
; [FACTION][PHASE][ROLE][DIFFICULTY][POOL?]

== DIFFICULTY SCALING NOTE ==
TeamTypes themselves have no difficulty flag — that lives in AITriggerTypes.
The tool generates one TeamType per taskforce and lets AITriggerTypes control
which difficulties activate it.
"""

import random
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from .rulesmd_parser import RulesMdParser, UnitRecord
from .taskforce_generator import (
    TaskforceEntry, IDRegistry, make_vanilla_name,
    _eligible_units, DEFAULT_MIN_GRADE,
    vehicle_capacity, unit_slots, needs_direct_attack,
    PHASE_SIZES, DIFFICULTY_SCALE,
)


# ---------------------------------------------------------------------------
# TeamType flag defaults
# ---------------------------------------------------------------------------

# Flags that are always fixed for all attack teams
FIXED_FLAGS = {
    "Autocreate":             "yes",
    "Prebuild":               "no",
    "Reinforce":              "no",
    "Full":                   "no",
    "Loadable":               "no",
    "VeteranLevel":           "1",
    "OnlyTargetHouseEnemy":   "no",
    "IonImmune":              "no",
    "Recruiter":              "no",
    "Group":                  "-1",
    "MindControlDecision":    "0",
    "IsBaseDefense":          "no",
    "OnTransOnly":            "no",
    # Required fields that vanilla always writes — absence can cause crashes
    "TechLevel":              "0",
    "House":                  "<none>",
    "Droppod":                "no",
    "UseTransportOrigin":     "no",
    "TransportsReturnOnUnload": "no",
    # Tag=<none> (angle brackets) is the correct null sentinel.
    # Without it, Ares/YR leaves the Tag field uninitialized → crash.
    # Tag=none (no brackets) causes a bad tag lookup → also crashes.
    "Tag":                    "<none>",
    "Waypoint":               "none",
}

# Role-based flag overrides
# Priority: LOWER = higher priority (DeeZire). Multiples of 5.
# Aggressive=yes: executes orders without stopping for combat (pushes to target)
# Suicide=yes: stronger - won't return fire, ignores threats, one-way
# GuardSlower=yes: slows defensive RESPONSE time — for defensive teams only
# AvoidThreats=yes: evades enemy GuardRange — for spy/engineer teams only
ROLE_FLAGS: dict[str, dict] = {
    # ----------------------------------------------------------------
    # RUSH / EARLY GAME — cheap, fast, expendable, high instance count
    # ----------------------------------------------------------------
    "RUSH": {
        "Priority":                  "5",
        "Max":                       "3",
        "Suicide":                   "yes",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "no",
        "AreTeamMembersRecruitable": "no",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    "RUSH_WAVE": {   # alias for RUSH
        "Priority":                  "5",
        "Max":                       "3",
        "Suicide":                   "yes",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "no",
        "AreTeamMembersRecruitable": "no",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    # Suicide rush: Demo Trucks, Terrorists — no retaliation, pure one-way
    "SUICIDE_RUSH": {
        "Priority":                  "5",
        "Max":                       "3",
        "Suicide":                   "yes",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "no",
        "AreTeamMembersRecruitable": "no",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    # Early infantry rush — no defenses, no/low enemy units
    "INFANTRY_RUSH_EARLY": {
        "Priority":                  "5",
        "Max":                       "3",
        "Suicide":                   "yes",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "no",
        "AreTeamMembersRecruitable": "no",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    # ----------------------------------------------------------------
    # GENERAL ATTACK — standard mixed waves
    # ----------------------------------------------------------------
    "GENERAL_ATTACK": {
        "Priority":                  "10",
        "Max":                       "2",
        "Suicide":                   "no",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "yes",
        "AreTeamMembersRecruitable": "yes",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    # ----------------------------------------------------------------
    # ECONOMY HARASSMENT — miner killers, economy disruption
    # ----------------------------------------------------------------
    "MINER_KILLER": {
        "Priority":                  "15",
        "Max":                       "2",
        "Suicide":                   "no",
        "Aggressive":                "yes",   # push through to reach miners
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "yes",
        "AreTeamMembersRecruitable": "yes",
        "Annoyance":                 "yes",   # persistent harassment
        "GuardSlower":               "no",
    },
    # ----------------------------------------------------------------
    # DEFENSE STRIPPING — clears defenses before main assault
    # ----------------------------------------------------------------
    "ANTI_DEFENSES": {
        "Priority":                  "10",
        "Max":                       "2",
        "Suicide":                   "no",
        "Aggressive":                "yes",   # push to target, don't stop
        "AvoidThreats":              "no",    # artillery holds range naturally
        "Whiner":                    "no",
        "LooseRecruit":              "no",
        "AreTeamMembersRecruitable": "no",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    # ----------------------------------------------------------------
    # ARTILLERY — long-range bombardment, don't chase
    # ----------------------------------------------------------------
    "ARTILLERY": {
        "Priority":                  "10",
        "Max":                       "1",
        "Suicide":                   "no",
        "Aggressive":                "no",    # don't chase — hold position and fire
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "no",
        "AreTeamMembersRecruitable": "no",
        "Annoyance":                 "no",
        "GuardSlower":               "no",    # NOT for speed-matching — defensive delay only
    },
    # ----------------------------------------------------------------
    # BUILDING KILLERS — target structures specifically
    # ----------------------------------------------------------------
    "ANTI_BUILDING": {
        "Priority":                  "10",
        "Max":                       "2",
        "Suicide":                   "no",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "yes",
        "AreTeamMembersRecruitable": "yes",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    # Factory killer — high priority, destroy production
    "FACTORY_KILLER": {
        "Priority":                  "5",     # high priority — crippling attack
        "Max":                       "1",
        "Suicide":                   "no",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "no",
        "AreTeamMembersRecruitable": "no",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    # ConYard killer — highest priority target in game
    "CY_KILLER": {
        "Priority":                  "5",
        "Max":                       "1",
        "Suicide":                   "no",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "no",
        "AreTeamMembersRecruitable": "no",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    # ----------------------------------------------------------------
    # ANTI-UNIT ROLES
    # ----------------------------------------------------------------
    "ANTI_VEHICLE": {
        "Priority":                  "10",
        "Max":                       "2",
        "Suicide":                   "no",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "yes",
        "AreTeamMembersRecruitable": "yes",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    "ANTI_INF": {
        "Priority":                  "10",
        "Max":                       "2",
        "Suicide":                   "no",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "yes",
        "AreTeamMembersRecruitable": "yes",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    # ----------------------------------------------------------------
    # AA — area guard while available, escorts
    # ----------------------------------------------------------------
    "AA": {
        "Priority":                  "10",
        "Max":                       "2",
        "Suicide":                   "no",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "yes",
        "AreTeamMembersRecruitable": "yes",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    # ----------------------------------------------------------------
    # AERIAL ROLES — three bands based on AA threat
    # ----------------------------------------------------------------
    "AERIAL_RUSH": {
        "Priority":                  "5",
        "Max":                       "3",
        "Suicide":                   "no",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "yes",
        "AreTeamMembersRecruitable": "yes",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    "AERIAL_WAVE": {
        "Priority":                  "10",
        "Max":                       "2",
        "Suicide":                   "no",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "yes",
        "AreTeamMembersRecruitable": "yes",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    "AERIAL_ASSAULT": {
        "Priority":                  "10",
        "Max":                       "2",
        "Suicide":                   "no",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "yes",
        "AreTeamMembersRecruitable": "yes",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    # ----------------------------------------------------------------
    # ENGINEER RUSH — precision capture team, sneaks in
    # ----------------------------------------------------------------
    "ENGINEER": {
        "Priority":                  "5",     # high priority
        "Max":                       "1",
        "Suicide":                   "no",
        "Aggressive":                "yes",   # push to target
        "AvoidThreats":              "yes",   # sneak past defenders
        "Whiner":                    "no",
        "LooseRecruit":              "no",
        "AreTeamMembersRecruitable": "no",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
        "Prebuild":                  "yes",   # must arrive at full strength
    },
    # ----------------------------------------------------------------
    # SPY — infiltration, needs to reach target alive
    # ----------------------------------------------------------------
    "SPY": {
        "Priority":                  "5",
        "Max":                       "1",
        "Suicide":                   "no",
        "Aggressive":                "no",    # don't engage, sneak
        "AvoidThreats":              "yes",   # evade guards
        "Whiner":                    "no",
        "LooseRecruit":              "no",
        "AreTeamMembersRecruitable": "no",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
        "Prebuild":                  "yes",
    },
    # ----------------------------------------------------------------
    # NAVAL
    # ----------------------------------------------------------------
    "NAVAL": {
        "Priority":                  "10",
        "Max":                       "2",
        "Suicide":                   "no",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "yes",
        "AreTeamMembersRecruitable": "yes",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    "ANTI_NAVAL": {
        "Priority":                  "10",
        "Max":                       "2",
        "Suicide":                   "no",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "yes",
        "AreTeamMembersRecruitable": "yes",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
    # ----------------------------------------------------------------
    # HIGH DPS — elite strike force
    # ----------------------------------------------------------------
    "HIGH_DPS": {
        "Priority":                  "10",
        "Max":                       "2",
        "Suicide":                   "no",
        "Aggressive":                "yes",
        "AvoidThreats":              "no",
        "Whiner":                    "no",
        "LooseRecruit":              "yes",
        "AreTeamMembersRecruitable": "yes",
        "Annoyance":                 "no",
        "GuardSlower":               "no",
    },
}


# Default for roles not in the table
DEFAULT_ROLE_FLAGS = {
    "Priority":                  "10",
    "Max":                       "2",
    "Suicide":                   "no",
    "Aggressive":                "yes",
    "AvoidThreats":              "no",
    "Whiner":                    "no",
    "LooseRecruit":              "yes",
    "AreTeamMembersRecruitable": "yes",
    "Annoyance":                 "no",
    "GuardSlower":               "no",
}


# Pool team flags
POOL_FLAGS = {
    "Priority":                  "5",
    "Max":                       "1",
    "Autocreate":                "yes",
    "Prebuild":                  "no",
    "Reinforce":                 "no",
    "Full":                      "no",
    "Loadable":                  "no",
    "VeteranLevel":              "1",
    "OnlyTargetHouseEnemy":      "no",
    "IonImmune":                 "no",
    "Recruiter":                 "no",
    "Group":                     "-1",
    "MindControlDecision":       "0",
    "IsBaseDefense":             "yes",
    "Whiner":                    "yes",
    "Aggressive":                "yes",
    "Suicide":                   "no",
    "AvoidThreats":              "no",
    "GuardSlower":               "no",
    "AreTeamMembersRecruitable": "yes",
    "LooseRecruit":              "no",
    "Annoyance":                 "no",
    "OnTransOnly":               "no",
    # Required fields vanilla always writes
    "TechLevel":                 "0",
    "House":                     "<none>",
    "Droppod":                   "no",
    "UseTransportOrigin":        "no",
    "TransportsReturnOnUnload":  "no",
    "Tag":                       "<none>",
    "Waypoint":                  "none",
}

# Pool team script: Guard mission (skirmish-safe, units stay recruitable)
POOL_SCRIPT_ACTIONS = [(11, 5)]  # Assign mission: Guard


# ---------------------------------------------------------------------------
# TeamType entry
# ---------------------------------------------------------------------------

@dataclass
class TeamEntry:
    team_id:       str
    name:          str
    label_comment: str
    taskforce_id:  str
    script_id:     str
    flags:         dict[str, str]
    faction:       str
    phase:         str
    role:          str
    difficulty:    str
    is_pool:       bool = False


def make_team_label_comment(faction: str, phase: str, role: str,
                             difficulty: str, is_pool: bool = False,
                             pool_type: str = "") -> str:
    """
    Label comment above TeamType section.
    Format: ; [FACTION][PHASE][ROLE][DIFFICULTY][POOL?]
    """
    parts = [f"; [{faction.upper()}]"]
    if is_pool:
        parts.append(f"[POOL]")
        if pool_type:
            parts.append(f"[{pool_type.upper()}]")
    else:
        phase_lbl = f"{phase.upper()}_PHASE" if phase == "Rush" else phase.upper()
        parts.append(f"[{phase_lbl}]")
        parts.append(f"[{role}]")
        parts.append(f"[{difficulty.upper()}]")
    return "".join(parts)


def build_team_flags(role: str, rng: random.Random) -> dict[str, str]:
    """
    Build the full flag dict for a TeamType from role-based defaults.
    Some flags have randomized variance (AvoidThreats for general attack).
    """
    flags = dict(FIXED_FLAGS)
    role_overrides = ROLE_FLAGS.get(role, DEFAULT_ROLE_FLAGS)
    flags.update(role_overrides)

    # Random variance for General Attack: 30% chance AvoidThreats=yes
    if role == "GENERAL_ATTACK":
        flags["AvoidThreats"] = "yes" if rng.random() < 0.30 else "no"

    return flags


# ---------------------------------------------------------------------------
# Pool team unit selection
# ---------------------------------------------------------------------------

def _select_pool_units(parser: RulesMdParser,
                        faction: str,
                        tier: str,
                        unit_category: str) -> list[tuple[int, str]]:
    """
    Select units for a pool team taskforce.
    Separate by category: Infantry or Vehicle.
    Picks the best-graded unit at exactly that tier (not cumulative).
    Filters out naval and aerial units from vehicle pools.
    """
    from .taskforce_generator import MOBILITY_NAVAL, MOBILITY_AMPHIBIOUS, MOBILITY_AIRCRAFT

    tier_map = {
        "Early":   (0, 3),
        "Mid":     (4, 4),
        "Late":    (5, 5),
        "EndGame": (6, 99),
    }
    depth_lo, depth_hi = tier_map.get(tier, (0, 3))

    candidates = []
    for rec in parser.buildable:
        if rec.faction != faction:
            continue
        if rec.category != unit_category:
            continue
        if rec.prereq_depth < depth_lo or rec.prereq_depth > depth_hi:
            continue
        if rec.is_suicide or rec.is_utility or rec.is_no_combat:
            continue
        if rec.build_limit > 0:
            continue  # heroes never go in pool teams
        if rec.grade < DEFAULT_MIN_GRADE:
            continue
        # Vehicle pools: exclude naval and aircraft
        if unit_category == "Vehicle":
            if rec.mobility in (MOBILITY_NAVAL, MOBILITY_AIRCRAFT):
                continue
            if rec.is_aircraft_type:
                continue
        candidates.append(rec)

    if not candidates:
        # Fall back to depth 0-3 (Early) for tiers with no unique units
        return _select_pool_units_fallback(
            parser, faction, "Early", unit_category)

    # Sort by grade descending; pick the best
    candidates.sort(key=lambda r: -r.grade)
    best = candidates[0]

    count_map = {
        "Infantry": {"Early": 4, "Mid": 5, "Late": 6, "EndGame": 6},
        "Vehicle":  {"Early": 2, "Mid": 3, "Late": 3, "EndGame": 4},
    }
    count = count_map.get(unit_category, {}).get(tier, 3)

    if best.build_limit > 0:
        count = min(count, best.build_limit)

    return [(count, best.unit_id)]


def _select_pool_units_fallback(parser, faction, tier, unit_category):
    """Fallback for tiers with no qualifying units."""
    from .taskforce_generator import MOBILITY_NAVAL, MOBILITY_AIRCRAFT
    candidates = [
        rec for rec in parser.buildable
        if rec.faction == faction
        and rec.category == unit_category
        and not rec.is_suicide and not rec.is_utility and not rec.is_no_combat
        and rec.build_limit == 0
        and rec.grade >= DEFAULT_MIN_GRADE
        and (unit_category != "Vehicle" or
             rec.mobility not in (MOBILITY_NAVAL, MOBILITY_AIRCRAFT))
    ]
    if not candidates:
        return []
    best = sorted(candidates, key=lambda r: -r.grade)[0]
    count = 4 if unit_category == "Infantry" else 2
    if best.build_limit > 0:
        count = min(count, best.build_limit)
    return [(count, best.unit_id)]


def _select_pool_units_elite(parser: RulesMdParser,
                              faction: str,
                              tier: str,
                              unit_category: str) -> list[tuple[int, str]]:
    """
    Elite variant of _select_pool_units: picks the highest-prereq-depth unit
    of the given category available up to the tier's max depth, rather than
    the best-graded one at the tier's exact depth. This produces an "elite"
    defense pool team using late-tech units.

    Result may be identical to _select_pool_units if there are no higher-
    prereq alternatives at that tier — the caller compares and skips if so.
    """
    from .taskforce_generator import MOBILITY_NAVAL, MOBILITY_AIRCRAFT

    depth_ceiling = {"Mid": 4, "Late": 5, "EndGame": 99}.get(tier, 3)

    candidates = []
    for rec in parser.buildable:
        if rec.faction != faction:
            continue
        if rec.category != unit_category:
            continue
        if rec.prereq_depth < 3 or rec.prereq_depth > depth_ceiling:
            continue   # elite = depth 3+ minimum
        if rec.is_suicide or rec.is_utility or rec.is_no_combat:
            continue
        if rec.build_limit > 0:
            continue
        if rec.grade < DEFAULT_MIN_GRADE:
            continue
        if unit_category == "Vehicle":
            if rec.mobility in (MOBILITY_NAVAL, MOBILITY_AIRCRAFT):
                continue
            if rec.is_aircraft_type:
                continue
        candidates.append(rec)

    if not candidates:
        return []

    # Prefer HIGHEST prereq depth, then highest grade — the whole point of
    # elite pools is to use late-tech units even if a cheaper one grades
    # equally well.
    candidates.sort(key=lambda r: (-r.prereq_depth, -r.grade))
    best = candidates[0]

    # Elite pools are smaller (harder to build) — half the count of base pool
    count_map = {
        "Infantry": {"Mid": 3, "Late": 3, "EndGame": 3},
        "Vehicle":  {"Mid": 2, "Late": 2, "EndGame": 2},
    }
    count = count_map.get(unit_category, {}).get(tier, 2)
    if best.build_limit > 0:
        count = min(count, best.build_limit)

    return [(count, best.unit_id)]



# ---------------------------------------------------------------------------
# TeamType registry (name dedup, ID collision prevention)
# ---------------------------------------------------------------------------

class TeamRegistry:
    """
    Tracks TeamType IDs and names.
    Uses GlobalIDRegistry for ID generation.
    """

    _PATTERN = re.compile(r"^\[([0-9A-Fa-f]{8}-G)\]")

    def __init__(self, global_registry,
                 existing_names: set[str] | None = None):
        from .id_registry import GlobalIDRegistry
        self._global = global_registry
        self._names: set[str] = set(existing_names or [])

    @classmethod
    def from_aimd(cls, aimd_path: str | Path,
                  global_registry=None) -> "TeamRegistry":
        from .id_registry import GlobalIDRegistry as GIR
        if global_registry is None:
            global_registry = GIR.from_aimd(aimd_path)

        existing_names: set[str] = set()
        path = Path(aimd_path)
        if path.exists():
            current_id = None
            with path.open(encoding="latin-1") as fh:
                for line in fh:
                    s = line.strip()
                    m = cls._PATTERN.match(s)
                    if m:
                        current_id = m.group(1)
                    elif current_id and s.lower().startswith("name="):
                        name = s[5:].strip()
                        if name:
                            existing_names.add(name.lower())

        return cls(global_registry, existing_names)

    def generate(self) -> str:
        return self._global.generate()

    def unique_name(self, name: str) -> str:
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


# ---------------------------------------------------------------------------
# TeamType generator
# ---------------------------------------------------------------------------

class TeamGenerator:
    """
    Generates TeamType entries by combining TaskforceEntry + ScriptEntry.

    Also generates pool teams for each faction — dedicated base defense
    units that keep the AI stocked with recruitable units.

    Usage:
        global_reg = GlobalIDRegistry.from_aimd("aimd.ini")
        team_reg   = TeamRegistry.from_aimd("aimd.ini", global_reg)
        gen        = TeamGenerator(parser, team_reg, seed=42)

        # Attack teams from existing taskforces + scripts
        teams = gen.generate_for_taskforces(taskforces, scripts)

        # Pool teams (base defense + recruitment pool)
        pool_teams = gen.generate_pool_teams(factions)

        gen.write_to_ini(teams + pool_teams, "aimd.ini")
    """

    # Pool script ID cache: action tuple -> script ID
    # Populated during generate_pool_teams, reuses script if already exists
    _pool_script_id: str | None = None

    def __init__(self, parser: RulesMdParser,
                 registry: TeamRegistry,
                 seed: int | None = None):
        self.parser   = parser
        self.registry = registry
        self.rng      = random.Random(seed)

    def generate_for_taskforces(self,
                                 taskforces: list[TaskforceEntry],
                                 scripts: list) -> list[TeamEntry]:
        """
        Generate one TeamType per taskforce, pairing it with its script.
        """
        # Build script lookup: taskforce_id -> script_id
        script_lookup: dict[str, str] = {}
        for s in scripts:
            for tf_id in s.taskforce_ids:
                script_lookup[tf_id] = s.script_id

        teams = []
        for tf in taskforces:
            script_id = getattr(tf, 'script_id', None)
            if not script_id:
                script_id = script_lookup.get(tf.tf_id)
            if not script_id:
                continue

            # Parse labels from taskforce comment
            labels = set(re.findall(r"\[([^\]]+)\]", tf.label_comment))

            flags = build_team_flags(tf.role, self.rng)

            # Human-readable name: "GDI Mid General Attack Hard"
            faction_name = {
                "gdi":       "GDI",
                "nod":       "Soviet",
                "thirdside": "Yuri",
            }.get(tf.faction, tf.faction.upper())
            raw_name = f"{faction_name} {tf.phase} {tf.role.replace('_',' ').title()} {tf.difficulty}"
            name = self.registry.unique_name(raw_name)

            label = make_team_label_comment(
                tf.faction, tf.phase, tf.role, tf.difficulty)

            team_id = self.registry.generate()

            teams.append(TeamEntry(
                team_id       = team_id,
                name          = name,
                label_comment = label,
                taskforce_id  = tf.tf_id,
                script_id     = script_id,
                flags         = flags,
                faction       = tf.faction,
                phase         = tf.phase,
                role          = tf.role,
                difficulty    = tf.difficulty,
            ))

        return teams

    def generate_pool_teams(self,
                             factions: list[str],
                             tf_registry: IDRegistry,
                             script_registry,
                             aimd_path: str | Path,
                             taskforce_scale: float = 1.0) -> list[TeamEntry]:
        """
        Generate pool/defense teams for each faction.
        Per faction, per tier, per unit category (Infantry + Vehicle).
        Uses a shared simple Guard script (11,5).

        Pool teams:
          - GDI/NOD/Thirdside × Early/Mid/Late × Infantry/Vehicle = 18 teams
          - Plus AA pool if faction has AA units
        """
        from .script_generator import ScriptRegistry, ScriptEntry

        # Get or create the pool Guard script
        pool_actions = list(POOL_SCRIPT_ACTIONS)
        existing_pool_script = script_registry.find_duplicate(pool_actions)
        if existing_pool_script:
            pool_script_id = existing_pool_script
        else:
            pool_script_id = script_registry.generate_id()
            pool_name      = script_registry.unique_name("Pool Guard")
            script_registry.register(pool_script_id, pool_actions, pool_name)
            # Store for writing later — caller must handle this
            self._new_pool_script = ScriptEntry(
                script_id     = pool_script_id,
                name          = pool_name,
                label_comment = "; [SCRIPT][POOL][GUARD]",
                actions       = pool_actions,
                phobos        = False,
                is_duplicate  = False,
                taskforce_ids = [],
            )

        tiers = ["Early", "Mid", "Late"]
        categories = [("Infantry", "Inf"), ("Vehicle", "Veh")]

        pool_teams = []
        pool_taskforces = []

        for faction in factions:
            faction_name = {
                "gdi":       "GDI",
                "nod":       "Soviet",
                "thirdside": "Yuri",
            }.get(faction, faction.upper())

            for tier in tiers:
                for category, cat_label in categories:
                    # Pool teams span prerequisite spread: at Mid/Late/EndGame
                    # we also generate an "elite" variant using higher-prereq
                    # units (best-graded at the tier's max depth), so defense
                    # pools include both cheap early-tier units AND late-tier
                    # units unlocked once the AI progresses.
                    variants: list[tuple[str, list[tuple[int, str]]]] = []
                    base_units = _select_pool_units(
                        self.parser, faction, tier, category)
                    if base_units:
                        variants.append(('cheap', base_units))
                    if tier in ('Mid', 'Late', 'EndGame'):
                        elite_units = _select_pool_units_elite(
                            self.parser, faction, tier, category)
                        # only add if the elite pick differs from the base
                        if elite_units and elite_units != base_units:
                            variants.append(('elite', elite_units))
                    if not variants:
                        continue

                    for variant_kind, units in variants:

                        # Apply scale to pool unit counts. Any positive
                        # BuildLimit caps the scaled count (BuildLimit=1
                        # heroes stay at 1, BuildLimit=3 caps at 3, etc.).
                        if taskforce_scale != 1.0:
                            import math
                            scaled = []
                            for count, uid in units:
                                sec = self.parser.ini.section(uid)
                                try:
                                    bl = int(sec.get('buildlimit', '0') or '0') if sec else 0
                                except ValueError:
                                    bl = 0
                                raw = count * taskforce_scale
                                if taskforce_scale < 1.0:
                                    new_count = max(1, math.ceil(raw))
                                else:
                                    new_count = max(1, math.floor(raw))
                                if bl > 0:
                                    new_count = min(new_count, bl)
                                scaled.append((new_count, uid))
                            units = scaled

                        # Check if this exact composition already exists
                        existing_tf_id = tf_registry.find_duplicate(units)
                        if existing_tf_id:
                            tf_id = existing_tf_id
                        else:
                            tf_id   = tf_registry.generate()
                            tf_name = make_vanilla_name(units, self.parser, tf_registry)
                            tf_registry.register_composition(tf_id, units)
                            pool_taskforces.append({
                                "tf_id":   tf_id,
                                "name":    tf_name,
                                "units":   units,
                                "faction": faction,
                                "tier":    tier,
                                "category": category,
                            })

                        # TeamType — variant kind ('cheap'/'elite') distinguishes
                        # the low- and high-prereq pool teams at each tier.
                        variant_suffix = '' if variant_kind == 'cheap' else f' {variant_kind.title()}'
                        raw_name = f"{faction_name} {tier} Pool {category}{variant_suffix}"
                        name     = self.registry.unique_name(raw_name)
                        label    = make_team_label_comment(
                            faction, tier, "POOL", "all",
                            is_pool=True,
                            pool_type=f"{tier}_{cat_label}"
                                       f"{'_ELITE' if variant_kind == 'elite' else ''}")
                        team_id  = self.registry.generate()

                        flags = dict(POOL_FLAGS)

                        pool_teams.append(TeamEntry(
                            team_id       = team_id,
                            name          = name,
                            label_comment = label,
                            taskforce_id  = tf_id,
                            script_id     = pool_script_id,
                            flags         = flags,
                            faction       = faction,
                            phase         = tier,
                            role          = "POOL",
                            difficulty    = "all",
                            is_pool       = True,
                        ))

        # Store pool taskforces for write_to_ini
        self._pool_taskforces = pool_taskforces
        return pool_teams

    def write_to_ini(self,
                      teams:       list[TeamEntry],
                      aimd_path:   str | Path,
                      output_path: str | Path | None = None) -> Path:
        """
        Write TeamTypes to aimd.ini.
        Also writes pool taskforces and pool script if generated.
        """
        aimd_path   = Path(aimd_path)
        output_path = Path(output_path) if output_path else aimd_path

        lines = []
        if aimd_path.exists():
            with aimd_path.open(encoding="latin-1") as fh:
                lines = fh.readlines()

        while lines and lines[-1].strip() in (";", ""):
            lines.pop()

        # --- Write pool taskforces to [TaskForces] list ---
        pool_tfs = getattr(self, "_pool_taskforces", [])
        if pool_tfs:
            tf_idx = None
            for i, line in enumerate(lines):
                if line.strip() == "[TaskForces]":
                    tf_idx = i
                    break

            if tf_idx is not None:
                # Find end of [TaskForces] list and highest index
                highest = -1
                i = tf_idx + 1
                while i < len(lines):
                    s = lines[i].strip()
                    if s.startswith("[") and s != "[TaskForces]": break
                    if "=" in s and not s.startswith(";"):
                        try:
                            highest = max(highest, int(s.split("=")[0].strip()))
                        except ValueError: pass
                    i += 1
                insert_at = i
                new_list = [
                    f"{highest + 1 + j}={ptf['tf_id']}  ; YR-AI-Tool\r\n"
                    for j, ptf in enumerate(pool_tfs)
                ]
                lines[insert_at:insert_at] = new_list

            # Append pool taskforce sections
            lines.append("\r\n")
            for ptf in pool_tfs:
                lines.append(f"; [POOL][{ptf['faction'].upper()}][{ptf['tier'].upper()}][{ptf['category'].upper()}]\r\n")
                lines.append(f"[{ptf['tf_id']}]\r\n")
                lines.append(f"Name={ptf['name']}\r\n")
                for idx, (count, uid) in enumerate(ptf['units']):
                    lines.append(f"{idx}={count},{uid}\r\n")
                lines.append(f"Group=-1\r\n")
                lines.append("\r\n")

        # --- Write pool Guard script to [ScriptTypes] if new ---
        new_pool_script = getattr(self, "_new_pool_script", None)
        if new_pool_script:
            sc_idx = None
            for i, line in enumerate(lines):
                if line.strip() == "[ScriptTypes]":
                    sc_idx = i
                    break
            if sc_idx is not None:
                highest = -1
                i = sc_idx + 1
                while i < len(lines):
                    s = lines[i].strip()
                    if s.startswith("[") and s != "[ScriptTypes]": break
                    if "=" in s and not s.startswith(";"):
                        try:
                            highest = max(highest, int(s.split("=")[0].strip()))
                        except ValueError: pass
                    i += 1
                lines[i:i] = [
                    f"{highest + 1}={new_pool_script.script_id}  ; YR-AI-Tool\r\n"
                ]
            # Append script section
            lines.append(f"{new_pool_script.label_comment}\r\n")
            lines.append(f"[{new_pool_script.script_id}]\r\n")
            lines.append(f"Name={new_pool_script.name}\r\n")
            for idx, (action, arg) in enumerate(new_pool_script.actions):
                lines.append(f"{idx}={action},{arg}\r\n")
            lines.append("\r\n")

        # --- Write [TeamTypes] list and sections ---
        tt_idx = None
        for i, line in enumerate(lines):
            if line.strip() == "[TeamTypes]":
                tt_idx = i
                break
        if tt_idx is None:
            lines.append("\n[TeamTypes]\n")
            tt_idx = len(lines) - 1

        highest = -1
        i = tt_idx + 1
        while i < len(lines):
            s = lines[i].strip()
            if s.startswith("[") and s != "[TeamTypes]": break
            if "=" in s and not s.startswith(";"):
                try:
                    highest = max(highest, int(s.split("=")[0].strip()))
                except ValueError: pass
            i += 1
        insert_at = i

        new_list = [
            f"{highest + 1 + j}={t.team_id}  ; YR-AI-Tool\r\n"
            for j, t in enumerate(teams)
        ]
        lines[insert_at:insert_at] = new_list

        # Append TeamType sections
        lines.append("\r\n")
        for t in teams:
            lines.append(f"{t.label_comment}\r\n")
            lines.append(f"[{t.team_id}]\r\n")
            lines.append(f"Name={t.name}\r\n")
            lines.append(f"Script={t.script_id}\r\n")
            lines.append(f"TaskForce={t.taskforce_id}\r\n")
            for flag, val in sorted(t.flags.items()):
                lines.append(f"{flag}={val}\r\n")
            lines.append("\r\n")

        lines.append(";\r\n")

        with output_path.open("w", encoding="latin-1") as fh:
            fh.writelines(lines)

        return output_path
