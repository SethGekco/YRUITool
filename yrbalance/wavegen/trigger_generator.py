"""
trigger_generator.py
====================
Generates AITriggerType entries for YR / YR-mod aimd.ini files.

An AITriggerType is the final piece of the AI pipeline:
  TaskForce (WHAT) + Script (HOW) + TeamType (CONFIG) + AITrigger (WHEN)

AITriggers control WHEN the AI creates a team — based on game conditions,
difficulty settings, and probability weights.

== FORMAT ==
Each trigger is a single line in [AITriggerTypes]:

  ID=Name,Team1,Owner,TechLevel,CondType,CondObj,Comparator,
     StartW,MinW,MaxW,Skirmish,Unused,Side,IsBaseDef,Team2,
     EnabledInE,EnabledInM,EnabledInH

Fields:
  ID          Unique hex ID (same format as TaskForce/Script/TeamType)
  Name        Human readable, no commas, max 47 chars
  Team1       Primary TeamType ID to create
  Owner       House/Country or <all>
  TechLevel   Min tech level (game recalculates from units anyway, use 1)
  CondType    Condition that must be true (-1=always, 0=enemy owns,
              1=AI owns, 2=enemy low power yellow, 3=enemy low power red,
              4=enemy credits, 5=Iron Curtain ready, 6=Chronosphere ready)
  CondObj     Unit/building ID for CondType 0/1/7, or <none>
  Comparator  64-char hex string encoding quantity + operator
              Format: QQQQQQQQOOOOOOOO + 48 zeros
              QQQQQQQQ = quantity as 4-byte little-endian hex
              OOOOOOOO = operator (0=<, 1=<=, 2==, 3=>=, 4=>, 5=!=)
              Example: >=1  = "01000000 03000000" + zeros
              Example: >=0  = "00000000 03000000" + zeros
  StartW      Starting weight (higher = more likely to fire)
  MinW        Minimum weight after decay
  MaxW        Maximum weight ceiling
  Skirmish    1=enabled in skirmish/multiplayer, 0=campaign only
  Unused      Always 0
  Side        1=Allied/GDI, 2=Soviet/NOD, 3=Yuri, 0=any side
  IsBaseDef   Unused, always 0
  Team2       Support TeamType ID or <none>
  EnabledInE  1=fires on Easy difficulty
  EnabledInM  1=fires on Normal difficulty
  EnabledInH  1=fires on Hard difficulty

== WEIGHT DESIGN NOTE ==
The weight adjustment system is effectively broken for attack teams:
action 0 (Attack) only "succeeds" when ALL targets of that type are
destroyed, which almost never happens. Triggers thus always "fail" and
decay toward MinimumWeight. Design accordingly:
  - Set MinimumWeight close to StartingWeight
  - Don't rely on weight growth for important triggers
  - Use StartingWeight to tune relative frequency

== COMPARATOR CONSTANTS ==
  ALWAYS_TRUE  = "0000000003000000" + "0"*48  (>=0, always)
  OWN_AT_LEAST_1 = "0100000003000000" + "0"*48  (>=1)
  OWN_AT_LEAST_5 = "0500000003000000" + "0"*48  (>=5)
  CRED_GE_5000   = "8813000003000000" + "0"*48  (>=5000 credits)

== TRIGGER STRATEGY ==
Attack teams:
  CondType=4 (enemy credits >=0) → always fires when AI is active
  TechLevel=1 (game recalculates from unit prereqs anyway)
  Owner=<all>, Side=faction side number
  Difficulty flags match the TeamType's difficulty tag

Pool teams:
  CondType=4, always true, all difficulties enabled
  No Team2 support needed

Support teams (Team2):
  Rush triggers paired with pool infantry team as backup
  General attack triggers paired with pool vehicle team
"""

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import re

from .team_generator import TeamEntry


# ---------------------------------------------------------------------------
# Comparator helpers
# ---------------------------------------------------------------------------

def _octet(value: int) -> str:
    """Encode a 32-bit little-endian integer as 8 hex characters."""
    return struct.pack('<I', value).hex().upper()


# Operator codes
OP_LT  = 0   # <
OP_LTE = 1   # <=
OP_EQ  = 2   # ==
OP_GTE = 3   # >=
OP_GT  = 4   # >
OP_NEQ = 5   # !=

ZEROS_48 = "0" * 48

def make_comparator(quantity: int, operator: int) -> str:
    """
    Build the 64-character comparator string.
    quantity: the value to compare against
    operator: one of OP_LT, OP_LTE, OP_EQ, OP_GTE, OP_GT, OP_NEQ
    """
    return _octet(quantity) + _octet(operator) + ZEROS_48

# Pre-built common comparators
ALWAYS_TRUE    = make_comparator(0,    OP_GTE)   # >=0, always true
OWN_AT_LEAST_1 = make_comparator(1,    OP_GTE)   # >=1
OWN_AT_LEAST_5 = make_comparator(5,    OP_GTE)   # >=5
CRED_GE_5000   = make_comparator(5000, OP_GTE)   # credits >=5000


# ---------------------------------------------------------------------------
# Condition types
# ---------------------------------------------------------------------------

COND_ALWAYS        = -1
COND_ENEMY_OWNS    =  0
COND_AI_OWNS       =  1
COND_ENEMY_PLOW_Y  =  2
COND_ENEMY_PLOW_R  =  3
COND_ENEMY_CREDITS =  4
COND_IRON_CURTAIN  =  5
COND_CHRONOSPHERE  =  6


# ---------------------------------------------------------------------------
# Weight presets by role
# ---------------------------------------------------------------------------

# (Start, Min, Max) — Start < Max everywhere so success can promote a
# trigger above its initial weight (the engine's adaptive-weight mechanic).
ROLE_WEIGHTS = {
    "RUSH":           (40.0,  10.0, 60.0),
    "RUSH_WAVE":      (40.0,  10.0, 60.0),
    "GENERAL_ATTACK": (50.0,  10.0, 70.0),
    "MINER_KILLER":   (55.0,  10.0, 80.0),
    "ANTI_DEFENSES":  (50.0,  10.0, 70.0),
    "ANTI_DEFENSES_AIR": (50.0,  10.0, 70.0),
    "ANTI_DEFENSES_BREACH": (50.0,  10.0, 70.0),
    "ANTI_BUILDING":  (50.0,  10.0, 70.0),
    "ANTI_VEHICLE":   (50.0,  10.0, 70.0),
    "ANTI_INF":       (50.0,  10.0, 70.0),
    "FACTORY_KILLER": (50.0,  10.0, 70.0),
    "ARTILLERY":      (50.0,  10.0, 70.0),
    "AA":             (35.0,  10.0, 55.0),
    "NAVAL":          (50.0,  10.0, 70.0),
    "HIGH_DPS":       (50.0,  10.0, 70.0),
    "POOL":           (30.0,  10.0, 50.0),
    "PATROL":         (30.0,  10.0, 50.0),
    "CAPTURE_TECH":   (55.0,  10.0, 80.0),
}

DEFAULT_WEIGHTS = (50.0, 10.0, 70.0)


# ---------------------------------------------------------------------------
# Side number mapping
# ---------------------------------------------------------------------------

def build_side_map(rulesmd_path: str | Path) -> dict[str, int]:
    """
    Read [Sides] from rulesmd.ini and return faction -> side number.
    Side numbers are 1-based (index in Sides list + 1).
    Side 0 = any side.
    """
    from .rulesmd_parser import IniParser
    ini    = IniParser(str(rulesmd_path))
    result = {}

    # Read [Sides] as ordered key=value pairs
    # Each key is a side name, value is comma-separated countries
    sides_section = ini.raw_section("Sides") if hasattr(ini, 'raw_section') else {}

    # Fallback: parse directly
    sides_order = []
    path = Path(rulesmd_path)
    in_sides = False
    with path.open(encoding='latin-1') as f:
        for line in f:
            s = line.strip()
            if s == '[Sides]':
                in_sides = True
                continue
            if in_sides and s.startswith('['):
                break
            if in_sides and '=' in s and not s.startswith(';'):
                side_name, _, rest = s.partition('=')
                # Strip inline comments (;...) from the value
                countries_str = rest.split(';')[0]
                side_name  = side_name.strip()
                countries  = [c.strip() for c in countries_str.split(',')
                              if c.strip()]
                sides_order.append((side_name, countries))

    # Build faction -> side number
    for side_num, (side_name, countries) in enumerate(sides_order, start=1):
        # The side name itself is used as the faction key
        result[side_name.lower()] = side_num
        # Also map each country to the same side
        for country in countries:
            result[country.lower()] = side_num

    return result


# ---------------------------------------------------------------------------
# AITrigger entry
# ---------------------------------------------------------------------------

@dataclass
class TriggerEntry:
    trigger_id:  str
    name:        str
    team1_id:    str
    owner:       str          # <all> or country name
    tech_level:  int
    cond_type:   int
    cond_obj:    str          # unit/building ID or <none>
    comparator:  str          # 64-char hex
    start_w:     float
    min_w:       float
    max_w:       float
    skirmish:    int          # 1=yes
    side:        int          # 0=any, 1=GDI, 2=NOD, 3=Yuri
    team2_id:    str          # <none> or support TeamType ID
    easy:        int
    normal:      int
    hard:        int
    is_pool:     bool = False
    aiext_conditions: Optional[object] = None  # AIExtConditions | None

    def to_ini_line(self) -> str:
        """Serialize to the single-line AITriggerTypes format."""
        name_safe = self.name[:47].replace(',', ' ')
        return (
            f"{self.trigger_id}={name_safe},"
            f"{self.team1_id},"
            f"{self.owner},"
            f"{self.tech_level},"
            f"{self.cond_type},"
            f"{self.cond_obj},"
            f"{self.comparator},"
            f"{self.start_w:.6f},"
            f"{self.min_w:.6f},"
            f"{self.max_w:.6f},"
            f"{self.skirmish},"
            f"0,"                   # unused
            f"{self.side},"
            f"0,"                   # IsBaseDefense (unused)
            f"{self.team2_id},"
            f"{self.easy},"
            f"{self.normal},"
            f"{self.hard}"
        )


# ---------------------------------------------------------------------------
# Trigger ID registry
# ---------------------------------------------------------------------------

class TriggerRegistry:
    """
    Tracks AITriggerType IDs and names.
    Uses GlobalIDRegistry for global uniqueness.
    Note: AITriggerType IDs appear in [AITriggerTypes] lines, not as
    [SECTION] headers — but they still must be globally unique.
    """

    # AITrigger IDs appear as keys in the list section
    _ID_PATTERN = re.compile(r"^([0-9A-Fa-f]{8}-G)=")

    def __init__(self, global_registry, existing_names: set[str] | None = None):
        from .id_registry import GlobalIDRegistry
        self._global = global_registry
        self._names: set[str] = set(existing_names or [])

    @classmethod
    def from_aimd(cls, aimd_path: str | Path, global_registry=None) -> "TriggerRegistry":
        from .id_registry import GlobalIDRegistry as GIR
        if global_registry is None:
            global_registry = GIR.from_aimd(aimd_path)

        existing_names: set[str] = set()
        path = Path(aimd_path)
        if path.exists():
            in_section = False
            with path.open(encoding='latin-1') as f:
                for line in f:
                    s = line.strip()
                    if s == '[AITriggerTypes]':
                        in_section = True
                        continue
                    if in_section and s.startswith('['):
                        break
                    if in_section:
                        m = cls._ID_PATTERN.match(s)
                        if m:
                            # Register ID with global registry
                            global_registry.register(m.group(1))
                            # Extract name (first field after ID=)
                            rest = s[len(m.group(0)):]
                            name = rest.split(',')[0].strip()[:47]
                            if name:
                                existing_names.add(name.lower())

        return cls(global_registry, existing_names)

    def generate(self) -> str:
        return self._global.generate()

    def unique_name(self, name: str) -> str:
        clean = name[:47].replace(',', ' ')
        if clean.lower() not in self._names:
            self._names.add(clean.lower())
            return clean
        i = 2
        while True:
            candidate = f"{name[:44]} {i}"
            if candidate.lower() not in self._names:
                self._names.add(candidate.lower())
                return candidate
            i += 1


# ---------------------------------------------------------------------------
# Trigger generator
# ---------------------------------------------------------------------------

class TriggerGenerator:
    """
    Generates AITriggerType entries for each TeamType.

    Each attack TeamType gets one trigger controlling when it fires.
    Pool teams get always-true triggers with all difficulties enabled.
    Rush triggers are paired with pool infantry as Team2 support.
    """

    def __init__(self, registry: TriggerRegistry,
                 side_map: dict[str, int],
                 pool_teams: list[TeamEntry] | None = None,
                 seed: int | None = None):
        import random
        self.registry   = registry
        self.side_map   = side_map
        self.rng        = random.Random(seed)
        # Pool teams indexed by (faction, unit_type) for Team2 lookup
        self._pool_index: dict[tuple[str,str], str] = {}
        if pool_teams:
            for pt in pool_teams:
                # label like ; [GDI][POOL][EARLY_INF]
                labels = set(re.findall(r'\[([^\]]+)\]', pt.label_comment))
                is_inf = any('INF' in l for l in labels)
                is_veh = any('VEH' in l for l in labels)
                cat    = 'infantry' if is_inf else ('vehicle' if is_veh else '')
                if cat:
                    self._pool_index[(pt.faction, cat)] = pt.team_id

    def _find_support_team(self, faction: str, role: str) -> str:
        """Find best pool team to use as Team2 support."""
        # Rush waves: infantry pool support
        # Vehicle waves: vehicle pool support
        if role in ('RUSH', 'RUSH_WAVE', 'ANTI_INF'):
            t = self._pool_index.get((faction, 'infantry'))
        else:
            t = self._pool_index.get((faction, 'vehicle'))
        return t or '<none>'

    def _difficulty_flags(self, difficulty: str) -> tuple[int, int, int]:
        """Return (easy, medium, hard) enable flags for a given difficulty.

        Vanilla pattern: triggers fire on their tier AND all HARDER tiers.
        - Easy triggers:   1,1,1  (Easy AI, Medium AI, and Hard AI all use them)
        - Medium triggers: 0,1,1  (Medium AI and Hard AI use them)
        - Hard triggers:   0,0,1  (Hard AI only)
        - all:             1,1,1

        This means Hard AI gets the widest pool of triggers (all three tiers),
        Easy AI only gets the Easy-tagged triggers.
        """
        return {
            'Easy':   (1, 1, 1),
            'Medium': (0, 1, 1),
            'Hard':   (0, 0, 1),
            'all':    (1, 1, 1),
        }.get(difficulty, (0, 0, 1))

    def generate_for_teams(self,
                            teams: list[TeamEntry]) -> list[TriggerEntry]:
        """Generate one AITrigger per TeamType."""
        triggers = []

        for team in teams:
            if team.is_pool:
                trigger = self._make_pool_trigger(team)
            else:
                trigger = self._make_attack_trigger(team)
            triggers.append(trigger)

        return triggers

    def _make_attack_trigger(self, team: TeamEntry) -> TriggerEntry:
        role       = team.role
        faction    = team.faction
        difficulty = team.difficulty
        side       = self.side_map.get(faction, 0)

        start_w, min_w, max_w = ROLE_WEIGHTS.get(role, DEFAULT_WEIGHTS)
        easy, normal, hard    = self._difficulty_flags(difficulty)
        team2                 = self._find_support_team(faction, role)

        # Name: max 47 chars, no commas
        raw_name = (f"{faction.upper()} {team.phase} {role.replace('_',' ').title()}"
                    f" {difficulty}")
        name = self.registry.unique_name(raw_name)

        # Condition: enemy credits >=0 (always true in practice)
        # Simple and reliable — avoids tech-tree detection issues
        cond_type  = COND_ENEMY_CREDITS
        cond_obj   = '<none>'
        comparator = ALWAYS_TRUE

        return TriggerEntry(
            trigger_id = self.registry.generate(),
            name       = name,
            team1_id   = team.team_id,
            owner      = '<all>',
            tech_level = 1,
            cond_type  = cond_type,
            cond_obj   = cond_obj,
            comparator = comparator,
            start_w    = start_w,
            min_w      = min_w,
            max_w      = max_w,
            skirmish   = 1,
            side       = side,
            team2_id   = team2,
            easy       = easy,
            normal     = normal,
            hard       = hard,
        )

    def _make_pool_trigger(self, team: TeamEntry) -> TriggerEntry:
        faction = team.faction
        side    = self.side_map.get(faction, 0)
        start_w, min_w, max_w = ROLE_WEIGHTS['POOL']

        # Extract unit category from pool team label comment
        labels   = set(re.findall(r'\[([^\]]+)\]', team.label_comment))
        cat_hint = 'Inf' if any('INF' in l for l in labels) else \
                   'Veh' if any('VEH' in l for l in labels) else ''
        raw_name = (f"{faction.upper()} Pool {team.phase}"
                    + (f" {cat_hint}" if cat_hint else ""))
        name     = self.registry.unique_name(raw_name)

        return TriggerEntry(
            trigger_id = self.registry.generate(),
            name       = name,
            team1_id   = team.team_id,
            owner      = '<all>',
            tech_level = 1,
            cond_type  = COND_ENEMY_CREDITS,
            cond_obj   = '<none>',
            comparator = ALWAYS_TRUE,
            start_w    = start_w,
            min_w      = min_w,
            max_w      = max_w,
            skirmish   = 1,
            side       = side,
            team2_id   = '<none>',
            easy       = 1,
            normal     = 1,
            hard       = 1,
            is_pool    = True,
        )

    def write_to_ini(self, triggers: list[TriggerEntry],
                     aimd_path: str | Path,
                     output_path: str | Path | None = None) -> Path:
        """
        Write AITriggerTypes to aimd.ini.
        Appends new trigger lines to the [AITriggerTypes] section.
        Also updates [AITriggerTypesEnable] with the new IDs.
        """
        aimd_path   = Path(aimd_path)
        output_path = Path(output_path) if output_path else aimd_path

        lines = []
        if aimd_path.exists():
            with aimd_path.open(encoding='latin-1') as f:
                lines = f.readlines()

        while lines and lines[-1].strip() in (';', ''):
            lines.pop()

        # Find [AITriggerTypes] section end
        at_idx = None
        for i, line in enumerate(lines):
            if line.strip() == '[AITriggerTypes]':
                at_idx = i
                break

        if at_idx is None:
            lines.append('\r\n[AITriggerTypes]\r\n')
            at_idx = len(lines) - 1

        # Find end of section
        end_idx = at_idx + 1
        while end_idx < len(lines):
            s = lines[end_idx].strip()
            if s.startswith('[') and s != '[AITriggerTypes]':
                break
            end_idx += 1

        # Insert new trigger lines before section end
        new_lines = []
        for t in triggers:
            comment = '; YR-AI-Tool'
            if t.is_pool:
                comment += ' [POOL]'
            new_lines.append(f"{t.to_ini_line()}  {comment}\r\n")

        lines[end_idx:end_idx] = new_lines

        lines.append(';\r\n')

        with output_path.open('w', encoding='latin-1') as f:
            f.writelines(lines)

        return output_path
