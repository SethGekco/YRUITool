"""
taskforce_generator.py
======================
Generates Taskforce entries for YR / YR-mod aimd.ini files.

A Taskforce defines WHAT units are in an attack wave. Combined with a
Script (HOW they behave) and a Team (configuration) via a Trigger.

== SIZE SYSTEM ==
RelaxedStray (R) from rulesmd.ini [General] determines safe gathering size.
  Vehicle slots  = R * 2 * R  (vanilla R=3.0 -> 18 slots)
  Infantry slots = vehicle slots * 3  (3 infantry per cell)
  Aerial units   ignore slot limits entirely (stack freely, no gather)
  Mixed:         each vehicle = 1 slot, each infantry = 1/3 slot

If total slots > vehicle capacity: taskforce uses DIRECT ATTACK script
If total slots <= vehicle capacity: taskforce CAN use GATHER script

== PHASE SIZE DEFAULTS (vehicle slots) ==
  Rush:    16   Medium: 8    Easy: 4
  Early:   16   Medium: 8    Easy: 4
  Mid:     20   Medium: 10   Easy: 5
  Late:    24   Medium: 12   Easy: 6
  EndGame: 32   Medium: 16   Easy: 8

== RUSH CONSTRAINTS ==
  Early game units only
  Total cost <= $10,000
  Under 16 vehicle slots / 48 infantry / or combination

== ROLES ==
Roles are extensible strings. Each has a scoring function and
optional unit filter. Current implemented roles:
  RUSH           - fastest/cheapest, straight attack, early only
  ANTI_INF       - high DPS vs infantry armor
  ANTI_VEHICLE   - high DPS vs vehicle armor (includes miner killers)
  ANTI_BUILDING  - high DPS vs building armor, range not critical
  ANTI_DEFENSES  - outranges base defenses + high building DPS
                   fallback: high health if no outrange units exist
  ARTILLERY      - range well above faction average
  HIGH_DPS       - raw damage across all targets
  MINER_KILLER   - units tagged Miner_Killer
  AA             - anti-air units
  NAVAL          - naval mobility units
  ANTI_NAVAL     - AN role tag (future, stubbed)
  GENERAL_ATTACK - balanced, no dominant DPS category
                   detected by collective unit score balance

Role is either:
  Predetermined: tool picks role first, selects qualifying units
  Emergent:      tool picks by grade, labels the dominant role

Future roles (TODO): transport loading, garrison/building entry,
  AI personalities (RUSHER/STORMROLLER/TURTLE/GUERILLA), etc.

== COMPOSITION PHILOSOPHY ==
Varied per taskforce (randomly selected):
  grade_dominant  - best grade units dominate selection
  cost_dominant   - cheapest units dominate
  blended         - mix of grade and cost
  inverted        - lower grade units for flavor/Easy difficulty

== NAMING ==
Name= mirrors vanilla: "3 GIs, 3 GGIs"
Comment line above section carries machine-readable labels:
  ; [FACTION][PHASE][ROLE][COUNTRY?][NO_COUNTRY?][DIFFICULTY]

== TASKFORCE TYPES ==
  Pure infantry, pure vehicle, mixed, aerial (future)
  Role-oriented or grade-driven

== EXISTING ENTRY HANDLING ==
User chooses: delete / keep+use / keep+ignore
No duplicates — if accidental duplicate, merge labels instead.
"""

import random
import re
import secrets
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from rulesmd_parser import (
    RulesMdParser, UnitRecord,
    TIER_DEPTH_CEILING,
    LAND_MIX_GROUP, NAVAL_MIX_GROUP, AIRCRAFT_GROUP, SPECIAL_EXCLUDED,
    ARMOR_INFANTRY, ARMOR_VEHICLE, ARMOR_BUILDING,
    effective_dps,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Phase size limits in vehicle slots (Hard difficulty = 100%)
PHASE_SIZES = {
    "Rush":    16,
    "Early":   16,
    "Mid":     20,
    "Late":    24,
    "EndGame": 32,
}

DIFFICULTY_SCALE = {
    "Easy":   0.25,
    "Medium": 0.50,
    "Hard":   1.00,
}

# Rush cost ceiling
RUSH_MAX_COST = 10_000

# Minimum grade threshold default
DEFAULT_MIN_GRADE = 0

# Grade weight constants for selection
GRADE_BASE_WEIGHT  = 5
GRADE_WEIGHT_SCALE = 2

# Role dominance threshold: one DPS category must exceed others by this ratio
# to be considered a specialist role vs GENERAL_ATTACK
ROLE_DOMINANCE_THRESHOLD = 1.5

# Composition philosophies
COMPOSITIONS = ["grade_dominant", "cost_dominant", "blended", "inverted"]

# Faction label map
FACTION_LABELS = {}  # populated from parser.sides at runtime

# Difficulty labels
DIFFICULTY_LABEL = {
    "Easy":   "EASY",
    "Medium": "MEDIUM",
    "Hard":   "HARD",
}

# All currently implemented roles
ALL_ROLES = [
    "RUSH",
    "INFANTRY_RUSH_EARLY",   # early game infantry rush, gates on elapsed time + no defenses
    "ANTI_INF",
    "ANTI_VEHICLE",
    "ANTI_BUILDING",
    "ANTI_DEFENSES",
    "ARTILLERY",
    "HIGH_DPS",
    "MINER_KILLER",
    "AA",
    "AERIAL_RUSH",           # fast light aircraft, no/zero AA present
    "AERIAL_WAVE",           # medium aerial, low AA present
    "AERIAL_ASSAULT",        # heavy + fodder mix, moderate AA present
    "NAVAL",
    "ANTI_NAVAL",   # stub — AN role not yet implemented
    "GENERAL_ATTACK",
]

# Roles that only apply to specific unit types
AERIAL_ONLY_ROLES  = {"AA", "AERIAL_RUSH", "AERIAL_WAVE", "AERIAL_ASSAULT"}
NAVAL_ONLY_ROLES   = {"NAVAL", "ANTI_NAVAL"}
GROUND_ONLY_ROLES  = {"RUSH", "INFANTRY_RUSH_EARLY", "ANTI_INF", "ANTI_VEHICLE",
                       "ANTI_BUILDING", "ANTI_DEFENSES", "ARTILLERY", "HIGH_DPS",
                       "MINER_KILLER", "GENERAL_ATTACK"}

# Mobility group labels for taskforce compatibility
# LAND + NAVAL cannot coexist. AMPHIBIOUS mixes with either. AERIAL mixes with either.
MOBILITY_LAND      = "Land"
MOBILITY_NAVAL     = "Naval"
MOBILITY_AMPHIBIOUS = "Amphibious"
MOBILITY_AERIAL    = "Jumpjet"
MOBILITY_AIRCRAFT  = "Aircraft"

# Groups that cannot mix
INCOMPATIBLE_MOBILITY_PAIRS = {
    (MOBILITY_LAND, MOBILITY_NAVAL),
    (MOBILITY_NAVAL, MOBILITY_LAND),
}

# Country special inclusion rate (30% of taskforces target a specific country)
COUNTRY_SPECIAL_RATE = 0.30


# ---------------------------------------------------------------------------
# ID registry
# ---------------------------------------------------------------------------

class IDRegistry:
    """
    Tracks taskforce IDs, Name= values, and unit compositions.
    Delegates ID generation to a GlobalIDRegistry to ensure uniqueness
    across ALL section types (TaskForces, ScriptTypes, TeamTypes, etc.).
    """

    _PATTERN     = re.compile(r"^\[([0-9A-Fa-f]{8}-G)\]")
    _UNIT_ENTRY  = re.compile(r"^(\d+)=(\d+),(\S+)")

    def __init__(self, global_registry,
                 existing_names: set[str] | None = None,
                 existing_compositions: dict | None = None):
        from id_registry import GlobalIDRegistry
        self._global        = global_registry
        self._names:        set[str] = set(existing_names        or [])
        # fingerprint -> existing tf_id
        self._compositions: dict[frozenset, str] = existing_compositions or {}

    def generate(self) -> str:
        return self._global.generate()

    def register_name(self, name: str):
        self._names.add(name.lower())

    def is_name_taken(self, name: str) -> bool:
        return name.lower() in self._names

    def unique_name(self, name: str) -> str:
        """Return name, appending a suffix if already taken."""
        if not self.is_name_taken(name):
            self.register_name(name)
            return name
        i = 2
        while True:
            candidate = f"{name[:47]} {i}"
            if not self.is_name_taken(candidate):
                self.register_name(candidate)
                return candidate
            i += 1

    def find_duplicate(self,
                       units: list[tuple[int, str]]) -> str | None:
        """
        Return existing taskforce ID if an identical unit composition exists.
        Composition is order-insensitive: (3,ZEP) == (3,ZEP) regardless
        of which line they appear on.
        """
        fp = frozenset(units)
        return self._compositions.get(fp)

    def register_composition(self, tf_id: str,
                              units: list[tuple[int, str]]):
        """Register a generated taskforce composition for future dedup."""
        self._compositions[frozenset(units)] = tf_id
        if not hasattr(self, '_units_by_id'):
            self._units_by_id = {}
        self._units_by_id[tf_id] = list(units)

    def register_entry(self, tf_id: str, entry: object) -> None:
        """Cache a TaskforceEntry for reuse on duplicate detection."""
        if not hasattr(self, '_entries_by_id'):
            self._entries_by_id = {}
        self._entries_by_id[tf_id] = entry

    def get_entry(self, tf_id: str) -> object | None:
        """Return cached TaskforceEntry for tf_id, or None."""
        if not hasattr(self, '_entries_by_id'):
            return None
        return self._entries_by_id.get(tf_id)

    def get_units(self, tf_id: str) -> list[tuple[int, str]]:
        """Return units for a registered taskforce, or empty list."""
        if not hasattr(self, '_units_by_id'):
            return []
        return self._units_by_id.get(tf_id, [])

    @classmethod
    def from_aimd(cls, aimd_path: str | Path,
                  global_registry=None) -> "IDRegistry":
        """
        Load existing taskforce names and compositions from aimd.ini.
        Requires a GlobalIDRegistry that has already scanned the file for IDs.
        """
        from id_registry import GlobalIDRegistry as GIR
        if global_registry is None:
            global_registry = GIR.from_aimd(aimd_path)

        existing_names:        set[str]             = set()
        existing_compositions: dict[frozenset, str] = {}
        path = Path(aimd_path)

        if path.exists():
            current_id:   str | None = None
            current_units: list[tuple[int, str]] = []

            with path.open(encoding="latin-1") as fh:
                for line in fh:
                    s = line.strip()
                    m = cls._PATTERN.match(s)
                    if m:
                        # Save previous taskforce composition
                        if current_id and current_units:
                            existing_compositions[frozenset(current_units)] = current_id
                        current_id    = m.group(1)
                        current_units = []
                        continue
                    if current_id:
                        if s.lower().startswith("name="):
                            name = s[5:].strip()
                            if name:
                                existing_names.add(name.lower())
                        else:
                            m2 = cls._UNIT_ENTRY.match(s)
                            if m2:
                                count = int(m2.group(2))
                                uid   = m2.group(3)
                                current_units.append((count, uid))
                # Save last entry
                if current_id and current_units:
                    existing_compositions[frozenset(current_units)] = current_id

        return cls(global_registry, existing_names, existing_compositions)


# ---------------------------------------------------------------------------
# Role-weighted grade system
# ---------------------------------------------------------------------------

# Per-role metric weights. Each entry is a dict of grade_breakdown key -> multiplier.
# Metrics not listed default to 1.0x.
# These weights determine how important each metric is for a given role,
# allowing the selection system to favor units that are genuinely suited
# to the wave's purpose rather than just highest overall grade.
ROLE_METRIC_WEIGHTS: dict[str, dict[str, float]] = {
    "RUSH": {
        "dps_infantry": 1.0,
        "dps_vehicle":  1.0,
        "dps_building": 0.5,
        "miner_killer": 0.5,
        "range":        0.5,
        "outranges_def":0.5,
        "health":       0.5,
        "surv_per_cost":0.5,
        "cost_spam":    3.0,
        "time_spam":    3.0,
        "speed":        3.0,
    },
    "ANTI_INF": {
        "dps_infantry": 3.0,
        "dps_vehicle":  0.5,
        "dps_building": 0.5,
        "miner_killer": 0.5,
        "range":        0.5,
        "speed":        1.0,
        "cost_spam":    1.0,
        "time_spam":    1.0,
    },
    "ANTI_VEHICLE": {
        "dps_infantry": 0.5,
        "dps_vehicle":  3.0,
        "dps_building": 0.5,
        "miner_killer": 2.0,
        "range":        0.5,
        "speed":        1.0,
    },
    "ANTI_BUILDING": {
        "dps_infantry": 0.5,
        "dps_vehicle":  0.5,
        "dps_building": 3.0,
        "building_bonus":3.0,
        "miner_killer": 0.5,
        "range":        1.0,
        "speed":        0.5,
        "health":       1.0,
    },
    "ANTI_DEFENSES": {
        "dps_infantry": 0.5,
        "dps_vehicle":  0.5,
        "dps_building": 2.0,
        "building_bonus":2.0,
        "range":        3.0,
        "outranges_def":3.0,
        "speed":        0.5,
        "health":       2.0,
        "surv_per_cost":2.0,
    },
    "ARTILLERY": {
        "dps_infantry": 0.5,
        "dps_vehicle":  0.5,
        "dps_building": 1.0,
        "range":        3.0,
        "outranges_def":3.0,
        "speed":        0.5,
        "cost_spam":    0.5,
    },
    "HIGH_DPS": {
        "dps_infantry": 2.0,
        "dps_vehicle":  2.0,
        "dps_building": 2.0,
        "building_bonus":1.5,
        "miner_killer": 1.5,
        "range":        0.5,
        "speed":        0.5,
    },
    "MINER_KILLER": {
        "dps_vehicle":  1.0,
        "miner_killer": 3.0,
        "range":        1.0,
        "speed":        2.0,
        "cost_spam":    1.5,
    },
    "AERIAL": {
        "dps_infantry": 1.5,
        "dps_vehicle":  1.5,
        "dps_building": 1.0,
        "building_bonus":1.0,
        "health":       1.0,
        "speed":        2.0,
        "cost_spam":    1.0,
    },
    # AERIAL_RUSH: fast light aircraft, hit and run, no AA present
    # Favours speed and cost efficiency — get in fast, don't need to survive long
    "AERIAL_RUSH": {
        "speed":        3.0,
        "cost_spam":    3.0,
        "time_spam":    2.0,
        "dps_infantry": 1.0,
        "dps_vehicle":  1.0,
        "dps_building": 1.0,
        "health":       0.5,   # speed over durability
    },
    # AERIAL_WAVE: medium aerial, some AA present — needs enough durability to survive
    "AERIAL_WAVE": {
        "speed":        2.0,
        "dps_infantry": 1.5,
        "dps_vehicle":  1.5,
        "dps_building": 1.0,
        "health":       1.5,   # more survivable than rush
        "cost_spam":    1.5,
    },
    # AERIAL_ASSAULT: heavy + cheap fodder mix, moderate AA present
    # Splits into two tiers:
    #   - cheap fast units (high cost_spam, low grade) absorb AA fire
    #   - expensive heavy units (high grade) deal real damage
    # The generation system handles the split via blended/inverted composition
    "AERIAL_ASSAULT": {
        "dps_infantry": 2.0,
        "dps_vehicle":  2.0,
        "dps_building": 1.5,
        "building_bonus":1.5,
        "health":       2.0,   # needs to survive AA
        "speed":        1.5,
        "cost_spam":    2.0,   # high count of cheap units + some expensive
        "surv_per_cost":1.5,
    },
    # INFANTRY_RUSH_EARLY: cheap fast infantry, early game, target undefended bases
    "INFANTRY_RUSH_EARLY": {
        "cost_spam":    3.0,
        "time_spam":    3.0,
        "speed":        2.5,
        "dps_infantry": 1.5,
        "dps_building": 0.5,
        "health":       0.5,   # expendable
        "range":        0.3,   # close quarters
    },
    "AERIAL_TRANSPORT": {
        "health":       2.0,
        "speed":        2.0,
        "cost_spam":    1.0,
    },
    "AA": {
        "aa_capability_bonus": 3.0,
        "range":        2.0,
        "speed":        1.5,
        "health":       1.0,
    },
    "NAVAL": {
        "dps_infantry": 1.0,
        "dps_vehicle":  1.0,
        "dps_building": 1.0,
        "health":       1.5,
        "range":        1.5,
    },
    "GENERAL_ATTACK": {
        "dps_infantry": 1.0,
        "dps_vehicle":  1.0,
        "dps_building": 1.0,
        "building_bonus":0.5,
        "range":        1.0,
        "health":       1.0,
        "surv_per_cost":1.0,
        "cost_spam":    1.5,
        "time_spam":    1.5,
        "speed":        1.0,
    },
    "RUSH_WAVE": {  # alias for RUSH role label
        "cost_spam": 3.0, "time_spam": 3.0, "speed": 3.0,
        "dps_building": 0.5, "health": 0.5,
    },
    "SUICIDE_RUSH": {},  # handled separately
}

# Default weights for unlisted roles
DEFAULT_METRIC_WEIGHTS: dict[str, float] = {}   # 1.0x for all


def role_weighted_grade(rec: UnitRecord, role: str) -> float:
    """
    Compute a role-specific weighted grade for a unit.

    Takes the unit's per-metric grade breakdown and applies per-role
    multipliers, summing to a single score that reflects how well-suited
    the unit is for the given wave role.

    Metrics not listed in the role weights table use a 1.0x multiplier.
    This allows SHAD to score very low in combat roles (weak DPS metrics
    weighted heavily) while HTK scores well (speed + AA capability).
    """
    weights  = ROLE_METRIC_WEIGHTS.get(role, DEFAULT_METRIC_WEIGHTS)
    bd       = rec.grade_breakdown
    if not bd:
        return float(max(rec.grade, 0))

    total = 0.0
    for metric, value in bd.items():
        w      = weights.get(metric, 1.0)
        total += value * w

    return total



def _rebalance_general_attack(
        composition: list[tuple[int, str]],
        eligible: list[UnitRecord],
        parser: RulesMdParser,
        rng: random.Random,
        max_slots: float) -> list[tuple[int, str]]:
    """
    Rebalance a GENERAL_ATTACK composition so no DPS category dominates.

    Algorithm:
    1. Compute collective DPS totals across inf/veh/bld
    2. Find weakest and strongest categories
    3. If weakest < 30% of strongest:
       a. Find units in composition that contribute most to the dominant category
       b. Remove worst offender (partially or fully)
       c. Add a unit strong in the weakest category
    4. Repeat until balanced or no improvement possible (max 5 iterations)
    """
    BALANCE_THRESHOLD = 0.30  # weakest must be >= 30% of strongest

    def collective_dps(comp):
        totals = {"inf": 0.0, "veh": 0.0, "bld": 0.0}
        for count, uid in comp:
            rec = parser.units.get(uid)
            if not rec:
                continue
            totals["inf"] += count * _dps_vs(rec, ARMOR_INFANTRY)
            totals["veh"] += count * _dps_vs(rec, ARMOR_VEHICLE)
            totals["bld"] += count * _dps_vs(rec, ARMOR_BUILDING)
        return totals

    def unit_category_contribution(rec, category):
        if category == "inf": return _dps_vs(rec, ARMOR_INFANTRY)
        if category == "veh": return _dps_vs(rec, ARMOR_VEHICLE)
        if category == "bld": return _dps_vs(rec, ARMOR_BUILDING)
        return 0.0

    # Build lookup for eligible units by uid
    eligible_map = {u.unit_id: u for u in eligible}

    comp = list(composition)

    # Lock the composition's mobility group up front so the rebalancer can
    # never introduce an incompatible unit (e.g. naval Destroyer into a
    # land Sniper wave). Land/Amphibious/Jumpjet can mix with each other;
    # Naval/Amphibious/Jumpjet can mix with each other; Land and Naval
    # (excluding Amphibious/Jumpjet) never mix.
    comp_recs = [eligible_map.get(uid) or parser.units.get(uid)
                 for _, uid in comp]
    comp_recs = [r for r in comp_recs if r is not None]
    locked_mobility = _dominant_mobility(comp_recs) if comp_recs else None

    for _ in range(5):  # max iterations
        totals = collective_dps(comp)
        if not any(totals.values()):
            break

        strongest = max(totals, key=totals.get)
        weakest   = min(totals, key=totals.get)

        if totals[strongest] == 0:
            break
        ratio = totals[weakest] / totals[strongest]
        if ratio >= BALANCE_THRESHOLD:
            break  # balanced enough

        # Find the unit in composition contributing most to the dominant category
        dominant_contribs = []
        for i, (count, uid) in enumerate(comp):
            rec = eligible_map.get(uid) or parser.units.get(uid)
            if not rec:
                continue
            contrib = count * unit_category_contribution(rec, strongest)
            dominant_contribs.append((contrib, i, uid, count, rec))

        if not dominant_contribs:
            break

        dominant_contribs.sort(reverse=True)
        worst_idx    = dominant_contribs[0][1]
        worst_count  = dominant_contribs[0][3]
        worst_rec    = dominant_contribs[0][4]
        worst_slots  = unit_slots(worst_rec)

        # Find a candidate strong in the weakest category, restricted to
        # units compatible with the composition's locked mobility group.
        candidates = [
            u for u in eligible
            if unit_category_contribution(u, weakest) >
               unit_category_contribution(worst_rec, weakest)
            and u.unit_id not in {uid for _, uid in comp}
            and (locked_mobility is None
                 or u in _filter_compatible_mobility([u], locked_mobility))
        ]
        if not candidates:
            break

        # Pick best candidate for the weak category
        candidates.sort(
            key=lambda u: unit_category_contribution(u, weakest),
            reverse=True)
        new_unit = candidates[0]

        # Reduce the dominant unit by 1 (or remove if count=1)
        remove_count = max(1, worst_count // 2)
        if worst_count - remove_count <= 0:
            comp.pop(worst_idx)
            freed_slots = worst_count * worst_slots
        else:
            comp[worst_idx] = (worst_count - remove_count, comp[worst_idx][1])
            freed_slots = remove_count * worst_slots

        # Add the new unit with freed slots
        new_slots = unit_slots(new_unit)
        if new_slots > 0:
            add_count = max(1, int(freed_slots / new_slots))
        else:
            add_count = max(1, int(freed_slots))

        # Check if this unit type already exists in comp
        existing = [(i, c, uid) for i, (c, uid) in enumerate(comp)
                    if uid == new_unit.unit_id]
        if existing:
            i, c, uid = existing[0]
            comp[i] = (c + add_count, uid)
        else:
            comp.append((add_count, new_unit.unit_id))

    return comp


def _generalist_slot_ratio(composition: list[tuple[int, str]],
                            parser: RulesMdParser) -> float:
    """
    Compute the ratio of slot-weighted GENERALIST units in a composition.
    Each vehicle = 1 slot, each infantry = 1/3 slot, aerial included.
    Returns 0.0–1.0.
    """
    total_slots     = 0.0
    generalist_slots = 0.0
    for count, uid in composition:
        rec = parser.units.get(uid)
        if not rec:
            continue
        s = unit_slots(rec)
        if s == 0:
            s = 0.5  # aerial units count at half a vehicle slot
        total_slots += count * s
        if "GENERALIST" in rec.roles:
            generalist_slots += count * s
    return generalist_slots / max(total_slots, 0.01)


def _rebalance_generalist_ratio(
        composition: list[tuple[int, str]],
        eligible: list[UnitRecord],
        parser: RulesMdParser,
        rng: random.Random,
        min_ratio: float = 0.50) -> list[tuple[int, str]]:
    """
    Ensure GENERALIST units make up at least min_ratio of slot-weighted
    composition. If below threshold:
    1. Find the highest-count specialist group
    2. Reduce it
    3. Add to an existing GENERALIST group if possible, otherwise
       add a new GENERALIST unit (only if result avoids token quantities)
    4. Repeat up to 5 iterations.

    Infantry quantity guard: never add fewer than 3 infantry of a new type
    unless the wave is small (<= 8 slots) or all infantry counts are small.
    """
    GENERALIST_MIN_INF_ADD = 3  # minimum infantry to add of a new type

    generalist_eligible = [u for u in eligible if "GENERALIST" in u.roles]
    if not generalist_eligible:
        return composition  # nothing to do

    eligible_map = {u.unit_id: u for u in eligible}
    comp = list(composition)

    # Lock mobility group from the existing composition so generalist
    # backfill can never introduce an incompatible mobility (e.g. naval
    # unit into a land wave).
    comp_recs_init = [eligible_map.get(uid) or parser.units.get(uid)
                       for _, uid in comp]
    comp_recs_init = [r for r in comp_recs_init if r is not None]
    locked_mobility = _dominant_mobility(comp_recs_init) if comp_recs_init else None
    if locked_mobility is not None:
        generalist_eligible = _filter_compatible_mobility(
            generalist_eligible, locked_mobility)
        if not generalist_eligible:
            return composition

    total_slots_all = sum(
        (unit_slots(parser.units[uid]) or 0.5) * c
        for c, uid in comp if uid in parser.units
    )

    for _ in range(5):
        ratio = _generalist_slot_ratio(comp, parser)
        if ratio >= min_ratio:
            break

        # Find highest-count specialist group to reduce
        specialist_entries = [
            (count, i, uid) for i, (count, uid) in enumerate(comp)
            if uid in parser.units
            and "GENERALIST" not in parser.units[uid].roles
        ]
        if not specialist_entries:
            break
        specialist_entries.sort(reverse=True)
        s_count, s_idx, s_uid = specialist_entries[0]
        s_rec = parser.units[s_uid]
        s_slots = unit_slots(s_rec) or 0.5

        remove = max(1, s_count // 3)
        if s_count - remove <= 0:
            comp.pop(s_idx)
            freed = s_count * s_slots
        else:
            comp[s_idx] = (s_count - remove, s_uid)
            freed = remove * s_slots

        # Try to add to existing GENERALIST group first
        existing_gen = [
            (i, c, uid) for i, (c, uid) in enumerate(comp)
            if uid in parser.units and "GENERALIST" in parser.units[uid].roles
        ]
        if existing_gen:
            # Pick largest existing generalist group to add to
            existing_gen.sort(key=lambda x: -x[1])
            g_idx, g_count, g_uid = existing_gen[0]
            g_rec = parser.units[g_uid]
            g_slots = unit_slots(g_rec) or 0.5
            add = max(1, int(freed / g_slots))
            comp[g_idx] = (g_count + add, g_uid)
        else:
            # No existing generalist — add new type if quantity is acceptable
            # Pick best generalist from eligible not already in comp
            new_gen = [
                u for u in generalist_eligible
                if u.unit_id not in {uid for _, uid in comp}
            ]
            if not new_gen:
                # All generalists already in comp — add to strongest
                if existing_gen:
                    g_idx, g_count, g_uid = existing_gen[0]
                    g_rec = parser.units[g_uid]
                    g_slots = unit_slots(g_rec) or 0.5
                    add = max(1, int(freed / g_slots))
                    comp[g_idx] = (g_count + add, g_uid)
                break

            new_gen.sort(key=lambda u: u.grade, reverse=True)
            new_unit  = new_gen[0]
            new_slots = unit_slots(new_unit) or 0.5
            add_count = max(1, int(freed / new_slots))

            # Infantry quantity guard
            if new_unit.category == "Infantry" and new_slots < 1.0:
                # Check if wave is small or all infantry are in small quantities
                all_inf_counts = [
                    c for c, uid in comp
                    if uid in parser.units
                    and parser.units[uid].category == "Infantry"
                ]
                wave_is_small = total_slots_all <= 8
                all_small     = all_inf_counts and max(all_inf_counts) <= 4
                if not wave_is_small and not all_small:
                    add_count = max(GENERALIST_MIN_INF_ADD, add_count)

            comp.append((add_count, new_unit.unit_id))

    return comp


def _infantry_wave_ratio(composition: list[tuple[int, str]],
                          parser: RulesMdParser) -> float:
    """Returns fraction of total slots that are infantry."""
    inf_slots   = 0.0
    total_slots = 0.0
    for count, uid in composition:
        rec = parser.units.get(uid)
        if not rec: continue
        s = unit_slots(rec) if unit_slots(rec) > 0 else 0.5
        total_slots += count * s
        if rec.category == "Infantry":
            inf_slots += count * s
    return inf_slots / max(total_slots, 0.01)


def _find_country_anchor(faction: str,
                          country: str,
                          phase: str,
                          role: str,
                          parser: RulesMdParser,
                          min_grade: int) -> UnitRecord | None:
    """
    Find the best anchor unit for a country-specific taskforce.
    Anchor = highest role-scoring unit with RequiredHouses=country.

    Returns None if the country has no unique buildable units.
    """
    depth_ceiling = {
        "Rush":    TIER_DEPTH_CEILING.get("Early", 3),
        "Early":   TIER_DEPTH_CEILING.get("Early", 3),
        "Mid":     TIER_DEPTH_CEILING.get("Mid",   4),
        "Late":    TIER_DEPTH_CEILING.get("Late",  5),
        "EndGame": 99,
    }.get(phase, 4)

    role_fn    = ROLE_SCORERS.get(role, role_score_general)
    candidates = []

    for rec in parser.buildable:
        if rec.faction != faction:
            continue
        if rec.is_no_combat or rec.is_utility:
            continue
        if rec.mobility in SPECIAL_EXCLUDED:
            continue
        # Aircraft locomotor excluded from general pools but allowed for AERIAL role
        if rec.mobility == MOBILITY_AIRCRAFT and role not in ("AERIAL", "ANTI_AA"):
            continue
        if rec.is_suicide:
            continue
        if rec.prereq_depth > depth_ceiling:
            continue
        if rec.grade < min_grade:
            continue
        # Must be restricted to this country specifically
        if country not in rec.countries:
            continue
        # Must have at least one other country NOT able to build it
        faction_countries = set(parser.sides.faction_countries.get(faction, []))
        if rec.countries >= faction_countries:
            continue  # available to all countries — not a unique unit
        candidates.append(rec)

    if not candidates:
        return None

    # Sort by role score descending
    candidates.sort(key=lambda u: role_fn(u, parser), reverse=True)
    return candidates[0]


def _dominant_mobility(units: list[UnitRecord]) -> str:
    """
    Determine the dominant mobility group of a unit list.
    Returns LAND, NAVAL, AMPHIBIOUS, or AERIAL.
    AMPHIBIOUS and AERIAL are neutral and never dominant by themselves.
    """
    counts = defaultdict(int)
    for u in units:
        counts[u.mobility] += 1
    # Pure naval or pure land dominates
    if counts[MOBILITY_NAVAL] > 0 and counts[MOBILITY_LAND] == 0:
        return MOBILITY_NAVAL
    if counts[MOBILITY_LAND] > 0 and counts[MOBILITY_NAVAL] == 0:
        return MOBILITY_LAND
    # Mixed land+naval: conflict
    if counts[MOBILITY_NAVAL] > 0 and counts[MOBILITY_LAND] > 0:
        # Return whichever has more
        return MOBILITY_NAVAL if counts[MOBILITY_NAVAL] >= counts[MOBILITY_LAND] else MOBILITY_LAND
    return MOBILITY_LAND  # default


def _filter_compatible_mobility(units: list[UnitRecord],
                                 target_mobility: str) -> list[UnitRecord]:
    """
    Filter units to those compatible with the target mobility group.
    AMPHIBIOUS and AERIAL are always compatible with any group.
    AIRCRAFT is always excluded from standard waves.
    """
    result = []
    for u in units:
        mob = u.mobility
        if mob == MOBILITY_AIRCRAFT:
            continue  # always excluded from standard waves
        if mob in (MOBILITY_AMPHIBIOUS, MOBILITY_AERIAL):
            result.append(u)  # always compatible
            continue
        if mob == target_mobility:
            result.append(u)
    return result


def _dps_vs(rec: UnitRecord, armor_indices) -> float:
    """Primary + 50% secondary DPS against given armor indices."""
    def wpn_dps(wpn, indices):
        if not wpn or not wpn.get("is_combat"):
            return 0.0
        verses = wpn.get("verses", [0.0] * 11)
        damage = wpn.get("damage", 0.0)
        rof    = wpn.get("rof",    1.0)
        return max((effective_dps(damage, rof, verses, ai) for ai in indices),
                   default=0.0)

    pri = wpn_dps(rec.role_weapon, armor_indices)
    sec = wpn_dps(rec.secondary_analysis, armor_indices) * 0.5
    return pri + sec


def role_score_anti_inf(rec: UnitRecord, parser: RulesMdParser) -> float:
    return _dps_vs(rec, ARMOR_INFANTRY)

def role_score_anti_vehicle(rec: UnitRecord, parser: RulesMdParser) -> float:
    return _dps_vs(rec, ARMOR_VEHICLE)

def role_score_anti_building(rec: UnitRecord, parser: RulesMdParser) -> float:
    return _dps_vs(rec, ARMOR_BUILDING)

def role_score_anti_defenses(rec: UnitRecord, parser: RulesMdParser) -> float:
    """
    Combines range (outranges defenses) and building DPS.
    If unit outranges avg base defense: full score = range * building_dps
    If not: fallback to effective_health as a proxy for tanking ability.
    """
    targets     = parser._scan_strategic_targets()
    base_ranges = targets.get("base_def_ranges", [])
    avg_def_rng = sum(base_ranges) / len(base_ranges) if base_ranges else 6.0
    bld_dps     = _dps_vs(rec, ARMOR_BUILDING)

    if rec.weapon_range > avg_def_rng:
        return rec.weapon_range * bld_dps
    else:
        # Fallback: health proxy for tanking through defenses
        armor_mults = parser._derive_armor_multipliers()
        mult = armor_mults.get(rec.armor, 1.0)
        return rec.strength * mult * 0.1  # scaled down vs outrange

def role_score_artillery(rec: UnitRecord, parser: RulesMdParser) -> float:
    return float(rec.weapon_range)

def role_score_high_dps(rec: UnitRecord, parser: RulesMdParser) -> float:
    return (_dps_vs(rec, ARMOR_INFANTRY) +
            _dps_vs(rec, ARMOR_VEHICLE)  +
            _dps_vs(rec, ARMOR_BUILDING))

def _rush_score(rec: UnitRecord, dps: float, speed_exponent: float) -> float:
    """
    Score a unit for rush-style roles.

    Formula: dps × (speed_ratio ** speed_exponent)
    Where speed_ratio = unit_speed / combat_speed_avg (transport-excluded baseline).

    speed_exponent controls how much speed matters for this role:
      0.0 = speed irrelevant, pure DPS
      0.3 = DPS dominant, speed is a small bonus (miner killing — miners are slow)
      0.5 = balanced speed and DPS (CY rush)
      0.7 = speed-heavy, DPS is secondary (rush waves — need to reach enemy quickly)
      1.0 = pure speed scoring

    A unit with very high DPS can afford to be slow (TNKD beats IFV for miners
    because 3.0 dps × (5/7)^0.3 > 2.0 dps × (7/7)^0.3).
    """
    if dps <= 0:
        return 0.0
    avg = rec.combat_speed_avg
    if avg <= 0:
        return dps
    ratio = rec.speed / avg
    return dps * (ratio ** speed_exponent)


def role_score_miner_killer(rec: UnitRecord, parser: RulesMdParser) -> float:
    """
    Miner killers: DPS dominant, speed is a small bonus.
    Miners/harvesters move slowly — getting there fast is less important
    than having enough DPS to destroy them before they escape.
    Speed exponent = 0.3 (DPS dominates).
    """
    targets      = parser._scan_strategic_targets()
    miner_armors = targets.get("miner_armors", set())
    dps          = _dps_vs(rec, miner_armors) if miner_armors else 0.0
    return _rush_score(rec, dps, speed_exponent=0.3)


def role_score_aa(rec: UnitRecord, parser: RulesMdParser) -> float:
    has_aa = "AA" in rec.roles or "AA_Secondary" in rec.roles
    return float(rec.grade) if has_aa else 0.0


def role_score_naval(rec: UnitRecord, parser: RulesMdParser) -> float:
    return float(rec.grade) if rec.mobility in NAVAL_MIX_GROUP else 0.0


def role_score_rush(rec: UnitRecord, parser: RulesMdParser) -> float:
    """
    Rush waves: speed-heavy scoring, DPS against structures/vehicles required.
    Infantry and vehicles are both valid rushers but are scored separately —
    they should not be mixed in the same taskforce.

    Vehicles get a small bonus (×1.2) for Strength and armor — they survive
    base defenses better than infantry during a rush, making them more
    reliable delivery of damage.

    DPS used: anti-vehicle + anti-building average (rush targets structures).
    Speed exponent = 0.7 (speed-heavy but structure DPS still matters).
    """
    gb      = rec.grade_breakdown or {}
    av_dps  = max(gb.get('dps_vehicle',  0), 0)
    bld_dps = max(gb.get('dps_building', 0), 0)
    combat_dps = (av_dps + bld_dps) / 2.0
    if combat_dps <= 0:
        return 0.0
    score = _rush_score(rec, combat_dps, speed_exponent=0.7)
    # Vehicle bonus: tanks survive rush better than infantry
    if rec.category in ('Vehicle', 'Tank'):
        surv  = (rec.grade_breakdown or {}).get('eff_health', 0)
        bonus = 1.2 if surv > 0 else 1.1
        score *= bonus
    return score

def role_score_general(rec: UnitRecord, parser: RulesMdParser) -> float:
    return float(rec.grade)


# Role registry — extensible: add new roles by adding entries here
ROLE_SCORERS: dict[str, Callable] = {
    "RUSH":           role_score_rush,
    "ANTI_INF":       role_score_anti_inf,
    "ANTI_VEHICLE":   role_score_anti_vehicle,
    "ANTI_BUILDING":  role_score_anti_building,
    "ANTI_DEFENSES":  role_score_anti_defenses,
    "ARTILLERY":      role_score_artillery,
    "HIGH_DPS":       role_score_high_dps,
    "MINER_KILLER":   role_score_miner_killer,
    "AA":             role_score_aa,
    "NAVAL":          role_score_naval,
    "ANTI_NAVAL":     role_score_naval,   # stub
    "GENERAL_ATTACK": role_score_general,
}

def role_unit_filter(role: str, rec: UnitRecord,
                     parser: RulesMdParser) -> bool:
    """
    Pre-filter: does this unit make sense for this role at all?
    Extensible — add new roles here as needed.
    """
    # Aerial (Aircraft/Jumpjet) transports are delivery vehicles, never
    # combat picks — even when a buffed cannon makes them grade as
    # is_combat_transport (SHAD). Their one legitimate role is
    # AERIAL_TRANSPORT.
    if (rec.is_transport
            and rec.mobility in (MOBILITY_AERIAL, MOBILITY_AIRCRAFT,
                                 "Jumpjet", "Aircraft", "Helicopter")
            and role != "AERIAL_TRANSPORT"):
        return False
    if role == "AA":
        # Must have AA role tag
        if "AA" not in rec.roles and "AA_Secondary" not in rec.roles:
            return False
        # Exclude pure aerial transports (SHAD) — their AA gun is incidental.
        # HTK is a ground combat vehicle that also transports, so it stays.
        # Rule: aerial transports are excluded; ground transports are judged
        # by whether AA is in their primary roles (not just AA_Secondary).
        if rec.is_transport and rec.mobility in (
                MOBILITY_AERIAL, MOBILITY_AIRCRAFT, "Jumpjet", "Aircraft"):
            return False
        return True
    if role in ("AERIAL", "ANTI_AA"):
        # Aerial spam: only Jumpjet/Aircraft mobility units that are combat units
        # Exclude transports (is_transport=True) since they won't contribute DPS
        return (rec.mobility in (MOBILITY_AERIAL, MOBILITY_AIRCRAFT,
                                  "Jumpjet", "Aircraft", "Helicopter")
                and not rec.is_transport)
    if role == "AERIAL_TRANSPORT":
        # Transport drop: only aerial transports
        return (rec.mobility in (MOBILITY_AERIAL, MOBILITY_AIRCRAFT,
                                  "Jumpjet", "Aircraft", "Helicopter")
                and rec.is_transport)
    if role == "NAVAL":
        return rec.mobility in NAVAL_MIX_GROUP
    if role == "ANTI_NAVAL":
        return "AN-Surface" in rec.roles or "AN-Submerged" in rec.roles
    if role == "MINER_KILLER":
        return "Miner_Killer" in rec.roles
    if role == "ANTI_DEFENSES":
        # Include all building-capable units (outrange check done in scorer)
        return "AG-Anti_Building" in rec.roles or "AG-Multi" in rec.roles
    if role == "RUSH":
        # Aircraft have their own AERIAL role — exclude from ground rush waves.
        if rec.mobility in (MOBILITY_AERIAL, MOBILITY_AIRCRAFT,
                            "Jumpjet", "Aircraft", "Helicopter"):
            return False
        # Rush waves use Early-tier units — fast enough to reach the enemy
        # before the game reaches mid-game tech.
        return rec.tier in ("Early", "Rush")
    if role == "INFANTRY_RUSH_EARLY":
        # Infantry ONLY — the role name is a promise. Tanks that score well
        # on cost_spam/speed must not slip in through the generic fallthrough.
        if rec.category != "Infantry":
            return False
        if rec.mobility in (MOBILITY_AERIAL, MOBILITY_AIRCRAFT,
                            "Jumpjet", "Aircraft", "Helicopter"):
            return False
        return rec.tier in ("Early", "Rush")
    if role in ("AERIAL_RUSH", "AERIAL_WAVE", "AERIAL_ASSAULT"):
        # Aerial wave roles: only Jumpjet/Aircraft mobility, non-transport
        return (rec.mobility in (MOBILITY_AERIAL, MOBILITY_AIRCRAFT,
                                  "Jumpjet", "Aircraft", "Helicopter")
                and not rec.is_transport)
    # All other roles: exclude PURE transports (won't contribute DPS).
    # Combat-viable transports (Gunner=yes IFVs, weaponized Nighthawks) still
    # belong — see rulesmd_parser.is_combat_transport.
    if (rec.is_transport or rec.is_open_transport) and not getattr(
            rec, 'is_combat_transport', False):
        return False
    return True


# ---------------------------------------------------------------------------
# Emergent role detection
# ---------------------------------------------------------------------------

def detect_emergent_role(units: list[UnitRecord],
                          parser: RulesMdParser) -> str:
    """
    Given a set of selected units, determine the emergent role by
    comparing collective DPS scores across categories.

    If one category dominates by ROLE_DOMINANCE_THRESHOLD -> specialist role.
    Otherwise -> GENERAL_ATTACK.

    Also checks for AA, Naval, Artillery dominance.
    """
    if not units:
        return "GENERAL_ATTACK"

    total_inf = sum(_dps_vs(u, ARMOR_INFANTRY) for u in units)
    total_veh = sum(_dps_vs(u, ARMOR_VEHICLE)  for u in units)
    total_bld = sum(_dps_vs(u, ARMOR_BUILDING) for u in units)
    total_rng = sum(u.weapon_range for u in units) / len(units)

    targets     = parser._scan_strategic_targets()
    base_ranges = targets.get("base_def_ranges", [])
    avg_def_rng = sum(base_ranges) / len(base_ranges) if base_ranges else 6.0

    # AA check
    aa_count = sum(1 for u in units
                   if "AA" in u.roles or "AA_Secondary" in u.roles)
    if aa_count / len(units) >= 0.6:
        return "AA"

    # Naval check
    naval_count = sum(1 for u in units if u.mobility in NAVAL_MIX_GROUP)
    if naval_count / len(units) >= 0.6:
        return "NAVAL"

    # Artillery check — avg range well above base defenses
    if total_rng > avg_def_rng * 1.3:
        return "ARTILLERY"

    # DPS category dominance
    totals = {"inf": total_inf, "veh": total_veh, "bld": total_bld}
    dominant = max(totals, key=totals.get)
    others   = [v for k, v in totals.items() if k != dominant]
    avg_others = sum(others) / len(others) if others else 0

    if avg_others > 0 and totals[dominant] >= avg_others * ROLE_DOMINANCE_THRESHOLD:
        role_map = {
            "inf": "ANTI_INF",
            "veh": "ANTI_VEHICLE",
            "bld": "ANTI_BUILDING",
        }
        # Refine building -> ANTI_DEFENSES if range is also good
        role = role_map[dominant]
        if role == "ANTI_BUILDING" and total_rng > avg_def_rng:
            role = "ANTI_DEFENSES"
        return role

    return "GENERAL_ATTACK"


# ---------------------------------------------------------------------------
# Size / slot calculations
# ---------------------------------------------------------------------------

def vehicle_capacity(relaxed_stray: float) -> int:
    """Max vehicle slots for a safely gathering taskforce."""
    return int(relaxed_stray * 2 * relaxed_stray)

def infantry_capacity(relaxed_stray: float) -> int:
    return vehicle_capacity(relaxed_stray) * 3

def unit_slots(rec: UnitRecord) -> float:
    """How many vehicle-equivalent slots does one of this unit occupy?"""
    if rec.mobility in AIRCRAFT_GROUP or rec.mobility == "Jumpjet":
        return 0.0  # aerial units don't count toward slot limit
    if rec.category == "Infantry":
        return 1.0 / 3.0
    return 1.0

def total_slots(units_list: list[tuple[int, str]],
                parser: RulesMdParser) -> float:
    total = 0.0
    for count, uid in units_list:
        rec = parser.units.get(uid)
        if rec:
            total += count * unit_slots(rec)
    return total

def total_cost(units_list: list[tuple[int, str]],
               parser: RulesMdParser) -> int:
    return sum(count * parser.units[uid].cost
               for count, uid in units_list if uid in parser.units)

def needs_direct_attack(units_list: list[tuple[int, str]],
                         parser: RulesMdParser) -> bool:
    """True if taskforce is too large to safely gather before enemy base."""
    slots = total_slots(units_list, parser)
    cap   = vehicle_capacity(parser.relaxed_stray)
    return slots > cap


# ---------------------------------------------------------------------------
# Name generation (vanilla style)
# ---------------------------------------------------------------------------

def make_vanilla_name(units_list: list[tuple[int, str]],
                       parser: RulesMdParser,
                       registry: "IDRegistry | None" = None) -> str:
    """
    Generate a name like vanilla: '3 GIs, 2 Rhinos'
    Uses unit Name= from rulesmd.ini. Truncates to ~50 chars.
    If registry is provided, ensures the name is unique (no duplicates).
    """
    parts = []
    for count, uid in units_list:
        rec  = parser.units.get(uid)
        name = rec.name if rec else uid
        if len(name) > 12:
            name = name[:12].strip()
        parts.append(f"{count} {name}")
    result = ", ".join(parts)
    if len(result) > 50:
        result = result[:50]
    if registry:
        result = registry.unique_name(result)
    return result


def make_label_comment(faction: str,
                        phase: str,
                        role: str,
                        difficulty: str,
                        countries: set[str],
                        forbidden: set[str],
                        direct_attack: bool,
                        mobility_label: str = "LAND",
                        all_faction_countries: set[str] | None = None) -> str:
    """
    Generate machine-readable comment line above the taskforce section.
    Format: ; [FACTION][PHASE_LABEL][ROLE_LABEL][MOBILITY][...][DIFFICULTY][FLAGS]

    Phase Rush -> [RUSH_PHASE]
    Role Rush  -> [RUSH_WAVE]   (avoids [RUSH][RUSH] duplication)

    If `all_faction_countries` is given AND the countries set covers all of
    them, the country tags are omitted from the label as redundant — the
    faction tag [FACTION] already covers "any country in that faction".
    """
    phase_label = f"{phase.upper()}_PHASE" if phase == "Rush" else phase.upper()
    role_label  = "RUSH_WAVE" if role == "RUSH" else role

    parts = [f"[{faction.upper()}]", f"[{phase_label}]",
             f"[{role_label}]", f"[{mobility_label.upper()}]"]

    # Skip country tags when they cover ALL faction countries (redundant).
    show_countries = countries
    if all_faction_countries and countries and countries >= all_faction_countries:
        show_countries = set()

    for c in sorted(show_countries):
        parts.append(f"[{c.upper()}]")
    for c in sorted(forbidden):
        parts.append(f"[NO_{c.upper()}]")
    parts.append(f"[{difficulty.upper()}]")
    if direct_attack:
        parts.append("[DIRECT_ATTACK]")
    return "; " + "".join(parts)


# ---------------------------------------------------------------------------
# Taskforce record
# ---------------------------------------------------------------------------

@dataclass
class TaskforceEntry:
    tf_id:         str
    name:          str
    label_comment: str
    units:         list[tuple[int, str]]   # [(count, unit_id), ...]
    faction:       str
    phase:         str
    role:          str
    difficulty:    str
    countries:     set[str] = field(default_factory=set)
    forbidden:     set[str] = field(default_factory=set)
    direct_attack: bool = False
    notes:         list[str] = field(default_factory=list)
    script_id:     str | None = None   # assigned by ScriptGenerator

    @property
    def labels(self) -> set[str]:
        """Extract label tags from comment for duplicate checking."""
        return set(re.findall(r"\[([^\]]+)\]", self.label_comment))


# ---------------------------------------------------------------------------
# Eligible unit pool
# ---------------------------------------------------------------------------

def _eligible_units(parser: RulesMdParser,
                    faction: str,
                    phase: str,
                    role: str,
                    country: str | None,
                    min_grade: int,
                    difficulty: str) -> list[UnitRecord]:
    """
    Return units eligible for the given faction/phase/role/country combo.

    Phase depth ceilings are cumulative (Late includes Mid and Early units).
    Role filter applied via role_unit_filter().
    Grade filter applied via min_grade.
    Country filter: if country specified, unit must be buildable by it.
    Forbidden/mismatch units excluded.
    Aircraft excluded from non-AA/non-aerial roles.
    """
    depth_ceiling = {
        "Rush":    TIER_DEPTH_CEILING.get("Early", 3),
        "Early":   TIER_DEPTH_CEILING.get("Early", 3),
        "Mid":     TIER_DEPTH_CEILING.get("Mid",   4),
        "Late":    TIER_DEPTH_CEILING.get("Late",  5),
        "EndGame": 99,
    }.get(phase, 4)

    eligible = []
    for rec in parser.buildable:
        if rec.faction != faction:
            continue
        if rec.is_no_combat or rec.is_utility:
            continue
        if rec.mobility in SPECIAL_EXCLUDED:
            continue
        # Aircraft excluded unless role is AA or aerial
        if rec.is_aircraft_type and role not in ("AA",):
            continue
        # Suicide units excluded from standard attack waves
        if rec.is_suicide:
            continue

        # Country filter
        if country and country != "Generic":
            if rec.countries and country not in rec.countries:
                continue
        else:
            if rec.countries:
                faction_countries = set(
                    parser.sides.faction_countries.get(faction, []))
                if not rec.countries.intersection(faction_countries):
                    continue
                # For general (non-country-specific) waves: exclude units that
                # are only buildable by a strict subset of faction countries.
                # Country-exclusive units belong in country waves, not general waves.
                if rec.countries < faction_countries:
                    continue

        # Tier ceiling: use chain-based tier, not raw prereq_depth.
        # Phase→tier mapping: Rush/Early = Early only, Mid = Early+Mid,
        # Late = Early+Mid+Late, EndGame = all.
        TIER_ORDER = {"Early": 0, "Mid": 1, "Late": 2, "EndGame": 3}
        phase_tier = {"Rush": 0, "Early": 0, "Mid": 1,
                      "Late": 2, "EndGame": 3}.get(phase, 1)
        if TIER_ORDER.get(rec.tier, 0) > phase_tier:
            continue

        # Grade filter
        if rec.grade < min_grade:
            continue

        # Role-weighted grade filter — exclude units that score very poorly
        # for this specific role even if their flat grade is acceptable.
        # Different thresholds per role type:
        #   Specialized roles (RUSH, ANTI_INF etc): 10.0 — must meaningfully contribute
        #   General Attack: 5.0 — broader, but still excludes genuinely useless units
        # Not applied to EMERGENT (any unit valid) or SUICIDE_RUSH (handled separately)
        if role not in ("EMERGENT", "SUICIDE_RUSH"):
            threshold = 8.0 if role == "GENERAL_ATTACK" else 10.0
            if role_weighted_grade(rec, role) < threshold:
                continue

        # Role filter
        if not role_unit_filter(role, rec, parser):
            continue

        eligible.append(rec)

    return eligible


# ---------------------------------------------------------------------------
# Composition engine
# ---------------------------------------------------------------------------

def _selection_weight(rec: UnitRecord,
                       role: str,
                       parser: RulesMdParser,
                       philosophy: str,
                       composition: list[tuple[int, str]] | None = None,
                       difficulty: str = "Hard") -> float:
    """
    Compute selection weight for a unit.

    Uses role-weighted grade as the primary signal.
    Philosophy modifiers applied on top.

    Special reduction: aerial units with high death weapon scores
    in infantry-heavy waves are pushed to Easy difficulty only
    (0.1x weight on Medium/Hard, still available on Easy as an oddity).
    """
    rwg     = role_weighted_grade(rec, role)
    shifted = rwg + 50.0
    cost_sc = 1000.0 / max(rec.cost, 1)

    base_weight: float
    if philosophy == "grade_dominant":
        base_weight = max(0.01, shifted * 2.0)
    elif philosophy == "cost_dominant":
        base_weight = max(0.01, shifted + cost_sc * 10.0)
    elif philosophy == "inverted":
        base_weight = max(0.01, 100.0 - shifted)
    else:
        base_weight = max(0.01, shifted)

    # UNDESIRABLE units add variety to waves — AI shows all units in the game.
    # More common on Easy (the AI is less optimized), less on Hard (AI is serious).
    # But they appear on all difficulties for flavor.
    if "UNDESIRABLE" in rec.roles:
        if difficulty == "Easy":
            base_weight *= 0.4    # fairly common on Easy — AI throws everything
        elif difficulty == "Medium":
            base_weight *= 0.15   # occasional on Medium
        else:  # Hard
            base_weight *= 0.05   # rare on Hard, but not impossible

    # Aerial death weapon reduction in infantry-heavy waves.
    # Slow aerial units with powerful death weapons crashing into your
    # own infantry is an AI mistake — push to Easy only as an oddity.
    if (composition is not None and
            rec.grade_breakdown.get("death_weapon", 0) >= 1 and
            (rec.is_aircraft_type or rec.mobility in ("Jumpjet", "Aircraft"))):
        inf_ratio = _infantry_wave_ratio(composition, parser)
        if inf_ratio > 0.60:
            if difficulty in ("Medium", "Hard"):
                base_weight *= 0.1  # very rare on medium/hard
            else:
                base_weight *= 0.3  # uncommon but possible on easy

    return base_weight


def _has_minimum_combat_dps(rec: UnitRecord) -> bool:
    """
    Returns False if a unit lacks meaningful offensive DPS.

    Requirements:
      - Must score > 0 (positive) in at least one DPS category
      - Must score >= 0 in at least one additional DPS category

    This filters out transport units like SHAD (inf=2, veh=0, bld=0 would
    pass a simple 2-of-3 check, but SHAD only genuinely contributes in one
    niche context). Also filters pure utility units with no weapon DPS.
    """
    bd = rec.grade_breakdown
    if not bd:
        return True
    inf = bd.get("dps_infantry", 0)
    veh = bd.get("dps_vehicle",  0)
    bld = bd.get("dps_building", 0)
    vals = (inf, veh, bld)
    positive  = sum(1 for v in vals if v > 0)   # strictly above average
    nonneg    = sum(1 for v in vals if v >= 0)   # at average or above
    # Must be positive in at least 1 category AND non-negative in at least 2
    return positive >= 1 and nonneg >= 2


# Composition modes with their probabilities
COMPOSITION_MODE_WEIGHTS = {
    "spam":     40,   # 1-2 high-grade unit types, larger counts
    "diverse":  40,   # 3-4 unit types, symmetric counts
    "absurd":   15,   # weird combos, extreme counts
    "suicide":   5,   # suicide units only, Rush phase Easy only (see generate_batch)
}


def _compose(units: list[UnitRecord],
             phase: str,
             role: str,
             difficulty: str,
             philosophy: str,
             parser: RulesMdParser,
             rng: random.Random,
             max_cost: int | None = None,
             mode: str | None = None) -> list[tuple[int, str]]:
    """
    Select unit types and quantities for a taskforce.

    Composition modes:
      spam    — 1-2 high-grade unit types, spammable, larger counts
      diverse — 3-4 unit types, roughly symmetric 1:1:1 counts
      absurd  — unusual combinations, occasionally extreme counts (rare)

    Minimum count enforcement: no unit type gets fewer than
    max_slots / (num_types * 2) units to avoid token quantities.

    Units with no meaningful combat DPS (score -2 on all categories)
    are excluded regardless of grade.

    BuildLimit enforced on all unit counts.
    """
    if not units:
        return []

    # Filter out units with no meaningful offensive DPS
    units = [u for u in units if _has_minimum_combat_dps(u)]
    if not units:
        return []

    dominant_mob = _dominant_mobility(units)
    units = _filter_compatible_mobility(units, dominant_mob)
    if not units:
        return []

    phase_key = phase if phase in PHASE_SIZES else "Mid"
    max_slots = PHASE_SIZES.get(phase_key, 16) * DIFFICULTY_SCALE.get(difficulty, 1.0)
    max_slots = max(1.0, max_slots)

    weights = {u.unit_id: _selection_weight(u, role, parser, philosophy,
                                              difficulty=difficulty)
               for u in units}
    total_w = sum(weights.values())
    if total_w <= 0:
        return []

    # Determine composition mode if not specified
    if mode is None:
        mode_keys   = list(COMPOSITION_MODE_WEIGHTS.keys())
        mode_probs  = list(COMPOSITION_MODE_WEIGHTS.values())
        # suicide mode only valid in Rush/Easy — handled externally
        mode_keys   = [m for m in mode_keys if m != "suicide"]
        mode_probs  = [COMPOSITION_MODE_WEIGHTS[m] for m in mode_keys]
        mode        = rng.choices(mode_keys, weights=mode_probs)[0]

    # Number of unit types by mode.
    # Cap by available slots to avoid token quantities —
    # with only 4 slots, 4 unit types means 1 of each which is silly.
    # Minimum 3 slots per unit type as a practical floor.
    max_types_by_slots = max(1, int(max_slots / 3))
    max_types = min(4, len(units), max_types_by_slots)

    if mode == "spam":
        num_types = rng.choices([1, 2], weights=[40, 60])[0]
        num_types = min(num_types, max_types)
    elif mode == "diverse":
        if max_types >= 3:
            num_types = rng.choices(
                range(3, max_types + 1),
                weights=[3, 1][:max_types - 2]
            )[0]
        else:
            num_types = min(2, max_types)
    elif mode == "absurd":
        num_types = rng.randint(1, max_types)
    else:
        num_types = rng.choices(range(1, max_types + 1),
                                weights=[1, 3, 3, 1][:max_types])[0]

    # Select unit types
    selected: list[UnitRecord] = []
    pool = list(units)
    seen: set[str] = set()
    attempts = 0
    while len(selected) < num_types and pool and attempts < 100:
        attempts += 1
        w_list = [weights[u.unit_id] for u in pool]
        pick   = rng.choices(pool, weights=w_list)[0]
        if pick.unit_id not in seen:
            seen.add(pick.unit_id)
            selected.append(pick)
            pool = [u for u in pool if u.unit_id != pick.unit_id]

    if not selected:
        return []

    slot_sizes = [unit_slots(u) for u in selected]
    avg_cost_sel = sum(u.cost for u in selected) / len(selected) if selected else 1

    def would_be_token(u: UnitRecord, s: float, slots_per: float) -> bool:
        """Returns True if this unit would get count < 2 and doesn't qualify for it."""
        if u.build_limit > 0: return False   # hero always ok
        if max_slots <= 8:    return False   # small wave ok
        if u.cost > avg_cost_sel * 1.5: return False  # expensive ok
        if s == 0: return False              # aerial, count handled differently
        projected = round(slots_per / s) if s > 0 else 1
        return projected < 2  # drop if would get fewer than 2

    # Pre-filter: remove unit types that would result in token quantities.
    # Iterate until stable — removing a unit frees slots for the others.
    if mode in ("spam", "diverse"):
        for _ in range(5):  # max iterations to reach stable state
            if not selected: break
            slots_per_preview = max_slots / len(selected)
            filtered = [(u, s) for u, s in zip(selected, slot_sizes)
                        if not would_be_token(u, s, slots_per_preview)]
            if not filtered or len(filtered) == len(selected):
                break  # stable
            selected   = [u for u, s in filtered]
            slot_sizes = [s for u, s in filtered]

    def min_count_for(u: UnitRecord, s: float) -> int:
        """
        Minimum unit count to avoid token quantities.
        count=1 allowed when:
          - Unit has build_limit > 0 (hero — always exactly that limit)
          - Unit is significantly more expensive than average (costly specialist)
          - Wave is small (Easy difficulty with few slots)
          - Unit is aerial (stackable, count=1 is fine)
        Otherwise: at least max_slots / (num_types * 1.5) slots worth.
        """
        if u.build_limit > 0:
            return 1  # hero unit — always capped at build_limit anyway
        if s == 0:
            return 1  # aerial units stack freely
        if u.cost > avg_cost_sel * 1.5:
            return 1  # expensive specialist
        if max_slots <= 8:
            return 1  # small wave (Easy difficulty)
        return max(2, int((max_slots / (len(selected) * 1.5)) / s))

    result: list[tuple[int, str]] = []
    remaining_slots = max_slots
    remaining_cost  = max_cost if max_cost is not None else 999_999_999

    if mode in ("spam", "diverse") or (mode == "absurd" and rng.random() < 0.5):
        # Equal distribution with minimum enforcement
        slots_per_type = max_slots / len(selected)
        for u, s in zip(selected, slot_sizes):
            min_c = min_count_for(u, s)
            if s == 0:
                count = max(min_c, round(slots_per_type * 0.5))
            else:
                count = max(min_c, round(slots_per_type / s))

            if u.build_limit > 0:
                count = min(count, u.build_limit)
            count = min(count, max(1, remaining_cost // max(u.cost, 1)))
            if count == 0:
                continue
            result.append((count, u.unit_id))
            remaining_slots -= count * s
            remaining_cost  -= count * u.cost
            if remaining_cost <= 0:
                break
    else:
        # Weighted uneven distribution (absurd mode or rare variance)
        sel_weights = [weights[u.unit_id] for u in selected]
        sel_total_w = sum(sel_weights)
        for u, s, w in zip(selected, slot_sizes, sel_weights):
            min_c = min_count_for(u, s)
            share = w / max(sel_total_w, 1)
            if s == 0:
                count = max(min_c, round(max_slots * share * 0.5))
            else:
                slot_alloc = min(remaining_slots,
                                 max_slots * share * rng.uniform(0.6, 1.4))
                count = max(min_c, int(slot_alloc / s))

            if u.build_limit > 0:
                count = min(count, u.build_limit)
            count = min(count, max(1, remaining_cost // max(u.cost, 1)))
            if count == 0:
                continue
            result.append((count, u.unit_id))
            remaining_slots -= count * s
            remaining_cost  -= count * u.cost
            if remaining_slots <= 0 or remaining_cost <= 0:
                break

    # Post-process: remove token quantities.
    if result and parser:
        result = _remove_token_quantities(result, parser)

    # Garnish pass: add 1 off-role unit at low count for flavor variety.
    # Conditions:
    #   - Hard difficulty only (Easy/Medium keep it simple)
    #   - At least 2 unit types already in composition (don't garnish single-unit spam)
    #   - Mode is not spam (spam waves should stay focused)
    #   - 35% chance per composition
    #   - Garnish unit must be from a DIFFERENT role tag than the primary composition
    #   - Garnish count is always 1 (or build_limit for heroes) — flavor, not filler
    # Examples: a tank wave gets 1 Flak Track; a GI wave gets 1 Sniper;
    #           a Rhino wave gets 1 Engineer to threaten ConYard captures.
    if (result and parser and difficulty == "Hard"
            and mode != "spam"
            and len(result) >= 2
            and rng.random() < 0.35):

        # Determine mobility of primary composition
        primary_ids = {uid for _, uid in result}
        primary_recs = [parser.units[uid] for uid in primary_ids if uid in parser.units]
        dom_mob = _dominant_mobility(primary_recs) if primary_recs else None

        # Garnish candidates: same faction mobility, NOT already in wave,
        # has combat DPS, not a pure transport
        garnish_pool = [
            u for u in units
            if u.unit_id not in primary_ids
            and _has_minimum_combat_dps(u)
            and not u.is_transport
            and not u.is_open_transport
            and (dom_mob is None or _dominant_mobility([u]) == dom_mob)
        ]

        if garnish_pool:
            # Pick a garnish unit with inverted weighting — slightly off-meta choices
            # are more interesting as garnish than the same top units
            garnish_weights = [
                max(0.1, 100.0 - (role_weighted_grade(u, role) + 50.0))
                for u in garnish_pool
            ]
            garnish_pick = rng.choices(garnish_pool, weights=garnish_weights)[0]
            garnish_count = min(
                garnish_pick.build_limit if garnish_pick.build_limit > 0 else 1,
                1
            )
            result.append((garnish_count, garnish_pick.unit_id))

    return result


def _add_fodder(units_list: list[tuple[int, str]],
                parser: RulesMdParser,
                eligible: list[UnitRecord],
                relaxed_stray: float,
                rng: random.Random,
                fodder_ratio: float = 1.0) -> list[tuple[int, str]]:
    """
    Opportunistically fill leftover slot capacity with FODDER units.

    FODDER is contextual — a unit qualifies for THIS wave if it is
    significantly cheaper and similarly-paced to the main units.
    FODDER cost is NOT counted against the wave cost budget.

    Trigger conditions:
      - Wave average unit cost > FODDER_COST_THRESHOLD
      - Remaining slots > 0 within RelaxedStray vehicle capacity
      - A qualifying fodder candidate exists matching wave mobility

    FODDER candidate requirements (all must pass):
      - Cost <= 25% of wave's slot-weighted average cost
      - Speed within ±20% of wave's slot-weighted average speed
      - Effective health >= FODDER_MIN_HEALTH
      - Mobility matches wave's dominant mobility (land stays with land,
        aerial stays with aerial — mixed mobility defeats the purpose)
      - Not already in the wave

    Quantity: fodder_ratio:1 (fodder slots : main slots), never exceeding
    RelaxedStray vehicle capacity. Default 1:1.
    User-configurable via fodder_ratio parameter.
    """
    FODDER_COST_THRESHOLD = 600
    FODDER_COST_RATIO     = 0.25
    FODDER_SPEED_MARGIN   = 0.20
    FODDER_MIN_HEALTH     = 50

    if not units_list or not eligible:
        return units_list

    # Compute slot-weighted averages for existing wave
    total_slots_used = 0.0
    total_cost_w     = 0.0
    total_speed_w    = 0.0
    existing_ids     = {uid for _, uid in units_list}

    for count, uid in units_list:
        rec = parser.units.get(uid)
        if not rec: continue
        s = unit_slots(rec)
        if s == 0: s = 0.5
        total_slots_used += count * s
        total_cost_w     += count * s * rec.cost
        total_speed_w    += count * s * rec.speed

    if total_slots_used == 0:
        return units_list

    avg_cost  = total_cost_w  / total_slots_used
    avg_speed = total_speed_w / total_slots_used

    if avg_cost <= FODDER_COST_THRESHOLD:
        return units_list

    # Remaining capacity within RelaxedStray
    vehicle_cap      = vehicle_capacity(relaxed_stray)
    remaining_slots  = vehicle_cap - total_slots_used
    if remaining_slots <= 0:
        return units_list

    # Determine wave's dominant mobility for matching
    mob_counts: dict[str, float] = defaultdict(float)
    for count, uid in units_list:
        rec = parser.units.get(uid)
        if not rec: continue
        s = unit_slots(rec) or 0.5
        mob_counts[rec.mobility] += count * s
    wave_mobility = max(mob_counts, key=mob_counts.get) if mob_counts else MOBILITY_LAND

    # Max fodder slots: fodder_ratio * main slots, capped at remaining capacity
    max_fodder_slots = min(remaining_slots, total_slots_used * fodder_ratio)
    if max_fodder_slots < 0.33:
        return units_list

    # Find candidates
    speed_lo = avg_speed * (1 - FODDER_SPEED_MARGIN)
    speed_hi = avg_speed * (1 + FODDER_SPEED_MARGIN)
    cost_cap = avg_cost * FODDER_COST_RATIO

    armor_mults = {"none":1.0,"flak":1.5,"plate":1.5,"light":0.75,
                   "medium":0.5,"heavy":0.25,"wood":2.0,"steel":0.25,"concrete":0.25}

    candidates = []
    for rec in eligible:
        if rec.unit_id in existing_ids: continue
        if rec.is_suicide:             continue
        if rec.cost > cost_cap:        continue
        if not (speed_lo <= rec.speed <= speed_hi): continue
        # Mobility must match wave — aerial stays with aerial, land with land
        # AMPHIBIOUS can join either
        if rec.mobility != MOBILITY_AMPHIBIOUS:
            if wave_mobility in (MOBILITY_LAND, MOBILITY_NAVAL):
                if rec.mobility != wave_mobility and rec.mobility != MOBILITY_AMPHIBIOUS:
                    continue
            elif wave_mobility in (MOBILITY_AERIAL, "Jumpjet", "Aircraft"):
                if rec.mobility not in (MOBILITY_AERIAL, "Jumpjet", "Aircraft"):
                    continue
        eff_hp = rec.strength * armor_mults.get(rec.armor, 1.0)
        if eff_hp < FODDER_MIN_HEALTH: continue
        candidates.append(rec)

    if not candidates:
        return units_list

    # Best candidate: highest health-to-cost ratio
    candidates.sort(key=lambda u: u.strength / max(u.cost, 1), reverse=True)
    fodder_unit  = candidates[0]
    fodder_slots = unit_slots(fodder_unit) or 0.5
    fodder_count = max(1, int(max_fodder_slots / fodder_slots))

    if fodder_unit.build_limit > 0:
        fodder_count = min(fodder_count, fodder_unit.build_limit)
    if fodder_count < 1:
        return units_list

    return list(units_list) + [(fodder_count, fodder_unit.unit_id)]
def _remove_token_quantities(units_list: list[tuple[int, str]],
                              parser: RulesMdParser) -> list[tuple[int, str]]:
    """
    Remove token quantities from a composed unit list.
    Drops count=1 non-hero units when the wave has other units with count >= 4.
    This catches lopsided patterns like '1 GI alongside 20 GGI'.
    """
    if not units_list or len(units_list) <= 1:
        return units_list
    max_count = max(count for count, _ in units_list)
    if max_count < 4:
        return units_list
    cleaned = [
        (count, uid) for count, uid in units_list
        if not (count == 1
                and uid in parser.units
                and parser.units[uid].build_limit == 0)
    ]
    return cleaned if cleaned else units_list


def _compose_suicide_rush(units: list[UnitRecord],
                           phase: str,
                           difficulty: str,
                           parser: RulesMdParser,
                           rng: random.Random) -> list[tuple[int, str]]:
    """
    Compose a suicide-only rush taskforce.
    All units must have is_suicide=True.
    Single unit type only — no mixing.
    """
    suicide_units = [u for u in units if u.is_suicide]
    if not suicide_units:
        return []

    phase_key = phase if phase in PHASE_SIZES else "Rush"
    max_slots = PHASE_SIZES.get(phase_key, 16) * DIFFICULTY_SCALE.get(difficulty, 1.0)

    # Pick highest-graded suicide unit
    pick  = max(suicide_units, key=lambda u: u.grade)
    s     = unit_slots(pick)
    count = max(1, int(max_slots / s)) if s > 0 else int(max_slots)
    count = min(count, RUSH_MAX_COST // max(pick.cost, 1))
    if pick.build_limit > 0:
        count = min(count, pick.build_limit)

    return [(count, pick.unit_id)]


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

class TaskforceGenerator:
    """
    Generates Taskforce entries from parsed rulesmd.ini data.

    Usage:
        parser    = RulesMdParser("rulesmd.ini").parse()
        registry  = IDRegistry.from_aimd("aimd.ini")
        generator = TaskforceGenerator(parser, registry)
        taskforces = generator.generate_batch(config)
        generator.write_to_ini(taskforces, "aimd.ini")
    """

    def __init__(self,
                 parser: RulesMdParser,
                 id_registry: IDRegistry,
                 seed: int | None = None):
        self.parser   = parser
        self.registry = id_registry
        self.rng      = random.Random(seed)

    def generate_batch(self,
                        config: "GeneratorConfig") -> list[TaskforceEntry]:
        """
        Generate taskforces from config specs.

        Difficulty distributed evenly (Easy/Medium/Hard) per spec.
        Country specials: 1:1 ratio with regular taskforces.
        Suicide rushes: injected for Rush specs where allow_suicide_* is True.
        """
        results             = []
        seen_fingerprints: set[frozenset] = set()
        difficulties        = ["Easy", "Medium", "Hard"]

        for spec in config.specs:
            diff_cycle = (difficulties * ((spec.count // 3) + 1))[:spec.count]
            self.rng.shuffle(diff_cycle)

            regular_count = 0
            for idx in range(spec.count):
                difficulty = diff_cycle[idx]
                iter_spec  = TaskforceSpec(
                    faction=spec.faction, phase=spec.phase, role=spec.role,
                    count=1, difficulty=difficulty, country=None,
                    min_grade=spec.min_grade, philosophy=spec.philosophy,
                )
                tf = self._generate_one(iter_spec, seen_fingerprints)
                if tf:
                    results.append(tf)
                    regular_count += 1

                # Suicide rush injection for Rush phase
                if spec.phase == "Rush":
                    allow = {
                        "Easy":   spec.allow_suicide_easy,
                        "Medium": spec.allow_suicide_medium,
                        "Hard":   spec.allow_suicide_hard,
                    }.get(difficulty, False)
                    if allow:
                        tf_s = self._generate_suicide_rush(
                            spec.faction, difficulty, seen_fingerprints)
                        if tf_s:
                            results.append(tf_s)

            # Country specials: 1:1 with regulars
            faction_countries = list(
                self.parser.sides.faction_countries.get(spec.faction, []))
            countries_with_specials = [
                c for c in faction_countries
                if _find_country_anchor(
                    spec.faction, c, spec.phase,
                    spec.role if spec.role != "EMERGENT" else "GENERAL_ATTACK",
                    self.parser, spec.min_grade)
            ]

            if countries_with_specials:
                special_diff_cycle = (
                    difficulties * ((regular_count // 3) + 1))[:regular_count]
                self.rng.shuffle(special_diff_cycle)

                for i in range(regular_count):
                    country    = countries_with_specials[
                        i % len(countries_with_specials)]
                    difficulty = special_diff_cycle[i]

                    if self.rng.random() < 0.50:
                        tf = self._generate_country_anchor(
                            spec, country, difficulty, seen_fingerprints)
                    else:
                        iter_spec = TaskforceSpec(
                            faction=spec.faction, phase=spec.phase,
                            role=spec.role, count=1,
                            difficulty=difficulty, country=country,
                            min_grade=spec.min_grade,
                            philosophy=spec.philosophy,
                        )
                        tf = self._generate_one(iter_spec, seen_fingerprints)

                    if tf:
                        results.append(tf)

        return results

    def _generate_suicide_rush(self,
                                faction: str,
                                difficulty: str,
                                seen_fingerprints: set) -> TaskforceEntry | None:
        """
        Generate a suicide-only Rush taskforce.
        Single unit type, no mixing. Label: [SUICIDE_RUSH].
        """
        eligible = [
            rec for rec in self.parser.buildable
            if rec.faction == faction
            and rec.is_suicide
            and rec.prereq_depth <= TIER_DEPTH_CEILING.get("Early", 3)
        ]
        if not eligible:
            return None

        units_list = _compose_suicide_rush(
            eligible, "Rush", difficulty, self.parser, self.rng)
        if not units_list:
            return None

        fingerprint = frozenset((uid, c) for c, uid in units_list)
        if fingerprint in seen_fingerprints:
            return None
        seen_fingerprints.add(fingerprint)

        direct = needs_direct_attack(units_list, self.parser)
        name   = make_vanilla_name(units_list, self.parser, self.registry)
        label  = make_label_comment(
            faction, "Rush", "SUICIDE_RUSH", difficulty,
            set(), set(), direct, "LAND")

        tf_id = self.registry.generate()
        self.registry.register_composition(tf_id, units_list)
        entry = TaskforceEntry(
            tf_id         = tf_id,
            name          = name,
            label_comment = label,
            units         = units_list,
            faction       = faction,
            phase         = "Rush",
            role          = "SUICIDE_RUSH",
            difficulty    = difficulty,
            direct_attack = direct)
        self.registry.register_entry(tf_id, entry)
        return entry

    def _generate_country_anchor(self,
                                  spec: "TaskforceSpec",
                                  country: str,
                                  difficulty: str,
                                  seen_fingerprints: set) -> "TaskforceEntry | None":
        """
        Generate a country-specific taskforce built around a country unique
        anchor unit. Anchor is the best role-scoring unit restricted to that
        country. Remaining slots filled with compatible generic faction units.
        """
        role = spec.role if spec.role != "EMERGENT" else "GENERAL_ATTACK"

        anchor = _find_country_anchor(
            spec.faction, country, spec.phase, role,
            self.parser, spec.min_grade)
        if not anchor:
            return None

        eligible = _eligible_units(
            self.parser, spec.faction, spec.phase, role,
            country=None, min_grade=spec.min_grade, difficulty=difficulty)
        eligible = [u for u in eligible if u.unit_id != anchor.unit_id]

        # Filter fill units to those compatible with anchor's RequiredHouses.
        # If anchor is Ukraine-only, fill units must have no RequiredHouses
        # OR have RequiredHouses that includes Ukraine.
        ini = self.parser.ini
        anchor_sec = ini.section(anchor.unit_id)
        anchor_rh_raw = anchor_sec.get('requiredhouses', '') if anchor_sec else ''
        if anchor_rh_raw.strip():
            anchor_rh = frozenset(h.strip() for h in anchor_rh_raw.split(',') if h.strip())
            compat = []
            for u in eligible:
                s = ini.section(u.unit_id)
                if not s:
                    compat.append(u)
                    continue
                rh_raw = s.get('requiredhouses', '')
                if not rh_raw.strip():
                    compat.append(u)  # no restriction — always compatible
                    continue
                rh = frozenset(h.strip() for h in rh_raw.split(',') if h.strip())
                if rh & anchor_rh:  # at least one country in common
                    compat.append(u)
            eligible = compat

        phase_key    = spec.phase if spec.phase in PHASE_SIZES else "Mid"
        max_slots    = PHASE_SIZES.get(phase_key, 16) * DIFFICULTY_SCALE.get(difficulty, 1.0)
        anchor_slots = unit_slots(anchor)
        anchor_count = max(1, int((max_slots / 3) / max(anchor_slots, 0.33)))
        remaining    = max_slots - anchor_count * anchor_slots

        dom_mob  = _dominant_mobility([anchor] + eligible)
        filtered = _filter_compatible_mobility(eligible, dom_mob)

        units_list = [(anchor_count, anchor.unit_id)]

        if filtered and remaining > 0:
            philosophy = spec.philosophy or self.rng.choice(COMPOSITIONS)
            fill       = _compose(filtered, spec.phase, role, difficulty,
                                  philosophy, self.parser, self.rng)
            for count, uid in fill:
                rec = self.parser.units.get(uid)
                if not rec: continue
                s       = unit_slots(rec)
                allowed = max(1, int(remaining / max(s, 0.33))) if s > 0 else count
                count   = min(count, allowed)
                if count > 0:
                    units_list.append((count, uid))
                    remaining -= count * s
                    if remaining <= 0: break

        if not units_list:
            return None

        # Clean up token quantities
        units_list = _remove_token_quantities(units_list, self.parser)
        if not units_list:
            return None

        if role == "GENERAL_ATTACK":
            all_elig   = [anchor] + filtered
            units_list = _rebalance_general_attack(
                units_list, all_elig, self.parser, self.rng, max_slots)
            units_list = _rebalance_generalist_ratio(
                units_list, all_elig, self.parser, self.rng)
        units_list = _remove_token_quantities(units_list, self.parser)

        fingerprint = frozenset((uid, c) for c, uid in units_list)
        if fingerprint in seen_fingerprints:
            return None  # already used in this session batch

        existing_id = self.registry.find_duplicate(units_list)
        if existing_id:
            seen_fingerprints.add(fingerprint)
            existing_tf = self.registry.get_entry(existing_id)
            if existing_tf is not None:
                return existing_tf   # reuse existing taskforce
            return None  # exists but not cached — skip

        seen_fingerprints.add(fingerprint)

        countries: set[str] = {country}
        forbidden: set[str] = set()
        for _, uid in units_list:
            sec = self.parser.ini.section(uid)
            fh  = sec.get("forbiddenhouses", "")
            if fh:
                forbidden.update(h.strip() for h in fh.split(",") if h.strip())

        direct        = needs_direct_attack(units_list, self.parser)
        sel_recs      = [self.parser.units[uid] for _, uid in units_list
                         if uid in self.parser.units]
        dom_mob_final = _dominant_mobility(sel_recs)
        mob_map       = {MOBILITY_LAND: "LAND", MOBILITY_NAVAL: "NAVAL",
                         MOBILITY_AMPHIBIOUS: "AMPHIBIOUS", MOBILITY_AERIAL: "AERIAL"}
        final_role    = (detect_emergent_role(sel_recs, self.parser)
                         if spec.role == "EMERGENT" else role)

        tf_id = self.registry.generate()
        self.registry.register_composition(tf_id, units_list)

        entry = TaskforceEntry(
            tf_id         = tf_id,
            name          = make_vanilla_name(units_list, self.parser, self.registry),
            label_comment = make_label_comment(
                spec.faction, spec.phase, final_role, difficulty,
                countries, forbidden, direct,
                mob_map.get(dom_mob_final, "LAND"),
                all_faction_countries=set(self.parser.sides.faction_countries.get(spec.faction, []))),
            units         = units_list,
            faction       = spec.faction, phase=spec.phase,
            role          = final_role,   difficulty=difficulty,
            countries     = countries,    forbidden=forbidden,
            direct_attack = direct)
        self.registry.register_entry(tf_id, entry)
        return entry

    def _generate_one(self,
                       spec: "TaskforceSpec",
                       seen_fingerprints: set) -> "TaskforceEntry | None":
        faction    = spec.faction
        phase      = spec.phase
        difficulty = spec.difficulty
        country    = spec.country
        min_grade  = spec.min_grade
        philosophy = spec.philosophy or self.rng.choice(COMPOSITIONS)

        role = spec.role
        use_emergent = (role == "EMERGENT" or role is None)
        if use_emergent:
            role_pool = [r for r in ALL_ROLES if r != "ANTI_NAVAL"]
            role = self.rng.choice(role_pool)

        eligible = _eligible_units(
            self.parser, faction, phase, role, country, min_grade, difficulty)

        if not eligible:
            if role != "GENERAL_ATTACK":
                eligible = _eligible_units(
                    self.parser, faction, phase, "GENERAL_ATTACK",
                    country, min_grade, difficulty)
                role = "GENERAL_ATTACK"
            if not eligible:
                return None

        # RUSH waves: never mix infantry and vehicles.
        # Randomly choose infantry-only or vehicle-only for this wave.
        # This ensures the AI sometimes rushes with tanks, sometimes with
        # infantry — but never a mixed mob that slows itself down.
        # Aircraft are already excluded from RUSH by role_unit_filter.
        if role == "RUSH" and eligible:
            vehicles  = [r for r in eligible
                         if r.category in ('Vehicle', 'Tank')]
            infantry  = [r for r in eligible
                         if r.category == 'Infantry']
            if vehicles and infantry:
                # Bias toward vehicles (they survive better)
                eligible = vehicles if self.rng.random() < 0.65 else infantry
            elif vehicles:
                eligible = vehicles
            elif infantry:
                eligible = infantry

        max_cost = RUSH_MAX_COST if phase == "Rush" else None
        dom_mob  = _dominant_mobility(eligible)
        eligible = _filter_compatible_mobility(eligible, dom_mob)
        if not eligible:
            return None

        # Pick composition mode
        mode_keys  = [m for m in COMPOSITION_MODE_WEIGHTS if m != "suicide"]
        mode_probs = [COMPOSITION_MODE_WEIGHTS[m] for m in mode_keys]
        mode       = self.rng.choices(mode_keys, weights=mode_probs)[0]

        units_list = _compose(
            eligible, phase, role, difficulty, philosophy,
            self.parser, self.rng, max_cost=max_cost, mode=mode)

        if not units_list:
            return None

        # Clean up token quantities from _compose or rebalancing
        units_list = _remove_token_quantities(units_list, self.parser)
        if not units_list:
            return None

        if role == "GENERAL_ATTACK":
            max_slots  = PHASE_SIZES.get(phase, 16) * DIFFICULTY_SCALE.get(difficulty, 1.0)
            units_list = _rebalance_general_attack(
                units_list, eligible, self.parser, self.rng, max_slots)
            units_list = _rebalance_generalist_ratio(
                units_list, eligible, self.parser, self.rng)
        # Final cleanup after rebalancing
        units_list = _remove_token_quantities(units_list, self.parser)

        # Add fodder to fill leftover slots in expensive waves
        units_list = _add_fodder(
            units_list, self.parser, eligible,
            self.parser.relaxed_stray, self.rng,
            fodder_ratio=spec.fodder_ratio)
        units_list = _remove_token_quantities(units_list, self.parser)

        if use_emergent:
            sel = [self.parser.units[uid] for _, uid in units_list
                   if uid in self.parser.units]
            role = detect_emergent_role(sel, self.parser)

        countries: set[str] = set()
        forbidden: set[str] = set()
        for _, uid in units_list:
            rec = self.parser.units.get(uid)
            if rec and rec.countries:
                countries.update(rec.countries)
            sec = self.parser.ini.section(uid)
            fh  = sec.get("forbiddenhouses", "")
            if fh:
                forbidden.update(h.strip() for h in fh.split(",") if h.strip())

        fingerprint = frozenset((uid, c) for c, uid in units_list)
        if fingerprint in seen_fingerprints:
            return None

        # Check against existing aimd.ini taskforces — skip if duplicate
        # Reuse existing composition if one already exists (same taskforce,
        # new trigger). Multiple triggers sharing one taskforce is valid.
        existing_id = self.registry.find_duplicate(units_list)
        if existing_id:
            seen_fingerprints.add(fingerprint)
            existing = self.registry.get_entry(existing_id)
            if existing is not None:
                return existing
            # Not cached — was loaded from existing aimd.ini this session.
            # Fall through to create a fresh entry from the same composition.

        seen_fingerprints.add(fingerprint)

        direct        = needs_direct_attack(units_list, self.parser)
        sel_recs      = [self.parser.units[uid] for _, uid in units_list
                         if uid in self.parser.units]
        dom_mob_final = _dominant_mobility(sel_recs)
        mob_map       = {MOBILITY_LAND: "LAND", MOBILITY_NAVAL: "NAVAL",
                         MOBILITY_AMPHIBIOUS: "AMPHIBIOUS", MOBILITY_AERIAL: "AERIAL",
                         MOBILITY_AIRCRAFT: "AIRCRAFT"}

        tf_id = self.registry.generate()
        self.registry.register_composition(tf_id, units_list)

        entry2 = TaskforceEntry(
            tf_id         = tf_id,
            name          = make_vanilla_name(units_list, self.parser, self.registry),
            label_comment = make_label_comment(
                faction, phase, role, difficulty, countries, forbidden,
                direct, mob_map.get(dom_mob_final, "LAND"),
                all_faction_countries=set(self.parser.sides.faction_countries.get(faction, []))),
            units         = units_list,
            faction       = faction,   phase=phase,
            role          = role,      difficulty=difficulty,
            countries     = countries, forbidden=forbidden,
            direct_attack = direct)
        self.registry.register_entry(tf_id, entry2)
        return entry2

    def write_to_ini(self,
                      taskforces: list[TaskforceEntry],
                      aimd_path: str | Path,
                      output_path: str | Path | None = None) -> Path:
        """
        Write generated taskforces into aimd.ini.
        Appends new IDs to [TaskForces] list and adds sections at the end.
        Always ends with a trailing semicolon.
        """
        from pathlib import Path as _Path
        aimd_path   = _Path(aimd_path)
        output_path = _Path(output_path) if output_path else aimd_path

        lines = []
        if aimd_path.exists():
            with aimd_path.open(encoding="latin-1") as fh:
                lines = fh.readlines()

        # Strip trailing semicolons and blanks
        while lines and lines[-1].strip() in (";", ""):
            lines.pop()

        # Find or create [TaskForces] section
        tf_idx = None
        for i, line in enumerate(lines):
            if line.strip() == "[TaskForces]":
                tf_idx = i
                break
        if tf_idx is None:
            lines.append("\n[TaskForces]\n")
            tf_idx = len(lines) - 1

        # Find highest existing index in [TaskForces]
        highest = -1
        i = tf_idx + 1
        while i < len(lines):
            s = lines[i].strip()
            if s.startswith("[") and s != "[TaskForces]":
                break
            if "=" in s and not s.startswith(";"):
                try:
                    highest = max(highest, int(s.split("=")[0].strip()))
                except ValueError:
                    pass
            i += 1

        # Insert new IDs into [TaskForces] list
        # Append ; YR-AI-Tool comment so entries are identifiable
        insert_at = i
        new_list  = [f"{highest + 1 + j}={tf.tf_id}  ; YR-AI-Tool\r\n"
                     for j, tf in enumerate(taskforces)]
        lines[insert_at:insert_at] = new_list

        # Append taskforce sections
        lines.append("\r\n")
        for tf in taskforces:
            lines.append(f"{tf.label_comment}\r\n")
            lines.append(f"[{tf.tf_id}]\r\n")
            lines.append(f"Name={tf.name}\r\n")
            for idx, (count, uid) in enumerate(tf.units):
                lines.append(f"{idx}={count},{uid}\r\n")
            lines.append(f"Group=-1\r\n")
            lines.append("\r\n")

        lines.append(";\r\n")

        with output_path.open("w", encoding="latin-1") as fh:
            fh.writelines(lines)

        return output_path


@dataclass
class TaskforceSpec:
    """Specifies parameters for generating one or more taskforces."""
    faction:       str
    phase:         str
    role:          str        = "EMERGENT"
    count:         int        = 1
    difficulty:    str        = "Hard"
    country:       str | None = None
    min_grade:     int        = DEFAULT_MIN_GRADE
    philosophy:    str | None = None
    allow_suicide_easy:   bool  = True
    allow_suicide_medium: bool  = False
    allow_suicide_hard:   bool  = False
    fodder_ratio:         float = 1.0  # fodder:main slot ratio (user-configurable)


@dataclass
class GeneratorConfig:
    specs:                list[TaskforceSpec]
    cost_ratio_tolerance: float = 0.05  # 5% of waves can violate ideal ratios
    cost_ratio_strict:    bool  = False  # force all waves perfectly balanced


def validate_cost_ratio(units_list: list[tuple[int, str]],
                         parser: RulesMdParser,
                         strict: bool = False,
                         tolerance: float = 0.05) -> bool:
    """
    Check if a taskforce composition has a reasonable cost distribution.

    The metric is TOTAL COST CONTRIBUTION per unit type (count × unit_cost),
    not per-unit cost. This correctly handles:
      2× Apocalypse (1750 each) + 1× Flak Trooper (300) = 3500 vs 300 = 11.7:1 → BAD
      2× Apocalypse (1750 each) + 8× Conscript (100)   = 3500 vs 800 = 4.4:1  → FINE

    Rule: if the most expensive group costs more than 5× the cheapest group,
    the composition is imbalanced. A lone cheap unit alongside a large
    expensive group is the problem case — not a cheap unit that has many
    copies to compensate.

    Returns True if the composition passes (balanced or within tolerance).
    """
    if len(units_list) < 2:
        return True  # single unit type always fine

    group_costs = []
    for count, uid in units_list:
        rec = parser.units.get(uid)
        if not rec:
            continue
        unit_cost = getattr(rec, 'cost', 0) or 0
        if unit_cost <= 0:
            continue
        group_costs.append(count * unit_cost)   # total cost for this group

    if len(group_costs) < 2:
        return True

    min_group = min(group_costs)
    max_group = max(group_costs)

    if min_group <= 0:
        return True

    ratio = max_group / min_group
    return ratio <= 5.0  # 5× group cost spread is acceptable


# ---------------------------------------------------------------------------
# Interactive CLI
# ---------------------------------------------------------------------------

def _print_header():
    print("=" * 60)
    print("  YR AI Wave Generator — Taskforce Generator")
    print("=" * 60)


def _pick_factions(parser: RulesMdParser) -> list[str]:
    factions = sorted(parser.sides.faction_countries.keys())
    print("\nAvailable factions:")
    for i, f in enumerate(factions, 1):
        countries = ", ".join(parser.sides.faction_countries[f])
        print(f"  {i}. {f}  ({countries})")
    print(f"  {len(factions)+1}. All factions")
    while True:
        raw = input("\nSelect factions (e.g. 1,3 or 'all'): ").strip().lower()
        if raw in ("all", str(len(factions) + 1)):
            return factions
        try:
            indices  = [int(x.strip()) - 1 for x in raw.split(",")]
            selected = [factions[i] for i in indices if 0 <= i < len(factions)]
            if selected:
                return selected
        except (ValueError, IndexError):
            pass
        print("  Invalid selection, try again.")


def _pick_phases() -> list[str]:
    phases = ["Rush", "Early", "Mid", "Late", "EndGame"]
    print("\nAvailable game phases:")
    hints = {
        "Rush":    "infantry only + small vehicles, pre-radar, <$10k",
        "Early":   "barracks/warfactory units",
        "Mid":     "radar-tier units",
        "Late":    "battle lab units",
        "EndGame": "all units, largest waves",
    }
    for i, p in enumerate(phases, 1):
        print(f"  {i}. {p:<10} {hints[p]}")
    print(f"  {len(phases)+1}. All phases")
    while True:
        raw = input("\nSelect phases (e.g. 1,3 or 'all'): ").strip().lower()
        if raw in ("all", str(len(phases) + 1)):
            return phases
        try:
            indices  = [int(x.strip()) - 1 for x in raw.split(",")]
            selected = [phases[i] for i in indices if 0 <= i < len(phases)]
            if selected:
                return selected
        except (ValueError, IndexError):
            pass
        print("  Invalid selection, try again.")


def _pick_difficulty() -> str:
    print("\nDifficulty scaling:")
    print("  1. Easy    (25% unit count)")
    print("  2. Medium  (50% unit count)")
    print("  3. Hard    (100% unit count — default)")
    while True:
        raw = input("Select difficulty [3]: ").strip()
        if not raw or raw == "3": return "Hard"
        if raw == "1": return "Easy"
        if raw == "2": return "Medium"
        print("  Please enter 1, 2, or 3.")


def _pick_roles() -> list[str]:
    available = [r for r in ALL_ROLES if r != "ANTI_NAVAL"]
    print("\nAvailable roles:")
    for i, r in enumerate(available, 1):
        print(f"  {i}. {r}")
    print(f"  {len(available)+1}. All roles (emergent detection)")
    while True:
        raw = input("\nSelect roles (e.g. 1,3 or 'all'): ").strip().lower()
        if raw in ("all", str(len(available) + 1)):
            return ["EMERGENT"]
        try:
            indices  = [int(x.strip()) - 1 for x in raw.split(",")]
            selected = [available[i] for i in indices if 0 <= i < len(available)]
            if selected:
                return selected
        except (ValueError, IndexError):
            pass
        print("  Invalid selection, try again.")


def _ask_min_grade() -> int:
    print("\nMinimum unit grade threshold:")
    print("  0  = neutral default (excludes negative-grade units)")
    print("  5  = above average only")
    print("  10 = strong units only")
    print("  -5 = very permissive (includes most units)")
    while True:
        raw = input(f"Minimum grade [default {DEFAULT_MIN_GRADE}]: ").strip()
        if not raw:
            return DEFAULT_MIN_GRADE
        try:
            return int(raw)
        except ValueError:
            print("  Please enter an integer.")


def _ask_existing_handling() -> str:
    print("\nHow should existing taskforces in aimd.ini be handled?")
    print("  1. Keep and use them  (default — add new ones alongside)")
    print("  2. Keep but ignore    (load IDs to avoid collision, don't reuse)")
    print("  3. Delete existing    (WARNING: removes all current taskforces)")
    while True:
        raw = input("Select [1]: ").strip()
        if not raw or raw == "1": return "keep_use"
        if raw == "2": return "keep_ignore"
        if raw == "3": return "delete"
        print("  Please enter 1, 2, or 3.")


def _basic_mode(parser: RulesMdParser,
                factions: list[str],
                phases: list[str],
                roles: list[str],
                difficulty: str,
                min_grade: int) -> GeneratorConfig:
    while True:
        raw = input("\nHow many taskforces to generate total? ").strip()
        try:
            total = int(raw)
            if total > 0:
                break
        except ValueError:
            pass
        print("  Please enter a positive integer.")

    combos    = [(f, p, r) for f in factions
                            for p in phases
                            for r in roles]
    if not combos:
        return GeneratorConfig([])

    per_combo = max(1, total // len(combos))
    remainder = total - per_combo * len(combos)
    specs     = []
    for i, (faction, phase, role) in enumerate(combos):
        count = per_combo + (1 if i < remainder else 0)
        specs.append(TaskforceSpec(
            faction=faction, phase=phase, role=role,
            count=count, difficulty=difficulty, min_grade=min_grade))
    return GeneratorConfig(specs)


def _advanced_mode(parser: RulesMdParser,
                   factions: list[str],
                   phases: list[str],
                   roles: list[str],
                   difficulty: str,
                   min_grade: int) -> GeneratorConfig:
    specs = []
    print("\nEnter quantity for each combination (0 to skip):\n")
    for faction in factions:
        for phase in phases:
            for role in roles:
                while True:
                    raw = input(f"  {faction}/{phase}/{role}: ").strip()
                    try:
                        count = int(raw)
                        if count >= 0:
                            break
                    except ValueError:
                        pass
                    print("    Please enter a non-negative integer.")
                if count > 0:
                    specs.append(TaskforceSpec(
                        faction=faction, phase=phase, role=role,
                        count=count, difficulty=difficulty, min_grade=min_grade))
    return GeneratorConfig(specs)


def run_interactive(rules_path: str, aimd_path: str):
    _print_header()

    print(f"\nParsing {rules_path}...")
    parser = RulesMdParser(rules_path).parse()
    print(f"  {len(parser.buildable)} buildable units across "
          f"{len(parser.sides.faction_countries)} factions.")
    print(f"  RelaxedStray={parser.relaxed_stray} "
          f"-> vehicle cap={vehicle_capacity(parser.relaxed_stray)}, "
          f"infantry cap={infantry_capacity(parser.relaxed_stray)}")

    print(f"\nLoading existing IDs from {aimd_path}...")
    existing_handling = _ask_existing_handling()
    registry = IDRegistry.from_aimd(aimd_path)
    print(f"  {len(registry._used)} existing IDs loaded.")

    factions   = _pick_factions(parser)
    phases     = _pick_phases()
    roles      = _pick_roles()
    difficulty = _pick_difficulty()
    min_grade  = _ask_min_grade()

    print("\nGeneration mode:")
    print("  1. Basic   — enter total count, distributed automatically")
    print("  2. Advanced — specify count per faction/phase/role combination")
    while True:
        mode = input("\nSelect mode (1 or 2): ").strip()
        if mode in ("1", "2"):
            break
        print("  Please enter 1 or 2.")

    if mode == "1":
        config = _basic_mode(parser, factions, phases, roles, difficulty, min_grade)
    else:
        config = _advanced_mode(parser, factions, phases, roles, difficulty, min_grade)

    print("\nOutput options:")
    print(f"  1. Write directly into {aimd_path}")
    print(f"  2. Write to a separate file")
    while True:
        out_mode = input("\nSelect output (1 or 2): ").strip()
        if out_mode in ("1", "2"):
            break
        print("  Please enter 1 or 2.")

    output_path = None
    if out_mode == "2":
        output_path = input("Output file path: ").strip() or "taskforces_output.ini"

    print("\nGenerating taskforces...")
    gen        = TaskforceGenerator(parser, registry)
    taskforces = gen.generate_batch(config)
    print(f"  Generated {len(taskforces)} taskforces.")

    # Summary
    by_role: dict[str, int] = defaultdict(int)
    for tf in taskforces:
        by_role[tf.role] += 1
    print("\n  Role breakdown:")
    for role, count in sorted(by_role.items()):
        print(f"    {role:<20} {count}")

    confirm = input(
        f"\nWrite {len(taskforces)} taskforces to "
        f"{output_path or aimd_path}? (y/n): "
    ).strip().lower()

    if confirm == "y":
        out = gen.write_to_ini(taskforces, aimd_path, output_path)
        print(f"\nDone. Written to: {out}")
    else:
        print("\nAborted. No files written.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python taskforce_generator.py <rulesmd.ini> <aimd.ini>")
        sys.exit(1)
    run_interactive(sys.argv[1], sys.argv[2])
