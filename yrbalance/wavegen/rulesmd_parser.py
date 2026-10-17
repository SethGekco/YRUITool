"""
rulesmd_parser.py
=================
Parses rulesmd.ini for the YR AI Wave Generator tool.

Armor index order (hardcoded in YR engine, 11 slots):
  0  none     1  flak     2  plate
  3  light    4  medium   5  heavy
  6  wood     7  steel    8  concrete
  9  special  10 defense

Role taxonomy (prefix = target domain):
  AG-Anti_Infantry   verses indices 0,1,2
  AG-Anti_Vehicle    verses indices 3,4,5
  AG-Anti_Building   verses indices 6,7,8
  AG-Multi           effective across multiple ground classes
  AA                 projectile AA=yes
  AN-Surface         (stub - shelved, reserved for future)
  AN-Submerged       (stub - shelved, reserved for future)

Mobility classes (from Locomotor GUID + MovementZone):
  Land        Drive, Walk - ground only
  Amphibious  Hover - ground + water
  Naval       Ship, Mech - water only
  Jumpjet     Jumpjet - flies but mixes with ground/naval
  Aircraft    Fly - pure aircraft, separate pool
  Teleport    Teleport/Chrono - own class for now
  Tunnel      Tunnel locomotor - own class for now
  Special     Rocket, DropPod, Levitate - excluded

Taskforce mixing rules:
  Land + Amphibious + Jumpjet     -> can mix
  Naval + Amphibious + Jumpjet    -> can mix
  Land + Naval                    -> cannot mix
  Aircraft                        -> separate pool only
  Teleport / Tunnel               -> own pools, not mixed yet
  Special                         -> excluded

Faction detection order (must complete before depth calculation):
  1. Read [Sides] for faction->country mapping
  2. Read BaseUnit= from [General] for MCV list
  3. Each MCV: Owner= -> countries, DeploysInto= -> construction yard ID
  4. Walk unit prerequisite chain using ONLY concrete building IDs
     (alias tokens POWER/FACTORY/BARRACKS/RADAR/TECH/PROC are skipped)
  5. First construction yard found = unit's faction
  6. No construction yard found -> uncategorized, excluded

Depth calculation (runs after faction is known):
  - For each prerequisite token:
      * Concrete building: use its depth directly
      * Alias token: expand, filter to same-faction buildings only,
        take the LOWEST depth among filtered results
  - Unit depth = HIGHEST value across all processed prerequisites
  - Relative depth = unit depth minus factory anchor depth
  - Tiers: Rush=0, Early=1, Mid=2, Late=3, EndGame=4+

Generic prerequisite alias keys (hardcoded, from [General]):
  POWER, FACTORY, BARRACKS, RADAR, TECH, PROC
"""

import sys
from pathlib import Path
from collections import defaultdict


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from ..engine.weapons import REGULAR_WEAPON_TAGS

ARMOR_NAMES = [
    "none", "flak", "plate",
    "light", "medium", "heavy",
    "wood", "steel", "concrete",
    "special", "defense",
]
ARMOR_INDEX    = {name: i for i, name in enumerate(ARMOR_NAMES)}
# "Out-ranges the base defenses" needs two numbers, not one: the range of a
# STANDARD high-range defense, and which defenses are unusually long-ranged and
# have to be handled as special cases instead.
#
# Earlier versions used the mean of every defense range (dragged down by
# pillboxes until mediocre units qualified), then "drop the single highest"
# (wrong when a mod fields several long-range defenses — and on a small set it
# discards the one defense that matters most).
#
# So: split the distribution with Tukey's fence, Q3 + 1.5*IQR, the textbook
# boundary for "unusual". Everything above it is an outlier, reported
# separately; the bar is the upper half of what remains, i.e. the dangerous end
# of normal. Vanilla YR ground defenses are
# [5, 5.5, 5.5, 5.5, 7, 7, 8, 15, 20] — the Grand Cannon at 15 and the Paris
# gun at 20 are exactly the "unusual" cases, and the bar lands at the Prism.
# A Miner_Killer must be able to actually kill a miner, not merely rank well
# against the mod's average. spectrum() is RELATIVE: in a mod where nothing hits
# medium armour hard, the least-bad units still clear the relative bar — the
# same flaw that let CTECH be suggested for combat roles. So the relative test
# is now paired with an ABSOLUTE floor.
#
# The floor is derived from the mod's own harvesters rather than hardcoded:
# a unit should be able to solo-kill a TYPICAL (median) harvester inside
# MINER_KILL_SECONDS. Median rather than weakest, for the same reason as
# defense_kill_dps_floor: one junk 10 HP entry otherwise sets the bar at zero
# and the floor stops filtering anything. effective_dps() is per-FRAME
# (damage*verses/rof, ROF being in frames), hence the 15 fps conversion.
MINER_KILL_SECONDS = 30.0
GAME_FPS = 15.0
DEFENSE_BAR_FALLBACK = 6.0
TUKEY_K = 1.5


def _quantile(sorted_vals, q):
    """Linear-interpolated quantile; small-sample friendly."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def defense_range_profile(ranges):
    """Split defense ranges into standard and unusually-long.

    Returns {"bar", "standard", "outliers", "fence"}:
      bar       range a unit must EXCEED to out-range standard defenses
      standard  the non-outlier ranges, sorted
      outliers  ranges above Tukey's fence — handle these individually
      fence     the fence value itself, for display

    An outlier is NOT discarded information: a unit that beats `bar` still
    loses to anything in `outliers`, and the caller should say so rather than
    silently pretend the Grand Cannon is not there.
    """
    vals = []
    for r in ranges or ():
        try:
            v = float(r)
        except (TypeError, ValueError):
            continue
        if v > 0:
            vals.append(v)
    if not vals:
        return {"bar": DEFENSE_BAR_FALLBACK, "standard": [], "outliers": [],
                "fence": DEFENSE_BAR_FALLBACK}
    vals.sort()
    q1, q3 = _quantile(vals, 0.25), _quantile(vals, 0.75)
    fence = q3 + TUKEY_K * (q3 - q1)
    standard = [v for v in vals if v <= fence]
    outliers = [v for v in vals if v > fence]
    if not standard:                       # degenerate (all equal / tiny set)
        standard, outliers = vals, []
    upper = standard[len(standard) // 2:]  # dangerous end of normal
    return {"bar": sum(upper) / len(upper), "standard": standard,
            "outliers": outliers, "fence": fence}


def dps_vs_weapon(wpn, armor_indices):
    """Best effective DPS of one analysed weapon against any of `armor_indices`."""
    verses = wpn.get("verses", [0.0] * 11) if wpn else [0.0] * 11
    damage = wpn.get("damage", 0.0) if wpn else 0.0
    rof = wpn.get("rof", 1.0) if wpn else 1.0
    return max((effective_dps(damage, rof, verses, ai) for ai in armor_indices),
               default=0.0)


def unit_dps_vs(rec, armor_indices):
    """Primary DPS plus a half-weight secondary bonus, never a penalty.

    Lifted to module level so the role/camp filters score units exactly the
    way the grading pass does instead of re-deriving it. A secondary whose
    projectile has AG=no cannot contribute against ground armour (APOC's
    MammothTusk is air-only); aerial armour indices are the exception.
    """
    AERIAL_ARMOR_IDX = {9, 10}
    armor_indices = set(armor_indices or ())
    if not armor_indices:
        return 0.0
    is_aerial_target = bool(armor_indices & AERIAL_ARMOR_IDX)
    pri = dps_vs_weapon(getattr(rec, "role_weapon", None), armor_indices)
    sec = 0.0
    sec_wpn = getattr(rec, "secondary_analysis", None)
    if sec_wpn and sec_wpn.get("is_combat"):
        if sec_wpn.get("can_ag", True) or is_aerial_target:
            sec = dps_vs_weapon(sec_wpn, armor_indices)
    return max(pri, pri + sec * 0.5)


def best_weapon_dps_vs(rec, armor_indices):
    """DPS of the SINGLE best weapon against `armor_indices`.

    Not unit_dps_vs(): that returns max(pri, pri + sec*0.5), a blend which is
    right for "how good is this unit overall" but wrong here. A unit fires ONE
    weapon at a defense — whichever suits the target — so it is graded on that
    weapon alone (Rex, 2026-10-16). The blend also halved the Magnetron's only
    building-damaging weapon, because its primary is a locomotor beam that does
    nothing to structures.

    A secondary whose projectile has AG=no cannot contribute against ground
    armour; aerial armour indices are the exception.
    """
    AERIAL_ARMOR_IDX = {9, 10}
    armor_indices = set(armor_indices or ())
    if not armor_indices:
        return 0.0
    is_aerial_target = bool(armor_indices & AERIAL_ARMOR_IDX)
    best = 0.0
    for key in ("role_weapon", "primary_analysis", "secondary_analysis"):
        w = getattr(rec, key, None)
        if not w:
            continue
        if key == "secondary_analysis" and not w.get("is_combat"):
            continue
        if not w.get("can_ag", True) and not is_aerial_target:
            continue
        best = max(best, dps_vs_weapon(w, armor_indices))
    return best


# A standoff unit is never in the defense's reach, so it can take as long as it
# likes — "slow is fine if the range is high" (Rex). The floor is only there to
# reject units that cannot meaningfully hurt a defense AT ALL: a Sniper needs
# ~6,750 seconds on the median defense and still fails this, while a Magnetron
# or Howitzer passes.
STANDOFF_KILL_SECONDS = 300.0


def standoff_kill_dps_floor(defense_strengths):
    """Relaxed floor for units that out-range what they are shooting."""
    vals = [s for s in (defense_strengths or ()) if s and s > 0]
    if not vals:
        return 0.0
    return _median(vals) / (STANDOFF_KILL_SECONDS * GAME_FPS)


def dps_is_trustworthy(rec):
    """False when the unit's damage is delivered by something we did not read.

    A spawner weapon (V3 launcher, Aircraft Carrier, Dreadnought) carries a
    placeholder warhead — V3Launcher is Damage=1 — and the real damage lives in
    the spawned missile or plane, which the analyser does not follow. Scoring
    such a unit on its own weapon would rank the premier defense killers at
    ~0, so callers must treat their DPS as UNKNOWN and decline to exclude them
    rather than exclude them wrongly.
    """
    if getattr(rec, "is_spawner", False):
        return False
    for key in ("role_weapon", "primary_analysis", "secondary_analysis"):
        a = getattr(rec, key, None)
        if a and a.get("is_spawner_wpn"):
            return False
    return True


# Defenses are tougher than miners and shoot back, so the allowance is longer.
# Vanilla defenses are 400-900 HP, all steel armour.
DEFENSE_KILL_SECONDS = 60.0


def _median(vals):
    s = sorted(vals)
    n = len(s)
    if not n:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def defense_kill_dps_floor(defense_strengths):
    """Minimum DPS vs defense armour to count as a defense killer.

    Uses the MEDIAN defense, not the weakest. "Weakest" is what this started
    as, and it is fragile: counting every armed building (correctly — an armed
    building shoots at you the same) pulls in civilian map props, and vanilla's
    CAMISC06/CAMSC11 at 10 HP dropped the floor to 0.011/frame. At that floor a
    Virus sniper — 35 MINUTES to kill a pillbox — counted as a defense killer.
    The median lands on a real defense (vanilla: 810, the Gattling Cannon)
    while ignoring both the 10 HP props and the 3000 HP ones.

    0.0 when nothing was found, which disables the floor rather than gating
    everything out.
    """
    vals = [s for s in (defense_strengths or ()) if s and s > 0]
    if not vals:
        return 0.0
    return _median(vals) / (DEFENSE_KILL_SECONDS * GAME_FPS)


def primary_defense_armor(armor_counts):
    """The armour a typical defense wears, as {armor_index: count}.

    Scoring against the whole armour SET is wrong here, because
    dps_vs_weapon() takes the MAX over the set: a unit strong against a wooden
    map prop would read as a defense killer. Vanilla's armed buildings are
    steel 12, concrete 6, wood 2, medium 1 — the mode is steel, which is what
    every real defense wears.
    """
    if not armor_counts:
        return set()
    best = max(armor_counts.items(), key=lambda kv: (kv[1], -kv[0]))
    return {best[0]}


def miner_kill_dps_floor(miner_strengths):
    """Minimum per-frame DPS vs miner armour to count as a miner killer.

    0.0 when no harvester was found, which disables the floor rather than
    gating everything out on a mod we could not read.
    """
    vals = [s for s in (miner_strengths or ()) if s and s > 0]
    if not vals:
        return 0.0
    return _median(vals) / (MINER_KILL_SECONDS * GAME_FPS)


def defense_range_bar(ranges):
    """Just the bar from defense_range_profile(); kept for existing callers."""
    return defense_range_profile(ranges)["bar"]


ARMOR_INFANTRY = frozenset({0, 1, 2})
ARMOR_VEHICLE  = frozenset({3, 4, 5})
ARMOR_BUILDING = frozenset({6, 7, 8})

ROLE_THRESHOLD = 0.50

# Tier depth ceilings (absolute prerequisite depth):
#   Early   = barracks units (depth <= 3)
#   Mid     = warfactory / radar units (depth <= 4)
#   Late    = battle lab units (depth <= 5)
#   EndGame = mod support for deeper chains
#
# Rush is NOT a parser-assigned tier. It is a taskforce generator concept:
#   Rush-Infantry: infantry only, small waves, triggered before warfactory
#   Rush-Vehicle:  vehicles + infantry, fast waves, triggered before radar
# The generator decides which rush phase applies at runtime based on what
# factory buildings the AI currently has available.

TIER_DEPTH_CEILING = {
    "Early":   3,
    "Mid":     4,
    "Late":    5,
    "EndGame": 99,
}


def compute_tier_ceilings(ini, sides=None) -> dict[str, int]:
    """
    Compute tier depth ceilings from the actual mod's prerequisite structure.

    Strategy: find the minimum depth of radar buildings and lab buildings
    by tracing their own prerequisite chains. Then:
      Early  = radar_depth - 1   (units not requiring radar)
      Mid    = lab_depth - 1     (units requiring radar but not lab)
      Late   = lab_depth         (units requiring lab)
      EndGame = 99               (no upper bound)

    Falls back to hardcoded defaults if radar/lab buildings can't be found.

    In vanilla YR: radar=depth 3, lab=depth 4 → Early≤3, Mid≤4, Late≤5
    In a deep mod:  radar=depth 5, lab=depth 7 → Early≤5, Mid≤7, Late≤8
    """
    ai  = ini.section('AI')
    gen = ini.section('General')

    def _ids(keys):
        result = set()
        for key in keys:
            for val in [ai.get(key, ''), gen.get(key, '')]:
                for x in val.split(','):
                    x = x.strip().split(';')[0].strip()
                    if x:
                        result.add(x)
        return result

    radar_ids = _ids(['buildradar', 'prerequisiteradar'])
    lab_ids   = _ids(['buildtech',  'prerequisitetech'])

    def _bld_depth(bid, visited=None):
        if visited is None: visited = set()
        if bid in visited: return 0
        visited.add(bid)
        sec = ini.section(bid)
        if not sec: return 0
        prereq_str = sec.get('prerequisite', '')
        prereqs    = [p.strip().split(';')[0].strip()
                      for p in prereq_str.split(',') if p.strip()]
        if not prereqs: return 1
        return 1 + max(_bld_depth(p, visited) for p in prereqs)

    radar_depths = [_bld_depth(b) for b in radar_ids if ini.section(b)]
    lab_depths   = [_bld_depth(b) for b in lab_ids   if ini.section(b)]

    if not radar_depths or not lab_depths:
        # Fall back to hardcoded defaults
        return dict(TIER_DEPTH_CEILING)

    radar_depth = min(radar_depths)   # shallowest radar building
    lab_depth   = min(lab_depths)     # shallowest lab building

    # Units requiring radar are at radar_depth+1; Early is everything below that
    early_ceil = radar_depth          # ≤ radar_depth means no radar in chain
    mid_ceil   = lab_depth            # ≤ lab_depth means radar but no lab yet
    late_ceil  = lab_depth + 1        # one step past lab = requires lab

    return {
        "Early":   early_ceil,
        "Mid":     mid_ceil,
        "Late":    late_ceil,
        "EndGame": 99,
    }

# Taskforce size guidance — used by the generator, not the parser
TIER_SIZE_HINT = {
    "Early":   "small",
    "Mid":     "regular",
    "Late":    "large",
    "EndGame": "xlarge",
}

# Rush composition rule — vehicles eligible for rushing must have rel=0
# (no prerequisites beyond their factory). Applied by the generator.
RUSH_MAX_VEHICLE_REL_DEPTH = 0

UNIT_CATEGORIES = ("InfantryTypes", "VehicleTypes", "AircraftTypes", "BuildingTypes")

AI_FACTORY_KEYS = (
    "BuildBarracks",
    "BuildWeapons",
    "BuildNavalYard",
)

# Hardcoded alias key names - used to identify alias tokens in prereq strings
PREREQ_ALIAS_KEYS = {
    "POWER":    "PrerequisitePower",
    "FACTORY":  "PrerequisiteFactory",
    "BARRACKS": "PrerequisiteBarracks",
    "RADAR":    "PrerequisiteRadar",
    "TECH":     "PrerequisiteTech",
    "PROC":     "PrerequisiteProc",
}
ALIAS_TOKENS = frozenset(PREREQ_ALIAS_KEYS.keys())

NON_COMBAT_FLAGS = (
    "engineer", "resourcegatherer", "stupidhunt", "slaved", "dontscore",
)

STOLEN_TECH_FLAGS = (
    "requiresstolenalliedtech",
    "requiresstolensoviettech",
    "requiresstolenthirdtech",
)

LOCOMOTOR_MOBILITY = {
    "{4A582741-9839-11D1-B709-00A024DDAFD1}": "Land",
    "{4A582742-9839-11D1-B709-00A024DDAFD1}": "Amphibious",
    "{4A582743-9839-11D1-B709-00A024DDAFD1}": "Tunnel",
    "{4A582744-9839-11D1-B709-00A024DDAFD1}": "Land",
    "{4A582745-9839-11D1-B709-00A024DDAFD1}": "Special",
    "{4A582746-9839-11D1-B709-00A024DDAFD1}": "Aircraft",
    "{4A582747-9839-11D1-B709-00A024DDAFD1}": "Teleport",
    "{55D141B8-DB94-11D1-AC98-006008055BB5}": "Naval",
    "{2BEA74E1-7CCA-11D3-BE14-00104B62A16C}": "Naval",
    "{92612C46-F71F-11D1-AC9F-006008055BB5}": "Jumpjet",
    "{B7B49766-E576-11D3-9BD9-00104B972FE8}": "Special",
    "{3DC0B295-6546-11D3-80B0-00902792494C}": "Special",
}

MOVEMENTZONE_MOBILITY = {
    "normal":              "Land",
    "crusher":             "Land",
    "destroyer":           "Land",
    "amphibious":          "Amphibious",
    "amphibiouscrusher":   "Amphibious",
    "amphibiousdestroyer": "Amphibious",
    "water":               "Naval",
    "subterranean":        "Tunnel",
    "fly":                 "Aircraft",
    "jumpjet":             "Jumpjet",
    "none":                "Special",
}

LAND_MIX_GROUP   = frozenset({"Land", "Amphibious", "Jumpjet", "Tunnel"})
NAVAL_MIX_GROUP  = frozenset({"Naval", "Amphibious", "Jumpjet"})
AIRCRAFT_GROUP   = frozenset({"Aircraft"})
SPECIAL_EXCLUDED = frozenset({"Special", "Teleport"})


def can_mix(mobility_a, mobility_b):
    for group in (LAND_MIX_GROUP, NAVAL_MIX_GROUP, AIRCRAFT_GROUP):
        if mobility_a in group and mobility_b in group:
            return True
    return False


# ---------------------------------------------------------------------------
# INI parser
# ---------------------------------------------------------------------------

class IniParser:
    def __init__(self, path):
        self.path = Path(path)
        self.sections = {}
        self._section_order = []
        # Combined lines of the rules file with Ares [#include] / Phobos
        # [$include] expanded (recursive, + .mix fallback) via the engine
        # resolver, so sections split across included files are parsed too.
        self._lines = self._read_resolved()
        self._parse()

    def _read_resolved(self):
        """Read the rules file with its include graph resolved into one line list.

        Uses the engine's resolver (recursive, both directive spellings, loose +
        .mix, cycle-safe, main-file-wins). Falls back to the single main file if
        the resolver can't be imported."""
        try:
            from ..engine.includes import resolve_includes
            lines, _prov = resolve_includes(str(self.path))
            return lines
        except Exception:
            try:
                with self.path.open(encoding="latin-1", errors="replace") as fh:
                    return fh.readlines()
            except OSError:
                return []

    def _strip_comment(self, text):
        idx = text.find(";")
        return text[:idx].strip() if idx != -1 else text.strip()

    def _parse(self):
        current = None
        for raw_line in self._lines:
            line = raw_line.strip()
            if not line or line.startswith(";"):
                continue
            if line.startswith("["):
                end = line.find("]")
                if end != -1:
                    current = line[1:end]
                    if current not in self.sections:
                        self.sections[current] = {}
                        self._section_order.append(current)
                continue
            if "=" in line and current is not None:
                key, _, val = line.partition("=")
                key = key.strip()
                if key.startswith(";"):
                    continue
                # Don't overwrite keys already set earlier (main file wins over
                # includes; first occurrence wins within a section).
                k_lower = key.lower()
                if k_lower not in self.sections[current]:
                    self.sections[current][k_lower] = self._strip_comment(val)

    def get(self, section, key, default=""):
        return self.sections.get(section, {}).get(key.lower(), default)

    def section(self, name):
        return self.sections.get(name, {})

    def has_section(self, name):
        return name in self.sections

    def list_from_section(self, section_name):
        sec = self.sections.get(section_name, {})
        numbered = [(int(k), v.strip()) for k, v in sec.items() if k.isdigit()]
        numbered.sort()
        return [v for _, v in numbered]


# ---------------------------------------------------------------------------
# Sides / faction registry
# ---------------------------------------------------------------------------

class SidesRegistry:
    """
    Reads [Sides] and BaseUnit= MCVs to build:
      faction_countries           : dict[faction -> list[country]]
      country_faction             : dict[country -> faction]
      construction_yard_faction   : dict[yard_id -> faction]
      faction_construction_yards  : dict[faction -> set[yard_id]]
    """

    def __init__(self, ini):
        self.ini = ini
        self.faction_countries:          dict[str, list[str]] = {}
        self.country_faction:            dict[str, str]       = {}
        self.construction_yard_faction:  dict[str, str]       = {}
        self.faction_construction_yards: dict[str, set]       = defaultdict(set)
        # Prerequisite group aliases — defined at INI root or [General] as
        # GATECHPRE=GATECH,GATECHFR,... (any group ID ending in PRE or similar)
        # These allow one name to represent multiple equivalent buildings.
        self.prereq_groups:              dict[str, list[str]] = {}
        self._load_prereq_groups()
        self._load_sides()
        self._load_mcvs()

    def _load_prereq_groups(self):
        """
        Scan the INI for prerequisite group alias definitions.
        These appear as bare key=value pairs NOT inside a named section,
        or inside [General], of the form:
            GATECHPRE=GATECH,GATECHFR,GATECHBR,...
        The key is typically ALL-CAPS and ends with PRE, GRP, or similar.
        We detect them by scanning the root (no-section) entries and [General].
        """
        import re
        # Root-level entries parsed into a pseudo-section called '' or None
        # The IniParser puts them into whatever section was current — scan all
        # sections for values that look like comma-separated building ID lists
        # where the key looks like a prereq alias (uppercase, ends in common suffix)
        group_pattern = re.compile(r'^[A-Z][A-Z0-9]{3,}(?:PRE|GRP|WFP|AU|BR)$', re.I)

        # Scan [General] first (common location)
        for key, val in self.ini.section('General').items():
            if group_pattern.match(key.upper()):
                blds = [b.strip() for b in val.split(',') if b.strip()]
                if blds:
                    self.prereq_groups[key.upper()] = blds

        # Also scan root-level entries stored under any section header that
        # looks like a building/group definition (the ini parser assigns them
        # to the last section seen before them)
        # Direct line scan for entries like GATECHPRE=GATECH,GATECHFR,... — over
        # the include-resolved lines so aliases defined in included files count.
        in_section = False
        for line in getattr(self.ini, "_lines", []):
            line = line.strip()
            if not line or line.startswith(';'):
                continue
            if line.startswith('['):
                end = line.find(']')
                current_section = line[1:end] if end > 0 else None
                in_section = current_section is not None
                continue
            if '=' in line and in_section:
                key, _, val = line.partition('=')
                key = key.strip()
                if key.startswith(';'):
                    continue
                if group_pattern.match(key.upper()):
                    # Strip inline comments
                    val = val.split(';')[0].strip()
                    blds = [b.strip() for b in val.split(',')
                            if b.strip() and not b.strip().startswith(';')]
                    if blds and key.upper() not in self.prereq_groups:
                        self.prereq_groups[key.upper()] = blds

    def _load_sides(self):
        # Skip factions where no country has Multiplay=yes.
        # This correctly excludes engine-internal factions (civilian, mutant)
        # while including any modded playable faction (AntFaction etc.)
        # regardless of its name.
        for key, val in self.ini.section("Sides").items():
            countries = [c.strip() for c in val.split(",") if c.strip()]
            # Check if at least one country in this faction is playable
            any_playable = any(
                self.ini.section(c) and
                self.ini.section(c).get('multiplay', 'no').lower() == 'yes'
                for c in countries
            )
            if not any_playable:
                continue
            self.faction_countries[key] = countries
            for country in countries:
                self.country_faction[country] = key

    def _load_mcvs(self):
        base_unit_str = self.ini.get("General", "BaseUnit", "")
        for mcv_id in [m.strip() for m in base_unit_str.split(",") if m.strip()]:
            sec = self.ini.section(mcv_id)
            if not sec:
                continue
            deploys_into = sec.get("deploysinto", "")
            owner_str    = sec.get("owner", "")
            if not deploys_into:
                continue
            owners  = [o.strip() for o in owner_str.split(",") if o.strip()]
            faction = None
            for owner in owners:
                faction = self.country_faction.get(owner)
                if faction:
                    break
            if faction:
                for yard in [y.strip() for y in deploys_into.split(",") if y.strip()]:
                    self.construction_yard_faction[yard] = faction
                    self.faction_construction_yards[faction].add(yard)

    def faction_from_prereq_chain(self, raw_prereq_str: str) -> str | None:
        """
        Walk prerequisite chain using ONLY concrete building IDs.
        Expands:
          1. Standard alias tokens (POWER, RADAR, etc.) via [General] keys
          2. Mod-defined prereq groups (GATECHPRE=GATECH,GATECHFR,...) via
             self.prereq_groups (populated by _load_prereq_groups)
        If an alias expands to buildings from multiple factions, it is
        treated as ambiguous and skipped (returns None for that alias).
        Returns faction name of first construction yard found, or None.
        """
        tokens = [t.strip() for t in raw_prereq_str.split(",") if t.strip()]

        # Expand alias tokens to concrete building IDs
        concrete = []
        for t in tokens:
            upper = t.upper()
            if upper in PREREQ_ALIAS_KEYS:
                # Expand via [General] key
                gen_key = PREREQ_ALIAS_KEYS[upper].lower()
                gen_val = self.ini.get("General", gen_key, "")
                for x in gen_val.split(","):
                    x = x.strip().split(";")[0].strip()
                    if x:
                        concrete.append(x)
            elif upper in self.prereq_groups:
                # Expand mod-defined prereq group alias
                for x in self.prereq_groups[upper]:
                    concrete.append(x)
            else:
                concrete.append(t)

        # Phase 1: try concrete prereqs (expand groups, skip std aliases)
        # These are the strongest faction signals
        concrete_no_alias = []
        for t in tokens:
            upper = t.upper()
            if upper in PREREQ_ALIAS_KEYS:
                pass  # skip std aliases in phase 1
            elif upper in self.prereq_groups:
                # Expand mod prereq group — these ARE concrete
                concrete_no_alias.extend(self.prereq_groups[upper])
            else:
                concrete_no_alias.append(t)

        visited  = set()
        stack    = list(concrete_no_alias)
        factions = set()
        while stack:
            bid = stack.pop()
            if bid in visited:
                continue
            visited.add(bid)
            if bid in self.construction_yard_faction:
                factions.add(self.construction_yard_faction[bid])
                continue
            sub_raw = self.ini.get(bid, "Prerequisite", "")
            for sub in [t.strip() for t in sub_raw.split(",") if t.strip()]:
                upper = sub.upper()
                if upper in PREREQ_ALIAS_KEYS:
                    pass  # skip std aliases
                elif upper in self.prereq_groups:
                    for x in self.prereq_groups[upper]:
                        if x not in visited:
                            stack.append(x)
                elif sub not in visited:
                    stack.append(sub)
        if len(factions) == 1:
            return factions.pop()

        # Phase 2: expand standard alias tokens and retry
        expanded = []
        for t in tokens:
            upper = t.upper()
            if upper in PREREQ_ALIAS_KEYS:
                gen_key = PREREQ_ALIAS_KEYS[upper].lower()
                gen_val = self.ini.get("General", gen_key, "")
                for x in gen_val.split(","):
                    x = x.strip().split(";")[0].strip()
                    if x:
                        expanded.append(x)
            elif upper in self.prereq_groups:
                expanded.extend(self.prereq_groups[upper])
            else:
                expanded.append(t)

        visited2 = set()
        stack2   = list(expanded)
        factions2 = set()
        while stack2:
            bid = stack2.pop()
            if bid in visited2:
                continue
            visited2.add(bid)
            if bid in self.construction_yard_faction:
                factions2.add(self.construction_yard_faction[bid])
                continue
            sub_raw = self.ini.get(bid, "Prerequisite", "")
            for sub in [t.strip() for t in sub_raw.split(",") if t.strip()]:
                if sub.upper() not in PREREQ_ALIAS_KEYS and sub not in visited2:
                    stack2.append(sub)
        if len(factions2) == 1:
            return factions2.pop()

        # Ambiguous (e.g. BARRACKS expands to all three factions)
        return None

    def buildings_in_faction(self, building_ids: list[str],
                              faction: str) -> list[str]:
        """
        Given a list of concrete building IDs, return only those whose
        prerequisite chain traces back to the given faction's construction yard.
        """
        result = []
        yards  = self.faction_construction_yards.get(faction, set())
        for bid in building_ids:
            if self._traces_to_yards(bid, yards, set()):
                result.append(bid)
        return result

    def _traces_to_yards(self, bid: str, yards: set,
                          visited: set) -> bool:
        if bid in visited:
            return False
        visited = visited | {bid}
        if bid in yards:
            return True
        sub_raw = self.ini.get(bid, "Prerequisite", "")
        for sub in [t.strip() for t in sub_raw.split(",") if t.strip()]:
            if sub.upper() not in ALIAS_TOKENS:
                if self._traces_to_yards(sub, yards, visited):
                    return True
        return False

    def faction_from_forbidden_houses(
            self,
            unit_id: str,
            category: str,
            aircraft_factories: set) -> tuple[str | None, set[str]]:
        """
        Fallback faction detection for units whose concrete prerequisite
        chain contains no construction yard (e.g. DOG, ADOG, ORCA, BEAG).

        Strategy:
          1. For Aircraft: resolve faction via Dock= buildings' construction
             yard chains. This is the authoritative source.
          2. For all units: compute allowed countries from RequiredHouses
             or (all known countries - ForbiddenHouses).
          3. Derive faction from the dominant faction among allowed countries,
             cross-checked against the aircraft factory faction if available.

        Returns (faction_name_or_None, allowed_countries_set).
        Stores raw ForbiddenHouses on the unit via notes for reference.
        """
        sec = self.ini.section(unit_id)
        if not sec:
            return None, set()

        forbidden_str = sec.get("forbiddenhouses", "")
        required_str  = sec.get("requiredhouses",  "")

        forbidden = {h.strip() for h in forbidden_str.split(",")
                     if h.strip()} if forbidden_str else set()
        required  = {h.strip() for h in required_str.split(",")
                     if h.strip()} if required_str else set()

        # Build full country set across all factions
        all_countries = set()
        for countries in self.faction_countries.values():
            all_countries.update(countries)

        # Determine allowed countries
        if required:
            allowed = required & all_countries
        else:
            allowed = all_countries - forbidden

        # For aircraft: use Dock= buildings to determine faction authoritatively
        dock_faction = None
        if category == "Aircraft":
            dock_str = sec.get("dock", "")
            for dock_id in [d.strip() for d in dock_str.split(",") if d.strip()]:
                if dock_id in aircraft_factories:
                    f = self.faction_from_prereq_chain(
                        self.ini.get(dock_id, "Prerequisite", ""))
                    if f:
                        dock_faction = f
                        break

        # Determine faction from allowed countries
        if dock_faction:
            # Trust dock faction, filter allowed countries to that faction
            faction = dock_faction
            faction_countries = set(self.faction_countries.get(faction, []))
            # Narrow allowed to faction countries, unless that leaves nothing
            narrowed = allowed & faction_countries
            allowed  = narrowed if narrowed else allowed
        else:
            # Vote: whichever faction has the most allowed countries wins
            faction_votes: dict[str, int] = {}
            for country in allowed:
                f = self.country_faction.get(country)
                if f:
                    faction_votes[f] = faction_votes.get(f, 0) + 1
            faction = (max(faction_votes, key=faction_votes.get)
                       if faction_votes else None)

        return faction, allowed


# ---------------------------------------------------------------------------
# Alias resolver
# ---------------------------------------------------------------------------

class AliasResolver:
    """
    Expands generic prerequisite tokens to their concrete building ID lists.
    Also supports faction-filtered expansion for depth calculation.
    """

    def __init__(self, ini):
        self.ini = ini
        self.aliases: dict[str, list[str]] = {}
        self._load()

    def _load(self):
        gen = self.ini.section("General")
        for token, gen_key in PREREQ_ALIAS_KEYS.items():
            val = gen.get(gen_key.lower(), "")
            if val:
                self.aliases[token] = [
                    b.strip() for b in val.split(",") if b.strip()]

    def expand(self, prereq_str: str) -> list[str]:
        """Expand all tokens (aliases and concrete) to a flat list."""
        if not prereq_str or prereq_str.lower() in ("<none>", "none"):
            return []
        result = []
        for token in prereq_str.split(","):
            token = token.strip()
            if not token:
                continue
            expanded = self.aliases.get(token.upper())
            result.extend(expanded if expanded else [token])
        return result

    def expand_faction_filtered(self, prereq_str: str,
                                 faction: str,
                                 sides: "SidesRegistry") -> list[str]:
        """
        Expand prereq string. For alias tokens, filter expansions to only
        buildings that trace back to the given faction's construction yards.
        Falls back to full expansion if filtering leaves nothing.
        """
        if not prereq_str or prereq_str.lower() in ("<none>", "none"):
            return []
        result = []
        for token in prereq_str.split(","):
            token = token.strip()
            if not token:
                continue
            upper = token.upper()
            if upper in ALIAS_TOKENS:
                all_buildings = self.aliases.get(upper, [])
                filtered = sides.buildings_in_faction(all_buildings, faction)
                result.extend(filtered if filtered else all_buildings)
            else:
                result.append(token)
        return result


# ---------------------------------------------------------------------------
# Depth resolver
# ---------------------------------------------------------------------------

class DepthResolver:
    """
    Computes prerequisite depth for any building ID.

    For a given unit/building, depth is computed as:
      depth(X) = 0                             if no prerequisites
      depth(X) = 1 + max(resolved_depths(...)) otherwise

    When resolving a prerequisite list:
      - Concrete building IDs: resolve depth directly (recursive)
      - Alias tokens: expand, optionally filter by faction, take LOWEST depth
    Then take the HIGHEST value across all resolved prerequisites.

    This means:
      - Within an alias (OR relationship): take lowest (any one suffices)
      - Across all prerequisites (AND relationship): take highest (all required)
    """

    def __init__(self, ini, aliases):
        self.ini     = ini
        self.aliases = aliases
        self._cache: dict[tuple, int] = {}  # (building_id, faction) -> depth

    def depth(self, building_id: str,
              faction: str | None = None,
              sides: "SidesRegistry | None" = None,
              _visiting: frozenset = frozenset()) -> int:
        """
        Compute depth of a building, optionally with faction filtering
        for alias token expansion.
        """
        cache_key = (building_id, faction)
        if cache_key in self._cache:
            return self._cache[cache_key]
        if building_id in _visiting:
            return 0

        prereq_str = self.ini.get(building_id, "Prerequisite", "")
        if not prereq_str or prereq_str.lower() in ("<none>", "none"):
            self._cache[cache_key] = 0
            return 0

        visiting = _visiting | {building_id}
        result   = self._resolve_prereq_depth(
            prereq_str, faction, sides, visiting, building_id)
        result   = 1 + result
        self._cache[cache_key] = result
        return result

    def _building_requires(self, candidate: str, target: str,
                            faction: str | None = None,
                            sides: "SidesRegistry | None" = None,
                            _visiting: frozenset = frozenset()) -> bool:
        """
        Return True if `candidate` has `target` anywhere in its concrete
        prerequisite chain (candidate is higher in the tech tree than target).

        For alias tokens, only expand to buildings that are direct
        faction-yard children (depth <= 1) — this prevents deep buildings
        like NANRCT appearing in POWER expansion and creating false positives
        when computing depth of early buildings like NAWEAP.
        """
        if candidate in _visiting:
            return False
        visiting = _visiting | {candidate}
        prereq_str = self.ini.get(candidate, "Prerequisite", "")
        if not prereq_str or prereq_str.lower() in ("<none>", "none"):
            return False
        tokens = [t.strip() for t in prereq_str.split(",") if t.strip()]
        expanded = []
        for t in tokens:
            if t.upper() in ALIAS_TOKENS:
                all_blds = self.aliases.aliases.get(t.upper(), [])
                # For back-edge detection, only consider shallow buildings
                # (those with no prerequisites beyond a construction yard)
                # to avoid NANRCT-type inflation
                shallow = []
                for b in all_blds:
                    b_prereq = self.ini.get(b, "Prerequisite", "")
                    b_tokens = [x.strip() for x in b_prereq.split(",") if x.strip()]
                    # Shallow = only has construction yards or power plants as prereqs
                    b_concrete = [x for x in b_tokens if x.upper() not in ALIAS_TOKENS]
                    if sides and faction:
                        yards = sides.faction_construction_yards.get(faction, set())
                        is_shallow = all(
                            x in yards or
                            self.ini.get(x, "Prerequisite", "").strip() in ("", "none")
                            for x in b_concrete
                        )
                    else:
                        is_shallow = len(b_concrete) <= 1
                    if is_shallow:
                        shallow.append(b)
                expanded.extend(b for b in shallow if b not in visiting)
            else:
                if t not in visiting:
                    expanded.append(t)
        for b in expanded:
            if b == target:
                return True
            if self._building_requires(b, target, faction, sides, visiting):
                return True
        return False

    def _resolve_prereq_depth(self, prereq_str: str,
                               faction: str | None,
                               sides: "SidesRegistry | None",
                               visiting: frozenset,
                               current_building: str | None = None) -> int:
        """
        For a raw prerequisite string, compute the highest depth across
        all prerequisites, treating alias tokens with lowest-wins logic.

        AI-only dummy tokens (buildings that no human player can own, such
        as DUM which has Owner=<all> AND ForbiddenHouses=<all>) are skipped —
        they signal AI targeting, not real tech-tree depth.

        GenericPrerequisites groups are treated as depth=1 (flat) rather than
        recursively expanded, to avoid depth inflation in mods with deep GP chains.
        """
        tokens = [t.strip() for t in prereq_str.split(",") if t.strip()]
        depths_per_token = []

        gp_upper = {}
        gp_sec = self.ini.section('GenericPrerequisites')
        if gp_sec:
            gp_upper = {k.upper(): v for k, v in gp_sec.items()}

        def _player_impossible(bid: str) -> bool:
            """True if no human player can ever build/own this building."""
            s = self.ini.section(bid)
            if not s:
                return True
            try:
                tl = int(s.get('techlevel', '-1') or '-1')
            except ValueError:
                tl = -1
            if tl >= 11:
                return True
            owner_raw = s.get('owner', '')
            fh_raw    = s.get('forbiddenhouses', '')
            if not owner_raw.strip():
                return True
            owners    = {o.strip() for o in owner_raw.split(',') if o.strip()}
            forbidden = {f.strip() for f in fh_raw.split(',') if f.strip()}
            return len(owners - forbidden) == 0

        def _is_ai_only_token(token: str) -> bool:
            """True if token is an AI-only dummy — skip for depth computation."""
            upper = token.upper()
            if upper in gp_upper:
                blds = [b.strip() for b in gp_upper[upper].split(',') if b.strip()]
                return bool(blds) and all(_player_impossible(b) for b in blds)
            return _player_impossible(token)

        for token in tokens:
            upper = token.upper()

            # Skip AI-only dummy tokens
            if _is_ai_only_token(token):
                continue

            if upper in ALIAS_TOKENS:
                all_buildings = self.aliases.aliases.get(upper, [])
                if faction and sides:
                    filtered = sides.buildings_in_faction(all_buildings, faction)
                    candidates = filtered if filtered else all_buildings
                else:
                    candidates = all_buildings
                if current_building:
                    candidates = [
                        b for b in candidates
                        if not self._building_requires(
                            b, current_building, faction, sides)
                    ]
                if not candidates:
                    continue
                candidate_depths = [
                    self.depth(b, faction, sides, visiting)
                    for b in candidates
                ]
                depths_per_token.append(min(candidate_depths))

            elif upper in gp_upper:
                # GenericPrerequisites group: treat as flat depth=1
                # (not recursively expanded to avoid chain inflation)
                blds = [b.strip() for b in gp_upper[upper].split(',') if b.strip()]
                possible = [b for b in blds if not _player_impossible(b)]
                if possible:
                    depths_per_token.append(1)

            else:
                if current_building and self._building_requires(
                        token, current_building, faction, sides):
                    continue
                depths_per_token.append(
                    self.depth(token, faction, sides, visiting))

        return max(depths_per_token) if depths_per_token else 0

    def unit_depth(self, unit_id: str,
                   faction: str | None = None,
                   sides: "SidesRegistry | None" = None) -> int:
        """Compute depth for a unit (same logic as building depth)."""
        return self.depth(unit_id, faction, sides)


# ---------------------------------------------------------------------------
# Factory anchor registry
# ---------------------------------------------------------------------------

class FactoryRegistry:
    """
    Identifies factory anchors from [AI] and aircraft factories by scanning
    for Factory=AircraftType on all buildings.
    """

    def __init__(self, ini, depth_resolver, aliases, sides):
        self.ini            = ini
        self.depth_resolver = depth_resolver
        self.aliases        = aliases
        self.sides          = sides

        self.factory_type:       dict[str, str] = {}
        self.factory_depth:      dict[str, int] = {}
        self.aircraft_factories: set[str]       = set()

        self._load_ai_factories()
        self._load_aircraft_factories()

    def _load_ai_factories(self):
        ai_sec = self.ini.section("AI")
        for key in AI_FACTORY_KEYS:
            val = ai_sec.get(key.lower(), "")
            if not val:
                continue
            for bld in [b.strip() for b in val.split(",") if b.strip()]:
                self.factory_type[bld]  = key
                self.factory_depth[bld] = self.depth_resolver.depth(bld)

    def _load_aircraft_factories(self):
        for uid in self.ini.list_from_section("BuildingTypes"):
            if uid and not uid.startswith(";"):
                if self.ini.get(uid, "Factory", "").lower() == "aircrafttype":
                    self.aircraft_factories.add(uid)

    def find_factory_for_unit(self, unit_id: str,
                               category: str,
                               is_naval: bool,
                               faction: str | None) -> str | None:
        if category == "Aircraft":
            return self._find_aircraft_factory(unit_id)

        if category == "Infantry":
            allowed_keys = {"BuildBarracks"}
        elif category == "Vehicle":
            allowed_keys = {"BuildNavalYard"} if is_naval else {"BuildWeapons"}
        else:
            return None

        prereq_str = self.ini.get(unit_id, "Prerequisite", "")
        # Expand with faction filtering for factory search
        if faction:
            prereqs = self.aliases.expand_faction_filtered(
                prereq_str, faction, self.sides)
        else:
            prereqs = self.aliases.expand(prereq_str)

        best_factory = None
        best_depth   = -1
        visited      = set()
        stack        = list(prereqs)

        while stack:
            bid = stack.pop()
            if bid in visited:
                continue
            visited.add(bid)
            if bid in self.factory_type and self.factory_type[bid] in allowed_keys:
                d = self.factory_depth[bid]
                if d > best_depth:
                    best_depth   = d
                    best_factory = bid
            sub_raw = self.ini.get(bid, "Prerequisite", "")
            for sub in self.aliases.expand(sub_raw):
                if sub not in visited:
                    stack.append(sub)

        return best_factory

    def _find_aircraft_factory(self, unit_id: str) -> str | None:
        dock_str = self.ini.get(unit_id, "Dock", "")
        if not dock_str:
            return None
        best = None
        best_depth = -1
        for dock_id in [d.strip() for d in dock_str.split(",") if d.strip()]:
            if dock_id in self.aircraft_factories:
                d = self.depth_resolver.depth(dock_id)
                if d > best_depth:
                    best_depth = d
                    best = dock_id
        return best

    def relative_depth(self, unit_id: str,
                        factory_id: str,
                        faction: str | None,
                        sides: "SidesRegistry") -> int:
        """
        Relative depth = unit's faction-aware depth minus factory's depth.
        Floored at 0.
        """
        unit_d    = self.depth_resolver.unit_depth(unit_id, faction, sides)
        factory_d = self.factory_depth.get(
            factory_id, self.depth_resolver.depth(factory_id))
        return max(unit_d - factory_d, 0)


# ---------------------------------------------------------------------------
# Warhead / weapon analysis
# ---------------------------------------------------------------------------

def parse_verses(verses_str):
    values = []
    for token in verses_str.split(","):
        token = token.strip().rstrip("%")
        try:    values.append(float(token) / 100.0)
        except: values.append(1.0)
    while len(values) < 11:
        values.append(1.0)
    return values[:11]


def is_fake_warhead(verses, warhead_id, ini, is_spawner_weapon):
    """
    Detect warheads that have no real combat effect.
    IMPORTANT: Spawner weapons (Spawner=yes) are NEVER fake regardless of
    their warhead or damage values. ASWLauncher (damage=1, warhead=Special)
    looks useless but actually spawns depth charge aircraft — it's a real
    combat weapon. Always check Spawner=yes before calling this function.
    """
    if is_spawner_weapon:
        return False  # spawner weapons are always real — they deploy projectiles
    if warhead_id.lower() == "special":
        return True
    if all(v == 0.0 for v in verses):
        return True
    if all(abs(v - 0.01) < 1e-9 for v in verses):
        return True
    if ini.get(warhead_id, "MakesDisguise", "no").lower() == "yes":
        return True
    return False


def is_scanner_projectile(projectile_id: str, ini) -> bool:
    """Unused — scanner detection is now done via NeverUse flag on the weapon."""
    return False


def is_neveruse_weapon(weapon_id: str, ini) -> bool:
    """
    Detect scanner/detection weapons via NeverUse=yes.
    These are never fired in combat — only used for range calculation
    (e.g. VirtualScanner on dogs maintains chase distance).
    The tell is NeverUse=yes on the weapon section itself.
    """
    if not weapon_id:
        return False
    sec = ini.section(weapon_id)
    if not sec:
        return False
    return sec.get("neveruse", "no").lower() == "yes"


def effective_dps(damage, rof, verses, armor_idx):
    if rof <= 0:
        return 0.0
    return (damage * verses[armor_idx]) / rof


class WeaponAnalyzer:
    def __init__(self, ini):
        self.ini            = ini
        self._weapon_cache  = {}
        self._warhead_cache = {}

    def _get_verses(self, warhead_id):
        if warhead_id in self._warhead_cache:
            return self._warhead_cache[warhead_id]
        verses_str = self.ini.get(warhead_id, "Verses", "")
        result = parse_verses(verses_str) if verses_str else [1.0] * 11
        self._warhead_cache[warhead_id] = result
        return result

    def analyze_weapon(self, weapon_id):
        if weapon_id in self._weapon_cache:
            return self._weapon_cache[weapon_id]
        sec = self.ini.section(weapon_id)
        if not sec:
            result = self._null_weapon()
            self._weapon_cache[weapon_id] = result
            return result

        try:    damage = float(sec.get("damage", "0"))
        except: damage = 0.0
        try:    rof = float(sec.get("rof", "0"))
        except: rof = 0.0

        warhead_id     = sec.get("warhead",    "")
        projectile_id  = sec.get("projectile", "")
        is_spawner_wpn = sec.get("spawner", "no").lower() == "yes"

        proj_sec = self.ini.section(projectile_id) if projectile_id else {}
        can_aa   = proj_sec.get("aa",  "no").lower()  == "yes"
        can_ag   = proj_sec.get("ag",  "yes").lower() != "no"
        can_an   = False  # stub
        can_sub  = False  # stub

        # NeverUse=yes weapons are never fired in combat (scanner/detection weapons).
        # Mark them as non-combat so they don't grant AA or AG roles.
        if is_neveruse_weapon(weapon_id, self.ini):
            can_aa = False
            can_ag = False

        verses    = self._get_verses(warhead_id) if warhead_id else [1.0] * 11
        fake      = is_fake_warhead(verses, warhead_id, self.ini, is_spawner_wpn)
        is_combat = damage > 0 and bool(warhead_id) and not fake and \
                    not is_neveruse_weapon(weapon_id, self.ini)

        result = {
            "damage": damage, "rof": rof, "warhead": warhead_id,
            "verses": verses, "can_aa": can_aa, "can_ag": can_ag,
            "can_an": can_an, "can_sub": can_sub,
            "is_spawner_wpn": is_spawner_wpn, "is_combat": is_combat,
        }
        self._weapon_cache[weapon_id] = result
        return result

    def analyze_weapon_from_parts(self, damage: float, warhead_id: str,
                                   rof: float = 50.0,
                                   weapon_range: float = 10.0) -> dict:
        """
        Build a weapon analysis dict from raw parts (for synthetic weapons
        like vanilla missiles that have no weapon section).
        """
        verses    = self._get_verses(warhead_id) if warhead_id else [0.75] * 11
        fake      = is_fake_warhead(verses, warhead_id, self.ini, False)
        is_combat = damage > 0 and not fake
        return {
            "damage": damage, "rof": rof, "warhead": warhead_id,
            "verses": verses, "can_aa": False, "can_ag": True,
            "can_an": False, "can_sub": False,
            "is_spawner_wpn": False, "is_combat": is_combat,
        }

    @staticmethod
    def _null_weapon():
        return {
            "damage": 0.0, "rof": 0.0, "warhead": "",
            "verses": [0.0] * 11,
            "can_aa": False, "can_ag": False,
            "can_an": False, "can_sub": False,
            "is_spawner_wpn": False, "is_combat": False,
        }

    def assign_roles(self, weapon_analysis):
        wpn = weapon_analysis
        if not wpn["is_combat"]:
            return []
        roles  = set()
        verses = wpn["verses"]
        if wpn["can_aa"]:
            roles.add("AA")
        if wpn["can_ag"]:
            hits_inf = any(verses[i] >= ROLE_THRESHOLD for i in ARMOR_INFANTRY)
            hits_veh = any(verses[i] >= ROLE_THRESHOLD for i in ARMOR_VEHICLE)
            hits_bld = any(verses[i] >= ROLE_THRESHOLD for i in ARMOR_BUILDING)
            ag_roles = []
            if hits_inf: ag_roles.append("AG-Anti_Infantry")
            if hits_veh: ag_roles.append("AG-Anti_Vehicle")
            if hits_bld: ag_roles.append("AG-Anti_Building")
            if len(ag_roles) > 1:
                roles.add("AG-Multi")
                roles.update(ag_roles)
            elif len(ag_roles) == 1:
                roles.add(ag_roles[0])
        return sorted(roles)


# ---------------------------------------------------------------------------
# Unit record
# ---------------------------------------------------------------------------

class UnitRecord:
    __slots__ = (
        "unit_id", "name", "category",
        "tech_level", "prereqs", "prereq_depth",
        "factory_anchor", "factory_type", "relative_depth", "tier",
        "cost", "strength", "armor", "speed", "build_time_multiplier",
        "self_healing",
        "required_houses", "faction", "countries",
        "primary_weapon", "secondary_weapon",
        "primary_analysis", "secondary_analysis", "role_weapon",
        "weapon_range",          # Range= value from primary weapon section
        "is_spawner", "spawns_unit",
        "is_utility",            # True if mind control / non-damage utility
        "roles", "mobility",
        "is_naval_unit", "is_aircraft_type",
        "is_combat", "is_no_combat",
        "cost_weight",
        "build_limit",           # BuildLimit= (0 = unlimited)
        "is_suicide",            # True if primary weapon has Suicide=yes
        "is_transport",          # True if carries passengers but they can't fire out
        "is_open_transport",     # True if OpenTopped=yes (passengers fire from inside)
        "is_naval_transport",    # True if naval/amphibious transport (may have StupidHunt)
        "is_combat_transport",   # True if transport also has real combat capability (Gunner=yes or damage>=25)
        "dps_score",             # raw damage-per-second, weapon damage/(rof/10)*burst
        "is_mc_immune",          # True if ImmuneToPsionics=yes (safe vs mind-control)
        "is_parasite_drone",     # Vehicle with parasite warhead (Terror Drone class)
        "is_parasite",           # True if uses Parasite locomotor (Terror Drone)
        "is_deployfire",         # True if DeployFire=yes (deploys to use secondary weapon)
        "is_submerged",          # True if Cloakable=yes+CloakingSpeed or Underwater=yes
        "has_sensors",           # True if Sensors=yes (can detect/target cloaked units)
        "naval_bombardment_score", # bld_dps × range score for land-bombardment role
        "is_mind_control",       # True if primary weapon has MindControl=yes warhead
        "is_chaos",              # True if primary weapon has CausesChaosDuration= effect
        "is_radiation",          # True if primary weapon has Radiation=yes warhead
        "is_dog",                # True if infantry parasite targeting infantry (Attack Dog)
        "is_temporal",           # True if primary warhead has Temporal=yes (chrono erasure)
        "is_ivan_bomb",          # True if primary weapon places IvanBomb=yes
        "is_locomotor_weapon",    # True if primary warhead IsLocomotor=yes (Magnetron beam)
        "is_powered_unit",        # True if PoweredUnit=yes (needs specific building to operate)
        "powered_by",            # list of building IDs that power this unit (from PowersUnit= scan)
        "is_electric_assault",   # True if any weapon has ElectricAssault=yes warhead
        "is_teleporter",         # True if unit uses ChronoInfantry locomotor
        "requires_stolen_tech",  # str: which stolen tech (allied/soviet/third) or None
        "forbidden_houses",      # frozenset of country names forbidden from building this
        "ifv_weapon_grade",      # float: DPS grade of the IFV weapon when this unit is passenger
        "ifv_weapon_id",         # str: weapon ID the Gunner vehicle uses with this passenger
        "open_transport_weapon", # 0=Primary, 1=Secondary fires from open transport
        "has_c4",                # True if C4=yes (instant building kill on contact)
        "combat_speed_avg",      # clean speed avg of group (transport-excluded)
        "_speed_baseline_viable",# True if unit meets surv/cost threshold for speed group
        # Composite grade (0-6) assigned after all units are parsed
        "grade",                 # int 0-6
        "grade_breakdown",       # dict of metric -> 0 or 1
        "notes",
    )

    def __init__(self):
        self.unit_id   = ""
        self.name      = ""
        self.category  = ""
        self.tech_level = -1
        self.prereqs   = []
        self.prereq_depth   = 0
        self.factory_anchor = None
        self.factory_type   = None
        self.relative_depth = 0
        self.tier      = "Rush"
        self.cost      = 0
        self.strength  = 0
        self.armor     = "none"
        self.speed     = 0
        self.build_time_multiplier = 1.0
        self.self_healing    = False
        self.required_houses = set()
        self.faction   = None
        self.countries = set()
        self.primary_weapon   = ""
        self.secondary_weapon = ""
        self.primary_analysis   = {}
        self.secondary_analysis = {}
        self.role_weapon  = {}
        self.weapon_range = 0.0
        self.is_spawner   = False
        self.spawns_unit  = ""
        self.is_utility   = False
        self.roles    = []
        self.mobility = "Land"
        self.is_naval_unit    = False
        self.is_aircraft_type = False
        self.is_combat    = False
        self.is_no_combat = False
        self.cost_weight  = 1.0
        self.build_limit  = 0
        self.is_suicide        = False
        self.is_transport      = False
        self.is_open_transport = False
        self.is_naval_transport = False
        self.is_combat_transport = False
        self.dps_score           = 0.0
        self.is_mc_immune        = False
        self.is_parasite_drone   = False
        self.is_parasite        = False
        self.is_deployfire      = False
        self.is_submerged       = False
        self.has_sensors              = False
        self.naval_bombardment_score  = 0.0
        self.is_mind_control          = False
        self.is_chaos                 = False
        self.is_radiation             = False
        self.is_dog                   = False
        self.is_temporal              = False
        self.is_ivan_bomb             = False
        self.is_locomotor_weapon      = False
        self.is_powered_unit          = False
        self.powered_by               = []    # building IDs
        self.is_electric_assault      = False
        self.is_teleporter            = False
        self.requires_stolen_tech     = None   # None | "allied" | "soviet" | "third"
        self.forbidden_houses         = frozenset()
        self.ifv_weapon_grade         = 0.0   # grade of IFV weapon when loaded
        self.ifv_weapon_id            = ""    # weapon ID the Gunner uses for this infantry
        self.open_transport_weapon = None
        self.has_c4                 = False
        self.combat_speed_avg       = 0.0
        self._speed_baseline_viable = True
        self.grade            = 0
        self.grade_breakdown  = {}
        self.notes = []

    def __repr__(self):
        return (
            f"<Unit {self.unit_id} | {self.name} | {self.faction} | "
            f"Tier={self.tier}(d={self.relative_depth}) | "
            f"Mob={self.mobility} | Cost={self.cost} | Roles={self.roles}>"
        )

    @staticmethod
    def relative_depth_to_tier(prereq_depth):
        """
        Assign tier based on absolute prerequisite depth.
        Uses the adaptive TIER_DEPTH_CEILING computed from the mod's
        actual radar and lab building depths.
          Early   = below radar tech
          Mid     = radar required, no lab
          Late    = lab required
          EndGame = beyond lab (mod support)
        """
        if prereq_depth <= TIER_DEPTH_CEILING["Early"]:
            return "Early"
        elif prereq_depth <= TIER_DEPTH_CEILING["Mid"]:
            return "Mid"
        elif prereq_depth <= TIER_DEPTH_CEILING["Late"]:
            return "Late"
        return "EndGame"

    @staticmethod
    def tier_from_prereq_chain(unit_id: str, ini,
                                radar_ids: set, lab_ids: set) -> str:
        """
        Assign tier by checking whether the unit's full prerequisite chain
        contains any radar or lab buildings. More accurate than depth proxy.
          Early   = no radar in chain
          Mid     = radar in chain, no lab
          Late    = lab in chain
          EndGame = beyond lab (reserved for mod support via depth)
        """
        def _chain(bid, visited=None):
            if visited is None: visited = set()
            if bid in visited: return set()
            visited.add(bid)
            sec = ini.section(bid)
            if not sec: return set()
            prereq_str = sec.get('prerequisite', '')
            prereqs    = {p.strip().split(';')[0].strip()
                          for p in prereq_str.split(',') if p.strip()}
            result = set(prereqs)
            for p in prereqs:
                result |= _chain(p, visited)
            return result

        chain = _chain(unit_id)
        if chain & lab_ids:
            return "Late"
        if chain & radar_ids:
            return "Mid"
        return "Early"


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

class RulesMdParser:
    def __init__(self, rules_path):
        self.rules_path = Path(rules_path)
        self.ini        = IniParser(self.rules_path)

        self.units               = {}
        self.buildable           = []
        self.no_combat_units     = []
        self.stolen_tech_units   = []
        self.naval_transports    = []   # naval/amphibious transports
        self.parasite_units      = []   # parasite locomotor units (Terror Drone etc.)
        self.naval_bombarders    = []   # naval units with high land bombardment score
        self.uncategorized_units = []
        self.spawner_units       = []

        self.pools             = defaultdict(list)
        self.faction_pools     = defaultdict(list)
        self.faction_baselines = {}
        self.relaxed_stray     = 3.0

        self.cy_armors           = set()   # all CY armor indices in mod
        self.cy_armor_by_bid     = {}      # bid -> armor index
        self.cy_armor_by_faction = {}      # faction -> armor index
        self.service_depots      = []      # UnitRepair=yes buildings (remove parasites)
        self.grinders            = []      # Grinding=yes buildings (also removes parasites)
        self.armor_eff_mult      = {}      # armor_name -> eff HP multiplier
        self.powered_unit_map    = {}      # unit_id -> [building_ids that power it]
        self.overpowerable_blds  = []      # building IDs with Overpowerable=true
        self.ifv_weapon_table    = {}      # IFVMode -> (weapon_id, dps_score) for primary gunner

        self.aliases          = None
        self.sides            = None
        self.depth_resolver   = None
        self.factory_registry = None
        self.analyzer         = None

    def parse(self):
        self._read_general()
        # Order matters: sides -> aliases -> depth_resolver -> factory -> analyzer
        self.sides            = SidesRegistry(self.ini)
        self.aliases          = AliasResolver(self.ini)
        self.depth_resolver   = DepthResolver(self.ini, self.aliases)
        self.factory_registry = FactoryRegistry(
            self.ini, self.depth_resolver, self.aliases, self.sides)
        self.analyzer         = WeaponAnalyzer(self.ini)

        # Compute adaptive tier depth ceilings from actual mod structure.
        # Updates the global TIER_DEPTH_CEILING so all downstream code
        # (taskforce generator, script generator, etc.) uses mod-correct values.
        from . import rulesmd_parser as _self_module
        computed = compute_tier_ceilings(self.ini, self.sides)
        _self_module.TIER_DEPTH_CEILING.update(computed)
        self.tier_ceilings = dict(computed)

        # Also store radar/lab ids for chain-based tier assignment.
        # Include both concrete building IDs and their [General] alias names
        # (RADAR, TECH, etc.) since prereqs often use the alias form.
        ai  = self.ini.section('AI')
        gen = self.ini.section('General')
        def _ids(*keys):
            result = set()
            for k in keys:
                for src in [ai, gen]:
                    for x in src.get(k, '').split(','):
                        x = x.strip().split(';')[0].strip()
                        if x: result.add(x)
            return result
        self._radar_ids = _ids('buildradar', 'prerequisiteradar')
        self._lab_ids   = _ids('buildtech',  'prerequisitetech')
        # Add alias keywords used in Prerequisite= fields
        # (RADAR expands to PrerequisiteRadar, TECH to PrerequisiteTech, etc.)
        self._radar_ids.add('RADAR')
        self._lab_ids.add('TECH')

        self._collect_unit_ids()
        self._resolve_all_units()
        self._build_pools()
        self._compute_faction_baselines()
        self._compute_unit_grades()
        self._classify_dps_rank()
        # Build naval_bombarders after grades are computed
        _min_bombard = 10.0
        self.naval_bombarders = sorted(
            [r for r in self.buildable
             if r.is_naval_unit and r.naval_bombardment_score >= _min_bombard],
            key=lambda r: -r.naval_bombardment_score
        )

        # Build powered_unit_map: scan buildings for PowersUnit= entries
        # and populate each powered unit's powered_by list.
        for bid in self.ini.list_from_section('BuildingTypes'):
            bsec = self.ini.section(bid)
            for key in ('powersunit', 'powersunit2'):
                uid = bsec.get(key, '').strip()
                if uid:
                    if uid not in self.powered_unit_map:
                        self.powered_unit_map[uid] = []
                    if bid not in self.powered_unit_map[uid]:
                        self.powered_unit_map[uid].append(bid)
                    # Also update the unit record itself
                    rec = self.units.get(uid)
                    if rec and bid not in rec.powered_by:
                        rec.powered_by.append(bid)

        # Build overpowerable_blds: buildings with Overpowerable=true
        self.overpowerable_blds = [
            bid for bid in self.ini.list_from_section('BuildingTypes')
            if self.ini.section(bid).get('overpowerable', 'no').lower() == 'true'
        ]

        # Build IFV weapon grades for all infantry with IFVMode=
        self._build_ifv_weapon_map()
        return self

    def add_ai_units(self, unit_ids: list[str], faction_hints: dict[str, str] | None = None):
        """
        Add AI-only units (e.g. TechLevel=-1 scanner finds) to the buildable
        pool and recompute grades for all units.

        unit_ids:      list of unit IDs to add (must exist in INI)
        faction_hints: optional {uid: faction} override for faction detection
                       (used when prereq chain is ambiguous, e.g. BARRACKS)

        Stolen tech units (RequiresStolenAlliedTech etc.) are included here.
        They get the faction of the tech they require:
          RequiresStolenAlliedTech → gdi
          RequiresStolenSovietTech → nod
          RequiresStolenThirdTech  → thirdside
        """
        STOLEN_FACTION = {
            'requiresstolenalliedtech': 'gdi',
            'requiresstolensoviettech': 'nod',
            'requiresstolenthirdtech':  'thirdside',
        }

        added = []
        for uid in unit_ids:
            if uid in self.units:
                # Already parsed — just ensure it's in buildable
                rec = self.units[uid]
                if rec not in self.buildable:
                    self.buildable.append(rec)
                    added.append(rec)
                # Still apply faction/stolen-tech overrides since the main
                # parse may have missed them (BARRACKS prereq → ambiguous faction)
            else:
                # Parse the unit fresh
                sec = self.ini.section(uid)
                if not sec:
                    continue
                # Determine which type list contains this unit.
                # Must scan ALL keys (not just numeric) since mods use
                # WP#, AIWP#, WPR# etc. which list_from_section() skips.
                source = None
                for section in ('InfantryTypes', 'VehicleTypes', 'AircraftTypes'):
                    sec_data = self.ini.section(section) or {}
                    if any(v.strip() == uid for v in sec_data.values() if v):
                        source = section.replace('Types', '')
                        break
                if source is None:
                    continue
                rec = self._parse_unit(uid, source)
                if rec is None:
                    continue
                self.units[uid] = rec
                self.buildable.append(rec)
                added.append(rec)

            # Apply faction overrides (for both new and pre-existing units)
            rec = self.units[uid]
            sec = self.ini.section(uid)
            if faction_hints and uid in faction_hints:
                rec.faction = faction_hints[uid]

            # Stolen tech units are faction-NEUTRAL — any faction can unlock
            # them by sending a spy into the appropriate Battle Lab.
            # They get waves generated for all factions via a special pool.
            # Leave faction as None; the wave generator handles them specially.
            if rec.requires_stolen_tech:
                rec.faction = None   # faction-neutral
            elif not rec.faction:
                prereq_raw = sec.get('prerequisite', '') if sec else ''
                if prereq_raw:
                    rec.faction = self.sides.faction_from_prereq_chain(prereq_raw)

        if added:
            # Recompute baselines and grades for ALL units (new units affect averages)
            self._compute_faction_baselines()
            self._compute_unit_grades()
            # Rebuild naval bombarders
            _min_bombard = 10.0
            self.naval_bombarders = sorted(
                [r for r in self.buildable
                 if r.is_naval_unit and r.naval_bombardment_score >= _min_bombard],
                key=lambda r: -r.naval_bombardment_score
            )

        return added
        try:
            self.relaxed_stray = float(
                self.ini.get("General", "RelaxedStray", "3.0"))
        except ValueError:
            self.relaxed_stray = 3.0

    def _classify_dps_rank(self):
        """Compute raw DPS for every buildable unit and tag the top and
        bottom quartiles with HIGH_DPS / LOW_DPS role markers.

        Raw DPS ignores armor coefficients — it's the weapon's damage per
        second against a neutral target. Used to rank units by damage
        output alone, so combat-viable transports get flagged separately
        from pure taxi transports (SHAD-class), and modders get a HIGH_DPS
        marker in the overview showing the roster's top damage dealers.
        """
        buildable = list(self.buildable)
        if not buildable:
            return

        dps_by_unit: dict[str, float] = {}
        for rec in buildable:
            primary = getattr(rec, 'primary_weapon', None) or ''
            if not primary:
                sec = self.ini.section(rec.unit_id)
                if sec:
                    primary = sec.get('primary', '').strip()
            if not primary:
                dps_by_unit[rec.unit_id] = 0.0
                continue
            wsec = self.ini.section(primary)
            if not wsec:
                dps_by_unit[rec.unit_id] = 0.0
                continue
            try:
                dmg = int(wsec.get('damage', '0') or '0')
                rof = int(wsec.get('rof', '10') or '10')
            except ValueError:
                dmg, rof = 0, 10
            burst = 1
            try:
                burst = int(wsec.get('burst', '1') or '1')
            except ValueError:
                burst = 1
            dps = ((dmg * burst) / (rof / 10.0)) if rof > 0 else 0.0
            dps_by_unit[rec.unit_id] = dps

        # Compute distribution thresholds — top and bottom quartiles get tagged
        values = sorted(v for v in dps_by_unit.values() if v > 0)
        if len(values) < 4:
            for rec in buildable:
                rec.dps_score = dps_by_unit.get(rec.unit_id, 0.0)
            return
        q_high = values[int(len(values) * 0.75)]
        q_low  = values[int(len(values) * 0.25)]

        for rec in buildable:
            dps = dps_by_unit.get(rec.unit_id, 0.0)
            rec.dps_score = dps
            if dps <= 0:
                continue
            if dps >= q_high and 'HIGH_DPS' not in rec.roles:
                rec.roles = rec.roles + ['HIGH_DPS']
            elif dps <= q_low and 'LOW_DPS' not in rec.roles:
                rec.roles = rec.roles + ['LOW_DPS']

    def _build_ifv_weapon_map(self):
        """
        For every Gunner=yes vehicle, build a table of:
            IFVMode → (weapon_id, dps_score)
        Then for every infantry unit with IFVMode=N, assign:
            rec.ifv_weapon_id    = weapon that Gunner fires with this passenger
            rec.ifv_weapon_grade = DPS-like score for that weapon

        IFVMode=N on infantry → Weapon(N+1)= on the Gunner vehicle.
        Only the FIRST passenger's IFVMode matters — it defines the weapon.

        Score = raw_dps adjusted for:
          - AA weapons get ×1.5 (air targeting is rare and valuable)
          - Mind control weapons: effective DPS = 600 (removes enemy unit)
          - Radiation weapons: +bonus like other radiation units
          - Repair weapons (negative DPS): score = 0 (useless in attack waves)
          - Default weapon (IFVMode=0): score = base weapon DPS
        """
        # Find all Gunner=yes vehicles
        gunners = {}
        for uid in self.ini.list_from_section('VehicleTypes'):
            sec = self.ini.section(uid)
            if sec.get('gunner', 'no').lower() != 'yes':
                continue
            try:
                weapon_count = int(sec.get('weaponcount', '1') or '1')
            except ValueError:
                weapon_count = 1

            # Build IFVMode → weapon_id table for this vehicle
            weapon_table = {}
            for n in range(1, weapon_count + 1):
                wpn_id = sec.get(f'weapon{n}', '')
                if not wpn_id:
                    continue
                wpn = self.ini.section(wpn_id)
                if not wpn:
                    continue
                try:
                    dmg = float(wpn.get('damage', 0) or 0)
                    rof = float(wpn.get('rof', 30) or 30)
                except ValueError:
                    dmg, rof = 0.0, 30.0

                rng    = float(wpn.get('range', 5) or 5)
                aa     = wpn.get('aa', 'no').lower() == 'yes'
                wh_id  = wpn.get('warhead', '')
                wh     = self.ini.section(wh_id) if wh_id else {}
                mc     = wh.get('mindcontrol', 'no').lower() == 'yes' if wh else False
                rad    = wh.get('radiation', 'no').lower() == 'yes' if wh else False
                psychic = wh.get('psychicdamage', 'no').lower() == 'yes' if wh else False

                dps = (dmg / rof * 30) if rof > 0 else 0.0
                if dps < 0:
                    score = 0.0   # repair weapon — useless in attack wave
                elif mc:
                    # True MindControl=yes warhead — permanently captures unit
                    score = 600.0
                elif psychic:
                    # PsychicDamage=yes — AOE psi blast, only hits infantry,
                    # range typically 1. Misleading weapon names (e.g. CRMindControl
                    # on Yuri Clone IFV is actually a psi blast, not mind control).
                    # Score purely on DPS vs infantry since that's all it hits.
                    score = dps * 0.5  # penalise for range=1 and inf-only
                elif rad:
                    score = dps + 8.0  # radiation bonus
                elif aa:
                    score = dps * 1.5  # AA is rare and valuable
                else:
                    score = dps

                ifvmode = n - 1  # IFVMode is 0-indexed, WeaponN is 1-indexed
                weapon_table[ifvmode] = (wpn_id, score)

            gunners[uid] = weapon_table

        if not gunners:
            return

        # For simplicity, use the first Gunner vehicle found as the reference
        # (in vanilla there's only FV; mods may have more)
        primary_table = next(iter(gunners.values()))

        # Assign ifv_weapon_grade to each infantry unit
        for rec in self.buildable:
            if rec.category != 'Infantry':
                continue
            sec = self.ini.section(rec.unit_id)
            try:
                ifvmode = int(sec.get('ifvmode', '') or -1)
            except ValueError:
                ifvmode = -1
            if ifvmode < 0:
                continue

            # Look up in all gunner tables (may differ per vehicle, use best)
            best_score = 0.0
            best_wpn   = ''
            for table in gunners.values():
                wpn_id, score = table.get(ifvmode, ('', 0.0))
                if score > best_score:
                    best_score = score
                    best_wpn   = wpn_id

            rec.ifv_weapon_grade = best_score
            rec.ifv_weapon_id    = best_wpn

        # Store the full table for wave generator use
        self.ifv_weapon_table = primary_table  # {ifvmode: (wpn_id, score)}

    def _read_general(self):
        try:
            self.relaxed_stray = float(
                self.ini.get("General", "RelaxedStray", "3.0"))
        except ValueError:
            self.relaxed_stray = 3.0

        # Alliance building list
        try:
            alliance_str = self.ini.get("General", "AlliedBuilding", "")
            self.alliance_buildings = [b.strip() for b in alliance_str.split(',')
                                       if b.strip()]
        except Exception:
            self.alliance_buildings = []

    def _collect_unit_ids(self):
        self._all_unit_ids = {}
        for section in UNIT_CATEGORIES:
            category = section.replace("Types", "")
            for uid in self.ini.list_from_section(section):
                if uid and not uid.startswith(";"):
                    self._all_unit_ids[uid] = category

    def _resolve_all_units(self):
        for uid, category in self._all_unit_ids.items():
            rec = self._parse_unit(uid, category)
            self.units[uid] = rec

            if rec.is_no_combat:
                # Capture parasite units even if StupidHunt=yes —
                # they need special wave handling separate from buildable
                if rec.is_parasite and rec.faction is not None:
                    self.parasite_units.append(rec)
                # Also capture naval transports (already handled above but keep)
                if rec.is_naval_transport and rec.faction is not None:
                    self.naval_transports.append(rec)
                self.no_combat_units.append(rec)
                continue

            sec = self.ini.section(uid)
            if any(sec.get(f, "no").lower() == "yes" for f in STOLEN_TECH_FLAGS):
                rec.notes.append("Excluded: requires stolen tech (future feature)")
                self.stolen_tech_units.append(rec)
                continue

            if not (1 <= rec.tech_level <= 10):
                rec.notes.append(f"Excluded: TechLevel={rec.tech_level}")
                continue

            if rec.faction is None:
                rec.notes.append(
                    "Excluded: no construction yard found in prereq chain")
                self.uncategorized_units.append(rec)
                continue

            if rec.mobility in SPECIAL_EXCLUDED:
                rec.notes.append(f"Excluded: mobility={rec.mobility}")
                continue

            self.buildable.append(rec)
            if rec.is_spawner:
                self.spawner_units.append(rec)
            if rec.is_parasite:
                self.parasite_units.append(rec)

    def _analyze_spawner_weapon(self, spawns_id: str,
                                  launcher_wpn_id: str) -> dict | None:
        """
        For spawner units (V3, DRED, BSUB, Carrier, Destroyer):
        Return a synthetic weapon analysis dict representing what the
        spawned unit/missile does, so the spawner inherits the correct role.

        Two cases:
        1. MISSILE (MissileSpawn=yes or missile locomotor):
           - Vanilla: use [General] V3RocketDamage/DMislDamage/CMislDamage
             with a synthetic anti-building warhead profile (missiles lob
             large explosions — they're pure ARTY/anti-building).
           - Ares custom: use Missile.Warhead= and Missile.Damage=
        2. AIRCRAFT (regular spawned plane/helicopter):
           - Read spawned unit's Primary= weapon and analyze normally.
        """
        if not spawns_id or spawns_id.lower() == 'yes':
            return None

        sec = self.ini.section(spawns_id)
        if not sec:
            return None

        MISSILE_LOCO = 'B7B49766-E576-11D3-9BD9-00104B972FE8'
        loco         = sec.get('locomotor', '').split(';')[0].strip().strip('{}').upper()
        is_missile   = (sec.get('missilespawn', 'no').lower() == 'yes'
                        or loco == MISSILE_LOCO)

        if is_missile:
            # Check for Ares custom missile warhead first
            launcher_sec = self.ini.section(spawns_id) or {}
            # Actually check on the SPAWNER unit, not the missile
            parent_sec   = {}  # will be set below via parent lookup
            # For vanilla: identify missile type via [General] type tags
            gen          = self.ini.section('General')
            missile_type = None
            for key in ('v3rockettype', 'dmisltype', 'cmisltype'):
                if gen.get(key, '').upper() == spawns_id.upper():
                    missile_type = key.replace('type', '')
                    break

            # Try Ares Missile.Custom on the spawner unit itself
            # (we don't have a direct back-reference, so check launcher weapon)
            launcher_wpn_sec = self.ini.section(launcher_wpn_id) or {}
            custom_warhead   = launcher_wpn_sec.get('missile.warhead', '')
            custom_damage_s  = launcher_wpn_sec.get('missile.damage', '')

            if custom_warhead and custom_damage_s:
                try:
                    dmg = float(custom_damage_s)
                except ValueError:
                    dmg = 100.0
                return self.analyzer.analyze_weapon_from_parts(
                    damage=dmg, warhead_id=custom_warhead,
                    rof=50.0, weapon_range=10.0)

            # Vanilla missiles: use warheads from [CombatDamage] section.
            # These are the actual warheads used in-game, not synthetic values.
            cd = self.ini.section('CombatDamage')
            warhead_map = {
                'v3rocket': cd.get('v3warhead',   ''),
                'dmisl':    cd.get('dmislwarhead', ''),
                'cmisl':    cd.get('cmislwarhead', ''),
            }
            damage_map = {
                'v3rocket': float(cd.get('v3rocketdamage', '') or
                                  self.ini.get('General','V3RocketDamage','200')),
                'dmisl':    float(cd.get('dmisldamage',    '') or
                                  self.ini.get('General','DMislDamage','300')),
                'cmisl':    float(cd.get('cmisldamage',    '') or
                                  self.ini.get('General','CMislDamage','200')),
            }
            warhead_id = warhead_map.get(missile_type, '')
            dmg        = damage_map.get(missile_type, 200.0) if missile_type else 200.0

            if warhead_id:
                return self.analyzer.analyze_weapon_from_parts(
                    damage=dmg, warhead_id=warhead_id,
                    rof=50.0, weapon_range=10.0)

            # Last resort: synthetic anti-building profile
            synthetic_verses = [
                0.75, 0.75, 0.75,  # infantry armor types
                0.75, 0.50, 0.50,  # vehicle armor types
                1.00, 0.75, 0.50,  # building armor types
                0.00, 0.00,        # air
            ]
            return {
                'damage': dmg, 'rof': 50.0,
                'warhead': f'[synthetic_missile:{spawns_id}]',
                'verses': synthetic_verses,
                'can_aa': False, 'can_ag': True,
                'can_an': False, 'can_sub': False,
                'is_spawner_wpn': False, 'is_combat': True,
            }

        else:
            # Regular aircraft spawn — read spawned unit's primary weapon
            spawn_pri_id = sec.get('primary', '')
            if not spawn_pri_id:
                return None
            return self.analyzer.analyze_weapon(spawn_pri_id)

    def _parse_unit(self, uid, category):
        rec         = UnitRecord()
        rec.unit_id  = uid
        rec.category = category

        sec = self.ini.section(uid)
        if not sec:
            rec.notes.append("No INI section found")
            rec.is_no_combat = True
            return rec

        rec.name = sec.get("name", uid)

        # Detect naval transport status early — before NON_COMBAT_FLAGS check
        # so StupidHunt=yes naval transports are still detectable
        try:
            _passengers_early = int(sec.get("passengers", "0"))
        except ValueError:
            _passengers_early = 0
        _naval_early = sec.get("naval", "no").lower() == "yes"
        if _passengers_early > 0 and _naval_early:
            rec.is_naval_unit    = True
            rec.is_naval_transport = True
            # Detect faction via naval yard prereq chain
            _raw_prereq = sec.get("prerequisite", "")
            _prereq_ids = [p.strip() for p in _raw_prereq.split(",")
                           if p.strip() and not p.strip().startswith(";")]
            for _pid in _prereq_ids:
                _pid_sec = self.ini.section(_pid)
                if not _pid_sec: continue
                if (_pid_sec.get("factory","").lower() == "unittype"
                        and _pid_sec.get("naval","no").lower() == "yes"):
                    _yard_prereq = _pid_sec.get("prerequisite","")
                    _yard_faction = self.sides.faction_from_prereq_chain(
                        _yard_prereq)
                    if _yard_faction:
                        rec.faction = _yard_faction
                        rec.notes.append(
                            f"Faction detected via naval yard {_pid}")
                        break

        # Detect parasite unit status early — before NON_COMBAT_FLAGS check
        # so StupidHunt=yes parasite units (DRON) are still detectable.
        # Detection: primary weapon warhead has Parasite=yes.
        _pri_early   = sec.get("primary", "")
        _pri_sec_e   = self.ini.section(_pri_early) if _pri_early else {}
        _pri_wh_e    = self.ini.section(_pri_sec_e.get("warhead","")) if _pri_sec_e else {}
        if _pri_wh_e and _pri_wh_e.get("parasite","no").lower() == "yes":
            rec.is_parasite = True
            # Detect faction via prereq chain for parasite units
            _raw_prereq = sec.get("prerequisite","")
            if _raw_prereq and not rec.faction:
                _pf = self.sides.faction_from_prereq_chain(_raw_prereq)
                if _pf:
                    rec.faction = _pf

        for flag in NON_COMBAT_FLAGS:
            if sec.get(flag, "no").lower() == "yes":
                rec.is_no_combat = True
                rec.notes.append(f"Non-combat flag: {flag}=yes")
                return rec

        if category == "Building":
            rec.is_no_combat = True
            return rec

        try:    rec.tech_level = int(sec.get("techlevel", "-1"))
        except: rec.tech_level = -1

        try:    rec.cost = int(sec.get("cost", "0"))
        except: rec.cost = 0
        rec.cost_weight = float(rec.cost) if rec.cost > 0 else 1.0

        try:    rec.strength = int(sec.get("strength", "0"))
        except: rec.strength = 0

        rec.armor = sec.get("armor", "none").lower()

        try:    rec.speed = int(sec.get("speed", "0"))
        except: rec.speed = 0

        try:
            rec.build_time_multiplier = float(
                sec.get("buildtimemultiplier", "1.0"))
        except:
            rec.build_time_multiplier = 1.0

        req_houses_str = sec.get("requiredhouses", "")
        if req_houses_str:
            rec.required_houses = {
                h.strip() for h in req_houses_str.split(",") if h.strip()}
            rec.countries = set(rec.required_houses)

        # Step 1a: Detect faction via concrete-only BFS (no alias expansion)
        raw_prereq_str = sec.get("prerequisite", "")
        rec.faction    = self.sides.faction_from_prereq_chain(raw_prereq_str)

        if rec.faction:
            # Filter countries to faction members if both are known
            if rec.required_houses:
                faction_countries = set(
                    self.sides.faction_countries.get(rec.faction, []))
                filtered = rec.required_houses & faction_countries
                rec.countries = filtered if filtered else rec.required_houses
            else:
                # No RequiredHouses — derive countries from Owner - ForbiddenHouses
                # intersected with the detected faction's countries
                owner_raw = sec.get("owner", "")
                fh_raw    = sec.get("forbiddenhouses", "")
                owners    = {o.strip() for o in owner_raw.split(",") if o.strip()}
                forbidden = {f.strip() for f in fh_raw.split(",") if f.strip()}
                allowed   = owners - forbidden
                faction_countries = set(
                    self.sides.faction_countries.get(rec.faction, []))
                rec.countries = allowed & faction_countries
        else:
            # Step 1b: Fallback — use ForbiddenHouses / RequiredHouses / Dock=
            fallback_faction, allowed_countries = \
                self.sides.faction_from_forbidden_houses(
                    uid, category,
                    self.factory_registry.aircraft_factories)
            rec.faction   = fallback_faction
            rec.countries = allowed_countries
            if rec.faction:
                rec.notes.append(
                    "Faction detected via ForbiddenHouses/Dock fallback")

        # Step 1c: Naval unit fallback — detect faction from naval yard prereq
        # Naval units require a naval yard (GAYARD/NAYARD/YAYARD), not a CY.
        # Trace the naval yard's own prereq chain to find the CY and faction.
        if not rec.faction and sec.get("naval", "no").lower() == "yes":
            prereq_ids = [p.strip() for p in raw_prereq_str.split(",")
                          if p.strip() and not p.strip().startswith(";")]
            for pid in prereq_ids:
                pid_sec = self.ini.section(pid)
                if not pid_sec:
                    continue
                # Check if this prereq is a naval factory (Factory=UnitType + Naval=yes)
                is_naval_fac = (pid_sec.get("factory","").lower() == "unittype"
                                and pid_sec.get("naval","no").lower() == "yes")
                if is_naval_fac:
                    yard_prereq = pid_sec.get("prerequisite","")
                    yard_faction = self.sides.faction_from_prereq_chain(yard_prereq)
                    if yard_faction:
                        rec.faction = yard_faction
                        rec.notes.append(
                            f"Faction detected via naval yard {pid} prereq chain")
                        break

        # Step 2: Compute depth with faction-aware alias filtering
        rec.prereq_depth = self.depth_resolver.unit_depth(
            uid, rec.faction, self.sides)

        # Mobility
        rec.is_aircraft_type = (category == "Aircraft")
        rec.is_naval_unit    = sec.get("naval", "no").lower() == "yes"

        # Strip braces AND any inline comment (e.g. "{GUID} ;jumpjet")
        locomotor_raw = sec.get("locomotor", "")
        locomotor = locomotor_raw.split(';')[0].strip().strip('{}').upper()
        movement_zone = sec.get("movementzone", "").lower()

        rec.mobility = "Land"
        for guid, mob in LOCOMOTOR_MOBILITY.items():
            if guid.strip('{}').upper() == locomotor:
                rec.mobility = mob
                break
        else:
            if movement_zone in MOVEMENTZONE_MOBILITY:
                rec.mobility = MOVEMENTZONE_MOBILITY[movement_zone]
            elif category == "Aircraft":
                rec.mobility = "Aircraft"

        # Factory anchor and tier (faction-aware)
        factory = self.factory_registry.find_factory_for_unit(
            uid, category, rec.is_naval_unit, rec.faction)
        rec.factory_anchor = factory
        rec.factory_type   = (self.factory_registry.factory_type.get(factory)
                               if factory and factory in self.factory_registry.factory_type
                               else None)

        if factory:
            rec.relative_depth = self.factory_registry.relative_depth(
                uid, factory, rec.faction, self.sides)
        else:
            rec.relative_depth = rec.prereq_depth
            if category != "Building":
                rec.notes.append(
                    "Warning: no factory anchor found — using full prereq "
                    "depth for tier. Check prerequisites.")

        # Tier: use prereq chain membership check for accuracy.
        # Chain check is more reliable than depth since it directly detects
        # whether radar/lab buildings are in the unit's prerequisites.
        radar_ids = getattr(self, '_radar_ids', set())
        lab_ids   = getattr(self, '_lab_ids',   set())
        if radar_ids and lab_ids:
            rec.tier = UnitRecord.tier_from_prereq_chain(
                uid, self.ini, radar_ids, lab_ids)
        else:
            rec.tier = UnitRecord.relative_depth_to_tier(rec.prereq_depth)

        # Build limit (0 = unlimited, 1+ = hard cap)
        try:
            rec.build_limit = int(sec.get("buildlimit", "0"))
        except ValueError:
            rec.build_limit = 0

        # Suicide weapon detection
        primary_id = sec.get("primary", "") or sec.get("weapon1", "")
        if primary_id:
            wpn_sec = self.ini.section(primary_id)
            rec.is_suicide = wpn_sec.get("suicide", "no").lower() == "yes"

        # Transport detection
        try:
            passengers = int(sec.get("passengers", "0"))
        except ValueError:
            passengers = 0
        opentopped = sec.get("opentopped", "no").lower() == "yes"
        is_naval   = sec.get("naval", "no").lower() == "yes"
        gunner     = sec.get("gunner", "no").lower() == "yes"
        rec.is_transport       = passengers > 0 and not opentopped
        rec.is_open_transport  = passengers > 0 and opentopped
        rec.is_naval_transport = passengers > 0 and is_naval  # may have StupidHunt

        # Combat-viable transports: transports that FIGHT while carrying.
        #  - Gunner=yes (FV / IFV) fights via passenger.
        #  - Non-Gunner transports need real primary-weapon DPS, not just
        #    a large damage number. SHAD's BlackHawkCannon is Damage=35 but
        #    ROF=40, so raw DPS = 35/(40/10) = 8.75. A Grizzly at Damage=65
        #    ROF=60 = 10.83. We require DPS >= 10 (roughly matching a
        #    light-tank cannon) so SHAD-class defensive-only cannons fail.
        rec.is_combat_transport = False
        if rec.is_transport:
            if gunner:
                rec.is_combat_transport = True
            elif primary_id:
                pri = self.ini.section(primary_id)
                if pri:
                    try:
                        dmg = int(pri.get('damage', '0') or '0')
                        rof = int(pri.get('rof', '10') or '10')
                    except ValueError:
                        dmg, rof = 0, 10
                    raw_dps = (dmg / (rof / 10.0)) if rof > 0 else 0.0
                    if raw_dps >= 10.0:
                        rec.is_combat_transport = True

        # Parasite units — primary weapon warhead has Parasite=yes
        rec.is_parasite = False
        pri_sec = self.ini.section(primary_id) if primary_id else {}
        if pri_sec:
            pri_wh_id = pri_sec.get('warhead', '')
            pri_wh    = self.ini.section(pri_wh_id) if pri_wh_id else {}
            if pri_wh and pri_wh.get('parasite', 'no').lower() == 'yes':
                rec.is_parasite = True

        # Mind-control immunity — units safe from enemy MIND / YAPSYT.
        # Explicit ImmuneToPsionics=yes flag is the primary source. Also
        # includes structures (no MC applies to buildings) and units whose
        # locomotor makes them unmindcontrollable — aircraft and jumpjets
        # can't be mind-controlled by ground MC units in practice.
        rec.is_mc_immune = (
            sec.get('immunetopsionics', 'no').strip().lower() == 'yes'
        )
        # Vehicles with LocomotorClass GUIDs for aircraft/jumpjet are also
        # effectively MC-immune in most mods, but detecting that reliably
        # requires GUID scanning that's outside scope here; we rely on
        # the explicit flag for now.

        # Dog detection: infantry parasite. Dogs share the same ParasiteDog
        # warhead — grade differences between DOG/ADOG come purely from
        # spectrum scoring against faction averages, not weapon differences.
        rec.is_dog = (rec.is_parasite and rec.category == "Infantry")

        # Parasite drone = Vehicle with parasite warhead. Terror Drone is
        # the canonical example. These pair effectively with OpenTopped
        # transports (Flak Track class) — driven to enemy vehicles as
        # portable anti-tank swarms. Community strategy guides consistently
        # cite this composition as a strong AI pattern.
        rec.is_parasite_drone = (rec.is_parasite and rec.category == "Vehicle")

        # Teleporter locomotor — units that teleport to their target.
        # Primary detection: Teleporter=yes explicit tag.
        # The ChronoInfantry locomotor GUID {4A582747-9839-11d1-B709-00A024DDAFD1}
        # is shared by CLEG/CCOMAND/CIVAN but NOT PTROOP (which uses standard
        # infantry locomotor {4A582744...} and has Teleporter=no).
        CHRONO_INF_LOCO = '{4A582747-9839-11d1-B709-00A024DDAFD1}'
        rec.is_teleporter = (
            sec.get('teleporter', 'no').lower() == 'yes' or
            sec.get('locomotor', '').lower() == CHRONO_INF_LOCO.lower()
        )

        # Temporal warhead (Temporal=yes) — the ChronoBeam erases units
        # from the timeline. Damage accumulates to 10× target HP then vanishes.
        # Detected on primary weapon warhead.
        if pri_sec:
            pri_wh_id = pri_sec.get('warhead', '')
            pri_wh    = self.ini.section(pri_wh_id) if pri_wh_id else {}
            if pri_wh and pri_wh.get('temporal', 'no').lower() == 'yes':
                rec.is_temporal = True

        # Ivan Bomb (IvanBomb=yes) — places a timed bomb on target.
        # When detonated, instantly destroys buildings regardless of HP.
        # Detected on primary weapon warhead.
        if pri_sec:
            pri_wh_id = pri_sec.get('warhead', '')
            pri_wh    = self.ini.section(pri_wh_id) if pri_wh_id else {}
            if pri_wh and pri_wh.get('ivanbomb', 'no').lower() == 'yes':
                rec.is_ivan_bomb = True

        # IsLocomotor warhead (IsLocomotor=yes) — beam lifts/moves the target.
        # Detection: primary OR secondary weapon warhead has IsLocomotor=yes.
        # (secondary_id not yet assigned here — read directly from INI section)
        for _wpn_id in [sec.get('primary',''), sec.get('secondary','')]:
            if not _wpn_id:
                continue
            _wpn = self.ini.section(_wpn_id)
            if not _wpn:
                continue
            _wh_id = _wpn.get('warhead', '')
            _wh    = self.ini.section(_wh_id) if _wh_id else {}
            if _wh and _wh.get('islocomotor', 'no').lower() == 'yes':
                rec.is_locomotor_weapon = True
                break

        # PoweredUnit=yes — unit requires a specific building to operate.
        # The building is identified by scanning [BuildingTypes] for PowersUnit=
        # entries that list this unit. (See also: ROBO requires GAROBO.)
        if sec.get('poweredunit', 'no').lower() == 'yes':
            rec.is_powered_unit = True
            # powered_by is populated later in _build_powered_unit_map()

        # ElectricAssault=yes warhead — unit can overcharge Overpowerable buildings.
        # Check both primary and secondary weapon warheads.
        # Vanilla: SHK (Shock Trooper) secondary uses [ElectricAssault] warhead.
        for _wpn_id in [sec.get('primary',''), sec.get('secondary','')]:
            if not _wpn_id:
                continue
            _wpn = self.ini.section(_wpn_id)
            if not _wpn:
                continue
            _wh_id = _wpn.get('warhead', '')
            _wh    = self.ini.section(_wh_id) if _wh_id else {}
            if _wh and _wh.get('electricassault', 'no').lower() == 'yes':
                rec.is_electric_assault = True
                break

        # Stolen tech requirements
        stolen_map = {
            'requiresstolenalliedtech': 'allied',
            'requiresstolensoviettech': 'soviet',
            'requiresstolenthirdtech':  'third',
        }
        for ini_key, label in stolen_map.items():
            if sec.get(ini_key, 'no').lower() == 'yes':
                rec.requires_stolen_tech = label
                break

        # ForbiddenHouses — countries that cannot build this unit even if
        # they own the building (e.g. captured enemy barracks).
        # This is the authoritative restriction on who can produce the unit.
        fh_raw = sec.get('forbiddenhouses', '')
        rec.forbidden_houses = frozenset(
            h.strip() for h in fh_raw.split(',') if h.strip()
        )

        # DeployFire=yes — unit deploys in place to use secondary weapon.
        # Primary weapon is the mobile weapon (often weak).
        # Secondary weapon is the deployed combat weapon (often powerful).
        rec.is_deployfire = sec.get('deployfire', 'no').lower() == 'yes'

        # OpenTransportWeapon: which weapon an infantry fires from open transport.
        # 0 = Primary, 1 = Secondary. Defaults to -1 which means "decide normally"
        # i.e. fire primary weapon if it exists. We treat absent/-1 as 0 (primary).
        otw_raw = sec.get("opentransportweapon", "")
        if otw_raw.strip().lstrip('-').isdigit():
            otw_val = int(otw_raw.strip())
            # -1 means default (primary), treat as 0
            rec.open_transport_weapon = max(0, otw_val)
        elif sec.get("primary", ""):
            # No tag set but has a primary weapon — defaults to firing primary
            rec.open_transport_weapon = 0

        # Spawner
        spawns_val      = sec.get("spawns", "")
        rec.is_spawner  = bool(spawns_val)
        rec.spawns_unit = spawns_val

        # Self healing
        rec.self_healing = sec.get("selfhealing", "no").lower() == "yes"

        # Weapons
        primary_id, secondary_id = self._resolve_weapons(sec)
        rec.primary_weapon   = primary_id
        rec.secondary_weapon = secondary_id

        pri = (self.analyzer.analyze_weapon(primary_id)
               if primary_id else WeaponAnalyzer._null_weapon())
        sec_wpn = (self.analyzer.analyze_weapon(secondary_id)
                   if secondary_id else WeaponAnalyzer._null_weapon())
        rec.primary_analysis   = pri
        rec.secondary_analysis = sec_wpn

        if rec.is_spawner and pri.get("is_spawner_wpn"):
            role_weapon = sec_wpn
        else:
            role_weapon = pri
        rec.role_weapon = role_weapon

        # Spawner role inheritance: instead of using the launcher weapon
        # (which is a spawner projectile, not a real weapon), inherit the
        # role from the spawned unit's weapon or missile stats.
        if rec.is_spawner:
            spawned_wpn = self._analyze_spawner_weapon(
                rec.spawns_unit, primary_id)
            if spawned_wpn is not None:
                role_weapon     = spawned_wpn
                rec.role_weapon = spawned_wpn

        # Weapon range — read from the role weapon's section
        # For spawner units whose primary is a spawner weapon:
        # if secondary exists use that, otherwise fall back to primary.
        # CARRIER/DRED have no secondary — their range is on the primary weapon.
        if rec.is_spawner and pri.get("is_spawner_wpn") and secondary_id:
            role_wpn_id = secondary_id
        else:
            role_wpn_id = primary_id
        if role_wpn_id:
            try:
                rec.weapon_range = float(
                    self.ini.get(role_wpn_id, "Range", "0"))
            except ValueError:
                rec.weapon_range = 0.0

        # Utility / special weapon detection
        if role_weapon.get("is_combat"):
            warhead_id = role_weapon.get("warhead", "")
            wh_sec = self.ini.section(warhead_id) if warhead_id else {}

            mind_control = (wh_sec.get("mindcontrol", "no").lower() == "yes"
                            if wh_sec else False)
            # Chaos: Psychedelic=yes on warhead (causes units to turn on each other)
            # Note: despite the name, this is the YR chaos effect — confused units
            # attack their own allies. Not a drug reference in game terms.
            is_chaos = (wh_sec.get("psychedelic", "no").lower() == "yes"
                        if wh_sec else False)
            # Radiation: Radiation=yes on warhead (DESO, radiation artillery)
            is_radiation = (wh_sec.get("radiation", "no").lower() == "yes"
                            if wh_sec else False)

            rec.is_mind_control = mind_control
            rec.is_chaos        = is_chaos
            rec.is_radiation    = is_radiation

            # Also check secondary weapon for mind control / chaos / radiation
            # (some units deploy to use their special secondary)
            if secondary_id:
                sec_wh_id = self.ini.get(secondary_id, "Warhead", "")
                sec_wh    = self.ini.section(sec_wh_id) if sec_wh_id else {}
                if sec_wh:
                    if sec_wh.get("mindcontrol","no").lower() == "yes":
                        rec.is_mind_control = True
                    if sec_wh.get("psychedelic","no").lower() == "yes":
                        rec.is_chaos = True
                    if sec_wh.get("radiation","no").lower() == "yes":
                        rec.is_radiation = True

            # Zero damage but has a warhead = utility effect weapon
            zero_damage = role_weapon.get("damage", 0.0) == 0.0
            # Mind control is powerful utility — keep as utility but don't
            # exclude from waves (handled by dedicated mind control wave type)
            rec.is_utility = mind_control or zero_damage
        else:
            rec.is_mind_control = False
            rec.is_chaos        = False
            rec.is_radiation    = False
            rec.is_utility      = False

        if not role_weapon["is_combat"]:
            rec.is_no_combat = True
            rec.notes.append("No-combat: no valid combat weapon found")
        else:
            rec.is_combat = True
            rec.roles = self.analyzer.assign_roles(role_weapon)
            if not rec.roles:
                rec.notes.append("Warning: combat unit with no assigned roles")

        # DeployFire=yes — unit switches to secondary weapon when deployed.
        # Replace both roles AND role_weapon with secondary so DPS grading
        # reflects what the unit actually does in combat (deployed mode).
        # Keep AA tags from primary if secondary doesn't provide them.
        deploy_fire = sec.get("deployfire", "no").lower() == "yes"
        if deploy_fire and secondary_id and sec_wpn.get("is_combat"):
            deploy_roles = self.analyzer.assign_roles(sec_wpn)
            if deploy_roles:
                aa_roles = [r for r in rec.roles if "AA" in r]
                rec.roles      = list(dict.fromkeys(deploy_roles + aa_roles))
                rec.role_weapon = sec_wpn   # grade by deployed weapon
                rec.notes.append(
                    f"DeployFire: roles+weapon replaced by secondary "
                    f"({secondary_id}): {deploy_roles}")

        # C4=yes — unit can instantly destroy any building on contact.
        # This is a dominant anti-building ability, not a normal weapon.
        # Mark the unit so transport payload selection can prioritize C4 units.
        rec.has_c4 = sec.get("c4", "no").lower() == "yes"

        # Submerged unit detection — YR uses cloaking as the underwater mechanic.
        rec.is_submerged = (
            (sec.get("cloakable", "no").lower() == "yes" and
             bool(sec.get("cloakingspeed", ""))) or
            sec.get("underwater", "no").lower() == "yes"
        )

        # Sensor units — Sensors=yes lets a unit detect and target cloaked/submerged.
        rec.has_sensors = sec.get("sensors", "no").lower() == "yes"

        # Naval roles via NavalTargeting and Sensors.
        # NavalTargeting semantics (from ModEnc + source analysis):
        #   0 = UNDERWATER_NEVER: can't attack underwater units
        #   1 = UNDERWATER_SECONDARY: use secondary on underwater
        #   2 = UNDERWATER_ONLY: primary only, underwater targets only
        #   3 = ORGANIC_SECONDARY: secondary on organic/unnatural
        #   4 = SEAL_SPECIAL: primary on organic/hover, secondary for others
        #   5 = NAVAL_ALL: shoot everything with primary
        #   6 = NAVAL_NONE: CANNOT shoot into water (e.g. AEGIS)
        #   7+ = same as 5 (falls through to NAVAL_ALL default)
        # Note: NT=5 and NT=7 are functionally identical per ModEnc source analysis.
        #
        # AN-Surface: NavalTargeting 1/3/4/5/7+ (can engage surface ships)
        # AN-Submerged: Sensors=yes on a naval unit (detects/targets cloaked units)
        try:
            naval_tgt = int(sec.get("navaltargeting", "0"))
        except ValueError:
            naval_tgt = 0
        _is_naval_mob = rec.is_naval_unit or rec.mobility in ('Naval', 'Amphibious')
        if _is_naval_mob:
            # NT=6 = NAVAL_NONE: cannot fire into water → no naval role
            # NT=5/7+ = NAVAL_ALL: shoots everything, but for submerged units
            #   this means they can fire at surface ships while submerged —
            #   still primarily a sub hunter rather than surface combatant.
            # AN-Surface: explicit NT 1/3/4 only (designed to fight surface ships)
            # AN-Submerged: Sensors=yes on naval unit
            _surface_capable = naval_tgt in (1, 3, 4)
            if _surface_capable and "AN-Surface" not in rec.roles:
                rec.roles = rec.roles + ["AN-Surface"]
            if rec.has_sensors and "AN-Submerged" not in rec.roles:
                rec.roles = rec.roles + ["AN-Submerged"]

        return rec

    def _resolve_weapons(self, sec):
        try:    weapon_count = int(sec.get("weaponcount", "0"))
        except: weapon_count = 0
        if weapon_count > 0:
            primary   = sec.get("weapon1", "")
            secondary = sec.get("weapon2", "") if weapon_count > 1 else ""
        else:
            primary   = sec.get("primary",   "")
            secondary = sec.get("secondary", "")
        return primary, secondary

    def _build_pools(self):
        for rec in self.buildable:
            if rec.is_no_combat or rec.faction is None:
                continue
            for role in rec.roles:
                self.pools[role].append(rec)
                self.faction_pools[(rec.faction, role)].append(rec)

    def _compute_faction_baselines(self):
        role_armor_map = {
            "AG-Anti_Infantry": list(ARMOR_INFANTRY),
            "AG-Anti_Vehicle":  list(ARMOR_VEHICLE),
            "AG-Anti_Building": list(ARMOR_BUILDING),
            "AG-Multi": list(ARMOR_INFANTRY | ARMOR_VEHICLE | ARMOR_BUILDING),
            "AA":               [],
            "AN-Surface":       list(ARMOR_VEHICLE),
            "AN-Submerged":     list(ARMOR_VEHICLE),
        }
        for (faction, role), units in self.faction_pools.items():
            tier_groups = defaultdict(list)
            for u in units:
                tier_groups[u.tier].append(u)
            relevant = role_armor_map.get(role, [])
            for tier, tier_units in tier_groups.items():
                armor_dps = defaultdict(list)
                for u in tier_units:
                    wpn = u.role_weapon
                    for ai in relevant:
                        armor_dps[ai].append(
                            effective_dps(
                                wpn["damage"], wpn["rof"], wpn["verses"], ai))
                self.faction_baselines[(faction, role, tier)] = {
                    ai: (sum(vals) / len(vals) if vals else 0.0)
                    for ai, vals in armor_dps.items()
                }

    def _derive_armor_multipliers(self) -> dict[str, float]:
        """
        Derive armor toughness multipliers empirically from all warhead
        Verses= data in rulesmd.ini.

        For each armor class, compute the average Verses percentage across
        all warheads. An armor class with average 50% Verses is twice as
        tough as one with 100% — so multiplier = 1.0 / avg_verses.
        Normalized so that the median armor class = 1.0.

        This approach is mod-accurate and requires no hardcoded values.
        """
        armor_verses: dict[int, list[float]] = defaultdict(list)

        # Scan all warhead sections
        for section_name, sec in self.ini.sections.items():
            verses_str = sec.get("verses", "")
            if not verses_str:
                continue
            verses = parse_verses(verses_str)
            for ai, v in enumerate(verses):
                if v > 0:
                    armor_verses[ai].append(v)

        if not armor_verses:
            # Fallback: all multipliers = 1.0
            return {name: 1.0 for name in ARMOR_NAMES}

        # Compute average verses per armor class
        avg_verses: dict[int, float] = {}
        for ai in range(11):
            vals = armor_verses.get(ai, [])
            avg_verses[ai] = sum(vals) / len(vals) if vals else 1.0

        # Multiplier = 1 / avg_verses (lower avg = tougher = higher multiplier)
        raw_mults = {ai: 1.0 / max(avg_verses[ai], 0.01) for ai in range(11)}

        # Normalize: median multiplier = 1.0
        sorted_mults = sorted(raw_mults.values())
        median = sorted_mults[len(sorted_mults) // 2]
        if median == 0:
            median = 1.0

        return {
            ARMOR_NAMES[ai]: raw_mults[ai] / median
            for ai in range(11)
        }

    def _weapon_hits_ground(self, weapon):
        """Whether this weapon can engage a GROUND target.

        Keyed off the projectile's AG flag, which defaults to yes in YR — so an
        unknown or missing projectile is treated as ground-capable rather than
        silently dropping a real defense.
        """
        if not weapon:
            return False
        proj = (self.ini.get(weapon, "Projectile", "") or "").strip()
        if not proj:
            return True
        ag = (self.ini.get(proj, "AG", "yes") or "yes").strip().lower()
        return ag not in ("no", "false", "0")

    def _weapon_hits_air(self, weapon):
        """Whether this weapon can engage an AIR target (projectile AA=yes)."""
        if not weapon:
            return False
        proj = (self.ini.get(weapon, "Projectile", "") or "").strip()
        if not proj:
            return False            # AA defaults to no, unlike AG
        aa = (self.ini.get(proj, "AA", "no") or "no").strip().lower()
        return aa not in ("no", "false", "0")

    def _scan_strategic_targets(self):
        """
        Scan rulesmd.ini for strategic target buildings/units and
        combat globals that affect unit grading.

        Returns:
          miner_armors         : set of armor indices for harvesters
          miner_strengths      : Strength of each harvester found
          cy_armor             : most common armor index for construction yards
          cy_armors            : set of ALL armor indices for construction yards
                                 (for per-faction CY grading)
          cy_armor_by_faction  : dict faction -> armor index for their CY
          factory_armors       : set of armor indices for factories
          base_def_armors      : set of armor indices for base defenses
          base_def_ranges      : ground-capable weapon ranges of ARMED
                                 buildings (any building with a weapon —
                                 IsBaseDefense is deliberately ignored)
          base_def_ranges_air  : the same for air-capable weapons
          base_def_strengths   : Strength of each armed building
          opentopped_dmg_mult  : OpenToppedDamageMultiplier from [CombatDamage]
          opentopped_rng_bonus : OpenToppedRangeBonus from [CombatDamage]
        """
        miner_armors       = set()
        miner_strengths    = []
        cy_armors          = set()           # all CY armor types seen
        cy_armor_by_bid    = {}              # bid -> armor_index
        factory_armors     = set()
        base_def_armors    = set()
        base_def_armor_counts = {}   # armor_index -> armed buildings
        base_def_strengths = []      # Strength of each armed building
        base_def_ranges    = []      # ground-capable building weapons
        base_def_ranges_air = []     # air-capable building weapons
        service_depots     = []              # buildings that remove parasites (UnitRepair=yes)
        grinders           = []              # buildings that grind/destroy units (Grinding=yes)

        factory_tag_values = {
            "infantrytype", "unittype", "aircrafttype", "buildingtype"
        }

        for section_name in ("InfantryTypes", "VehicleTypes"):
            for uid in self.ini.list_from_section(section_name):
                if not uid or uid.startswith(";"):
                    continue
                sec = self.ini.section(uid)
                if (sec.get("resourcegatherer", "no").lower() == "yes" or
                        sec.get("harvester", "no").lower() == "yes"):
                    ai = ARMOR_INDEX.get(sec.get("armor", "none").lower(), 0)
                    miner_armors.add(ai)
                    try:
                        st = float(sec.get("strength", "0") or 0)
                    except ValueError:
                        st = 0.0
                    if st > 0:
                        miner_strengths.append(st)

        for uid in self.ini.list_from_section("BuildingTypes"):
            if not uid or uid.startswith(";"):
                continue
            sec = self.ini.section(uid)
            ai  = ARMOR_INDEX.get(sec.get("armor", "none").lower(), 0)

            # Service depots: UnitRepair=yes but NOT naval yards
            # (naval yards have UnitRepair=yes for ship repair but aren't
            # the same as land service depots for parasite removal)
            is_naval_yard = sec.get("factory", "").lower() == "unittype" and \
                            sec.get("naval", "no").lower() == "yes"
            if sec.get("unitrepair", "no").lower() == "yes" and not is_naval_yard:
                service_depots.append(uid)

            if sec.get("grinding", "no").lower() == "yes":
                grinders.append(uid)

            if sec.get("constructionyard", "no").lower() == "yes":
                cy_armors.add(ai)
                cy_armor_by_bid[uid] = ai

            if sec.get("factory", "").lower() in factory_tag_values:
                factory_armors.add(ai)

            # Defense RANGES are taken from weapon tags only — never from
            # IsBaseDefense. Per Rex: modders often leave that flag unset, and
            # a building that is not nominally a defense but carries a weapon
            # fulfils the same role, so it is treated the same. That pulls in
            # vanilla's Grand Cannon (GTGCAN, range 15) and Psychic Tower
            # (YAPSYT, 7), both of which carry NO IsBaseDefense flag and were
            # therefore invisible to the old flag-gated scan — the Grand Cannon
            # being the longest-ranged ground defense in the game.
            #
            # Ground and air are kept apart: a projectile with AG=no cannot
            # touch a ground attacker (vanilla NASAM/NAFLAK, range 12), so its
            # range says nothing about out-ranging the base on the ground, and
            # mixing them pushed the bar above the Prism Tower.
            armed = False
            for wtag in REGULAR_WEAPON_TAGS:
                wname = (sec.get(wtag.lower(), "") or "").strip()
                if not wname:
                    continue
                armed = True
                try:
                    r = float(self.ini.get(wname, "Range", "0"))
                except ValueError:
                    continue
                if r <= 0:
                    continue
                if self._weapon_hits_ground(wname):
                    base_def_ranges.append(r)
                if self._weapon_hits_air(wname):
                    base_def_ranges_air.append(r)
            if armed:
                # Armour and Strength come from the same armed-building set as
                # the ranges. base_def_armors used to be flag-gated, which left
                # it inconsistent with the ranges beside it — and it had no
                # consumer at all until the defense-killer DPS floor.
                base_def_armors.add(ai)
                base_def_armor_counts[ai] = base_def_armor_counts.get(ai, 0) + 1
                try:
                    st = float(sec.get("strength", "0") or 0)
                except ValueError:
                    st = 0.0
                if st > 0:
                    base_def_strengths.append(st)

            if (sec.get("basedefense",   "no").lower() == "yes" or
                    sec.get("isbasedefense", "no").lower() == "yes"):
                base_def_armors.add(ai)

        # Most common CY armor (for backward compat / default scoring)
        if cy_armors:
            from collections import Counter
            armor_counts = Counter(cy_armor_by_bid.values())
            cy_armor = armor_counts.most_common(1)[0][0]
        else:
            cy_armor = ARMOR_INDEX.get("concrete", 8)
            cy_armors = {cy_armor}

        # Map CY armor back to faction via prereq chain
        cy_armor_by_faction = {}
        for bid, ai in cy_armor_by_bid.items():
            faction = self.sides.faction_from_prereq_chain(
                self.ini.section(bid).get("prerequisite", ""))
            if not faction:
                # CYs typically have no prereqs — detect by PoweredUnit or
                # by scanning which faction has this as their buildconyard
                for f, countries in self.sides.faction_countries.items():
                    # Rough heuristic: faction whose units prereq this CY
                    pass
            if faction and faction not in cy_armor_by_faction:
                cy_armor_by_faction[faction] = ai

        # Read OpenTopped combat globals from [CombatDamage]
        try:
            opentopped_dmg_mult = float(
                self.ini.get("CombatDamage", "OpenToppedDamageMultiplier", "1.0"))
        except ValueError:
            opentopped_dmg_mult = 1.0
        try:
            opentopped_rng_bonus = float(
                self.ini.get("CombatDamage", "OpenToppedRangeBonus", "0"))
        except ValueError:
            opentopped_rng_bonus = 0.0

        return {
            "miner_armors":        miner_armors,
            "miner_strengths":     miner_strengths,
            "cy_armor":            cy_armor,
            "cy_armors":           cy_armors,
            "cy_armor_by_faction": cy_armor_by_faction,
            "cy_armor_by_bid":     cy_armor_by_bid,
            "factory_armors":      factory_armors,
            "base_def_armors":     base_def_armors,
            "base_def_ranges":     base_def_ranges,
            "base_def_ranges_air": base_def_ranges_air,
            "base_def_strengths":  base_def_strengths,
            "base_def_armor_counts": base_def_armor_counts,
            "service_depots":      service_depots,
            "grinders":            grinders,
            "opentopped_dmg_mult": opentopped_dmg_mult,
            "opentopped_rng_bonus":opentopped_rng_bonus,
        }

    def _compute_global_threat_stats(self) -> dict:
        """
        Compute threat statistics across ALL factions combined.
        Used for "kill from all factions" grading principle.

        Returns:
          avg_hp              : dict[category -> avg HP]
          avg_miner_hp        : float
          crusher_count       : int
          crusher_ratio       : float
          anti_inf_units      : int
          total_vehicles      : int
          aa_unit_count       : int
          aa_ratio            : float
          aerial_bonus        : int  (0-3)
          aa_capability_bonus : int  (0-3)
          target_proportions  : dict[target_type -> proportion 0.0-1.0]
            target_types: "infantry", "vehicle", "building", "miner"
        """
        hp_by_cat      = {"Infantry": [], "Vehicle": [], "Aircraft": []}
        miner_hps      = []
        crusher_count  = 0
        total_vehicles = 0
        total_combat   = 0
        anti_inf_dps_list = []
        aa_unit_count  = 0

        # Target population counts (all factions, for proportion weighting)
        target_counts = {
            "infantry": 0,
            "vehicle":  0,
            "building": 0,
            "miner":    0,
        }

        cat_map = {
            "InfantryTypes": "Infantry",
            "VehicleTypes":  "Vehicle",
            "AircraftTypes": "Aircraft",
        }

        for section_name, category in cat_map.items():
            for uid in self.ini.list_from_section(section_name):
                if not uid or uid.startswith(";"):
                    continue
                sec = self.ini.section(uid)

                try:    hp = int(sec.get("strength", "0"))
                except: hp = 0
                if hp > 0:
                    hp_by_cat[category].append(hp)

                if category == "Vehicle":
                    total_vehicles += 1
                    if (sec.get("crusher",     "no").lower() == "yes" or
                            sec.get("omnicrusher", "no").lower() == "yes"):
                        crusher_count += 1
                    is_miner = (
                        sec.get("resourcegatherer", "no").lower() == "yes" or
                        sec.get("harvester",        "no").lower() == "yes"
                    )
                    if is_miner:
                        try:    miner_hps.append(int(sec.get("strength", "0")))
                        except: pass
                        target_counts["miner"] += 1

                # Count as target population (buildable combat units)
                try:    techlevel = int(sec.get("techlevel", "-1"))
                except: techlevel = -1
                if 1 <= techlevel <= 10:
                    if category == "Infantry":
                        target_counts["infantry"] += 1
                    elif category in ("Vehicle", "Aircraft"):
                        target_counts["vehicle"] += 1

                # Primary weapon analysis
                primary_id = sec.get("weapon1", "") or sec.get("primary", "")
                if primary_id:
                    wpn_sec = self.ini.section(primary_id)
                    wh_id   = wpn_sec.get("warhead",    "")
                    proj_id = wpn_sec.get("projectile", "")
                    try:
                        dmg = float(wpn_sec.get("damage", "0"))
                        rof = float(wpn_sec.get("rof",    "1"))
                    except:
                        dmg = rof = 0.0

                    if wh_id and dmg > 0 and rof > 0:
                        total_combat += 1
                        verses_str = self.ini.get(wh_id, "Verses", "")
                        verses = parse_verses(verses_str) if verses_str else [1.0]*11
                        dps_inf = max(
                            (effective_dps(dmg, rof, verses, ai)
                             for ai in ARMOR_INFANTRY),
                            default=0.0
                        )
                        anti_inf_dps_list.append(dps_inf)

                        if proj_id:
                            proj_sec = self.ini.section(proj_id)
                            if proj_sec.get("aa", "no").lower() == "yes":
                                aa_unit_count += 1

        # Count meaningful base buildings as targets
        for uid in self.ini.list_from_section("BuildingTypes"):
            if not uid or uid.startswith(";"):
                continue
            sec = self.ini.section(uid)
            try:
                strength  = int(sec.get("strength",  "0"))
                techlevel = int(sec.get("techlevel", "-1"))
            except:
                continue
            if strength <= 0 or techlevel < 0:
                continue
            if sec.get("invisible", "no").lower() == "yes":
                continue
            # Must have an owner (player-affiliated structure)
            if not sec.get("owner", ""):
                continue
            target_counts["building"] += 1

        # Compute proportions
        total_targets = sum(target_counts.values())
        target_proportions = {
            k: v / max(total_targets, 1)
            for k, v in target_counts.items()
        }

        avg_hp = {
            cat: sum(hps) / len(hps) if hps else 1.0
            for cat, hps in hp_by_cat.items()
        }
        avg_miner_hp = sum(miner_hps) / len(miner_hps) if miner_hps else 1.0

        if anti_inf_dps_list:
            global_avg_inf_dps = sum(anti_inf_dps_list) / len(anti_inf_dps_list)
            anti_inf_units = sum(
                1 for d in anti_inf_dps_list if d > global_avg_inf_dps)
        else:
            global_avg_inf_dps = 0.0
            anti_inf_units = 0

        aa_ratio = aa_unit_count / max(total_combat, 1)

        if aa_ratio < 0.10:
            aerial_bonus = 3
            aa_cap_bonus = 3
        elif aa_ratio < 0.25:
            aerial_bonus = 2
            aa_cap_bonus = 2
        elif aa_ratio < 0.50:
            aerial_bonus = 1
            aa_cap_bonus = 1
        else:
            aerial_bonus = 0
            aa_cap_bonus = 0

        return {
            "avg_hp":              avg_hp,
            "avg_miner_hp":        avg_miner_hp,
            "crusher_count":       crusher_count,
            "crusher_ratio":       crusher_count / max(total_vehicles, 1),
            "anti_inf_units":      anti_inf_units,
            "total_vehicles":      total_vehicles,
            "global_avg_inf_dps":  global_avg_inf_dps,
            "aa_unit_count":       aa_unit_count,
            "aa_ratio":            aa_ratio,
            "aerial_bonus":        aerial_bonus,
            "aa_capability_bonus": aa_cap_bonus,
            "target_proportions":  target_proportions,
        }


        hp_by_cat      = {"Infantry": [], "Vehicle": [], "Aircraft": []}
        miner_hps      = []
        crusher_count  = 0
        total_vehicles = 0
        total_combat   = 0
        anti_inf_dps_list = []
        aa_unit_count  = 0

        cat_map = {
            "InfantryTypes": "Infantry",
            "VehicleTypes":  "Vehicle",
            "AircraftTypes": "Aircraft",
        }

        for section_name, category in cat_map.items():
            for uid in self.ini.list_from_section(section_name):
                if not uid or uid.startswith(";"):
                    continue
                sec = self.ini.section(uid)

                try:
                    hp = int(sec.get("strength", "0"))
                except ValueError:
                    hp = 0
                if hp > 0:
                    hp_by_cat[category].append(hp)

                if category == "Vehicle":
                    total_vehicles += 1
                    if (sec.get("crusher",     "no").lower() == "yes" or
                            sec.get("omnicrusher", "no").lower() == "yes"):
                        crusher_count += 1
                    if (sec.get("resourcegatherer", "no").lower() == "yes" or
                            sec.get("harvester",       "no").lower() == "yes"):
                        try:
                            miner_hps.append(int(sec.get("strength", "0")))
                        except ValueError:
                            pass

                # Primary weapon analysis for anti-inf DPS and AA detection
                primary_id = sec.get("weapon1", "") or sec.get("primary", "")
                if primary_id:
                    wpn_sec = self.ini.section(primary_id)
                    wh_id   = wpn_sec.get("warhead", "")
                    proj_id = wpn_sec.get("projectile", "")
                    try:
                        dmg = float(wpn_sec.get("damage", "0"))
                        rof = float(wpn_sec.get("rof",    "1"))
                    except ValueError:
                        dmg = rof = 0.0

                    if wh_id and dmg > 0 and rof > 0:
                        total_combat += 1
                        verses_str = self.ini.get(wh_id, "Verses", "")
                        verses = parse_verses(verses_str) if verses_str else [1.0]*11
                        dps_inf = max(
                            (effective_dps(dmg, rof, verses, ai)
                             for ai in ARMOR_INFANTRY),
                            default=0.0
                        )
                        anti_inf_dps_list.append(dps_inf)

                        # AA detection: projectile with AA=yes
                        if proj_id:
                            proj_sec = self.ini.section(proj_id)
                            if proj_sec.get("aa", "no").lower() == "yes":
                                aa_unit_count += 1

        avg_hp = {
            cat: sum(hps) / len(hps) if hps else 1.0
            for cat, hps in hp_by_cat.items()
        }
        avg_miner_hp = sum(miner_hps) / len(miner_hps) if miner_hps else 1.0

        if anti_inf_dps_list:
            global_avg_inf_dps = sum(anti_inf_dps_list) / len(anti_inf_dps_list)
            anti_inf_units = sum(
                1 for d in anti_inf_dps_list if d > global_avg_inf_dps)
        else:
            global_avg_inf_dps = 0.0
            anti_inf_units = 0

        aa_ratio = aa_unit_count / max(total_combat, 1)

        # Aerial scarcity bonus: fewer AA units = bigger bonus for being aerial
        # AA capability bonus: fewer AA units = bigger bonus for having AA
        if aa_ratio < 0.10:
            aerial_bonus = 3
            aa_cap_bonus = 3
        elif aa_ratio < 0.25:
            aerial_bonus = 2
            aa_cap_bonus = 2
        elif aa_ratio < 0.50:
            aerial_bonus = 1
            aa_cap_bonus = 1
        else:
            aerial_bonus = 0
            aa_cap_bonus = 0

        return {
            "avg_hp":              avg_hp,
            "avg_miner_hp":        avg_miner_hp,
            "crusher_count":       crusher_count,
            "crusher_ratio":       crusher_count / max(total_vehicles, 1),
            "anti_inf_units":      anti_inf_units,
            "total_vehicles":      total_vehicles,
            "global_avg_inf_dps":  global_avg_inf_dps,
            "aa_unit_count":       aa_unit_count,
            "aa_ratio":            aa_ratio,
            "aerial_bonus":        aerial_bonus,
            "aa_capability_bonus": aa_cap_bonus,
        }

    def _compute_unit_grades(self):
        """
        Compute composite grade for every buildable unit.
        Score is unbounded; shown raw (e.g. 14 or -3).

        "Build from your faction" principle:
          Pool averages, cost, health, spammability -> faction/tier/category

        "Kill from all factions" principle:
          Target HP averages, crusher counts, anti-infantry counts -> all factions

        Spectrum metrics (-2 to +2 relative to faction/tier/category average):
          < 25% of average  -> -2   (very weak)
          25-49%            -> -1   (below average)
          50-74%            ->  0   (neutral, 50% IS average)
          75-99%            -> +1   (above average)
          100%+             -> +2   (highly above average)

        Spectrum metrics (10):
          1.  DPS vs infantry armor  (primary + 50% secondary)
          2.  DPS vs vehicle armor
          3.  DPS vs building armor
          4.  DPS vs miner armor
          5.  Range
          6.  Range vs base defenses
          7.  Effective Health
          8.  Survivability per cost
          9.  Cost spammability
          10. Build time spammability

        Flat bonus metrics:
          +1   Crusher=yes
          +3   OmniCrusher=yes
          +2   Turret=yes AND OpportunityFire=yes  (fire while moving)
          +N   OpenTopped=yes  (+1 per 2 passengers * OpenToppedDamageMultiplier)
          +2   C4=yes
          +1   per target category where unit can one-shot (kills-per-hit >= 1.0)
               against global average HP (infantry/vehicle/aircraft/miner)

        Aerial bonus:
          Death weapon DPS scored at 25% weight vs aerial peers
          Applies to: Jumpjet VehicleTypes + all AircraftTypes

        Infantry vulnerability penalty:
          Crushable=yes infantry penalized based on crusher ratio across all
          factions and proportion of above-average anti-infantry DPS units.
          Non-crushable infantry (Crushable=no or absent) immune to crush penalty.

        NOTE: Dynamic floor system commented out -- may revisit.
        NOTE: Specialist bonus commented out -- retired (spectrum handles it).
        """
        armor_mults = self._derive_armor_multipliers()
        targets     = self._scan_strategic_targets()
        # Store CY armor data on parser for downstream generators
        self.cy_armors           = targets.get("cy_armors", set())
        self.cy_armor_by_bid     = targets.get("cy_armor_by_bid", {})
        self.cy_armor_by_faction = targets.get("cy_armor_by_faction", {})
        self.service_depots      = targets.get("service_depots", [])
        self.grinders            = targets.get("grinders", [])
        # Warhead-derived armor effectiveness (raw, un-normalized).
        # armor_name -> effective HP multiplier (1 / avg_warhead_effectiveness).
        # Computed from all Verses= entries in rulesmd.ini.
        _armor_verses: dict[int, list] = defaultdict(list)
        for _sec in self.ini.sections.values():
            _v_str = _sec.get("verses", "")
            if not _v_str: continue
            _vals = parse_verses(_v_str)
            for _ai, _v in enumerate(_vals):
                if _v > 0:
                    _armor_verses[_ai].append(_v)
        self.armor_eff_mult = {}
        for _ai, _vals in _armor_verses.items():
            if _ai < len(ARMOR_NAMES) and _vals:
                avg = sum(_vals) / len(_vals)
                self.armor_eff_mult[ARMOR_NAMES[_ai]] = 1.0 / max(avg, 0.01)
        threat      = self._compute_global_threat_stats()

        miner_armors        = targets["miner_armors"]
        miner_dps_floor     = miner_kill_dps_floor(
            targets.get("miner_strengths"))
        cy_armor            = targets["cy_armor"]
        cy_armors           = targets.get("cy_armors", {cy_armor})
        factory_armors      = targets["factory_armors"]
        base_def_ranges     = targets["base_def_ranges"]
        ot_dmg_mult         = targets["opentopped_dmg_mult"]
        avg_base_def_range  = defense_range_bar(base_def_ranges)

        global_avg_hp       = threat["avg_hp"]
        avg_miner_hp        = threat["avg_miner_hp"]
        crusher_ratio       = threat["crusher_ratio"]
        anti_inf_units      = threat["anti_inf_units"]
        total_vehicles      = threat["total_vehicles"]
        global_avg_inf_dps  = threat["global_avg_inf_dps"]

        # Infantry vulnerability: how hostile is the environment to infantry?
        # crusher_ratio: fraction of vehicles that can crush
        # anti_inf_ratio: fraction of COMBAT units with above-avg anti-inf DPS
        # Use only buildable combat units as denominator for anti_inf_ratio
        buildable_combat_count = len([r for r in self.buildable if r.is_combat])
        anti_inf_ratio = anti_inf_units / max(buildable_combat_count, 1)

        # Combined threat: average of crusher presence and anti-inf weapon presence
        # crusher_ratio ~0.36 in vanilla, anti_inf_ratio varies
        # Scale to penalty: 0.0 threat = 0 penalty, 1.0 threat = -2 penalty
        inf_threat_score = min(1.0, (crusher_ratio + anti_inf_ratio) / 1.5)
        # Ensure at least -1 penalty if there are any crushers at all
        if crusher_ratio > 0.1:
            crush_penalty = min(-1, round(inf_threat_score * -2))
        else:
            crush_penalty = round(inf_threat_score * -2)

        # --- Commented out: dynamic floor system (may revisit) ---
        # def dynamic_floor(pool_size):
        #     if pool_size <= 3:  return 0.25
        #     if pool_size <= 8:  return 0.40
        #     if pool_size <= 15: return 0.55
        #     return 0.70
        # ----------------------------------------------------------

        # --- Commented out: specialist bonus (retired) ---
        # SPECIALIST_MULTIPLIER = 2.0
        # specialist = any(unit_val >= avg_val * SPECIALIST_MULTIPLIER ...)
        # -------------------------------------------------

        def spectrum(value: float, average: float) -> int:
            """
            Linear scale centered on 100% of average = 0.
            Every 25% below average = -1, every 25% above = +1.
            Negative side capped at -4 (completely useless at this metric).
            No cap on positive side -- excellence is fully rewarded.

              0-24%   -> -4  (cap)
              25-49%  -> -3
              50-74%  -> -2
              75-99%  -> -1
              100-124%->  0  (at average = neutral)
              125-149%-> +1
              150-174%-> +2
              175-199%-> +3
              200-224%-> +4
              225%+   -> +5 and beyond...
            """
            if average <= 0:
                return 0 if value <= 0 else 1
            ratio = value / average
            score = int((ratio - 1.0) / 0.25)
            return max(score, -4)

        def spectrum_dps(value: float, average: float) -> int:
            """
            Like spectrum() but with an absolute floor check.
            If both value and average are near-zero (< 0.01 DPS),
            score 0 rather than potentially positive — prevents units
            with 1% Verses scoring well just because peers also have
            near-zero DPS against that target type (e.g. VIRUS vs buildings).
            """
            if value < 0.01 and average < 0.01:
                return 0
            return spectrum(value, average)

        def eff_health(rec):
            mult = armor_mults.get(rec.armor, 1.0)
            base = rec.strength * mult
            return base * 1.25 if rec.self_healing else base

        dps_vs_weapon = globals()["dps_vs_weapon"]

        def dps_vs(rec, armor_indices):
            """Module-level unit_dps_vs(); kept as a local name for the many
            call sites below."""
            return unit_dps_vs(rec, armor_indices)

        def kills_per_hit(rec, armor_indices, target_avg_hp: float) -> float:
            """
            Max damage in one shot against the given armor class,
            divided by target average HP. >= 1.0 = can one-shot.
            Uses primary weapon only (the decisive strike).
            """
            wpn    = rec.role_weapon
            verses = wpn.get("verses", [0.0] * 11)
            damage = wpn.get("damage", 0.0)
            return max(
                (damage * verses[ai] / max(target_avg_hp, 1)
                 for ai in armor_indices),
                default=0.0
            )

        def cost_spam(rec):   return 1.0 / max(rec.cost, 1)
        def time_spam(rec):   return 1.0 / max(rec.build_time_multiplier, 0.01)
        def surv_per_cost(rec): return eff_health(rec) / max(rec.cost, 1)

        def is_aerial(rec) -> bool:
            return (rec.is_aircraft_type or
                    rec.mobility in ("Jumpjet", "Aircraft"))

        def death_weapon_dps(rec, armor_indices) -> float:
            """
            Compute death weapon DPS at 25% weight.
            DeathWeapon= defaults to primary if absent.
            Applies DeathWeaponDamagedModifier= as damage multiplier.
            """
            sec_data = self.ini.section(rec.unit_id)
            dw_id    = sec_data.get("deathweapon", "")
            if not dw_id:
                dw_wpn = rec.role_weapon
            else:
                dw_wpn = self.analyzer.analyze_weapon(dw_id)

            modifier_str = sec_data.get("deathweapondamagedmodifier", "1.0")
            try:
                modifier = float(modifier_str)
            except ValueError:
                modifier = 1.0

            verses = dw_wpn.get("verses", [0.0] * 11)
            damage = dw_wpn.get("damage", 0.0) * modifier
            rof    = dw_wpn.get("rof",    1.0)
            raw = max(
                (effective_dps(damage, rof, verses, ai) for ai in armor_indices),
                default=0.0
            )
            return raw * 0.25  # 25% weight

        # Read new threat stats
        aerial_bonus     = threat["aerial_bonus"]
        aa_cap_bonus     = threat["aa_capability_bonus"]
        target_props     = threat["target_proportions"]
        inf_prop         = target_props.get("infantry", 0.2)
        veh_prop         = target_props.get("vehicle",  0.3)
        bld_prop         = target_props.get("building", 0.5)
        min_prop         = target_props.get("miner",    0.05)

        # --- Commented out: dynamic floor system (may revisit) ---
        # def dynamic_floor(pool_size):
        #     if pool_size <= 3:  return 0.25
        #     ...
        # ----------------------------------------------------------

        # --- Commented out: specialist bonus (retired) ---
        # SPECIALIST_MULTIPLIER = 2.0
        # specialist = any(unit_val >= avg_val * SPECIALIST_MULTIPLIER ...)
        # -------------------------------------------------

        # --- Commented out: suicide weapon grading (TODO) ---
        # Detect Suicide=yes on weapon section.
        # DPS = damage / build_time (fires once per unit built).
        # Suicide units with poor armor may detonate on friendly units.
        # --------------------------------------------------------

        def spectrum(value: float, average: float) -> int:
            """
            Linear scale centered on average with neutral zone ±15%.
            Positive: +1 per 5% above 115% of average (uncapped).
            Negative: -1 per 15% below 85% of average (capped at -7).

              85-115%  ->  0  (neutral zone)
              116-120% -> +1
              121-125% -> +2  ... uncapped upward
              70-84%   -> -1
              55-69%   -> -2
              40-54%   -> -3
              25-39%   -> -4
              10-24%   -> -5
              0-9%     -> -6
              cap      -> -7
            """
            if average <= 0:
                return 0 if value <= 0 else 1
            ratio = value / average
            if 0.85 <= ratio <= 1.15:
                return 0
            elif ratio > 1.15:
                return min(int((ratio - 1.15) / 0.05) + 1, 10)
            else:
                score = -(int((0.85 - ratio) / 0.15) + 1)
                return max(score, -7)

        def spectrum_dps(value: float, average: float,
                         proportion: float = 1.0) -> int:
            """
            Like spectrum() but:
            1. Near-zero absolute floor: if both value and average < 0.01,
               score 0 (prevents 1% Verses units scoring positively).
            2. Target proportion weighting: raw score multiplied by the
               proportion of that target type among all combat targets.
               Rounds to nearest int, minimum 0 if score was positive
               (can't gain from irrelevant damage) or floor if negative.
            """
            if value < 0.01 and average < 0.01:
                return 0
            raw = spectrum(value, average)
            if raw == 0:
                return 0
            # Apply proportion: positive scores reduced by proportion,
            # negative scores also reduced (fewer targets = less relevant
            # to be bad at killing them too)
            weighted = raw * proportion
            if raw > 0:
                return max(1, round(weighted)) if weighted >= 0.5 else 0
            else:
                return min(-1, round(weighted)) if weighted <= -0.5 else 0

        def is_death_weapon_eligible(rec) -> bool:
            """AircraftTypes and Jumpjet VehicleTypes only get death weapon bonus."""
            return (rec.is_aircraft_type or
                    (rec.category == "Vehicle" and
                     rec.mobility in ("Jumpjet", "Aircraft")))

        # Build groups by factory anchor + tier (the actual build queue comparison)
        # Units competing for the same factory slot in the same game phase
        # are graded against each other.
        # Fallback groups for small pools: all units from same factory (any tier),
        # then all units from same faction (any factory/tier).
        factory_tier_groups = defaultdict(list)  # (faction, factory, tier)
        factory_groups      = defaultdict(list)  # (faction, factory) -- fallback
        faction_groups      = defaultdict(list)  # (faction,) -- last resort fallback
        aerial_groups       = defaultdict(list)  # (faction, tier) aerial only
        aircraft_groups     = defaultdict(list)  # (faction, tier) AircraftType only — speed comparison
        ground_groups       = defaultdict(list)  # (faction, factory, tier) non-aircraft — speed comparison

        for rec in self.buildable:
            if not rec.faction:
                continue
            # Exclude suicide units from baseline DPS calculations.
            if rec.is_suicide:
                continue
            factory = rec.factory_anchor or "none"
            factory_tier_groups[(rec.faction, factory, rec.tier)].append(rec)
            factory_groups[(rec.faction, factory)].append(rec)
            faction_groups[(rec.faction,)].append(rec)
            if is_death_weapon_eligible(rec):
                aerial_groups[(rec.faction, rec.tier)].append(rec)
            # Separate speed groups: AircraftTypes are never compared to
            # AircraftTypes always get their own speed group — their speed=14+
            # must not inflate ground vehicle averages.
            # Ground units: all go into ground_groups initially. The speed
            # baseline will filter by combat DPS later (units with negligible
            # combat DPS are excluded from the average regardless of type).
            if rec.is_aircraft_type:
                aircraft_groups[(rec.faction, rec.tier)].append(rec)
            else:
                ground_groups[(rec.faction, factory, rec.tier)].append(rec)

        # Speed groups: pure factory-based comparison only.
        # SHAD (Jumpjet, GAWEAP) is compared to MTNK and FV — correct,
        # they share the warfactory queue. BEAG/ORCA are AircraftTypes
        # in GAAIRC — separate queue, separate comparison.
        # No aerial mobility exception needed — factory grouping handles it.

        def compute_avg(units, speed_units=None):
            """Compute all metric averages for a group of units.
            speed_units: pre-filtered list for speed baseline
                         (combat-viable units only, aircraft separated).
                         Falls back to units if None.
            """
            if not units:
                return {}
            su = speed_units if speed_units else units
            if not su:
                su = units
            return {
                "infantry": sum(dps_vs(u, ARMOR_INFANTRY) for u in units) / len(units),
                "vehicle":  sum(dps_vs(u, ARMOR_VEHICLE)  for u in units) / len(units),
                "building": sum(dps_vs(u, ARMOR_BUILDING) for u in units) / len(units),
                "miner":   (sum(dps_vs(u, miner_armors)   for u in units) / len(units)
                            if miner_armors else 0.0),
                "cy":       sum(dps_vs(u, cy_armors)     for u in units) / len(units),
                "factory_bld": (sum(dps_vs(u, factory_armors) for u in units) / len(units)
                            if factory_armors else 0.0),
                "speed":    sum(u.speed         for u in su) / len(su),
                "range":    sum(u.weapon_range  for u in units) / len(units),
                "health":   sum(eff_health(u)   for u in units) / len(units),
                "surv":     sum(surv_per_cost(u) for u in units) / len(units),
                "cspam":    sum(cost_spam(u)    for u in units) / len(units),
                "tspam":    sum(time_spam(u)    for u in units) / len(units),
            }

        # Precompute averages for all group levels.
        # Speed baseline filter: percentile-based value score exclusion.
        # For each factory group, compute a value score per unit:
        #   value = rush_dps x eff_hp / cost x sqrt(speed)
        # Exclude units below the 25th percentile of their group's value score.
        # Percentile-based => immune to ZEP/outlier distortion.

        def _value_score(u) -> float:
            rw     = u.role_weapon or {}
            verses = rw.get("verses", [0.0] * 11)
            dmg    = rw.get("damage", 0.0)
            rof    = rw.get("rof", 1.0) or 1.0
            av_dps = (dmg / rof) * max(verses[3:6]) if len(verses) >= 6 else 0
            ab_dps = (dmg / rof) * max(verses[6:9]) if len(verses) >= 9 else 0
            rush   = max(av_dps, ab_dps)
            hp     = eff_health(u)
            cost   = max(u.cost, 1)
            spd    = max(u.speed, 1)
            return rush * hp / cost * (spd ** 0.5)

        _gval = {k: [_value_score(u) for u in v]
                 for k, v in ground_groups.items()}

        def _viable(u, faction, factory, tier):
            """
            Is unit viable for the speed baseline?
            Exclude units whose value score is below 40% of the group median.
            This is more robust than percentile for small groups (4-6 units).
            Example: GAWEAP group — SHAD(0.55) is 34% of median(1.60) → excluded.
                     MTNK(1.92) is 120% of median → included. ✓
            Groups with < 3 units: no exclusion (too small to be meaningful).
            """
            gk = (faction, factory, tier)
            sc = _gval.get(gk, [])
            if len(sc) < 3:
                return True
            s = sorted(sc)
            median = s[len(s) // 2]
            if median <= 0:
                return True
            return _value_score(u) >= median * 0.4

        def _speed_units_for(faction, factory, tier, is_aircraft):
            if is_aircraft:
                return aircraft_groups.get((faction, tier), []) or None
            su = ground_groups.get((faction, factory, tier), [])
            viable = [u for u in su if _viable(u, faction, factory, tier)]
            return viable or su or None

        ft_avg  = {
            k: compute_avg(v, _speed_units_for(
                k[0], k[1], k[2],
                any(u.is_aircraft_type for u in v)))
            for k, v in factory_tier_groups.items()
        }
        f_avg   = {
            k: compute_avg(v, [u for u in v
                               if not u.is_aircraft_type
                               and _viable(u, k[0], k[1], "all")] or None)
            for k, v in factory_groups.items()
        }
        fac_avg = {k: compute_avg(v) for k, v in faction_groups.items()}

        MIN_POOL = 3  # minimum group size for meaningful comparison

        def get_avg(rec):
            """
            Get comparison averages for a unit, with fallback:
            1. (faction, factory, tier) -- primary: same queue, same phase
            2. (faction, factory)       -- fallback: same queue, any phase
            3. (faction,)               -- last resort: same faction, any queue
            """
            factory  = rec.factory_anchor or "none"
            ft_key   = (rec.faction, factory, rec.tier)
            f_key    = (rec.faction, factory)
            fac_key  = (rec.faction,)

            if len(factory_tier_groups.get(ft_key, [])) >= MIN_POOL:
                return ft_avg[ft_key]
            elif len(factory_groups.get(f_key, [])) >= MIN_POOL:
                return f_avg[f_key]
            else:
                return fac_avg.get(fac_key, {})

        # Precompute death weapon DPS averages for aerial group
        aerial_death_avg = {}
        for key, units in aerial_groups.items():
            for armor_key, armor_set in [
                ("infantry", ARMOR_INFANTRY),
                ("vehicle",  ARMOR_VEHICLE),
                ("building", ARMOR_BUILDING),
            ]:
                vals = [death_weapon_dps(u, armor_set) for u in units]
                aerial_death_avg[(key, armor_key)] = (
                    sum(vals) / len(vals) if vals else 0.0)
        for rec in self.buildable:
            # faction=None is normally skipped, but stolen tech units are
            # faction-neutral (spy mechanic) — still compute their grade
            # using cross-faction averages via the fallback path in get_avg().
            if not rec.faction and not rec.requires_stolen_tech:
                continue

            sec_data = self.ini.section(rec.unit_id)
            ac = get_avg(rec)  # factory-queue aware averages with fallback

            unit_is_aerial = (rec.is_aircraft_type or
                              rec.mobility in ("Jumpjet", "Aircraft"))

            bd = {}

            # --- Spectrum metrics (compared within build queue + game phase) ---
            if rec.is_utility:
                bd["dps_infantry"] = 2
                bd["dps_vehicle"]  = 2
                bd["dps_building"] = 2
                bd["miner_killer"] = 2
            else:
                bd["dps_infantry"] = spectrum_dps(dps_vs(rec, ARMOR_INFANTRY), ac.get("infantry", 0), inf_prop)
                bd["dps_vehicle"]  = spectrum_dps(dps_vs(rec, ARMOR_VEHICLE),  ac.get("vehicle",  0), veh_prop)
                bd["dps_building"] = spectrum_dps(dps_vs(rec, ARMOR_BUILDING), ac.get("building", 0), bld_prop)
                bd["miner_killer"] = (spectrum_dps(dps_vs(rec, miner_armors), ac.get("miner", 0), min_prop)
                                      if miner_armors else 0)

            bd["range"]         = spectrum(rec.weapon_range,  ac.get("range",  0))
            bd["outranges_def"] = spectrum(rec.weapon_range,  avg_base_def_range)
            bd["health"]        = spectrum(eff_health(rec),   ac.get("health", 0))
            bd["surv_per_cost"] = spectrum(surv_per_cost(rec),ac.get("surv",   0))
            bd["cost_spam"]     = spectrum(cost_spam(rec),    ac.get("cspam",  0))
            bd["time_spam"]     = spectrum(time_spam(rec),    ac.get("tspam",  0))

            bd["speed"] = spectrum(float(rec.speed), ac.get("speed", 0))
            rec.combat_speed_avg = ac.get("speed", float(rec.speed))

            # Naval bombardment score: building DPS × weapon range.
            # Used to identify long-range naval units suitable for
            # bombarding land targets when enemy has no shipyard.
            # Top scorers in vanilla: CARRIER (333), DRED (128), BSUB (24).
            if rec.is_naval_unit:
                rw      = rec.role_weapon or {}
                verses  = rw.get("verses", [0.0] * 11)
                dmg     = rw.get("damage", 0.0)
                rof_v   = rw.get("rof", 1.0) or 1.0
                bld_eff = max(verses[6:9]) if len(verses) >= 9 else 0.0
                bld_dps = (dmg / rof_v) * bld_eff
                # Range: try role_weapon range, then primary weapon section
                rng = rec.weapon_range  # already computed on UnitRecord
                rec.naval_bombardment_score = max(0.0, bld_dps * rng)
            # Mark whether this unit is viable for the speed baseline
            # (units below surv/cost threshold skip FAST/SLOW tagging)
            # Speed baseline viability: is this unit above the 25th percentile
            # of its group's value score? Uses _viable() from the group
            # computation above. Fallback to True if group not computed yet.
            _fact  = rec.factory_anchor or "none"
            rec._speed_baseline_viable = (
                rec.is_aircraft_type or
                _viable(rec, rec.faction or "", _fact, rec.tier)
            )

            # Building damage bonus: destroying structures is a primary AI goal.
            # Units that score positively on building DPS get that score again
            # as a bonus, doubling the reward for anti-structure capability.
            # Requires actual building DPS > 0.05 to avoid rewarding units
            # that technically hit buildings at 1% Verses (like VIRUS).
            bld_score  = bd.get("dps_building", 0)
            raw_bld_dps = dps_vs(rec, ARMOR_BUILDING)
            bd["building_bonus"] = bld_score if (bld_score > 0 and
                                                  raw_bld_dps > 0.05) else 0

            # --- Flat bonus metrics ---
            crusher     = sec_data.get("crusher",     "no").lower() == "yes"
            omnicrusher = sec_data.get("omnicrusher", "no").lower() == "yes"
            bd["crusher"]     = 1 if crusher     else 0
            bd["omnicrusher"] = 3 if omnicrusher else 0

            has_turret = sec_data.get("turret",          "no").lower() == "yes"
            opp_fire   = sec_data.get("opportunityfire", "no").lower() == "yes"
            bd["fire_while_moving"] = 2 if (has_turret and opp_fire) else 0

            opentopped = sec_data.get("opentopped", "no").lower() == "yes"
            if opentopped:
                try:    passengers = int(sec_data.get("passengers", "0"))
                except: passengers = 0
                base_ot = passengers // 2
                bd["opentopped"] = max(round(base_ot * ot_dmg_mult),
                                       1 if passengers > 0 else 0)
            else:
                bd["opentopped"] = 0

            # C4 bonus
            bd["c4"] = 2 if sec_data.get("c4", "no").lower() == "yes" else 0

            # Kills-per-hit bonus: +1 per target category where unit one-shots
            kph_bonus = 0
            if not rec.is_utility:
                for armor_set, cat_name in [
                    (ARMOR_INFANTRY, "Infantry"),
                    (ARMOR_VEHICLE,  "Vehicle"),
                ]:
                    avg_hp = global_avg_hp.get(cat_name, 0)
                    if avg_hp > 0 and kills_per_hit(rec, armor_set, avg_hp) >= 1.0:
                        kph_bonus += 1
                if avg_miner_hp > 0 and kills_per_hit(
                        rec, miner_armors if miner_armors else ARMOR_VEHICLE,
                        avg_miner_hp) >= 1.0:
                    kph_bonus += 1
            bd["one_shot_bonus"] = kph_bonus

            # --- Death weapon bonus ---
            if is_death_weapon_eligible(rec):
                aerial_key = (rec.faction, rec.tier)
                dw_scores = []
                for armor_key, armor_set in [
                    ("infantry", ARMOR_INFANTRY),
                    ("vehicle",  ARMOR_VEHICLE),
                    ("building", ARMOR_BUILDING),
                ]:
                    avg_dw = aerial_death_avg.get((aerial_key, armor_key), 0.0)
                    if avg_dw > 0:
                        dw_scores.append(spectrum(
                            death_weapon_dps(rec, armor_set), avg_dw))
                # No penalty for not having a death weapon — only reward
                bd["death_weapon"] = max(dw_scores) if dw_scores else 0
            else:
                bd["death_weapon"] = 0

            # --- Aerial scarcity bonus ---
            # Being aerial is advantageous when AA units are scarce.
            # Applies to ALL aerial unit types (Jumpjet infantry included).
            bd["aerial_bonus"] = aerial_bonus if unit_is_aerial else 0

            # --- AA capability bonus ---
            # Having AA capability is more valuable when AA units are scarce.
            has_aa = "AA" in rec.roles or "AA_Secondary" in rec.roles
            bd["aa_capability_bonus"] = aa_cap_bonus if has_aa else 0

            # --- Infantry crush vulnerability ---
            # Crushable infantry are vulnerable — but NOT if they are aerial
            # (aerial units fly over crushers and cannot be crushed in flight).
            if (rec.category == "Infantry" and
                    not rec.is_utility and
                    not unit_is_aerial and
                    sec_data.get("crushable", "no").lower() == "yes"):
                bd["crush_vulnerability"] = crush_penalty
            else:
                bd["crush_vulnerability"] = 0

            rec.grade           = sum(bd.values())
            rec.grade_breakdown = bd

            # --- Veterancy bonus ---
            # Units with Trainable=no cannot gain veterancy at all.
            # Units whose weapon cannot meaningfully damage any target also
            # get no veterancy — checked via raw weapon Verses percentages
            # rather than grade scores (which may be negative due to comparison
            # effects, not because the weapon is actually useless).
            # A unit can earn veterancy if its weapon has > 0% Verses against
            # any infantry, vehicle, or building armor class.
            vet_bonus = 0

            # Check raw weapon viability for veterancy
            role_wpn   = rec.role_weapon
            wpn_verses = role_wpn.get("verses", [0.0] * 11)
            # Check if weapon can damage anything in inf/veh/bld armor classes
            all_combat_armor = list(ARMOR_INFANTRY | ARMOR_VEHICLE | ARMOR_BUILDING)
            weapon_can_kill  = any(wpn_verses[ai] > 0 for ai in all_combat_armor
                                   if ai < len(wpn_verses))

            if (sec_data.get("trainable", "yes").lower() != "no"
                    and weapon_can_kill):
                # Ability bonuses (+1 per veteran ability, +1 per elite ability)
                vet_abil_str = sec_data.get("veteranabilities", "")
                eli_abil_str = sec_data.get("eliteabilities",  "")
                vet_abilities = [a.strip() for a in vet_abil_str.split(",") if a.strip()]
                eli_abilities = [a.strip() for a in eli_abil_str.split(",") if a.strip()]
                vet_bonus += len(vet_abilities) + len(eli_abilities)

                # ElitePrimary weapon contribution (25% weight)
                elite_pri_id = sec_data.get("eliteprimary", "")
                if elite_pri_id and not rec.is_utility:
                    elite_wpn = self.analyzer.analyze_weapon(elite_pri_id)
                    if elite_wpn.get("is_combat"):
                        elite_bd = {}
                        elite_bd["dps_infantry"] = spectrum_dps(
                            dps_vs_weapon(elite_wpn, ARMOR_INFANTRY),
                            ac.get("infantry", 0), inf_prop)
                        elite_bd["dps_vehicle"]  = spectrum_dps(
                            dps_vs_weapon(elite_wpn, ARMOR_VEHICLE),
                            ac.get("vehicle",  0), veh_prop)
                        elite_bd["dps_building"] = spectrum_dps(
                            dps_vs_weapon(elite_wpn, ARMOR_BUILDING),
                            ac.get("building", 0), bld_prop)
                        elite_bld = elite_bd["dps_building"]
                        elite_bd["building_bonus"] = elite_bld if elite_bld > 0 else 0
                        elite_score = sum(elite_bd.values())
                        base_dps_score = (bd.get("dps_infantry", 0) +
                                          bd.get("dps_vehicle",  0) +
                                          bd.get("dps_building", 0) +
                                          bd.get("building_bonus", 0))
                        vet_bonus += round(0.25 * (elite_score - base_dps_score))

                # VeteranPrimary weapon contribution (10% weight)
                vet_pri_id = sec_data.get("veteranprimary", "")
                if vet_pri_id and not rec.is_utility:
                    vet_wpn = self.analyzer.analyze_weapon(vet_pri_id)
                    if vet_wpn.get("is_combat"):
                        vet_bd = {}
                        vet_bd["dps_infantry"] = spectrum_dps(
                            dps_vs_weapon(vet_wpn, ARMOR_INFANTRY),
                            ac.get("infantry", 0), inf_prop)
                        vet_bd["dps_vehicle"]  = spectrum_dps(
                            dps_vs_weapon(vet_wpn, ARMOR_VEHICLE),
                            ac.get("vehicle",  0), veh_prop)
                        vet_bd["dps_building"] = spectrum_dps(
                            dps_vs_weapon(vet_wpn, ARMOR_BUILDING),
                            ac.get("building", 0), bld_prop)
                        vet_bld = vet_bd["dps_building"]
                        vet_bd["building_bonus"] = vet_bld if vet_bld > 0 else 0
                        vet_score = sum(vet_bd.values())
                        base_dps_score = (bd.get("dps_infantry", 0) +
                                          bd.get("dps_vehicle",  0) +
                                          bd.get("dps_building", 0) +
                                          bd.get("building_bonus", 0))
                        vet_bonus += round(0.10 * (vet_score - base_dps_score))


            bd["veterancy"] = vet_bonus
            rec.grade       = sum(bd.values())

            # Special role tags
            if not rec.is_utility:
                # Threshold >= 2 means "above-average DPS against miner armor"
                # (spectrum >= 2 = 150-174% of average). Previously >= 1 was
                # too permissive: GHOST's MP5 does trivial damage to medium
                # armor but was still passing the threshold, wrongly getting
                # tagged Miner_Killer.
                # Relative rank AND an absolute floor: "better than this mod's
                # average" is not the same as "can actually kill a miner".
                if (miner_armors
                        and spectrum(dps_vs(rec, miner_armors),
                                     ac.get("miner", 0)) >= 2
                        and dps_vs(rec, miner_armors) >= miner_dps_floor):
                    if "Miner_Killer" not in rec.roles:
                        rec.roles = rec.roles + ["Miner_Killer"]
                if spectrum(dps_vs(rec, cy_armors), ac.get("cy", 0)) >= 1:
                    if "CY_Killer" not in rec.roles:
                        rec.roles = rec.roles + ["CY_Killer"]
                if factory_armors and spectrum(
                        dps_vs(rec, factory_armors), ac.get("factory", 0)) >= 1:
                    if "Factory_Killer" not in rec.roles:
                        rec.roles = rec.roles + ["Factory_Killer"]
                if rec.secondary_analysis.get("can_aa") and "AA" not in rec.roles:
                    rec.roles = rec.roles + ["AA_Secondary"]

                # Promote AA_Secondary → AA for units whose secondary weapon
                # is a genuine dedicated AA weapon AND whose primary is not
                # primarily anti-vehicle (which would make AA incidental).
                # Rule:
                #   secondary.can_aa is True (projectile can target aircraft)
                #   AND ( primary_vehicle_verses < 80% (not a pure anti-tank unit)
                #         OR secondary projectile explicitly has AA=yes on itself )
                # The second condition catches APOC: 120mm is 100% vs vehicles
                # but MammothTusk fires the AAHeatSeeker projectile with AA=yes —
                # APOC is genuinely dual-role anti-tank AND anti-air.
                if "AA_Secondary" in rec.roles and "AA" not in rec.roles:
                    _sec_can_aa = rec.secondary_analysis.get("can_aa", False)
                    _pri_v      = rec.role_weapon.get("verses", [0.0] * 11)
                    _pri_veh    = max(_pri_v[3:6]) if len(_pri_v) >= 6 else 0.0

                    # Check if secondary projectile itself is flagged AA=yes
                    _sec_proj_aa = False
                    _secondary_id = sec_data.get("secondary", "")
                    if _secondary_id:
                        _sec_wpn_s = self.ini.section(_secondary_id) or {}
                        _proj_id   = _sec_wpn_s.get("projectile", "")
                        if _proj_id:
                            _proj = self.ini.section(_proj_id) or {}
                            _sec_proj_aa = _proj.get("aa", "no").lower() == "yes"

                    if _sec_can_aa and (_pri_veh < 0.8 or _sec_proj_aa):
                        rec.roles = [r for r in rec.roles
                                     if r != "AA_Secondary"] + ["AA"]

                # GENERALIST tag: primary weapon does meaningful damage to all
                # three target categories using a self-relative 20% floor.
                # Each category must be >= 20% of the unit's best DPS category.
                # Primary weapon only — secondary never gates GENERALIST status.
                # TODO: revisit with proper primary/secondary scoring system.
                pri_wpn = rec.role_weapon
                _pv  = pri_wpn.get("verses", [0.0]*11)
                _pd  = pri_wpn.get("damage", 0.0)
                _pr  = pri_wpn.get("rof",    1.0)
                def _pdps(ais):
                    return max((effective_dps(_pd,_pr,_pv,ai) for ai in ais), default=0.0)
                _di  = _pdps(ARMOR_INFANTRY)
                _dv  = _pdps(ARMOR_VEHICLE)
                _db  = _pdps(ARMOR_BUILDING)
                _best = max(_di, _dv, _db)
                if (_best > 0 and
                        _di >= _best * 0.20 and
                        _dv >= _best * 0.20 and
                        _db >= _best * 0.20):
                    if "GENERALIST" not in rec.roles:
                        rec.roles = rec.roles + ["GENERALIST"]


                # --- Special weapon bonuses ---
                # These weapons have effects that raw DPS cannot represent.
                # Give them a flat bonus so they don't get excluded from waves.
                # They get dedicated wave types rather than general pool inclusion.

                if rec.is_radiation:
                    # Radiation leaves a persistent contamination field.
                    bd["radiation_bonus"] = 8
                    rec.grade = sum(bd.values())
                    rec.grade_breakdown = bd
                    if "AG-Anti_Infantry" not in rec.roles:
                        rec.roles = rec.roles + ["AG-Anti_Infantry"]

                if rec.is_mind_control:
                    # Mind control permanently removes enemy unit from combat.
                    bd["mind_control_bonus"] = 12
                    rec.grade = sum(bd.values())
                    rec.grade_breakdown = bd

                if rec.is_chaos:
                    # Chaos drone forces enemy units to attack each other.
                    bd["chaos_bonus"] = 8
                    rec.grade = sum(bd.values())
                    rec.grade_breakdown = bd

                if rec.is_temporal:
                    # Chrono erasure: deals 8 damage/frame (= 240 DPS equivalent)
                    # against ANY armor class regardless of verses.
                    # Formula: erase_time = (10 × HP) / (dmg_per_frame × 30fps)
                    # Effective DPS = dmg_per_frame × 3 = 24 (basic) / 48 (elite)
                    # This is extremely high — comparable to heavy artillery.
                    # The beam also disables the target while erasing it (Temporal=yes
                    # prevents the target from acting).
                    # We use chrono_dps = damage × 3 (basic weapon damage × 30fps / 10)
                    _wpn_id  = self.ini.get(rec.unit_id, "Primary", "")
                    _wpn     = self.ini.section(_wpn_id) if _wpn_id else {}
                    _dmg     = float(_wpn.get("damage", "8")) if _wpn else 8.0
                    _chrono_dps = _dmg * 3.0   # damage_per_frame × 30fps / 10 normalizer
                    # Compare against faction average infantry DPS for spectrum score
                    _avg_inf = ac.get("infantry", _chrono_dps)
                    _spectrum = spectrum_dps(_chrono_dps, _avg_inf, inf_prop)
                    bd["temporal_chrono_dps"] = max(2, _spectrum)   # min +2 bonus
                    # Temporal disables target — this is "free DPS time"
                    bd["temporal_disable_bonus"] = 3
                    rec.grade = sum(bd.values())
                    rec.grade_breakdown = bd
                    # CLEG hits all armor classes equally — add multi-role tags
                    for role in ["AG-Anti_Infantry", "AG-Anti_Vehicle", "AG-Anti_Building"]:
                        if role not in rec.roles:
                            rec.roles = rec.roles + [role]

                if rec.is_ivan_bomb:
                    # Ivan bomb: places a time-delayed charge that instantly
                    # destroys any building it attaches to. Range 1.5 means
                    # CIVAN must get very close. High strategic value vs bases.
                    bd["ivan_bomb_bonus"] = 8
                    rec.grade = sum(bd.values())
                    rec.grade_breakdown = bd
                    if "AG-Anti_Building" not in rec.roles:
                        rec.roles = rec.roles + ["AG-Anti_Building"]
                    if "CY_Killer" not in rec.roles:
                        rec.roles = rec.roles + ["CY_Killer"]
                    if "Factory_Killer" not in rec.roles:
                        rec.roles = rec.roles + ["Factory_Killer"]

                if rec.is_locomotor_weapon:
                    # Locomotor beam (IsLocomotor=yes warhead) lifts vehicles and
                    # drags them to a new position. This immobilizes the target
                    # vehicle completely while held — equivalent to a stun.
                    # Particularly effective for pulling vehicles into grinders or
                    # mind control range. Magnetron (TELE) is the prime example.
                    # Secondary weapon (MagneShake) is high-damage vs buildings
                    # making TELE also an artillery unit.
                    bd["locomotor_lift_bonus"] = 5
                    rec.grade = sum(bd.values())
                    rec.grade_breakdown = bd
                    # Locomotor only works on vehicles — ensure anti-vehicle role
                    if "AG-Anti_Vehicle" not in rec.roles:
                        rec.roles = rec.roles + ["AG-Anti_Vehicle"]
                    if "Miner_Killer" not in rec.roles:
                        rec.roles = rec.roles + ["Miner_Killer"]

                if rec.is_teleporter:
                    # Teleporting unit bypasses base defenses to reach target.
                    bd["teleporter_bonus"] = 3
                    rec.grade = sum(bd.values())
                    rec.grade_breakdown = bd

                # Stolen tech units with special effects not captured by DPS:
                if rec.requires_stolen_tech:
                    _sec_d = sec_data
                    _has_chrono_erase = (
                        _sec_d.get("chronoinsound", "") != "" or
                        rec.is_temporal)
                    _has_ivan_bomb = rec.is_ivan_bomb
                    if _has_chrono_erase and "temporal_chrono_dps" not in bd:
                        bd["chrono_erase_bonus"] = 10
                        rec.grade = sum(bd.values())
                        rec.grade_breakdown = bd
                    if _has_ivan_bomb and "ivan_bomb_bonus" not in bd:
                        bd["ivan_bomb_bonus_2"] = 8
                        rec.grade = sum(bd.values())
                        rec.grade_breakdown = bd
                # Used by the taskforce generator to include such units rarely
                # (flavor/easy difficulty), never as primary composition.
                # Threshold: flat grade <= -5 and not already utility.
                # Never applied to special-weapon units (radiation, chaos,
                # mind control, parasite, deployfire) — they have dedicated waves.
                _is_special = (rec.is_mind_control or rec.is_chaos or
                               rec.is_radiation or rec.is_parasite or
                               rec.is_deployfire)
                if (rec.grade <= -5
                        and "GENERALIST" not in rec.roles
                        and not _is_special):
                    if "UNDESIRABLE" not in rec.roles:
                        rec.roles = rec.roles + ["UNDESIRABLE"]


                # Speed tags: FAST/SLOW based on speed spectrum score.
                # Only tag units that are viable general combat units.
                # Transports, suicide units, and utility units have
                # specialized generators — speed tags are meaningless for them
                # and can cause incorrect rush selection.
                _speed_taggable = (
                    not rec.is_transport
                    and not rec.is_open_transport
                    and not rec.is_suicide
                    and not rec.is_utility
                    and getattr(rec, '_speed_baseline_viable', True)
                )
                spd_score = bd.get("speed", 0)
                if _speed_taggable:
                    if spd_score >= 2:
                        if "FAST" not in rec.roles:
                            rec.roles = rec.roles + ["FAST"]
                    elif spd_score <= -2:
                        if "SLOW" not in rec.roles:
                            rec.roles = rec.roles + ["SLOW"]

                # ARTY tag: long range AND meaningful building DPS.
                # Uses absolute building DPS floor (not spectrum score)
                # to avoid false positives from 1%-Verses units like VIRUS
                # that technically outrange defenses but can't scratch buildings.
                # Floor of 0.10 DPS vs buildings = can meaningfully damage structures.
                _arty_bld_dps = dps_vs(rec, ARMOR_BUILDING)
                if (bd.get("outranges_def", 0) >= 2 and _arty_bld_dps >= 0.10):
                    if "ARTY" not in rec.roles:
                        rec.roles = rec.roles + ["ARTY"]


