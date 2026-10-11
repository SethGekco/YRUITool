"""
wave_generator.py
=================
Generates the main AI attack wave system:
  - N general triggers per faction per difficulty
  - M country-exclusive triggers per country per difficulty

This is separate from the strategic/conditional trigger system
(defense detection, no-AA aerial, MCV rebuild etc.) and handles
the bread-and-butter attack waves that define AI aggression.

== DESIGN ==

General waves (per faction × difficulty):
  Roles distributed evenly across N slots.
  Phases distributed evenly (Early → Mid → Late → EndGame cycling).
  Script matched to role via label_tags.

Country-exclusive waves (per country × difficulty):
  Only for countries WITH unique units.
  Compositions anchored to the country's unique unit.
  Same role/phase cycling as general waves.
  Script matched same way.

== ROLE DISTRIBUTION ==
Default role pool (cycled to fill N slots):
  RUSH, RUSH,
  GENERAL_ATTACK, GENERAL_ATTACK, GENERAL_ATTACK, GENERAL_ATTACK,
  ANTI_DEFENSES,
  MINER_KILLER, MINER_KILLER,
  FACTORY_KILLER,
  AA,
  AERIAL,
  NAVAL,
  CY_KILLER

If a role produces no eligible units (e.g. NAVAL on landlocked faction),
it falls back to GENERAL_ATTACK.

TODO: Make role pool user-configurable.
TODO: Make phase distribution user-configurable.
"""

from dataclasses import dataclass, field
from itertools import cycle

from .rulesmd_parser import RulesMdParser
from .taskforce_generator import (
    TaskforceGenerator, IDRegistry, TaskforceSpec,
    GeneratorConfig, TaskforceEntry,
)
from .script_generator import ScriptGenerator, ScriptRegistry, ScriptEntry
from .team_generator import TeamGenerator, TeamRegistry, TeamEntry
from .trigger_generator import (
    TriggerGenerator, TriggerRegistry, TriggerEntry,
    COND_ENEMY_CREDITS, ALWAYS_TRUE, OWN_AT_LEAST_1,
    make_comparator, OP_GTE,
)


# ---------------------------------------------------------------------------
# Role pool and phase pool
# ---------------------------------------------------------------------------

# Default role pool -- cycled to fill N general wave slots
DEFAULT_ROLE_POOL = [
    "RUSH",
    "INFANTRY_RUSH_EARLY",
    "GENERAL_ATTACK",
    "GENERAL_ATTACK",
    "GENERAL_ATTACK",
    "GENERAL_ATTACK",
    "ANTI_DEFENSES",
    "MINER_KILLER",
    "MINER_KILLER",
    "FACTORY_KILLER",
    "AA",
    "AERIAL_RUSH",
    "AERIAL_WAVE",
    "AERIAL_ASSAULT",
    "NAVAL",
    "CY_KILLER",
]

# Phase pool -- cycled to fill N slots
DEFAULT_PHASE_POOL = ["Rush", "Early", "Mid", "Mid", "Late", "Late", "EndGame"]

# Role → fallback if no eligible units found
ROLE_FALLBACKS = {
    "NAVAL":          "GENERAL_ATTACK",
    "AERIAL_RUSH":    "AERIAL_WAVE",
    "AERIAL_WAVE":    "AERIAL_ASSAULT",
    "AERIAL_ASSAULT": "GENERAL_ATTACK",
    "AA":             "GENERAL_ATTACK",
    "INFANTRY_RUSH_EARLY": "RUSH",
    "CY_KILLER":      "FACTORY_KILLER",
}

# Difficulty → weight settings
# (StartWeight, MinWeight, MaxWeight). Start sits BELOW Max so the engine's
# built-in success/failure feedback can PROMOTE triggers that work, not just
# punish failures — teams that succeed get picked more often ("experience").
DIFFICULTY_WEIGHTS = {
    "Easy":   (60.0,  20.0,  100.0),
    "Medium": (120.0, 40.0,  200.0),
    "Hard":   (250.0, 80.0,  500.0),
}

# Difficulty → AITriggerType enable flags (cumulative, not exclusive).
# Easy triggers fire on all difficulties; Medium on Medium+Hard; Hard on Hard only.
# This matches vanilla's pattern and means Hard AI gets the widest trigger pool.
DIFFICULTY_FLAGS = {
    "Easy":   (1, 1, 1),
    "Medium": (0, 1, 1),
    "Hard":   (0, 0, 1),
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class WaveGeneratorConfig:
    """User-facing configuration for wave generation."""
    general_waves_per_difficulty:  int = 20
    country_waves_per_difficulty:  int = 10
    difficulties: list[str]            = field(
        default_factory=lambda: ["Easy", "Medium", "Hard"])
    role_pool:    list[str]            = field(
        default_factory=lambda: list(DEFAULT_ROLE_POOL))
    phase_pool:   list[str]            = field(
        default_factory=lambda: list(DEFAULT_PHASE_POOL))
    role_counts:  dict[str, int]       = field(default_factory=dict)
    cost_ratio_tolerance: float        = 1.0   # 1.0 = disabled by default
    cost_ratio_strict:    bool         = False
    taskforce_scale:      float        = 1.0   # base multiplier for unit counts
    # Per-category scale multipliers, applied ON TOP of taskforce_scale.
    # Keys: 'rush', 'general', 'artillery', 'aerial', 'naval'.
    # e.g. {'rush': 2.0} makes rush waves twice as big as everything else.
    scale_by_group: dict                = field(default_factory=dict)
    # Ratio flattening for scaling (0.0-1.0). At 0, scaling is uniform and
    # vanilla ratios are preserved (a "1 Mastermind" support line stays token
    # even at scale 6). At 1, every line scales toward equal share. Community
    # rebalance mods sit around 0.3-0.5: specialists get boosted toward
    # parity while the spam line stays dominant.
    scale_flatten:  float               = 0.0
    # Split single-type taskforces (6+ units, non-rush) 50/50 with a
    # complementary unit of the other ground category — "20 Conscripts"
    # becomes "10 Conscripts, 10 Shock Troopers".
    diversify_mono: bool                = True
    # When a taskforce has both infantry and vehicles, and the vehicles
    # have Gunner=yes or OpenTopped=yes, tag the taskforce so the script
    # selector picks a load-then-attack script.
    auto_load_infantry: bool            = True
    # When a taskforce contains parasite-drone Vehicles (Terror Drone class)
    # and doesn't already have an OpenTopped transport, add one so the
    # drones can ride to enemy vehicles (Flak Track + Terror Drone pattern).
    auto_transport_parasites: bool      = True
    # When True the AIExt writer populates every generated trigger with a
    # full debug quad (MessageDisplay/ValueDisplay/LogMessage/LogWrite) for
    # every emitted tag + the three lifecycle overlay/log messages. Modder
    # can hand-edit the generated INI to tune specific triggers.
    debug_messages: bool                = False
    # Per-unit-category hard caps per taskforce (0 or absent = uncapped).
    # Keys: 'Infantry', 'Vehicle', 'Aircraft'. If a taskforce's total count
    # in a category exceeds the cap, counts are reduced proportionally.
    type_caps:      dict                = field(default_factory=dict)
    # Team size at/above which the LARGE_TEAM script family is used
    # ("oversized" waves skip the gather step — 10+ units block each other).
    oversized_threshold:  int          = 10
    # On Hard difficulty, Late/EndGame all-infantry waves of a few dozen or
    # fewer get one Gunner=yes vehicle (IFV-type) per infantryman plus a
    # load-then-attack script, when the faction can build one.
    gunner_hard_infantry: bool         = True
    gunner_max_infantry:  int          = 48   # "a few dozen" ceiling
    aiext_config: object | None        = None  # AIExtConfig | None
    exclude_unit_ids: frozenset        = frozenset()  # units excluded from all taskforces


# Role → scale group mapping for scale_by_group lookups.
SCALE_GROUP_OF_ROLE = {
    "RUSH":                "rush",
    "INFANTRY_RUSH_EARLY": "rush",
    "SUICIDE_RUSH":        "rush",
    "ARTILLERY":           "artillery",
    "BOMBARD":             "artillery",
    "AERIAL":              "aerial",
    "AERIAL_RUSH":         "aerial",
    "AERIAL_WAVE":         "aerial",
    "AERIAL_ASSAULT":      "aerial",
    "AERIAL_TRANSPORT":    "aerial",
    "NAVAL":               "naval",
    "ANTI_NAVAL":          "naval",
    "POOL":                "defense",
}


def scale_group_for_role(role: str) -> str:
    """Map a role name to its scale group ('general' if unmapped)."""
    if not role:
        return "general"
    return SCALE_GROUP_OF_ROLE.get(role.upper(), "general")


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

class WaveGenerator:
    """
    Generates general and country-exclusive attack wave triggers.

    Usage:
        gen = WaveGenerator(parser, global_reg, tf_reg, sc_reg,
                            team_reg, trig_reg, side_map, seed=42)
        gen.generate(config)
        gen.write_all(aimd_path, output_path)
    """

    def __init__(self, parser: RulesMdParser,
                 global_reg,
                 tf_reg:   IDRegistry,
                 sc_reg:   ScriptRegistry,
                 team_reg: TeamRegistry,
                 trig_reg: TriggerRegistry,
                 side_map: dict[str, int],
                 phobos:   bool = True,
                 seed:     int | None = None,
                 meta_map: dict | None = None,
                 scan_result: object | None = None):   # ModScanResult | None
        self.parser    = parser
        self.global_reg = global_reg
        self.tf_reg    = tf_reg
        self.sc_reg    = sc_reg
        self.team_reg  = team_reg
        self.trig_reg  = trig_reg
        self.side_map  = side_map
        self.phobos    = phobos
        self.meta_map: dict = meta_map or {}
        self.scan_result = scan_result

        # AIExt writer — collects conditions during generation,
        # writes all .AIExt sections in one pass at write_all() time
        from .aiext_generator import AIExtWriter
        self.aiext_writer = AIExtWriter()
        self._aiext_builder = None   # created lazily when config is known

        self.tf_gen   = TaskforceGenerator(parser, tf_reg, seed=seed)
        self.sc_gen   = ScriptGenerator(sc_reg, phobos=phobos, seed=seed)
        self.team_gen = TeamGenerator(parser, team_reg, seed=seed)
        self.trig_gen = TriggerGenerator(trig_reg, side_map, seed=seed)

        self.taskforces: list[TaskforceEntry] = []
        self.scripts:    list[ScriptEntry]    = []
        self.teams:      list[TeamEntry]      = []
        self.triggers:   list[TriggerEntry]   = []
        self._appended_tf_ids: set[str] = set()  # prevent duplicate taskforce writes

        self._generated_count = {'general': 0, 'country': 0}
        self._country_seen: dict[tuple, set] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, config: WaveGeneratorConfig | None = None):
        """Generate all wave triggers."""
        if config is None:
            config = WaveGeneratorConfig()

        # Store exclusion set — applied when selecting units for taskforces
        self._exclude_unit_ids: frozenset = config.exclude_unit_ids
        # LARGE_TEAM (no-gather) script threshold for oversized waves
        self._oversized_threshold = getattr(config, 'oversized_threshold', 10)

        # Initialise AIExt condition builder if scan results are available
        if self.scan_result is not None and config.aiext_config is not None:
            from .aiext_generator import AIExtConditionBuilder
            self._aiext_builder = AIExtConditionBuilder(
                self.scan_result, config.aiext_config,
                sides=getattr(self.parser, 'sides', None))
        else:
            self._aiext_builder = None

        factions = list(self.parser.sides.faction_countries.keys())

        for faction in factions:
            for difficulty in config.difficulties:
                self._generate_general_waves(
                    faction, difficulty, config)

        # Country-exclusive: only countries with unique units
        countries_with_exclusives = self._find_exclusive_countries()
        for faction, country, unique_units in countries_with_exclusives:
            for difficulty in config.difficulties:
                self._generate_country_waves(
                    faction, country, unique_units,
                    difficulty, config)

    def summary(self) -> str:
        g = self._generated_count['general']
        c = self._generated_count['country']
        return (
            f"Wave generator summary:\n"
            f"  General triggers:  {g}\n"
            f"  Country triggers:  {c}\n"
            f"  Total triggers:    {g + c}\n"
            f"  Taskforces:        {len(self.taskforces)}\n"
            f"  Scripts:           {len(self.scripts)}\n"
            f"  Teams:             {len(self.teams)}\n"
        )

    def write_all(self, aimd_path, output_path=None):
        """Write all generated entries to aimd.ini in vanilla section order:
        [TaskForces] list+sections → [ScriptTypes] list+sections →
        [TeamTypes] list+sections → [AITriggerTypes] → [ID.AIExt] sidecars
        """
        from pathlib import Path
        aimd_path   = Path(aimd_path)
        output_path = Path(output_path) if output_path else aimd_path

        # Ensure EVERY generated trigger is known to the AIExt writer before
        # we write. Many specialty generators (dogs, drones, naval, suicide,
        # transport, hunt, etc.) create triggers directly via self.triggers
        # .append without calling self.aiext_writer.collect. When debug
        # messages are enabled, those triggers otherwise emit no .AIExt
        # sidecar and can't be debugged. This backfill catches them.
        from .aiext_generator import AIExtConditions
        already_registered = {tid for tid, _ in self.aiext_writer._entries}
        for trg in self.triggers:
            if trg.trigger_id in already_registered:
                continue
            # Try to preserve any conditions the trigger may already carry
            existing_cond = getattr(trg, 'aiext_conditions', None)
            cond = existing_cond if isinstance(existing_cond, AIExtConditions) \
                                 else AIExtConditions()
            self.aiext_writer.collect(trg.trigger_id, cond, getattr(trg, 'name', None))

        # Vanilla order: TaskForces, then Scripts, then Teams, then Triggers
        if self.taskforces:
            self.tf_gen.write_to_ini(self.taskforces, output_path, output_path)
        if self.scripts:
            self.sc_gen.write_to_ini(
                [s for s in self.scripts if not s.is_duplicate],
                output_path, output_path)
        if self.teams:
            self.team_gen.write_to_ini(self.teams, output_path, output_path)
        if self.triggers:
            self.trig_gen.write_to_ini(self.triggers, output_path, output_path)
        # AIExt sidecars come after AITriggerTypes, never before
        if self.aiext_writer.count > 0:
            self.aiext_writer.write_to_ini(output_path, output_path)

    # ------------------------------------------------------------------
    # General wave generation
    # ------------------------------------------------------------------

    def _generate_general_waves(self, faction: str, difficulty: str,
                                  config: WaveGeneratorConfig):
        """Generate N general attack waves for one faction/difficulty."""
        import sys
        from .taskforce_generator import validate_cost_ratio
        n           = config.general_waves_per_difficulty
        role_cycle  = cycle(config.role_pool)
        phase_cycle = cycle(config.phase_pool)
        generated   = 0
        imbalanced  = 0

        attempts          = 0
        consecutive_fails = 0
        max_attempts      = n * 20
        max_consecutive   = max(30, len(config.role_pool) * 3)

        print(f"    {faction.upper()} {difficulty}: ", end='', flush=True)

        while generated < n and attempts < max_attempts:
            attempts += 1
            role  = next(role_cycle)
            phase = next(phase_cycle)

            tf = self._get_taskforce(faction, phase, role, difficulty,
                                      country=None)
            if tf is None:
                consecutive_fails += 1
                if consecutive_fails >= max_consecutive:
                    print(f"\r    {faction.upper()} {difficulty}: exhausted at {generated}/{n}")
                    break
                continue

            consecutive_fails = 0

            tf = self._apply_taskforce_scale(tf, config)
            tf = self._maybe_gunnerize(tf, faction, difficulty, phase, config)
            tf = self._maybe_diversify(tf, faction, phase, config)
            tf = self._maybe_load_infantry(tf, faction, config)
            tf = self._maybe_transport_parasites(tf, faction, config)

            # Cost ratio check
            if (config.cost_ratio_strict or config.cost_ratio_tolerance < 1.0):
                units_list = [(count, uid) for count, uid in tf.units]
                balanced   = validate_cost_ratio(units_list, self.parser,
                                                  strict=config.cost_ratio_strict)
                if not balanced:
                    imbalanced += 1
                    max_imbalanced = max(1, int(n * config.cost_ratio_tolerance))
                    if config.cost_ratio_strict or imbalanced > max_imbalanced:
                        continue  # skip this composition

            # If _get_taskforce fell back to a different role (e.g. no
            # aerial units → GENERAL_ATTACK), the ACTUAL role lives on
            # tf.role. Use it for label, team, and AIExt so the trigger
            # doesn't advertise itself as an aerial rush while carrying
            # a land composition.
            effective_role = getattr(tf, 'role', role) or role

            script = self._get_script_for_tf(tf)
            if script is None:
                continue
            rh_int = self._required_houses_intersection(tf.units, faction)
            if rh_int is not None and len(rh_int) == 0:
                continue  # contradictory RequiredHouses
            team    = self._create_team(tf, script, faction, phase,
                                         effective_role, difficulty)
            trigger = self._create_general_trigger(
                team, faction, difficulty,
                name=f"{faction.upper()} {effective_role} {phase} {difficulty} {generated+1}",
                tf_units=tf.units)
            if tf.tf_id not in self._appended_tf_ids:
                self._appended_tf_ids.add(tf.tf_id)
                self.taskforces.append(tf)
            self.teams.append(team)
            self.triggers.append(trigger)
            generated += 1
            self._generated_count['general'] += 1
            # Live progress — overwrite the same line with current count
            print(f"\r    {faction.upper()} {difficulty}: {generated}/{n}", end='', flush=True)

        print(f"\r    {faction.upper()} {difficulty}: done ({generated}/{n})")

    # ------------------------------------------------------------------
    # Country-exclusive wave generation
    # ------------------------------------------------------------------

    def _find_exclusive_countries(self) -> list[tuple[str, str, list[str]]]:
        """
        Return [(faction, country, [unique_unit_ids])] for countries
        that have at least one unit buildable only by them.
        """
        result = []
        for faction, countries in self.parser.sides.faction_countries.items():
            for country in countries:
                unique = [
                    r.unit_id for r in self.parser.buildable
                    if r.faction == faction
                    and r.countries == {country}
                ]
                if unique:
                    result.append((faction, country, unique))
        return result

    def _apply_taskforce_scale(self, tf, config):
        """Scale unit counts by config.taskforce_scale × the role's group
        multiplier (config.scale_by_group), then enforce per-category caps
        (config.type_caps). Any positive BuildLimit caps each line.
        Regenerates the name to match new counts. Never mutates the input
        (which may be a shared/reused registry entry)."""
        import math, dataclasses

        group      = scale_group_for_role(getattr(tf, 'role', '') or '')
        group_mult = float(config.scale_by_group.get(group, 1.0)) \
                     if config.scale_by_group else 1.0
        eff_scale  = config.taskforce_scale * group_mult

        caps = config.type_caps or {}
        if (eff_scale == 1.0 and not caps
                and getattr(config, 'scale_flatten', 0.0) <= 0.0):
            return tf

        ini = self.parser.ini
        # Ratio flattening: blend each line's multiplier between uniform
        # (preserve vanilla ratios) and equal-share (all lines end equal).
        flatten = max(0.0, min(1.0, getattr(config, 'scale_flatten', 0.0)))
        lines = list(tf.units)
        avg_count = (sum(c for c, _ in lines) / len(lines)) if lines else 1.0
        scaled_units = []
        for count, uid in lines:
            sec = ini.section(uid)
            try:
                bl = int(sec.get('buildlimit', '0') or '0') if sec else 0
            except ValueError:
                bl = 0
            if flatten > 0.0 and count > 0:
                line_mult = eff_scale * ((1.0 - flatten)
                                          + flatten * (avg_count / count))
            else:
                line_mult = eff_scale
            # Round up for scale-down (< 1), floor for scale-up, so small-
            # count support lines don't vanish when the wave is shrunk.
            raw = count * line_mult
            if line_mult < 1.0:
                new_count = max(1, math.ceil(raw))
            else:
                new_count = max(1, math.floor(raw))
            if bl > 0:
                new_count = min(new_count, bl)
            scaled_units.append((new_count, uid))

        # Per-category caps: if a category's total exceeds its cap, reduce
        # that category's lines proportionally (each line stays >= 1).
        if caps:
            from collections import defaultdict
            totals = defaultdict(int)
            cats   = {}
            for count, uid in scaled_units:
                rec = self.parser.units.get(uid)
                cat = rec.category if rec else ''
                cats[uid] = cat
                totals[cat] += count
            adjusted = []
            for count, uid in scaled_units:
                cat = cats.get(uid, '')
                cap = int(caps.get(cat, 0) or 0)
                if cap > 0 and totals[cat] > cap:
                    ratio = cap / totals[cat]
                    count = max(1, math.floor(count * ratio))
                adjusted.append((count, uid))
            scaled_units = adjusted

        if scaled_units == list(tf.units):
            return tf
        # Composition changed → this must become a DISTINCT taskforce.
        # Reusing the original tf_id would let the first-written variant
        # silently stand in for every trigger that shares the id (group
        # multipliers can scale the same base comp differently per role).
        existing_id = self.tf_reg.find_duplicate(scaled_units)
        if existing_id:
            new_id = existing_id            # identical scaled variant exists
        else:
            new_id = self.tf_reg.generate()
            self.tf_reg.register_composition(new_id, scaled_units)
        tf = dataclasses.replace(tf, tf_id=new_id, units=scaled_units)
        try:
            from .taskforce_generator import make_vanilla_name
            tf = dataclasses.replace(
                tf, name=make_vanilla_name(scaled_units, self.parser,
                                            self.tf_reg))
        except Exception:
            pass  # keep old name if renaming fails
        return tf

    @staticmethod
    def _is_flying_transport(rec) -> bool:
        """True for Aircraft/Jumpjet-mobility transports (e.g. a Jumpjet
        VehicleType like a BlackHawk). These must LAND before ground
        passengers can board, but the AI passengers crowd the landing cell
        while chasing the transport — it can never touch down and the team
        softlocks in a land-retry loop. Load-based waves must never pick
        them."""
        return getattr(rec, 'mobility', 'Land') in ('Aircraft', 'Jumpjet')

    def _find_gunner_vehicle(self, faction: str) -> str | None:
        """Cheapest buildable Gunner=yes vehicle for this faction, or None.
        Gunner=yes vehicles (IFV mechanic) change turret based on the single
        infantry passenger inside."""
        cache = getattr(self, '_gunner_cache', None)
        if cache is None:
            cache = self._gunner_cache = {}
        if faction in cache:
            return cache[faction]
        best_uid, best_cost = None, None
        for rec in self.parser.buildable:
            if rec.faction != faction:
                continue
            sec = self.parser.ini.section(rec.unit_id)
            if not sec:
                continue
            if sec.get('gunner', 'no').strip().lower() != 'yes':
                continue
            if self._is_flying_transport(rec):
                continue   # boarding softlock — see _is_flying_transport
            cost = getattr(rec, 'cost', 0) or 0
            if best_uid is None or cost < best_cost:
                best_uid, best_cost = rec.unit_id, cost
        cache[faction] = best_uid
        return best_uid

    def _maybe_load_infantry(self, tf, faction: str, config):
        """When a taskforce has BOTH infantry and vehicles, and any of the
        vehicles have Gunner=yes or OpenTopped=yes (transport-with-cargo),
        tag the taskforce so the script selector picks a load-then-attack
        script.

        Size heuristic: groups of ≤16 infantry can use smaller (Gunner)
        transports; larger groups prefer OpenTopped vehicles with higher
        passenger caps.

        This does NOT reshuffle the composition — the Load Onto Transports
        script (14,0 in vanilla or 10103,0 in Phobos) handles the actual
        boarding at runtime. We just add a [LOAD_INFANTRY] tag so the
        script picker knows to prepend the load action.
        """
        if not getattr(config, 'auto_load_infantry', True):
            return tf
        units = list(tf.units or [])
        if len(units) < 2:
            return tf   # not a mixed composition

        # Count infantry vs vehicles
        infantry_total = 0
        vehicle_ids: list[str] = []
        for count, uid in units:
            rec = self.parser.units.get(uid)
            if rec is None:
                return tf
            if rec.category == 'Infantry':
                infantry_total += count
            elif rec.category == 'Vehicle':
                vehicle_ids.append(uid)
        if infantry_total == 0 or not vehicle_ids:
            return tf   # not a mixed comp

        # Any vehicle Gunner=yes or OpenTopped=yes?
        ini = self.parser.ini
        has_gunner    = False
        has_opentop   = False
        for uid in vehicle_ids:
            sec = ini.section(uid)
            if not sec:
                continue
            rec = self.parser.units.get(uid)
            if rec is not None and self._is_flying_transport(rec):
                continue   # boarding softlock — never tag flyers as loadable
            if sec.get('gunner', 'no').strip().lower() == 'yes':
                has_gunner = True
            if sec.get('opentopped', 'no').strip().lower() == 'yes':
                has_opentop = True

        if not (has_gunner or has_opentop):
            return tf

        # Size heuristic: ≤16 → smaller transport preferred (Gunner OK);
        # larger → OpenTopped preferred if available.
        size_bucket = 'SMALL' if infantry_total <= 16 else 'LARGE'
        prefer_opentop = size_bucket == 'LARGE' and has_opentop

        import dataclasses
        label = (tf.label_comment or '').rstrip()
        tag = ' [LOAD_INFANTRY]'
        if prefer_opentop:
            tag += ' [LOAD_OPENTOP]'
        elif has_gunner:
            tag += ' [LOAD_GUNNER]'
        for t in ('[LOAD_INFANTRY]', '[LOAD_OPENTOP]', '[LOAD_GUNNER]'):
            if t in label:
                return tf   # already tagged
        return dataclasses.replace(tf, label_comment=label + tag)

    def _find_parasite_carrier(self, faction: str, exclude_uids: set,
                                 drone_size: int = 1):
        """Return the best Vehicle transport buildable by the given faction
        that can carry parasite drones. 'Best' = highest passenger capacity.

        The strategy is drop-bomb: the transport is expendable delivery,
        not a firing platform. Any Vehicle transport with Passengers >= 1
        and SizeLimit >= drone_size qualifies — OpenTopped is not required.
        When the transport dies engaged with the enemy, its passengers
        spill out and the parasite drones latch onto nearby enemy
        vehicles. Since the AI has flexible money, sacrificing the
        transport is an acceptable trade.

        In vanilla YR the Flak Track has Passengers=5, SizeLimit=1, so it
        satisfies. So do Battle Fortress, Amphibious Transport, IFV,
        and Hover Transport. Mods may add others.

        Returns unit_id or None.
        """
        ini = self.parser.ini
        best_uid: str | None = None
        best_capacity = 0
        for rec in self.parser.buildable:
            if rec.unit_id in exclude_uids:
                continue
            if rec.category != 'Vehicle':
                continue
            if not getattr(rec, 'is_transport', False):
                continue
            if self._is_flying_transport(rec):
                continue   # boarding softlock — see _is_flying_transport
            if faction and getattr(rec, 'faction', None) not in (faction, None):
                continue
            sec = ini.section(rec.unit_id)
            if not sec:
                continue
            try:
                cap = int(sec.get('passengers', '0') or '0')
            except ValueError:
                cap = 0
            if cap < 1:
                continue
            # SizeLimit filter: transport must be big enough for the drone.
            # Default SizeLimit=1 covers vanilla-size drones; larger drones
            # in mods need a bigger carrier.
            try:
                size_limit = int(sec.get('sizelimit', '1') or '1')
            except ValueError:
                size_limit = 1
            if size_limit < drone_size:
                continue
            if cap > best_capacity:
                best_capacity = cap
                best_uid = rec.unit_id
        return best_uid

    def _maybe_transport_parasites(self, tf, faction: str, config):
        """When a taskforce contains parasite-drone Vehicles (Terror Drone
        class in vanilla, or any Vehicle with a parasite warhead in mods)
        and lacks a transport, add one.

        The strategy is drop-bomb delivery: the transport carries the
        parasite drones toward enemy vehicles, engages, and dies. When it
        dies, the drones spill out and latch onto nearby enemy tanks. The
        AI can afford to sacrifice the transport since its money is
        flexible. From the player's perspective this is a frustratingly
        effective anti-armor tactic — a swarm of drones appearing suddenly
        inside a tank formation.

        The transport does NOT need to be OpenTopped; any Vehicle transport
        with sufficient passenger capacity qualifies. Post-loading the
        drones fight from wherever the transport dies.

        Runs after gunnerize / diversify / load_infantry so it inspects
        the final composition. Adds one carrier per full passenger
        capacity of drones (rounded up).
        """
        if not getattr(config, 'auto_transport_parasites', True):
            return tf

        units = list(tf.units or [])
        if not units:
            return tf

        # Find parasite drones in the composition + check if a suitable
        # transport is already present
        drone_ids: list[tuple[int, str]] = []
        existing_uids: set = set()
        already_has_carrier = False
        ini = self.parser.ini
        for count, uid in units:
            existing_uids.add(uid)
            rec = self.parser.units.get(uid)
            if rec is None:
                continue
            if getattr(rec, 'is_parasite_drone', False):
                drone_ids.append((count, uid))
                continue
            # Any Vehicle transport with passengers >= 1 already fills the
            # carrier role — no need to add another.
            sec = ini.section(uid)
            if (sec and rec.category == 'Vehicle'
                    and getattr(rec, 'is_transport', False)):
                try:
                    cap = int(sec.get('passengers', '0') or '0')
                except ValueError:
                    cap = 0
                if cap >= 1:
                    already_has_carrier = True

        if not drone_ids:
            return tf
        if already_has_carrier:
            return tf

        # Determine the drone size for SizeLimit matching (vanilla drone
        # size=1; larger custom drones need bigger transports)
        max_drone_size = 1
        for _, uid in drone_ids:
            sec = ini.section(uid)
            if not sec:
                continue
            try:
                sz = int(sec.get('size', '1') or '1')
            except ValueError:
                sz = 1
            max_drone_size = max(max_drone_size, sz)

        transport_uid = self._find_parasite_carrier(
            faction, existing_uids, drone_size=max_drone_size)
        if not transport_uid:
            return tf

        # One transport per full passenger capacity of drones
        total_drones = sum(c for c, _ in drone_ids)
        transport_sec = ini.section(transport_uid)
        try:
            cap = int(transport_sec.get('passengers', '5') or '5')
        except ValueError:
            cap = 5
        n_transport = max(1, (total_drones + cap - 1) // cap)

        # Respect transport build limit
        try:
            bl = int(transport_sec.get('buildlimit', '0') or '0')
        except ValueError:
            bl = 0
        if bl > 0:
            n_transport = min(n_transport, bl)

        import dataclasses
        new_units = list(units) + [(n_transport, transport_uid)]

        label = (tf.label_comment or '').rstrip()
        # PARASITE_TRANSPORT tags this specific pattern; LOAD_INFANTRY makes
        # the existing load-transport script templates fire (the 14,0 and
        # 10103,0 actions work for any passenger type).
        if '[PARASITE_TRANSPORT]' in label:
            return dataclasses.replace(tf, units=new_units)
        tag = ' [PARASITE_TRANSPORT]'
        if '[LOAD_INFANTRY]' not in label:
            tag += ' [LOAD_INFANTRY]'
        return dataclasses.replace(tf, units=new_units,
                                    label_comment=label + tag)

    def _maybe_gunnerize(self, tf, faction: str, difficulty: str,
                          phase: str, config):
        """On Hard, pair small late-game all-infantry waves with Gunner=yes
        vehicles (one per infantryman) and tag the taskforce [GUNNER] so the
        script selector picks the load-then-attack script."""
        if not getattr(config, 'gunner_hard_infantry', True):
            return tf
        if difficulty != 'Hard' or phase not in ('Late', 'EndGame'):
            return tf
        units = list(tf.units or [])
        if not units:
            return tf
        total = 0
        for count, uid in units:
            rec = self.parser.units.get(uid)
            if rec is None or rec.category != 'Infantry':
                return tf   # mixed/unknown composition — leave it alone
            total += count
        if total > getattr(config, 'gunner_max_infantry', 48):
            return tf
        gunner_uid = self._find_gunner_vehicle(faction)
        if not gunner_uid:
            return tf
        if any(uid == gunner_uid for _, uid in units):
            return tf
        import dataclasses
        new_units = units + [(total, gunner_uid)]   # one IFV per infantryman
        label = (tf.label_comment or '').rstrip()
        if '[GUNNER]' not in label:
            label = label + ' [GUNNER]'
        existing_id = self.tf_reg.find_duplicate(new_units)
        if existing_id:
            new_id = existing_id
        else:
            new_id = self.tf_reg.generate()
            self.tf_reg.register_composition(new_id, new_units)
        tf = dataclasses.replace(tf, tf_id=new_id, units=new_units,
                                  label_comment=label)
        try:
            from .taskforce_generator import make_vanilla_name
            tf = dataclasses.replace(
                tf, name=make_vanilla_name(new_units, self.parser, self.tf_reg))
        except Exception:
            pass
        return tf

    def _maybe_diversify(self, tf, faction: str, phase: str, config):
        """Split a single-type taskforce 50/50 with a complementary unit —
        '20 Conscripts' → '10 Conscripts, 10 Shock Troopers'. Mirrors the
        combined-arms rebalance pattern from community AI mods. Applies to
        6+ unit mono-type forces outside rush phases; skips GUNNER packs."""
        if not getattr(config, 'diversify_mono', True):
            return tf
        units = list(tf.units or [])
        if len(units) != 1:
            return tf
        count, uid = units[0]
        if count < 6:
            return tf
        if phase in ('Rush',) or 'RUSH' in (getattr(tf, 'role', '') or '').upper():
            return tf
        if '[GUNNER]' in (tf.label_comment or ''):
            return tf
        base = self.parser.units.get(uid)
        if base is None or getattr(base, 'is_transport', False):
            return tf
        if base.mobility in ('Aircraft', 'Jumpjet'):
            return tf   # aerial comps stay pure

        # Complement: same faction, mobility-compatible, different category
        # (Infantry↔Vehicle), combat-capable, best grade.
        try:
            from .taskforce_generator import _filter_compatible_mobility
        except ImportError:
            return tf
        want_cat = 'Vehicle' if base.category == 'Infantry' else 'Infantry'
        best, best_grade = None, None
        for rec in self.parser.buildable:
            if rec.faction != faction or rec.unit_id == uid:
                continue
            if rec.category != want_cat:
                continue
            if getattr(rec, 'is_transport', False):
                continue
            if not _filter_compatible_mobility([rec], base.mobility):
                continue
            grade = getattr(rec, 'grade', 0) or 0
            if grade <= 0:
                continue
            if best is None or grade > best_grade:
                best, best_grade = rec, grade
        if best is None:
            return tf

        import math, dataclasses
        half = max(1, count // 2)
        sec = self.parser.ini.section(best.unit_id)
        try:
            bl = int(sec.get('buildlimit', '0') or '0') if sec else 0
        except ValueError:
            bl = 0
        comp_count = min(half, bl) if bl > 0 else half
        new_units = [(count - half, uid), (comp_count, best.unit_id)]

        existing_id = self.tf_reg.find_duplicate(new_units)
        if existing_id:
            new_id = existing_id
        else:
            new_id = self.tf_reg.generate()
            self.tf_reg.register_composition(new_id, new_units)
        label = (tf.label_comment or '').rstrip()
        if '[MIXED]' not in label:
            label += ' [MIXED]'
        tf = dataclasses.replace(tf, tf_id=new_id, units=new_units,
                                  label_comment=label)
        try:
            from .taskforce_generator import make_vanilla_name
            tf = dataclasses.replace(
                tf, name=make_vanilla_name(new_units, self.parser, self.tf_reg))
        except Exception:
            pass
        return tf

    def _generate_country_waves(self, faction: str, country: str,
                                  unique_units: list[str],
                                  difficulty: str,
                                  config: WaveGeneratorConfig):
        """Generate M country-exclusive waves anchored to unique unit."""
        m           = config.country_waves_per_difficulty
        role_cycle  = cycle(config.role_pool)
        phase_cycle = cycle(config.phase_pool)
        generated   = 0

        attempts     = 0
        max_attempts = m * 4   # cap to prevent infinite loop when compositions exhausted
        while generated < m and attempts < max_attempts:
            attempts += 1
            role  = next(role_cycle)
            phase = next(phase_cycle)

            tf = self._get_taskforce(faction, phase, role, difficulty,
                                      country=country)
            if tf is None:
                continue
            tf = self._apply_taskforce_scale(tf, config)
            tf = self._maybe_gunnerize(tf, faction, difficulty, phase, config)
            tf = self._maybe_diversify(tf, faction, phase, config)
            tf = self._maybe_load_infantry(tf, faction, config)
            tf = self._maybe_transport_parasites(tf, faction, config)

            script = self._get_script_for_tf(tf)
            if script is None:
                continue
            rh_int = self._required_houses_intersection(tf.units, faction)
            if rh_int is not None and len(rh_int) == 0:
                continue  # contradictory RequiredHouses
            team = self._create_team(
                tf, script, faction, phase, role, difficulty,
                country=country)
            trigger = self._create_country_trigger(
                team, faction, country, difficulty,
                name=f"{country} {role} {phase} {difficulty} {generated+1}",
                tf_units=tf.units)

            if tf.tf_id not in self._appended_tf_ids:
                self._appended_tf_ids.add(tf.tf_id)
                self.taskforces.append(tf)
            self.teams.append(team)
            self.triggers.append(trigger)
            generated += 1
            self._generated_count['country'] += 1

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _tech_level_for_units(self, units: list[tuple[int,str]]) -> int:
        """
        Return the highest TechLevel among all units in the taskforce.
        Clamped to at least 1 and at most 10 (TL>=11 is engine hard cap,
        impossible even for AI; TL<1 units are AI-buildable but shouldn't
        set the trigger's TechLevel above 1).
        """
        ini = self.parser.ini
        max_tl = 0
        for _, uid in units:
            s = ini.section(uid)
            if not s:
                continue
            try:
                tl = int(s.get('techlevel', '1') or '1')
            except ValueError:
                tl = 1
            # Only count TL in valid human range (1-10)
            if 1 <= tl <= 10 and tl > max_tl:
                max_tl = tl
        return max(max_tl, 1)

    def _cond_obj_for_units(self, units: list[tuple[int,str]]) -> tuple[int, str]:
        """
        Return (cond_type, cond_obj) for a trigger based on the units in the taskforce.

        If any unit has a meta-dummy (Prerequisite.Negative), the trigger
        should use CondType=1 (AI owns) + CondObj=meta_dummy_section_id.
        This ensures the AI only recruits the team when the full prereq
        state (positive AND NOT negative) is satisfied.

        If multiple units have meta-dummies, use the first one found
        (any is sufficient — if one gate is unmet, the AI shouldn't recruit).

        Returns (COND_AI_OWNS=1, meta_section_id) or (COND_ENEMY_CREDITS=4, '<none>').
        """
        from .trigger_generator import COND_AI_OWNS, COND_ENEMY_CREDITS
        for _, uid in units:
            if uid in self.meta_map:
                meta = self.meta_map[uid]
                return COND_AI_OWNS, meta.section_id
        return COND_ENEMY_CREDITS, '<none>'

    def _required_houses_intersection(self, units, faction):
        """None = no restriction, frozenset = compatible countries (empty = contradictory)."""
        ini = self.parser.ini
        fc  = frozenset(self.parser.sides.faction_countries.get(faction, []))
        eff = None
        for _, uid in units:
            s = ini.section(uid)
            if not s: continue
            rh_raw = s.get('requiredhouses', '')
            if not rh_raw.strip(): continue
            rh = frozenset(h.strip() for h in rh_raw.split(',') if h.strip())
            eff = (rh & fc) if eff is None else eff & (rh & fc)
        return eff

    def _effective_owner_for_units(self, units, faction):
        """Returns OwnerHouse string for trigger. '<all>' or specific country."""
        eff = self._required_houses_intersection(units, faction)
        if eff is None or len(eff) == 0:
            return '<all>'
        if len(eff) == 1:
            return next(iter(eff))
        fc = self.parser.sides.faction_countries.get(faction, [])
        if eff == frozenset(fc):
            return '<all>'
        for c in fc:
            if c in eff:
                return c
        return '<all>'

    def _get_taskforce(self, faction: str, phase: str, role: str,
                        difficulty: str,
                        country: str | None) -> TaskforceEntry | None:
        """
        Generate one taskforce. Falls back to GENERAL_ATTACK if role
        has no eligible units.
        For country-exclusive waves, forces the country's unique unit
        as the composition anchor.
        """
        # Temporarily filter excluded units (e.g. CrateGoodie) from buildable pool
        exclude = getattr(self, '_exclude_unit_ids', frozenset())
        original_buildable = None
        if exclude:
            original_buildable = self.parser.buildable
            self.parser.buildable = [
                r for r in original_buildable if r.unit_id not in exclude]

        try:
            return self._get_taskforce_inner(faction, phase, role, difficulty, country)
        finally:
            if original_buildable is not None:
                self.parser.buildable = original_buildable

    def _get_taskforce_inner(self, faction: str, phase: str, role: str,
                              difficulty: str,
                              country: str | None) -> TaskforceEntry | None:
        """Inner implementation of taskforce generation."""
        roles_to_try = [role] + (
            [ROLE_FALLBACKS[role]] if role in ROLE_FALLBACKS else []
        ) + ["GENERAL_ATTACK"]

        for r in roles_to_try:
            spec = TaskforceSpec(
                faction    = faction,
                phase      = phase,
                role       = r,
                difficulty = difficulty,
                count      = 1,
                country    = country,
            )

            if country:
                # Force anchor unit into composition using shared dedup set
                ctx_key   = (country, r, phase)
                seen_fps  = self._country_seen.setdefault(ctx_key, set())
                tf = self.tf_gen._generate_country_anchor(
                    spec, country, difficulty,
                    seen_fingerprints=seen_fps)
                if tf:
                    return tf
                # If exhausted, fall through to next role
            else:
                batch = self.tf_gen.generate_batch(GeneratorConfig([spec]))
                if batch:
                    return batch[0]
        return None

    def _get_script_for_tf(self, tf: TaskforceEntry) -> ScriptEntry | None:
        """Pick the best-matching script for a taskforce.

        Injects tag hints so template selection can pick the right variant:
          [LARGE_TEAM]  — total units >= threshold; skip gather (too many)
          [NO_GATHER]   — artillery; skip gather (they shell from range)
          [NO_DEFENSES] — rush + AIExt has enemy defenses gated to 0;
                          urgency to strike before rebuild
        """
        total_units = sum(c for c, _ in (tf.units or []))
        threshold = getattr(self, '_oversized_threshold', 10)

        role_upper = (getattr(tf, 'role', '') or '').upper()
        is_artillery = 'ARTILLERY' in role_upper or 'ARTY' in role_upper

        no_defenses = False
        if 'RUSH' in role_upper:
            builder = getattr(self, '_aiext_builder', None)
            if builder is not None and getattr(builder, '_base_defense_ids', None):
                no_defenses = True

        tags = []
        if total_units >= threshold: tags.append('LARGE_TEAM')
        if is_artillery:             tags.append('NO_GATHER')
        if no_defenses:              tags.append('NO_DEFENSES')

        if tags:
            original_comment = tf.label_comment
            for t in tags:
                if f'[{t}]' not in tf.label_comment:
                    tf.label_comment = tf.label_comment.rstrip() + f' [{t}]'
            results = self.sc_gen.generate_for_taskforces([tf])
            tf.label_comment = original_comment
        else:
            results = self.sc_gen.generate_for_taskforces([tf])
        if results:
            sc = results[0]
            if not sc.is_duplicate:
                self.scripts.append(sc)
            return sc
        return None

    def _create_team(self, tf: TaskforceEntry, script: ScriptEntry,
                      faction: str, phase: str, role: str,
                      difficulty: str,
                      country: str | None = None) -> TeamEntry:
        """Create a TeamType binding taskforce + script."""
        from .team_generator import build_team_flags, FIXED_FLAGS
        import random

        flags = dict(FIXED_FLAGS)
        flags.update(build_team_flags(role, random.Random()))

        # Stray/RelaxedStray based on total unit count
        total_units = sum(c for c, _ in (tf.units or []))
        if total_units >= 20:
            flags['Stray']        = '25'
            flags['RelaxedStray'] = '30'
        elif total_units >= 10:
            flags['Stray']        = '15'
            flags['RelaxedStray'] = '20'

        faction_label = {
            'gdi': 'GDI', 'nod': 'Soviet', 'thirdside': 'Yuri'
        }.get(faction, faction.upper())

        base_name = (f"{country or faction_label} {phase} "
                     f"{role.replace('_',' ').title()} {difficulty}")
        name    = self.team_reg.unique_name(base_name)
        team_id = self.team_reg.generate()
        label   = (f"; [{faction.upper()}][{phase.upper()}][{role}]"
                   f"[{difficulty.upper()}]"
                   f"{f'[{country.upper()}]' if country else ''}")

        from .team_generator import TeamEntry as TE
        return TE(
            team_id       = team_id,
            name          = name,
            label_comment = label,
            taskforce_id  = tf.tf_id,
            script_id     = script.script_id,
            flags         = flags,
            faction       = faction,
            phase         = phase,
            role          = role,
            difficulty    = difficulty,
        )

    def _create_general_trigger(self, team: TeamEntry,
                                  faction: str, difficulty: str,
                                  name: str,
                                  tf_units: list | None = None) -> TriggerEntry:
        """
        General trigger: CondType=4 (enemy credits ≥ 0 = always true).
        tf_units: taskforce units passed directly to avoid timing issues
        (taskforce isn't appended to self.taskforces until after trigger creation).
        """
        easy, normal, hard = DIFFICULTY_FLAGS.get(difficulty, (0,0,1))
        start_w, min_w, max_w = DIFFICULTY_WEIGHTS.get(difficulty, (200,50,200))
        side       = self.side_map.get(faction, 0)
        trig_name  = self.trig_reg.unique_name(name)
        trigger_id = self.trig_reg.generate()

        # Use passed units first; fall back to taskforce lookup
        units = tf_units
        if units is None:
            tf = next((t for t in self.taskforces
                       if t.tf_id == team.taskforce_id), None)
            units = tf.units if tf else []
        owner = self._effective_owner_for_units(units, faction) if units else '<all>'

        # Determine condition: use meta-dummy CondObj if any unit has Prerequisite.Negative
        cond_type, cond_obj = self._cond_obj_for_units(units) if units else (4, '<none>')

        trigger = TriggerEntry(
            trigger_id = trigger_id,
            name       = trig_name,
            team1_id   = team.team_id,
            owner      = owner,
            tech_level = 1,
            cond_type  = cond_type,
            cond_obj   = cond_obj,
            comparator = ALWAYS_TRUE,
            start_w    = float(start_w),
            min_w      = float(min_w),
            max_w      = float(max_w),
            skirmish   = 1,
            side       = side,
            team2_id   = '<none>',
            easy       = easy,
            normal     = normal,
            hard       = hard,
        )

        # Attach AIExt conditions if builder is active
        if self._aiext_builder is not None:
            unit_ids = [uid for (_, uid) in (tf_units or [])]
            aiext_cond = self._aiext_builder.build(
                role=team.role,
                faction=faction,
                difficulty=difficulty,
                unit_ids=unit_ids,
            )
            trigger.aiext_conditions = aiext_cond
            self.aiext_writer.collect(trigger_id, aiext_cond, getattr(trigger, 'name', None))

        return trigger

    def _create_country_trigger(self, team: TeamEntry,
                                  faction: str, country: str,
                                  difficulty: str,
                                  name: str,
                                  tf_units: list | None = None) -> TriggerEntry:
        """Country-exclusive trigger."""
        easy, normal, hard = DIFFICULTY_FLAGS.get(difficulty, (0,0,1))
        start_w, min_w, max_w = DIFFICULTY_WEIGHTS.get(difficulty, (200,50,200))
        country_side = self._country_side_number(faction, country)
        trig_name    = self.trig_reg.unique_name(name)
        trigger_id   = self.trig_reg.generate()
        units = tf_units
        if units is None:
            tf = next((t for t in self.taskforces
                       if t.tf_id == team.taskforce_id), None)
            units = tf.units if tf else []
        owner = self._effective_owner_for_units(units, faction) if units else '<all>'
        cond_type, cond_obj = self._cond_obj_for_units(units) if units else (4, '<none>')
        tech_level = self._tech_level_for_units(units) if units else 1

        trigger = TriggerEntry(
            trigger_id = trigger_id,
            name       = trig_name,
            team1_id   = team.team_id,
            owner      = owner,
            tech_level = tech_level,
            cond_type  = cond_type,
            cond_obj   = cond_obj,
            comparator = ALWAYS_TRUE,
            start_w    = float(start_w),
            min_w      = float(min_w),
            max_w      = float(max_w),
            skirmish   = 1,
            side       = country_side,
            team2_id   = '<none>',
            easy       = easy,
            normal     = normal,
            hard       = hard,
        )

        # Attach AIExt conditions if builder is active
        if self._aiext_builder is not None:
            unit_ids = [uid for (_, uid) in (tf_units or [])]
            aiext_cond = self._aiext_builder.build(
                role=team.role,
                faction=faction,
                difficulty=difficulty,
                country=country,
                unit_ids=unit_ids,
            )
            trigger.aiext_conditions = aiext_cond
            self.aiext_writer.collect(trigger_id, aiext_cond, getattr(trigger, 'name', None))

        return trigger

    def _country_side_number(self, faction: str, country: str) -> int:
        """
        Get the side number for a specific country.
        Countries within a faction use the faction's side number in
        vanilla AITriggerTypes -- country exclusivity comes from the
        TaskForce containing only country-exclusive units, not the
        Side= field. Side= remains faction-level.
        """
        return self.side_map.get(faction, 0)


# ---------------------------------------------------------------------------
# Specialized transport composition generators
# ---------------------------------------------------------------------------

class TransportCompositionGenerator:
    """
    Generates specialized transport-based taskforces:
      1. BFRT open-topped (1 anti-inf + 4 anti-veh infantry)
      2. HTK + TERROR (NOD suicide transport, vanilla + Phobos multi-transport)
      3. Naval transport + suicide units (SAPC + TERROR/DTRUCK)

    These compositions are too specific for the generic role_unit_filter system
    and are generated directly from parser data.
    """

    def __init__(self, parser: RulesMdParser,
                 tf_reg, sc_reg, team_reg, trig_reg,
                 side_map: dict,
                 phobos: bool = True,
                 seed: int | None = None,
                 taskforce_scale: float = 1.0):
        self.parser          = parser
        self.tf_reg          = tf_reg
        self.sc_reg          = sc_reg
        self.team_reg        = team_reg
        self.trig_reg        = trig_reg
        self.side_map        = side_map
        self.phobos          = phobos
        self.ini             = parser.ini
        self.taskforce_scale = taskforce_scale

        from .taskforce_generator import TaskforceGenerator
        from .script_generator   import ScriptGenerator
        from .team_generator     import TeamGenerator
        from .trigger_generator  import TriggerGenerator

        self.tf_gen   = TaskforceGenerator(parser, tf_reg, seed=seed)
        self.sc_gen   = ScriptGenerator(sc_reg, phobos=phobos, seed=seed)
        self.team_gen = TeamGenerator(parser, team_reg, seed=seed)
        self.trig_gen = TriggerGenerator(trig_reg, side_map, seed=seed)

        self.taskforces: list = []
        self.scripts:    list = []
        self.teams:      list = []
        self.triggers:   list = []
        self._appended_tf_ids: set[str] = set()
        # _create_always_trigger and other shared helpers reference these;
        # transports don't attach AIExt conditions, but the attributes must
        # exist so the None-check paths run cleanly.
        self._aiext_builder = None
        from .aiext_generator import AIExtWriter
        self.aiext_writer = AIExtWriter()

    # ------------------------------------------------------------------
    # Taskforce scaling helper
    # ------------------------------------------------------------------

    def _required_houses_for_units(self, units, faction):
        """None = no restriction, frozenset = compatible countries (empty = contradictory)."""
        ini = self.parser.ini
        fc  = frozenset(self.parser.sides.faction_countries.get(faction, []))
        eff = None
        for _, uid in units:
            s = ini.section(uid)
            if not s: continue
            rh_raw = s.get('requiredhouses', '')
            if not rh_raw.strip(): continue
            rh = frozenset(h.strip() for h in rh_raw.split(',') if h.strip())
            eff = (rh & fc) if eff is None else eff & (rh & fc)
        return eff

    def _effective_owner(self, units, faction):
        """Returns OwnerHouse string, or None if contradictory."""
        eff = self._required_houses_for_units(units, faction)
        if eff is None:
            return '<all>'
        if len(eff) == 0:
            return None   # contradictory
        if len(eff) == 1:
            return next(iter(eff))
        fc = self.parser.sides.faction_countries.get(faction, [])
        if eff == frozenset(fc):
            return '<all>'
        for c in fc:
            if c in eff:
                return c
        return '<all>'

    def _scale_units(self, units: list[tuple[int, str]]) -> list[tuple[int, str]]:
        """
        Apply taskforce_scale to unit counts.

        Rules:
        - Scale factor applied to each count: new_count = floor(count * scale)
        - Minimum count is always 1 (never remove a unit type entirely)
        - BuildLimit=1 units (heroes: TANY, BORIS, YURIPR) are always kept at 1
          regardless of scale factor — you can't have 2 Tanyas

        E.g. scale=2.0: [(3,'MTNK'),(2,'E1')] → [(6,'MTNK'),(4,'E1')]
             scale=0.5: [(3,'MTNK'),(2,'E1')] → [(1,'MTNK'),(1,'E1')]
        """
        import math
        if self.taskforce_scale == 1.0:
            return units   # fast path — no change

        result = []
        for count, uid in units:
            sec = self.ini.section(uid)
            try:
                build_limit = int(sec.get('buildlimit', '0') or '0') if sec else 0
            except ValueError:
                build_limit = 0

            raw = count * self.taskforce_scale
            if self.taskforce_scale < 1.0:
                new_count = max(1, math.ceil(raw))
            else:
                new_count = max(1, math.floor(raw))
            if build_limit > 0:
                # Any positive BuildLimit caps the count (heroes at 1,
                # BuildLimit=3 units at 3, etc.)
                new_count = min(new_count, build_limit)
            result.append((new_count, uid))

        return result

    def _apply_transport_limits(self, units: list[tuple[int,str]],
                                 transport_uid: str,
                                 capacity: int) -> list[tuple[int,str]]:
        """
        Without Phobos (vanilla action 14): only ONE transport loads correctly.
        Cap the transport count to 1 and passengers to exactly that transport's
        capacity — more infantry won't fit, fewer leaves the transport under-filled.

        BuildLimit=1 units are always kept at 1 regardless.

        With Phobos (action 10103): no artificial cap; _scale_units already
        applied the user's scale factor.
        """
        if self.phobos:
            return units  # no cap needed

        result = []
        n_transports = 1  # vanilla: always exactly 1
        for count, uid in units:
            if uid == transport_uid:
                result.append((n_transports, uid))
            else:
                # Respect BuildLimit=1 even when filling transport
                sec = self.ini.section(uid)
                try:
                    bl = int(sec.get('buildlimit','0') or '0') if sec else 0
                except ValueError:
                    bl = 0
                if bl == 1:
                    result.append((1, uid))
                else:
                    result.append((capacity * n_transports, uid))
        return result

    # ------------------------------------------------------------------
    # 1. BFRT open-topped compositions
    # ------------------------------------------------------------------

    def generate_bfrt_waves(self, difficulties: list[str],
                             count_per_difficulty: int = 3):
        """
        Generate BFRT + infantry teams.
        Composition: 1 BFRT + 1 best anti-infantry + (passengers-1) anti-vehicle
        Uses OpenTransportWeapon to select which weapon each infantry fires.
        """
        BOARDING_OK = {'Land', 'Amphibious', 'Tunnel', 'Hover'}

        # BFRT must exist and be buildable
        bfrt = self.parser.units.get('BFRT')
        if not bfrt or not bfrt.is_open_transport:
            return
        faction = bfrt.faction or 'gdi'  # BFRT is always GDI

        bfrt_sec   = self.ini.section('BFRT')
        passengers = int(bfrt_sec.get('passengers', '5'))
        size_limit = int(bfrt_sec.get('sizelimit', '99'))

        # Rank infantry by anti-infantry effectiveness (for the 1 anti-inf slot)
        def anti_inf_score(r):
            gb = r.grade_breakdown or {}
            return gb.get('dps_infantry', 0)

        # Rank infantry by anti-vehicle effectiveness (for remaining slots)
        def anti_veh_score(r):
            gb = r.grade_breakdown or {}
            return gb.get('dps_vehicle', 0) + gb.get('dps_building', 0) * 0.5

        gdi_inf = [r for r in self.parser.buildable
                   if r.faction == 'gdi'
                   and r.category == 'Infantry'
                   and r.mobility in BOARDING_OK
                   and r.open_transport_weapon is not None
                   and not r.is_utility
                   and r.grade >= 0
                   and int(self.ini.section(r.unit_id).get('size','1')) <= size_limit]

        if not gdi_inf:
            return

        # Best anti-infantry unit (1 slot) -- prefer BuildLimit=1 as a special slot
        gdi_inf_sorted_ai  = sorted(gdi_inf, key=anti_inf_score, reverse=True)
        gdi_inf_sorted_av  = sorted(gdi_inf, key=anti_veh_score, reverse=True)

        best_ai = gdi_inf_sorted_ai[0]
        # Best anti-vehicle that isn't the same unit
        best_av = next((r for r in gdi_inf_sorted_av
                        if r.unit_id != best_ai.unit_id), gdi_inf_sorted_ai[-1])

        # Build composition: 1 BFRT + 1 anti-inf + (passengers-1) anti-veh
        bl_ai = int(self.ini.section(best_ai.unit_id).get('buildlimit','0'))
        ai_count = 1  # always 1 anti-infantry
        av_count = passengers - ai_count

        units = [(1, 'BFRT'), (ai_count, best_ai.unit_id)]
        if bl_ai == 1:
            if av_count > 0 and best_av:
                units.append((av_count, best_av.unit_id))
        else:
            if av_count > 0 and best_av:
                units.append((av_count, best_av.unit_id))

        units    = self._scale_units(units)
        # Vanilla action 14: cap to 1 BFRT, passengers = exact capacity
        units    = self._apply_transport_limits(units, 'BFRT', passengers)

        # Register taskforce
        _eff_owner = self._effective_owner(units, faction)
        if _eff_owner is None:
            return  # contradictory RequiredHouses
        existing = self.tf_reg.find_duplicate(units)
        if existing:
            tf_id = existing
        else:
            tf_id = self.tf_reg.generate()
            self.tf_reg.register_composition(tf_id, units)
            from .taskforce_generator import TaskforceEntry
            tf_entry = TaskforceEntry(
                tf_id         = tf_id,
                name          = f"1 Battle Fortress {ai_count} {best_ai.name} {av_count} {best_av.name}",
                label_comment = "; [GDI][LATE][BFRT_OPEN_TRANSPORT][DIRECT_ATTACK]",
                units         = units,
                faction       = 'gdi',
                phase         = 'Late',
                role          = 'BFRT_TRANSPORT',
                difficulty    = 'all',
            )
            if tf_entry.tf_id not in self._appended_tf_ids:
                self._appended_tf_ids.add(tf_entry.tf_id)
                self.taskforces.append(tf_entry)

        # Script: gather at warfactory, load, move to enemy base, unload, attack
        script_actions = self._bfrt_script_actions()
        script_id      = self._get_or_create_script(
            'bfrt_transport', script_actions, 'Battle Fortress Transport Drop')

        for difficulty in difficulties:
            team    = self._create_team(tf_id, script_id, 'gdi', 'Late',
                                         'BFRT_TRANSPORT', difficulty)
            trigger = self._create_always_trigger(
                team, 'gdi', difficulty,
                f"GDI BFRT Transport {difficulty}")
            self.teams.append(team)
            self.triggers.append(trigger)

    def _bfrt_script_actions(self) -> list[tuple[int,int]]:
        """
        BFRT drops gather at warfactory, load, move to enemy base,
        unload, then attack. Uses Phobos action 10103,0. Requires Phobos.
        Vanilla action 14 is broken for multiple transports — not used.
        """
        # GAWEAP is BuildingTypes index 7 → structure 131072+7=131079
        gaweap_idx  = self._building_idx('GAWEAP')
        # Without Phobos: vanilla action 14 only loads ONE transport correctly.
        # Limit to 1 BFRT and cap passengers to its exact capacity so infantry
        # don't get lost or left behind.
        load_action = 10103 if self.phobos else 14
        max_transports = 999 if self.phobos else 1
        # BFRT (Battle Fortress) is OPEN-TOPPED: its passengers fire from inside.
        # Unloading (action 8) would dump them out — and doing it after a gather
        # (53) unloads them OUTSIDE the enemy base. So we do NOT unload: gather
        # near the enemy and attack while loaded (the valid fight-while-loaded
        # pattern 53 -> 0). See script_validator for the rule.
        actions = [
            (58, 131072 + gaweap_idx) if gaweap_idx else (54, 0),
            (load_action, 0),   # load infantry into the open-topped BFRT(s)
            (43, 0),            # wait until loaded
            (53, 0),            # gather near the enemy base (stay loaded)
            (0,  2),            # attack structures (passengers fire from inside)
            (0,  1),            # attack anything
            (49, 0),
        ]
        return actions

    # ------------------------------------------------------------------
    # 2. HTK + TERROR compositions
    # ------------------------------------------------------------------

    def generate_htk_terror_waves(self, difficulties: list[str],
                                   count_per_difficulty: int = 3):
        """
        Generate HTK + TERROR transport teams for NOD.
        Reuses/extends vanilla [0EC245EC-G] 1 HTK, 5 TERROR.
        With Phobos: scales to multiple HTKs + more TERROR.
        """
        htk   = self.parser.units.get('HTK')
        terror = self.parser.units.get('TERROR')
        if not htk or not terror:
            return
        if not htk.is_transport:
            return
        faction = htk.faction or 'nod'  # HTK is always NOD

        htk_sec    = self.ini.section('HTK')
        passengers = int(htk_sec.get('passengers', '5'))
        size_limit = int(htk_sec.get('sizelimit', '99'))

        # Vanilla: 1 HTK + 5 TERROR
        # Phobos: can scale to 2-3 HTKs + full load
        configs = [(1, passengers)]  # (num_transports, terror_per_transport)
        if self.phobos:
            configs.append((2, passengers))  # 2 HTK + 10 TERROR

        for n_htk, n_terror_per in configs:
            if not self.phobos:
                n_htk = 1  # vanilla action 14: only 1 transport loads
            units    = [(n_htk, 'HTK'), (n_htk * n_terror_per, 'TERROR')]
            units    = self._scale_units(units)
            units    = self._apply_transport_limits(units, 'HTK', n_terror_per)
            _eff_owner = self._effective_owner(units, faction)
            if _eff_owner is None:
                continue  # contradictory RequiredHouses
            existing = self.tf_reg.find_duplicate(units)
            if not existing:
                tf_id = self.tf_reg.generate()
                self.tf_reg.register_composition(tf_id, units)
                from .taskforce_generator import TaskforceEntry
                tf_entry = TaskforceEntry(
                    tf_id         = tf_id,
                    name          = f"{n_htk} Flak Track {n_htk*n_terror_per} Terrorist",
                    label_comment = "; [NOD][EARLY][HTK_TERROR][SUICIDE_TRANSPORT]",
                    units         = units,
                    faction       = 'nod',
                    phase         = 'Early',
                    role          = 'SUICIDE_TRANSPORT',
                    difficulty    = 'all',
                )
                if tf_entry.tf_id not in self._appended_tf_ids:
                    self._appended_tf_ids.add(tf_entry.tf_id)
                    self.taskforces.append(tf_entry)
                tf_id_use = tf_id
            else:
                tf_id_use = existing

            is_phobos_variant = self.phobos and n_htk > 1
            variant_tag = 'phobos' if is_phobos_variant else 'vanilla'
            # Per-matchup: one script + trigger per enemy CY, each gated on it.
            cy_targets = self._conyard_targets() or [(None, None)]
            for cy_id, cy_arg in cy_targets:
                suffix     = f" vs {cy_id}" if cy_id else ""
                sc_actions = self._htk_terror_script(phobos=is_phobos_variant, cy_arg=cy_arg)
                sc_key     = f"htk_terror_{variant_tag}_{cy_id or 'edge'}"
                script_id  = self._get_or_create_script(
                    sc_key, sc_actions, f"HTK Terror Transport{suffix}")

                for difficulty in difficulties:
                    team    = self._create_team(tf_id_use, script_id, 'nod',
                                                 'Early', 'SUICIDE_TRANSPORT', difficulty)
                    trigger = self._create_always_trigger(
                        team, 'nod', difficulty,
                        f"NOD HTK Terror {n_htk}x{suffix} {difficulty}")
                    if cy_id:
                        self._gate_trigger_enemy_building(trigger, cy_id)
                    self.teams.append(team)
                    self.triggers.append(trigger)

    def _htk_terror_script(self, phobos: bool,
                           cy_arg: int | None = None) -> list[tuple[int,int]]:
        """
        Flak-Track + Terror-Drone transport. Loads at the war factory, moves to
        the enemy Construction Yard (47) and unloads the drones THERE (8) so they
        spawn inside the base, then attacks. `cy_arg` is the BwP-encoded enemy CY;
        None falls back to dropping at the base edge.
        """
        naweap_idx = self._building_idx('NAWEAP')
        load_act   = 10103 if self.phobos else 14
        actions: list[tuple[int,int]] = [
            (58, self.BWP_NEAREST + naweap_idx) if naweap_idx else (54, 0),
            (load_act, 0),
            (43, 0),    # wait until loaded
        ]
        if cy_arg is not None:
            actions += [(47, cy_arg), (8, 0), (46, cy_arg)]  # move to CY, drop drones, hit it
        else:
            actions += [(53, 0), (8, 0)]                     # fallback: drop at the edge
        actions += [(0, 6), (0, 2), (0, 1), (49, 0)]
        return actions

    # ------------------------------------------------------------------
    # 3. Naval transport + suicide compositions
    # ------------------------------------------------------------------

    def generate_naval_transport_waves(self, difficulties: list[str],
                                        count_per_difficulty: int = 2):
        """
        Generate naval transport + suicide unit teams.
        NOD: SAPC + TERROR/DTRUCK
        GDI: LCRF + infantry (no suicide units available)
        Yuri: YHVR + infantry
        """
        # Without Phobos: cap to 1 transport, passengers = exact capacity
        max_transports = 999 if self.phobos else 1
        for transport in self.parser.naval_transports:
            faction   = transport.faction
            if not faction:
                continue

            trans_sec  = self.ini.section(transport.unit_id)
            passengers = int(trans_sec.get('passengers', '12'))
            size_limit = int(trans_sec.get('sizelimit', '99'))

            # Find best payload for this faction
            # Prefer suicide units for NOD, otherwise best infantry
            suicide_units = [
                r for r in self.parser.units.values()
                if r.is_suicide and r.faction == faction
                and not r.is_no_combat
                and int(self.ini.section(r.unit_id).get('size','1')) <= size_limit
            ]

            BOARDING_OK = {'Land', 'Amphibious', 'Tunnel', 'Hover'}
            regular_inf = [
                r for r in self.parser.buildable
                if r.faction == faction
                and r.category == 'Infantry'
                and r.mobility in BOARDING_OK
                and not r.is_utility
                and not r.is_open_transport
                and r.grade >= 0
                and int(self.ini.section(r.unit_id).get('size','1')) <= size_limit
            ]

            payload = suicide_units if suicide_units else regular_inf
            if not payload:
                continue

            # Best payload unit by grade
            best = max(payload, key=lambda r: r.grade)
            units = [(1, transport.unit_id), (passengers, best.unit_id)]
            units = self._scale_units(units)
            units = self._apply_transport_limits(
                units, transport.unit_id, passengers)
            _eff_owner = self._effective_owner(units, faction)
            if _eff_owner is None:
                return  # contradictory RequiredHouses
            existing = self.tf_reg.find_duplicate(units)
            if not existing:
                tf_id = self.tf_reg.generate()
                self.tf_reg.register_composition(tf_id, units)
                from .taskforce_generator import TaskforceEntry
                tf_entry = TaskforceEntry(
                    tf_id         = tf_id,
                    name          = f"1 {transport.name} {passengers} {best.name}",
                    label_comment = f"; [{faction.upper()}][EARLY][NAVAL_TRANSPORT]",
                    units         = units,
                    faction       = faction,
                    phase         = 'Early',
                    role          = 'NAVAL_TRANSPORT',
                    difficulty    = 'all',
                )
                if tf_entry.tf_id not in self._appended_tf_ids:
                    self._appended_tf_ids.add(tf_entry.tf_id)
                    self.taskforces.append(tf_entry)
                tf_id_use = tf_id
            else:
                tf_id_use = existing

            load_act  = 10103  # Phobos only — vanilla 14 breaks with multiple transports
            # Per-matchup: one script + trigger per possible enemy Construction
            # Yard, each gated on that CY existing (so 47/8/46 always has a valid
            # target). Falls back to gather-at-edge if the mod has no CYs.
            cy_targets = self._conyard_targets() or [(None, None)]
            for cy_id, cy_arg in cy_targets:
                suffix    = f" vs {cy_id}" if cy_id else ""
                sc_key    = f"naval_transport_{faction}_{cy_id or 'edge'}"
                sc_acts   = self._naval_transport_script(faction, load_act, cy_arg)
                script_id = self._get_or_create_script(
                    sc_key, sc_acts, f"{faction.upper()} Naval Transport{suffix}")

                for difficulty in difficulties:
                    team    = self._create_team(tf_id_use, script_id, faction,
                                                 'Early', 'NAVAL_TRANSPORT', difficulty)
                    trigger = self._create_always_trigger(
                        team, faction, difficulty,
                        f"{faction.upper()} Naval Transport{suffix} {difficulty}")
                    if cy_id:
                        self._gate_trigger_enemy_building(trigger, cy_id)
                    self.teams.append(team)
                    self.triggers.append(trigger)

    def _naval_transport_script(self, faction: str, load_act: int,
                                 cy_arg: int | None = None) -> list[tuple[int,int]]:
        """Naval transport: load at naval yard, MOVE TO the enemy Construction
        Yard (47), unload THERE (8), attack it (46), then anything. Matches the
        vanilla per-matchup transport pattern (47->8->46). `cy_arg` is the
        BwP-encoded enemy CY; when None (no CY in the mod) it falls back to the
        gather-at-edge behaviour so the wave still functions."""
        yard_ids = {'gdi': 'GAYARD', 'nod': 'NAYARD', 'thirdside': 'YAYARD'}
        yard_idx = self._building_idx(yard_ids.get(faction, 'GAYARD'))
        actions: list[tuple[int,int]] = [
            (58, self.BWP_NEAREST + yard_idx) if yard_idx else (54, 0),
            (load_act, 0),
            (43, 0),
        ]
        if cy_arg is not None:
            actions += [(47, cy_arg), (8, 0), (46, cy_arg)]  # move to CY, unload there, hit it
        else:
            actions += [(53, 0), (8, 0)]                     # fallback: unload at the edge
        actions += [(0, 2), (0, 1), (49, 0)]
        return actions

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _building_idx(self, bid: str) -> int:
        buildings = self.ini.list_from_section('BuildingTypes')
        try:
            return buildings.index(bid)
        except ValueError:
            return 0

    # BwP mode 2 (NEAREST) — used to target the nearest ENEMY instance.
    BWP_NEAREST = 131072

    def _conyard_targets(self) -> list[tuple[str, int]]:
        """Every Construction Yard in the mod (identified by Factory=BuildingType)
        as (building_id, BwP-NEAREST-encoded arg). Used to build one transport
        script per possible enemy CY — the per-matchup pattern vanilla uses."""
        out: list[tuple[str, int]] = []
        for idx, bid in enumerate(self.ini.list_from_section('BuildingTypes')):
            sec = self.ini.section(bid)
            if sec and sec.get('factory', '').lower() == 'buildingtype':
                out.append((bid, self.BWP_NEAREST + idx))
        return out

    def _gate_trigger_enemy_building(self, trigger, bid: str) -> None:
        """AND a RequiredEnemyBuildings=<bid> (>=1) gate onto a trigger's AIExt
        conditions, so the trigger only fires when that enemy building exists.
        This is what makes a CY-targeting transport script safe: the script's
        47/46 target is guaranteed to have an instance when the trigger fires."""
        from .aiext_generator import AIExtConditions
        cond = getattr(trigger, 'aiext_conditions', None)
        if not isinstance(cond, AIExtConditions):
            cond = AIExtConditions()
            trigger.aiext_conditions = cond
        cond.enemy_buildings.append(bid)
        cond.enemy_buildings_min.append(1)
        cond.enemy_buildings_max.append(-1)

    def _get_or_create_script(self, key: str,
                               actions: list[tuple[int,int]],
                               name: str) -> str:
        # Validate action ordering against the vanilla-AI grammar, AUTO-REPAIR the
        # known-safe ordering bugs (e.g. an assembly action sitting right before
        # an unload → MoveToEnemyBldg so units unload AT the target, not at the
        # gather point), and surface any remaining ERRORs at generation time
        # rather than in-game.
        actions = list(actions)
        try:
            from . import script_validator
            fixed, notes = script_validator.repair(actions)
            if notes:
                actions = fixed
                for note in notes:
                    print(f"  [script-repair] '{name}' ({key}): {note}")
            for f in script_validator.validate(actions):
                if f.severity == "ERROR":
                    print(f"  [script-validate] ERROR in '{name}' ({key}) "
                          f"@action {f.index}: {f.message}")
        except Exception:
            pass
        existing = self.sc_gen.registry.find_duplicate(list(actions))
        if existing:
            return existing
        script_id = self.sc_gen.registry.generate_id()
        sc_name   = self.sc_gen.registry.unique_name(name)
        self.sc_gen.registry.register(script_id, list(actions), sc_name)
        from .script_generator import ScriptEntry
        entry = ScriptEntry(
            script_id     = script_id,
            name          = sc_name,
            label_comment = f"; [SCRIPT][TRANSPORT][{key.upper()}]",
            actions       = list(actions),
            phobos        = self.phobos,
            is_duplicate  = False,
            taskforce_ids = [],
        )
        self.scripts.append(entry)
        return script_id

    def generate_gunner_waves(self, difficulties: list[str],
                               taskforce_scale: float = 1.0):
        """
        Gunner=yes combo waves (Hard difficulty only).

        Pairs late-game infantry 1:1 with Gunner=yes vehicles (IFV-style —
        the passenger becomes the turret). The wave loads infantry into the
        vehicles, moves to the enemy base loaded, and attacks.

        Requirements:
          - Vehicle: Gunner=yes, Passengers >= 1, buildable, faction match
          - Infantry: Late/EndGame tier, buildable, same faction,
            not an engineer (Engineer=yes turns the IFV into a repair
            vehicle — useless as a weapon platform)
          - Phobos: multi-vehicle loading needs action 10103. On vanilla
            (action 14) only one transport loads, so the wave is skipped
            with a note instead of generating a broken team.

        Size: 8 pairs base, multiplied by taskforce_scale, capped at 24
        ("a few dozen").
        """
        if 'Hard' not in difficulties:
            return
        if not self.phobos:
            print("    Gunner combo waves skipped: requires Phobos "
                  "(action 10103) for multi-vehicle loading.")
            return

        import math
        ini = self.parser.ini

        # Gunner vehicles per faction
        gunners_by_faction: dict[str, list] = {}
        for r in self.parser.buildable:
            if r.category != 'Vehicle' or not r.faction:
                continue
            s = ini.section(r.unit_id)
            if not s:
                continue
            if s.get('gunner', 'no').strip().lower() != 'yes':
                continue
            try:
                passengers = int(s.get('passengers', '0') or '0')
            except ValueError:
                passengers = 0
            if passengers < 1:
                continue
            if self._is_flying_transport(r):
                continue   # boarding softlock — see _is_flying_transport
            gunners_by_faction.setdefault(r.faction, []).append(r)

        if not gunners_by_faction:
            return

        # Late-game infantry per faction (skip engineers/civilians)
        late_inf_by_faction: dict[str, list] = {}
        for r in self.parser.buildable:
            if r.category != 'Infantry' or not r.faction:
                continue
            if r.tier not in ('Late', 'EndGame'):
                continue
            s = ini.section(r.unit_id)
            if not s:
                continue
            if s.get('engineer', 'no').strip().lower() == 'yes':
                continue
            late_inf_by_faction.setdefault(r.faction, []).append(r)

        generated = 0
        for faction, gunner_vehicles in gunners_by_faction.items():
            infantry = late_inf_by_faction.get(faction, [])
            if not infantry:
                continue
            # Best gunner vehicle = highest grade; best infantry = highest grade
            gunner = max(gunner_vehicles, key=lambda r: r.grade)
            inf    = max(infantry,        key=lambda r: r.grade)

            pairs = max(1, min(24, math.floor(8 * taskforce_scale)))
            # Respect BuildLimit on both halves
            for rec in (gunner, inf):
                s = ini.section(rec.unit_id)
                try:
                    bl = int(s.get('buildlimit', '0') or '0') if s else 0
                except ValueError:
                    bl = 0
                if bl > 0:
                    pairs = min(pairs, bl)

            units = [(pairs, gunner.unit_id), (pairs, inf.unit_id)]

            _eff_owner = self._effective_owner(units, faction)
            if _eff_owner is None:
                continue

            existing = self.tf_reg.find_duplicate(units)
            if not existing:
                tf_id = self.tf_reg.generate()
                self.tf_reg.register_composition(tf_id, units)
                from .taskforce_generator import TaskforceEntry
                tf_entry = TaskforceEntry(
                    tf_id         = tf_id,
                    name          = f"{pairs} {gunner.name[:12]}, {pairs} {inf.name[:12]}",
                    label_comment = (f"; [{faction.upper()}][LATE]"
                                     f"[GUNNER_COMBO][HARD]"),
                    units         = units,
                    faction       = faction,
                    phase         = 'Late',
                    role          = 'GUNNER_COMBO',
                    difficulty    = 'Hard',
                )
                if tf_entry.tf_id not in self._appended_tf_ids:
                    self._appended_tf_ids.add(tf_entry.tf_id)
                    self.taskforces.append(tf_entry)
                tf_id_use = tf_id
            else:
                tf_id_use = existing

            sc_acts = [
                (10103, 0),   # Phobos: load all transports properly
                (43,    0),   # wait until fully loaded
                (54,    0),   # regroup at friendly base (assemble)
                (53,    0),   # gather at enemy base
                (0,     1),   # attack anything
                (49,    0),   # register success / retry
                (6,     4),   # jump back to gather (1-indexed line 5 = 53)
            ]
            script_id = self._get_or_create_script(
                f'gunner_combo_{faction}', sc_acts,
                f'Gunner Combo {faction}')

            team = self._create_team(
                tf_id_use, script_id, faction, 'Late',
                'GUNNER_COMBO', 'Hard',
                overrides={
                    'Priority':   '5',
                    'Max':        '1',
                    'Aggressive': 'yes',
                    'Suicide':    'no',
                    'Loadable':   'no',
                },
                tf_units=units)
            trigger = self._create_always_trigger(
                team, faction, 'Hard',
                f"{faction.upper()} Gunner Combo Hard")
            self.teams.append(team)
            self.triggers.append(trigger)
            generated += 1

        if generated:
            print(f"    Gunner combo waves: {generated} (Hard only, "
                  f"IFV-style Gunner=yes vehicles + late infantry)")

    def _create_team(self, tf_id: str, script_id: str,
                      faction: str, phase: str, role: str,
                      difficulty: str, overrides: dict | None = None,
                      country: str | None = None,
                      tf_units: list | None = None):
        from .team_generator import build_team_flags, FIXED_FLAGS, TeamEntry
        import random
        flags = dict(FIXED_FLAGS)
        flags.update(build_team_flags(role, random.Random()))
        flags['Aggressive'] = 'yes'
        flags['Suicide']    = 'no'
        flags['AreTeamMembersRecruitable'] = 'no'

        # Stray/RelaxedStray: larger teams need more room to manoeuvre
        # Total unit count = sum of counts across all unit slots
        total_units = sum(c for c, _ in (tf_units or []))
        if total_units >= 20:
            # Very large team — wide stray, no gather needed
            flags['Stray']        = '25'
            flags['RelaxedStray'] = '30'
        elif total_units >= 10:
            # Medium-large team
            flags['Stray']        = '15'
            flags['RelaxedStray'] = '20'
        # Small teams use engine defaults (no explicit Stray needed)

        if overrides:
            flags.update(overrides)

        faction_label = {'gdi':'GDI','nod':'Soviet','thirdside':'Yuri'}.get(
            faction, faction.upper())
        country_label = country or faction_label
        name    = self.team_reg.unique_name(
            f"{country_label} {role.replace('_',' ').title()} {difficulty}")
        team_id = self.team_reg.generate()

        return TeamEntry(
            team_id       = team_id,
            name          = name,
            label_comment = f"; [{faction.upper()}][{phase.upper()}][{role}][{difficulty.upper()}]",
            taskforce_id  = tf_id,
            script_id     = script_id,
            flags         = flags,
            faction       = faction,
            phase         = phase,
            role          = role,
            difficulty    = difficulty,
        )

    def _create_always_trigger(self, team, faction: str,
                                difficulty: str, name: str,
                                owner: str = '<all>'):
        from .trigger_generator import (
            TriggerEntry, COND_ENEMY_CREDITS, ALWAYS_TRUE,
        )
        # Compute owner from taskforce units if not explicitly provided
        if owner == '<all>':
            units = (self.tf_reg.get_units(team.taskforce_id)
                     if hasattr(self.tf_reg, 'get_units') else [])
            if units:
                computed = self._effective_owner(units, faction)
                if computed is not None:
                    owner = computed
        easy, normal, hard = {
            'Easy': (1,1,1), 'Medium': (0,1,1), 'Hard': (0,0,1)
        }.get(difficulty, (0,0,1))
        start_w, min_w, max_w = {
            'Easy':   (60.0,  20.0, 100.0),
            'Medium': (120.0, 40.0, 200.0),
            'Hard':   (250.0, 80.0, 400.0),
        }.get(difficulty, (120.0, 40.0, 200.0))

        # Compute TechLevel from taskforce units (clamped 1-10)
        tf_units_for_tl = (self.tf_reg.get_units(team.taskforce_id)
                           if hasattr(self.tf_reg, 'get_units') else [])
        tl_computed = 1
        if tf_units_for_tl:
            ini = self.parser.ini
            valid_tls = [
                int(ini.section(u).get('techlevel','1') or '1')
                for _,u in tf_units_for_tl
                if ini.section(u)
                and 1 <= int(ini.section(u).get('techlevel','1') or '1') <= 10
            ]
            tl_computed = max(valid_tls) if valid_tls else 1

        trigger = TriggerEntry(
            trigger_id = self.trig_reg.generate(),
            name       = self.trig_reg.unique_name(name),
            team1_id   = team.team_id,
            owner      = owner,
            tech_level = tl_computed,
            cond_type  = COND_ENEMY_CREDITS,
            cond_obj   = '<none>',
            comparator = ALWAYS_TRUE,
            start_w    = start_w, min_w = min_w, max_w = max_w,
            skirmish   = 1,
            side       = self.side_map.get(faction, 0),
            team2_id   = '<none>',
            easy = easy, normal = normal, hard = hard,
        )

        # Attach AIExt prerequisite gate — hero-escort/hunt triggers otherwise
        # ship with no prerequisites, letting the AI 'cheat' by spawning a
        # composition (e.g. Tanya+BEAG requires GATECH+GAPILE+GAAIRC) without
        # actually needing to build the prerequisite structures.
        if self._aiext_builder is not None:
            unit_ids = [uid for _, uid in tf_units_for_tl]
            aiext_cond = self._aiext_builder.build(
                role=getattr(team, 'role', 'GENERAL_ATTACK'),
                faction=faction,
                difficulty=difficulty,
                unit_ids=unit_ids,
            )
            trigger.aiext_conditions = aiext_cond
            self.aiext_writer.collect(trigger.trigger_id, aiext_cond, getattr(trigger, 'name', None))

        return trigger

    def write_all(self, aimd_path, output_path=None):
        from pathlib import Path
        aimd_path   = Path(aimd_path)
        output_path = Path(output_path) if output_path else aimd_path
        # Vanilla order: TaskForces → Scripts → Teams → Triggers
        if self.taskforces:
            self.tf_gen.write_to_ini(self.taskforces, output_path, output_path)
        new_scripts = [s for s in self.scripts if not s.is_duplicate]
        if new_scripts:
            self.sc_gen.write_to_ini(new_scripts, output_path, output_path)
        if self.teams:
            self.team_gen.write_to_ini(self.teams, output_path, output_path)
        if self.triggers:
            self.trig_gen.write_to_ini(self.triggers, output_path, output_path)

    def summary(self) -> str:
        return (f"Transport compositions:\n"
                f"  Taskforces: {len(self.taskforces)}\n"
                f"  Scripts:    {len(self.scripts)}\n"
                f"  Teams:      {len(self.teams)}\n"
                f"  Triggers:   {len(self.triggers)}")

    def generate_naval_general_waves(self,
                                      difficulties: list[str],
                                      count_per_difficulty: int = 5):
        """
        Generate varied naval transport waves with non-suicide payloads.
        Uses Phobos action 10103 for loading — requires Phobos.
        """
        # Without Phobos: cap to 1 transport, passengers = exact capacity
        max_transports = 999 if self.phobos else 1
        BOARDING_OK = {'Land', 'Amphibious', 'Tunnel', 'Hover'}

        for transport in self.parser.naval_transports:
            faction = transport.faction
            if not faction:
                continue

            trans_sec  = self.ini.section(transport.unit_id)
            passengers = int(trans_sec.get('passengers', '12'))
            size_limit = int(trans_sec.get('sizelimit', '99'))

            # Build payload pool per tier
            payload_by_tier: dict[str, list] = {'Early': [], 'Mid': [], 'Late': [], 'EndGame': []}
            for r in self.parser.buildable:
                if r.faction != faction: continue
                if r.mobility not in BOARDING_OK: continue
                # Exclude open-topped transports -- they exit the naval transport
                # empty since their infantry never gets pre-loaded inside them.
                # Regular transports (HTK) are fine -- they board and exit normally.
                if r.is_utility or r.is_open_transport: continue
                if r.is_suicide: continue
                if r.grade < 0: continue
                size = int(self.ini.section(r.unit_id).get('size', '1'))
                if size > size_limit: continue
                payload_by_tier[r.tier].append(r)

            # For each tier, pick best unit by grade and compute composition
            phase_payloads = []
            for tier in ['Early', 'Mid', 'Late']:
                pool = payload_by_tier[tier]
                # Also include lower tiers
                for lower in ['Early'] if tier == 'Mid' else (['Early', 'Mid'] if tier == 'Late' else []):
                    pool = pool + [r for r in payload_by_tier[lower]
                                   if r not in pool]
                if not pool:
                    continue
                # Sort by grade descending, pick top candidates
                pool.sort(key=lambda r: -r.grade)
                # Try top 3 units for variety
                for unit in pool[:3]:
                    size  = int(self.ini.section(unit.unit_id).get('size', '1'))
                    count = passengers // size
                    if count < 1: continue
                    units = [(1, transport.unit_id), (count, unit.unit_id)]
                    phase_payloads.append((tier, units, unit))

            if not phase_payloads:
                continue

            # Generate waves cycling through payloads
            from itertools import cycle as _cycle
            payload_cycle = _cycle(phase_payloads)
            generated = 0

            for difficulty in difficulties:
                for _ in range(count_per_difficulty):
                    tier, units, unit = next(payload_cycle)

                    # Register or find existing TF
                    units    = self._scale_units(units)
                    trans_cap = int(self.ini.section(transport.unit_id).get('passengers','12'))
                    units    = self._apply_transport_limits(
                        units, transport.unit_id, trans_cap)
                    _eff_owner = self._effective_owner(units, faction)
                    if _eff_owner is None:
                        return  # contradictory RequiredHouses
                    existing = self.tf_reg.find_duplicate(units)
                    if not existing:
                        tf_id = self.tf_reg.generate()
                        self.tf_reg.register_composition(tf_id, units)
                        from .taskforce_generator import TaskforceEntry
                        count = units[1][0]
                        tf_entry = TaskforceEntry(
                            tf_id         = tf_id,
                            name          = f"1 {transport.name} {count} {unit.name}",
                            label_comment = f"; [{faction.upper()}][{tier.upper()}]"
                                            f"[NAVAL_TRANSPORT][DIRECT_ATTACK]",
                            units         = units,
                            faction       = faction,
                            phase         = tier,
                            role          = 'NAVAL_TRANSPORT',
                            difficulty    = 'all',
                        )
                        if tf_entry.tf_id not in self._appended_tf_ids:
                            self._appended_tf_ids.add(tf_entry.tf_id)
                            self.taskforces.append(tf_entry)
                        tf_id_use = tf_id
                    else:
                        tf_id_use = existing

                    # Per-matchup: one variant per enemy CY, each gated on it.
                    load_act  = 10103  # Phobos only — vanilla 14 breaks with multiple transports
                    cy_targets = self._conyard_targets() or [(None, None)]
                    for cy_id, cy_arg in cy_targets:
                        suffix    = f" vs {cy_id}" if cy_id else ""
                        sc_key    = f"naval_general_{faction}_{cy_id or 'edge'}"
                        sc_acts   = self._naval_transport_script(faction, load_act, cy_arg)
                        script_id = self._get_or_create_script(
                            sc_key, sc_acts, f"{faction.upper()} Naval General{suffix}")

                        team    = self._create_team(tf_id_use, script_id, faction,
                                                     tier, 'NAVAL_TRANSPORT', difficulty)
                        trigger = self._create_always_trigger(
                            team, faction, difficulty,
                            f"{faction.upper()} Naval {unit.name}{suffix} {difficulty} {generated+1}")
                        if cy_id:
                            self._gate_trigger_enemy_building(trigger, cy_id)
                        self.teams.append(team)
                        self.triggers.append(trigger)
                    generated += 1

    def generate_dtruck_waves(self, difficulties: list[str]):
        """
        Generate Demolition Truck rush waves.
        DTRUCK is a high-risk/high-reward unit:
          - Hard difficulty only (too unpredictable for Easy/Medium)
          - Rush waves only (no gathering -- DTRUCK needs to reach its target fast)
          - Also in SAPC amphibious transport for naval assault variants
          - Iron Curtain coordination: TODO -- when AI has NAIRON, boost weight
            so Iron Curtain fires on DTRUCK before it enters enemy base

        DTRUCK excluded from Easy/Medium: DTRUCKs can detonate on allied units
        if there are any obstacles, and at lower difficulties the AI's targeting
        is less reliable.
        """
        dtruck = self.parser.units.get('DTRUCK')
        if not dtruck or dtruck.faction != 'nod':
            return
        faction = 'nod'  # DTRUCK is always NOD

        # Hard difficulty only
        hard_difficulties = [d for d in difficulties if d == 'Hard']
        if not hard_difficulties:
            return

        BOARDING_OK = {'Land', 'Amphibious', 'Tunnel', 'Hover'}

        # 1. Pure DTRUCK rush wave (Hard only)
        # Small count -- 2-3 DTRUCKs are devastating, more is overkill
        for count in [2, 3]:
            units    = [(count, 'DTRUCK')]
            units    = self._scale_units(units)
            _eff_owner = self._effective_owner(units, faction)
            if _eff_owner is None:
                continue  # contradictory RequiredHouses
            existing = self.tf_reg.find_duplicate(units)
            if not existing:
                tf_id = self.tf_reg.generate()
                self.tf_reg.register_composition(tf_id, units)
                from .taskforce_generator import TaskforceEntry
                tf_entry = TaskforceEntry(
                    tf_id         = tf_id,
                    name          = f"{count} Demolitions Truck",
                    label_comment = ("; [NOD][EARLY][DTRUCK_RUSH][HARD_ONLY]"
                                     "\n; TODO: Iron Curtain coordination -- "
                                     "boost trigger weight when NAIRON is built"),
                    units         = units,
                    faction       = 'nod',
                    phase         = 'Early',
                    role          = 'SUICIDE_RUSH',
                    difficulty    = 'Hard',
                )
                if tf_entry.tf_id not in self._appended_tf_ids:
                    self._appended_tf_ids.add(tf_entry.tf_id)
                    self.taskforces.append(tf_entry)
                tf_id_use = tf_id
            else:
                tf_id_use = existing

            sc_acts   = self._dtruck_rush_script()
            script_id = self._get_or_create_script(
                f'dtruck_rush_{count}', sc_acts,
                f'Demolitions Truck Rush x{count}')

            team    = self._create_team(tf_id_use, script_id, 'nod',
                                         'Early', 'SUICIDE_RUSH', 'Hard',
                                         overrides={
                                             'Priority':  '8',
                                             'Max':       '1',
                                             'Aggressive':'yes',
                                             'Suicide':   'yes',
                                         })
            trigger = self._create_always_trigger(
                team, 'nod', 'Hard',
                f"NOD Demolitions Truck Rush x{count} Hard")
            self.teams.append(team)
            self.triggers.append(trigger)

        # 2. DTRUCK in SAPC amphibious transport (Hard only)
        # Without Phobos: 1 SAPC only, DTRUCKs capped to its passenger capacity
        sapc = next((r for r in self.parser.naval_transports
                     if r.unit_id == 'SAPC'), None)
        if sapc:
            trans_sec  = self.ini.section('SAPC')
            passengers = int(trans_sec.get('passengers', '12'))
            # DTRUCK size=3 → 4 fit in SAPC (12/3=4)
            dtruck_sec  = self.ini.section('DTRUCK')
            size        = int(dtruck_sec.get('size', '3'))
            count       = passengers // size

            units    = [(1, 'SAPC'), (count, 'DTRUCK')]
            units    = self._scale_units(units)
            units    = self._apply_transport_limits(
                units, 'SAPC', count)   # count = passengers // size
            _eff_owner = self._effective_owner(units, faction)
            if _eff_owner is None:
                return  # contradictory RequiredHouses
            existing = self.tf_reg.find_duplicate(units)
            if not existing:
                tf_id = self.tf_reg.generate()
                self.tf_reg.register_composition(tf_id, units)
                from .taskforce_generator import TaskforceEntry
                tf_entry = TaskforceEntry(
                    tf_id         = tf_id,
                    name          = f"1 Armored Transport {count} Demolitions Truck",
                    label_comment = ("; [NOD][EARLY][SAPC_DTRUCK][HARD_ONLY]"
                                     "\n; TODO: Iron Curtain on transport before "
                                     "unloading for guaranteed delivery"),
                    units         = units,
                    faction       = 'nod',
                    phase         = 'Early',
                    role          = 'NAVAL_TRANSPORT',
                    difficulty    = 'Hard',
                )
                if tf_entry.tf_id not in self._appended_tf_ids:
                    self._appended_tf_ids.add(tf_entry.tf_id)
                    self.taskforces.append(tf_entry)
                tf_id_use = tf_id
            else:
                tf_id_use = existing

            load_act  = 10103  # Phobos only — vanilla 14 breaks with multiple transports
            # Per-matchup: deliver the demo trucks TO the enemy CY (47->8->46).
            cy_targets = self._conyard_targets() or [(None, None)]
            for cy_id, cy_arg in cy_targets:
                suffix    = f" vs {cy_id}" if cy_id else ""
                sc_acts   = self._naval_transport_script('nod', load_act, cy_arg)
                script_id = self._get_or_create_script(
                    f'sapc_dtruck_{cy_id or "edge"}', sc_acts,
                    f'SAPC Demolitions Truck Assault{suffix}')

                team    = self._create_team(tf_id_use, script_id, 'nod',
                                             'Early', 'NAVAL_TRANSPORT', 'Hard',
                                             overrides={
                                                 'Priority':  '7',
                                                 'Max':       '1',
                                                 'Aggressive':'yes',
                                                 'Suicide':   'yes',
                                             })
                trigger = self._create_always_trigger(
                    team, 'nod', 'Hard',
                    f"NOD SAPC Demolitions Truck{suffix} Hard")
                if cy_id:
                    self._gate_trigger_enemy_building(trigger, cy_id)
                self.teams.append(team)
                self.triggers.append(trigger)

    def _dtruck_rush_script(self) -> list[tuple[int,int]]:
        """DTRUCK rush: straight to enemy base, no gathering needed."""
        return [
            (53, 0),   # move to enemy base
            (0,  2),   # attack structures
            (0,  1),   # attack anything
            (49, 0),   # repeat
        ]


    def generate_suicide_rush_waves(self, difficulties: list[str]):
        """
        Generate rush waves for all suicide units (Weapon.Suicide=yes).

        ── Infantry suicide (TERROR, TERRORR, etc.) ───────────────────
          1. Land transport available → transport wave (always preferred)
          2. No transport, no war factory in faction → foot rush on Easy only
             (early game only; once war factory exists, better options exist)
          3. No transport, war factory exists → skip foot rush

        ── Vehicle suicide (DTRUCK, etc.) ─────────────────────────────
          1. Naval transport available (naval=yes, sizelimit ≥ unit size)
             → naval delivery wave (safest delivery, works across water)
          2. No naval transport → solo drive wave (always, if speed viable)
             Speed check: speed ≥ faction_avg_vehicle_speed × 0.5
             Iron Curtain: no special scripting — AI fires IC naturally
          3. Land transport for vehicles: only if no naval AND land transport
             has large enough sizelimit (vehicles are often size=3+)

        All waves use RUSH phase and Early difficulty framing.
        """
        from .rulesmd_parser import IniParser
        from .taskforce_generator import TaskforceEntry
        import random
        ini = IniParser(self.parser.ini.path)

        FODDER_COST_MAX = 300
        SOLO_SPEED_FACTOR = 0.5   # min fraction of faction avg for solo viability

        # ── helpers ────────────────────────────────────────────────────

        def _slots(uid):
            """(available_slots, sizelimit) for a transport unit.
            A transport is considered FULL and unusable if InitialPayload
            occupies all passenger slots (ip_total >= passengers).
            """
            s = ini.section(uid)
            if not s: return 0, 0
            try:
                p  = int(s.get('passengers','0') or '0')
                ip = s.get('initialpayload.nums','')
                ip_total = sum(int(n.strip()) for n in ip.split(',')
                               if n.strip()) if ip else 0
                if ip_total >= p:
                    return 0, 0   # fully preloaded — no room for additional units
                sl = int(s.get('sizelimit','99') or '99')
                return max(0, p - ip_total), sl
            except ValueError:
                return 0, 0

        def _unit_speed(uid):
            s = ini.section(uid)
            if not s: return 0
            try: return int(s.get('speed','0') or '0')
            except ValueError: return 0

        def _unit_size(uid):
            s = ini.section(uid)
            if not s: return 1
            try: return int(s.get('size','1') or '1')
            except ValueError: return 1

        def _rh(uid):
            s = ini.section(uid)
            if not s: return frozenset()
            raw = s.get('requiredhouses','')
            return frozenset(h.strip() for h in raw.split(',') if h.strip())

        def _rh_compatible(uid_a, uid_b, faction):
            fc = frozenset(self.parser.sides.faction_countries.get(faction, []))
            rh_a = _rh(uid_a) & fc if _rh(uid_a) else fc
            rh_b = _rh(uid_b) & fc if _rh(uid_b) else fc
            return bool(rh_a & rh_b)

        def _get_transports(faction, unit_size):
            """All transports (land or naval) with sizelimit >= unit_size
            and at least 1 available slot (not fully preloaded).
            Sorted best-first: most available slots."""
            result = []
            seen = set()
            for r in self.parser.buildable:
                if r.faction != faction: continue
                if r.unit_id in seen: continue
                if self._is_flying_transport(r):
                    continue   # boarding softlock — see _is_flying_transport
                s = ini.section(r.unit_id)
                if not s: continue
                passengers = int(s.get('passengers','0') or '0')
                if passengers < 1: continue
                # Must be a transport (land or naval)
                naval = s.get('naval','no').lower() == 'yes'
                if not (r.is_transport or r.is_open_transport or naval):
                    continue
                avail, sl = _slots(r.unit_id)
                if avail >= 1 and sl >= unit_size:
                    seen.add(r.unit_id)
                    result.append((r, avail, sl))
            # Best: most available slots
            return sorted(result, key=lambda x: -x[1])

        def _has_war_factory(faction):
            """True if faction has any vehicle-producing buildable unit."""
            for r in self.parser.buildable:
                if r.faction != faction: continue
                if r.category != 'Vehicle': continue
                if r.is_suicide or r.is_transport: continue
                if r.grade >= 5:
                    return True
            return False

        def _has_iron_curtain(faction):
            """True if faction's side has a usable Iron Curtain SW."""
            sw_sec = ini.section('SuperWeaponTypes') or {}
            for k, swid in sw_sec.items():
                swid = swid.strip() if swid else ''
                if not swid: continue
                sw = ini.section(swid)
                if not sw: continue
                if sw.get('type','').lower() != 'ironcurtain': continue
                if sw.get('sw.allowai','yes').lower() == 'no': continue
                # Check if this SW is hosted on a building this faction can build
                # or is AlwaysGranted
                if sw.get('sw.alwaysgranted','no').lower() == 'yes':
                    return True
                # Check if any faction building hosts this SW
                for bid in ini.list_from_section('BuildingTypes'):
                    bs = ini.section(bid)
                    if not bs: continue
                    owner = bs.get('owner','')
                    fc = self.parser.sides.faction_countries.get(faction, [])
                    if any(c in owner for c in fc):
                        sw_list = bs.get('superweapons','') + ',' + bs.get('superweapon','')
                        if swid in sw_list:
                            return True
            return False

        def _faction_avg_vehicle_speed(faction):
            speeds = []
            for r in self.parser.buildable:
                if r.faction != faction: continue
                if r.category != 'Vehicle': continue
                if r.is_suicide: continue
                spd = _unit_speed(r.unit_id)
                if spd > 0:
                    speeds.append(spd)
            return sum(speeds) / len(speeds) if speeds else 5.0

        def _get_fodder(faction, exclude_uid):
            out = []
            for r in self.parser.buildable:
                if r.faction != faction: continue
                if r.category != 'Infantry': continue
                if r.is_suicide or r.unit_id == exclude_uid: continue
                s = ini.section(r.unit_id)
                if not s: continue
                try:
                    cost = int(s.get('cost','9999') or '9999')
                except ValueError:
                    cost = 9999
                if cost <= FODDER_COST_MAX:
                    out.append(r)
            return out

        # ── shortcuts for team/trigger creation ───────────────────────

        def _make_wave(su, units, faction, phase, role, difficulty,
                       label, script_acts, script_key, trigger_name,
                       aggressive=True):
            eff = self._effective_owner(units, faction)
            if eff is None:
                return False

            existing = self.tf_reg.find_duplicate(units)
            if not existing:
                tf_id = self.tf_reg.generate()
                self.tf_reg.register_composition(tf_id, units)
                tf_e = TaskforceEntry(
                    tf_id         = tf_id,
                    name          = label,
                    label_comment = f'; [{faction.upper()}][{phase.upper()}][SUICIDE_{role}]',
                    units         = units, faction = faction,
                    phase         = phase, role    = 'RUSH', difficulty = difficulty,
                )
                if tf_e.tf_id not in self._appended_tf_ids:
                    self._appended_tf_ids.add(tf_e.tf_id)
                    self.taskforces.append(tf_e)
                tf_id_use = tf_id
            else:
                tf_id_use = existing

            script_id = self._get_or_create_script(
                script_key, script_acts, trigger_name + ' script')
            overrides = {'Aggressive': 'yes', 'Suicide': 'yes'}
            team = self._create_team(
                tf_id_use, script_id, faction, phase, 'RUSH', difficulty,
                overrides=overrides)
            trigger = self._create_always_trigger(
                team, faction, difficulty, trigger_name)
            self.teams.append(team)
            self.triggers.append(trigger)
            return True

        # ── main loop ─────────────────────────────────────────────────

        suicide_units = [r for r in self.parser.buildable
                         if r.is_suicide and r.faction]
        if not suicide_units:
            return

        load_act = 10103 if self.phobos else 14

        for su in suicide_units:
            faction  = su.faction
            unit_sz  = _unit_size(su.unit_id)
            su_speed = _unit_speed(su.unit_id)
            rng      = random.Random(hash(su.unit_id) & 0xFFFFFF)

            # All transports that can carry this unit (land or naval)
            all_tr = _get_transports(faction, unit_sz)
            compat_tr = [(r, av, sl) for r, av, sl in all_tr
                         if _rh_compatible(su.unit_id, r.unit_id, faction)]

            has_ic = _has_iron_curtain(faction)

            if su.category == 'Infantry':
                # ── Infantry suicide ──────────────────────────────────
                # Transport wave if available (always preferred)
                # Foot rush:
                #   Easy: always allowed (early game spirit)
                #   Medium/Hard: only if no transport available
                for difficulty in difficulties:
                    if compat_tr:
                        # Transport wave
                        tr_rec, avail, _ = compat_tr[0]  # best (most slots first)
                        n_su   = avail
                        units  = [(1, tr_rec.unit_id), (n_su, su.unit_id)]
                        units  = self._scale_units(units)
                        n_su   = max(1, units[1][0]) if len(units) > 1 else 1
                        sc_key  = f'suicide_tr_{faction}_{su.unit_id}_{difficulty}'
                        # Load at base first, THEN gather near the enemy and spill.
                        sc_acts = [(load_act,0), (43,0), (53,0), (0,0), (49,0)]
                        _make_wave(su, units, faction, 'Early', 'TRANSPORT',
                                   difficulty,
                                   f'1 {tr_rec.name} {n_su} {su.name}',
                                   sc_acts, sc_key,
                                   f'{faction.upper()} Suicide Transport '
                                   f'{su.name} {difficulty}')
                    else:
                        # Foot rush
                        # Easy: always generate (even with war factory)
                        # Medium/Hard: skip if war factory exists
                        if difficulty != 'Easy' and _has_war_factory(faction):
                            continue
                        fodder  = _get_fodder(faction, su.unit_id)
                        escort  = rng.sample(fodder, min(3, len(fodder)))
                        n_su    = {'Easy':3,'Medium':5,'Hard':8}.get(difficulty,3)
                        units   = [(n_su, su.unit_id)]
                        for e in escort:
                            units.append((2, e.unit_id))
                        escort_str = (', '.join(e.name[:10] for e in escort)
                                      if escort else 'no escort')
                        sc_key  = f'suicide_foot_{faction}_{su.unit_id}_{difficulty}'
                        sc_acts = [(53,0), (0,0), (49,0)]
                        _make_wave(su, units, faction, 'Early', 'FOOT',
                                   difficulty,
                                   f'{n_su} {su.name} ({escort_str})',
                                   sc_acts, sc_key,
                                   f'{faction.upper()} Suicide Foot '
                                   f'{su.name} {difficulty}')

            else:
                # ── Vehicle suicide (DTRUCK etc.) ─────────────────────
                # Transport wave: best available (naval preferred via slot count)
                # IC script: if Iron Curtain exists, inject action 55 before attack
                # Solo: always generated if speed viable (with or without transport)
                avg_speed   = _faction_avg_vehicle_speed(faction)
                solo_viable = su_speed >= avg_speed * SOLO_SPEED_FACTOR

                for difficulty in difficulties:
                    # Transport wave (best transport regardless of naval/land)
                    if compat_tr:
                        tr_rec, avail, _ = compat_tr[0]
                        n_su  = max(1, avail // unit_sz) if unit_sz > 0 else 1
                        units = [(1, tr_rec.unit_id), (n_su, su.unit_id)]
                        units = self._scale_units(units)
                        n_su  = max(1, units[1][0]) if len(units) > 1 else 1
                        sc_key = f'suicide_tr_{faction}_{su.unit_id}_{difficulty}'
                        # IC: move → load → [fire IC] → attack
                        if has_ic:
                            # Load, gather near enemy, THEN fire IC (at the target,
                            # so it doesn't expire in transit), then attack.
                            sc_acts = [(load_act,0),(43,0),(53,0),(55,0),(0,6),(49,0)]
                            wave_type = 'TRANSPORT_IC'
                        else:
                            sc_acts = [(load_act,0),(43,0),(53,0),(0,0),(49,0)]
                            wave_type = 'TRANSPORT'
                        _make_wave(su, units, faction, 'Early', wave_type,
                                   difficulty,
                                   f'1 {tr_rec.name} {n_su} {su.name}',
                                   sc_acts, sc_key,
                                   f'{faction.upper()} Suicide Transport '
                                   f'{su.name} {difficulty}')

                    # Solo drive (always if speed viable)
                    if solo_viable:
                        n_su  = {'Easy':1,'Medium':2,'Hard':3}.get(difficulty,1)
                        units = [(n_su, su.unit_id)]
                        sc_key = f'suicide_solo_{faction}_{su.unit_id}_{difficulty}'
                        # IC solo: move → [fire IC] → attack
                        if has_ic:
                            sc_acts = [(53,0),(55,0),(0,6),(49,0)]
                            wave_type = 'SOLO_IC'
                        else:
                            sc_acts = [(53,0),(0,0),(49,0)]
                            wave_type = 'SOLO'
                        _make_wave(su, units, faction, 'Early', wave_type,
                                   difficulty,
                                   f'{n_su} {su.name} solo',
                                   sc_acts, sc_key,
                                   f'{faction.upper()} Suicide Solo '
                                   f'{su.name} {difficulty}')


    def generate_deployfire_waves(self, difficulties: list[str]):
        """
        Generate waves for DeployFire=yes units whose secondary weapon is
        their primary combat weapon (e.g. SCHP Siege Chopper).

        These units need a specialized script:
          1. Move to enemy base proximity
          2. Deploy (action 4) — switches to secondary weapon
          3. Attack structures (secondary weapon fires automatically when deployed)
          4. Undeploy (action 5) when done
          5. Repeat

        Only generates waves for units where:
          - is_deployfire=True
          - secondary weapon is significantly stronger than primary
            (secondary dps_building > primary dps_building × 2)
        """
        BOARDING_OK = {'Land', 'Amphibious', 'Tunnel', 'Hover'}

        deployfire_units = [
            r for r in self.parser.buildable
            if r.is_deployfire
            and not r.is_utility
            and not r.is_transport
            and not r.is_aircraft_type   # AI can't deploy aircraft — see Phobos PR #565
            and r.grade >= 0
        ]

        for r in deployfire_units:
            sec        = self.ini.section(r.unit_id)
            pri_id     = sec.get('primary', '')
            sec_id     = sec.get('secondary', '')
            if not sec_id:
                continue

            # Check that secondary is stronger for building damage
            from .rulesmd_parser import WeaponAnalyzer
            wa      = WeaponAnalyzer(self.ini)
            pri_wpn = wa.analyze_weapon(pri_id) if pri_id else {}
            sec_wpn = wa.analyze_weapon(sec_id)

            def bld_dps(wpn):
                v   = wpn.get('verses', [0]*11)
                dmg = wpn.get('damage', 0)
                rof = wpn.get('rof', 1) or 1
                bld = max(v[6:9]) if len(v) >= 9 else 0
                return (dmg / rof) * bld

            pri_bld = bld_dps(pri_wpn)
            sec_bld = bld_dps(sec_wpn)

            # Check that secondary is substantially better for building damage,
            # OR has significantly longer range (artillery role)
            # Threshold: secondary building DPS >= primary × 1.2
            # OR secondary range >= primary range × 1.5 (ranged arty)
            pri_wpn_sec = self.ini.section(pri_id) if pri_id else {}
            sec_wpn_sec = self.ini.section(sec_id) if sec_id else {}
            pri_range = float(pri_wpn_sec.get('range', '0')) if pri_wpn_sec else 0
            sec_range = float(sec_wpn_sec.get('range', '0')) if sec_wpn_sec else 0

            is_artillery = (sec_bld >= pri_bld * 1.2 or
                           (sec_range >= pri_range * 1.5 and sec_bld > 0) or
                           r.is_radiation)   # radiation field is always valuable

            if not is_artillery:
                continue

            faction = r.faction
            units   = [(3, r.unit_id)]   # small group — deploy units work best in numbers

            units    = self._scale_units(units)
            _eff_owner = self._effective_owner(units, faction)
            if _eff_owner is None:
                return  # contradictory RequiredHouses
            existing = self.tf_reg.find_duplicate(units)
            if not existing:
                tf_id = self.tf_reg.generate()
                self.tf_reg.register_composition(tf_id, units)
                from .taskforce_generator import TaskforceEntry
                tf_entry = TaskforceEntry(
                    tf_id         = tf_id,
                    name          = f"3 {r.name}",
                    label_comment = (f"; [{faction.upper()}][{r.tier.upper()}]"
                                     f"[DEPLOYFIRE_ARTY][DIRECT_ATTACK]"),
                    units         = units,
                    faction       = faction,
                    phase         = r.tier,
                    role          = 'DEPLOYFIRE_ARTY',
                    difficulty    = 'all',
                )
                if tf_entry.tf_id not in self._appended_tf_ids:
                    self._appended_tf_ids.add(tf_entry.tf_id)
                    self.taskforces.append(tf_entry)
                tf_id_use = tf_id
            else:
                tf_id_use = existing

            # Script: move to enemy base → Deploy → attack → Undeploy → loop
            sc_acts   = self._deployfire_script(r.unit_id)
            script_id = self._get_or_create_script(
                f'deployfire_{r.unit_id.lower()}', sc_acts,
                f'{r.name} Deploy Artillery')

            for difficulty in difficulties:
                team    = self._create_team(tf_id_use, script_id, faction,
                                             r.tier, 'DEPLOYFIRE_ARTY', difficulty)
                trigger = self._create_always_trigger(
                    team, faction, difficulty,
                    f"{faction.upper()} {r.name} Artillery {difficulty}")
                self.teams.append(team)
                self.triggers.append(trigger)

    def _deployfire_script(self, unit_id: str) -> list[tuple[int,int]]:
        """
        Deploy-fire artillery script for ground-based DeployFire units
        (Desolator, Prism Tank, etc). Aircraft-based DeployFire units
        (Siege Chopper) are FILTERED OUT upstream — vanilla YR AI cannot
        deploy aircraft. See TODO: Phobos PR #565 may lift this restriction.

        Ground DeployFire units auto-transition between undeployed and
        deployed state as they move and stop. This script relies on that
        auto-behavior rather than explicit deploy/undeploy actions:

          53,0  → move to enemy base (unit unfolds legs when it stops)
          4,0   → Guard for 0 minutes (near-immediate pass-through,
                  gives time for auto-deploy to complete)
          0,2   → attack structures (deployed weapon fires)
          0,1   → attack anything
          5,0   → Guard area (undeploys on next move)
          49,0  → repeat until success
        """
        return [
            (53, 0),   # move to enemy base
            (4,  0),   # Guard 0 min — allows auto-deploy after 53,0 movement
            (0,  2),   # attack structures
            (0,  1),   # attack anything
            (5,  0),   # Guard area — undeploys on next move if needed
            (49, 0),   # repeat
        ]

    def generate_parasite_waves(self, difficulties: list[str]):
        """
        Generate Terror Drone (parasite) harassment waves.

        Strategy:
          - Parasite units attach to enemy vehicles and deal continuous
            damage that cannot be stopped without a Service Depot or Grinder.
          - Only effective if enemy has NO service depot AND NO grinder.
          - Condition: DMPRNOSERVICEDEPOT and DMPRNOGRINDER absence dummies
            (if dummy prereq system is available).
          - Without dummy prereqs: generate as standard Always-true triggers
            with lower weight — the AI will naturally stop using them if
            the dummy system is present and detects countermeasures.

          - Optimal target: enemy vehicle concentrations.
          - Groups of 5-8 drones work well — once they infect multiple
            vehicles the damage compounds.
          - NOD only in vanilla (DRON is Russian/Confederation/African/Arab).
        """
        if not self.parser.parasite_units:
            return

        # Service depot / grinder detection
        # These buildings let enemies remove parasites — makes drones useless
        countermeasure_buildings = (
            self.parser.service_depots + self.parser.grinders)

        for r in self.parser.parasite_units:
            faction = r.faction
            if not faction:
                continue

            # Only generate parasite waves for vehicle-targeting parasites.
            # Dogs target infantry — already in general waves.
            # Giant Squid targets naval units (Naval=yes) — different handling.
            # Terror Drone targets land vehicles — the intended use case.
            if r.is_naval_unit:
                continue  # naval parasite — skip (TODO: naval harassment waves)

            # Check: primary weapon verses vs vehicles (indices 3-5) > 0
            # AND unit is not a dog-class infantry unit (they have their own waves)
            rw  = r.role_weapon or {}
            v   = rw.get('verses', [0]*11)
            av  = max(v[3:6]) if len(v) >= 6 else 0
            if av <= 0:
                continue  # doesn't target vehicles — skip

            # Generate two wave sizes: harassment (5) and swarm (8)
            for count in [5, 8]:
                units    = [(count, r.unit_id)]
                units    = self._scale_units(units)
                _eff_owner = self._effective_owner(units, faction)
                if _eff_owner is None:
                    continue  # contradictory RequiredHouses
                existing = self.tf_reg.find_duplicate(units)
                if not existing:
                    tf_id = self.tf_reg.generate()
                    self.tf_reg.register_composition(tf_id, units)
                    from .taskforce_generator import TaskforceEntry
                    label = (f"; [{faction.upper()}][EARLY][PARASITE_HARASS]"
                             f"\n; Only effective if enemy has no service depot"
                             f" ({', '.join(countermeasure_buildings[:3])})")
                    tf_entry = TaskforceEntry(
                        tf_id         = tf_id,
                        name          = f"{count} {r.name}",
                        label_comment = label,
                        units         = units,
                        faction       = faction,
                        phase         = 'Early',
                        role          = 'PARASITE_HARASS',
                        difficulty    = 'all',
                    )
                    if tf_entry.tf_id not in self._appended_tf_ids:
                        self._appended_tf_ids.add(tf_entry.tf_id)
                        self.taskforces.append(tf_entry)
                    tf_id_use = tf_id
                else:
                    tf_id_use = existing

                sc_acts   = self._parasite_script()
                script_id = self._get_or_create_script(
                    f'parasite_harass_{count}', sc_acts,
                    f'Parasite Harassment x{count}')

                for difficulty in difficulties:
                    # Hard only for swarm, all difficulties for harassment
                    if count == 8 and difficulty != 'Hard':
                        continue

                    team = self._create_team(
                        tf_id_use, script_id, faction, 'Early',
                        'PARASITE_HARASS', difficulty,
                        overrides={
                            'Priority':  '5',   # lower priority than main attacks
                            'Max':       '2',
                            'Aggressive':'yes',
                            'Suicide':   'no',
                        })

                    # Weight: lower if enemy has countermeasures available
                    # (full strategic suppression requires dummy prereq system)
                    trigger = self._create_always_trigger(
                        team, faction, difficulty,
                        f"{faction.upper()} {r.name} x{count} {difficulty}")

                    self.teams.append(team)
                    self.triggers.append(trigger)

    def _parasite_script(self) -> list[tuple[int,int]]:
        """
        Parasite harassment: hunt enemy vehicles.
        Action 0,5 = hunt specific target type (vehicles).
        """
        return [
            (53, 0),   # move to enemy base
            (0,  4),   # attack vehicles specifically
            (0,  1),   # attack anything else
            (49, 0),   # repeat
        ]

    def generate_mind_control_waves(self, difficulties: list[str]):
        """
        Mind control waves: YURI/YURIPR/MIND and any other unit whose primary
        weapon has MindControl=yes.

        Mind control permanently converts an enemy unit — it's treated as
        instantly killing them AND gaining a free unit. The AI should use these
        units aggressively to target high-value vehicles and hero units.

        Script: Hunt vehicles (preferred targets) → anything → loop.
        Uses Aggressive + Hunt rather than a scripted base attack, since mind
        control works on whatever is closest. Small groups work best to avoid
        having one MC unit re-control what another just controlled.
        """
        # Faction=None units (stolen tech) skip normal waves
        mc_units = [r for r in self.parser.buildable
                    if r.is_mind_control and not r.is_transport
                    and r.faction is not None]

        for r in mc_units:
            faction = r.faction
            # Small groups — MC units shouldn't clump (they'd fight over targets)
            counts = [2, 3] if r.grade >= 20 else [2]

            for count in counts:
                units = [(count, r.unit_id)]
                units    = self._scale_units(units)
                _eff_owner = self._effective_owner(units, faction)
                if _eff_owner is None:
                    continue  # contradictory RequiredHouses
                existing = self.tf_reg.find_duplicate(units)
                if not existing:
                    tf_id = self.tf_reg.generate()
                    self.tf_reg.register_composition(tf_id, units)
                    from .taskforce_generator import TaskforceEntry
                    tf_entry = TaskforceEntry(
                        tf_id         = tf_id,
                        name          = f"{count} {r.name}",
                        label_comment = (f"; [{faction.upper()}][{r.tier.upper()}]"
                                         f"[MIND_CONTROL][HUNT]"),
                        units         = units,
                        faction       = faction,
                        phase         = r.tier,
                        role          = 'MIND_CONTROL',
                        difficulty    = 'all',
                    )
                    if tf_entry.tf_id not in self._appended_tf_ids:
                        self._appended_tf_ids.add(tf_entry.tf_id)
                        self.taskforces.append(tf_entry)
                    tf_id_use = tf_id
                else:
                    tf_id_use = existing

                # Hunt script — MC works best when units seek targets themselves
                sc_acts = [
                    (53, 0),   # move to enemy base
                    (0,  4),   # attack vehicles (prime MC targets)
                    (0,  1),   # attack anything
                    (49, 0),   # repeat
                ]
                script_id = self._get_or_create_script(
                    f'mind_control_{count}', sc_acts,
                    f'Mind Control Hunt x{count}')

                for difficulty in difficulties:
                    # Hard gets both group sizes; Easy/Medium only small group
                    if count > 2 and difficulty != 'Hard':
                        continue

                    team = self._create_team(
                        tf_id_use, script_id, faction, r.tier,
                        'MIND_CONTROL', difficulty,
                        overrides={
                            'Priority':  '6',
                            'Max':       '3',
                            'Aggressive':'yes',
                            'Suicide':   'no',
                        })

                    trigger = self._create_always_trigger(
                        team, faction, difficulty,
                        f"{faction.upper()} {r.name} x{count} MC {difficulty}")

                    self.teams.append(team)
                    self.triggers.append(trigger)

    def generate_chaos_waves(self, difficulties: list[str]):
        """
        Chaos drone waves: units whose primary warhead has Psychedelic=yes.

        The Chaos Drone (CAOS) deploys a psychic field causing nearby enemy units
        to attack each other. Tactically equivalent to removing a cluster of
        enemy units from the fight. Best used ahead of a main attack wave.

        The drone deploys to use its primary weapon (ChaosAttack) — it doesn't
        need to manually attack, just get close and the field propagates.
        Script: move to enemy base → hunt (chaos fires automatically) → loop.
        """
        chaos_units = [r for r in self.parser.buildable
                       if r.is_chaos and not r.is_transport
                       and r.faction is not None]

        for r in chaos_units:
            faction = r.faction
            # 1–2 chaos drones are enough to affect a large area
            for count in [1, 2]:
                units = [(count, r.unit_id)]
                units    = self._scale_units(units)
                _eff_owner = self._effective_owner(units, faction)
                if _eff_owner is None:
                    continue  # contradictory RequiredHouses
                existing = self.tf_reg.find_duplicate(units)
                if not existing:
                    tf_id = self.tf_reg.generate()
                    self.tf_reg.register_composition(tf_id, units)
                    from .taskforce_generator import TaskforceEntry
                    tf_entry = TaskforceEntry(
                        tf_id         = tf_id,
                        name          = f"{count} {r.name}",
                        label_comment = (f"; [{faction.upper()}][{r.tier.upper()}]"
                                         f"[CHAOS_FIELD][HUNT]"),
                        units         = units,
                        faction       = faction,
                        phase         = r.tier,
                        role          = 'CHAOS_FIELD',
                        difficulty    = 'all',
                    )
                    if tf_entry.tf_id not in self._appended_tf_ids:
                        self._appended_tf_ids.add(tf_entry.tf_id)
                        self.taskforces.append(tf_entry)
                    tf_id_use = tf_id
                else:
                    tf_id_use = existing

                sc_acts = [
                    (53, 0),   # move to enemy base — chaos field fires automatically
                    (0,  1),   # attack anything (ensures engagement)
                    (49, 0),   # repeat
                ]
                script_id = self._get_or_create_script(
                    f'chaos_hunt_{count}', sc_acts,
                    f'Chaos Drone x{count}')

                for difficulty in difficulties:
                    if count > 1 and difficulty == 'Easy':
                        continue

                    team = self._create_team(
                        tf_id_use, script_id, faction, r.tier,
                        'CHAOS_FIELD', difficulty,
                        overrides={
                            'Priority':  '5',
                            'Max':       '2',
                            'Aggressive':'yes',
                            'Suicide':   'no',
                        })

                    trigger = self._create_always_trigger(
                        team, faction, difficulty,
                        f"{faction.upper()} {r.name} x{count} Chaos {difficulty}")

                    self.teams.append(team)
                    self.triggers.append(trigger)

    def generate_sqd_naval_waves(self, difficulties: list[str]):
        """
        Giant Squid anti-naval waves.

        SQD is a submerged parasite unit that grabs enemy naval vessels,
        instantly halting them and applying steady damage. A group of squids
        on Hunt mode is a devastating anti-naval force that can lock down
        an entire enemy fleet.

        Unlike normal parasite waves, SQD specifically hunts naval targets,
        not land vehicles. Uses vehicle hunt script since naval units are
        classified as vehicles internally.

        Grade is -6 due to low raw damage, but the grab effect makes SQD
        uniquely powerful against naval units.
        """
        sqd_units = [r for r in self.parser.buildable
                     if r.is_submerged and r.is_parasite
                     and not r.is_transport
                     and r.faction is not None]

        for r in sqd_units:
            faction = r.faction
            # Groups of 3 or 5 squids
            for count in [3, 5]:
                units = [(count, r.unit_id)]
                units    = self._scale_units(units)
                _eff_owner = self._effective_owner(units, faction)
                if _eff_owner is None:
                    continue  # contradictory RequiredHouses
                existing = self.tf_reg.find_duplicate(units)
                if not existing:
                    tf_id = self.tf_reg.generate()
                    self.tf_reg.register_composition(tf_id, units)
                    from .taskforce_generator import TaskforceEntry
                    tf_entry = TaskforceEntry(
                        tf_id         = tf_id,
                        name          = f"{count} {r.name}",
                        label_comment = (f"; [{faction.upper()}][{r.tier.upper()}]"
                                         f"[NAVAL_PARASITE][HUNT]"),
                        units         = units,
                        faction       = faction,
                        phase         = 'Mid',
                        role          = 'NAVAL_PARASITE',
                        difficulty    = 'all',
                    )
                    if tf_entry.tf_id not in self._appended_tf_ids:
                        self._appended_tf_ids.add(tf_entry.tf_id)
                        self.taskforces.append(tf_entry)
                    tf_id_use = tf_id
                else:
                    tf_id_use = existing

                sc_acts = [
                    (0,  4),   # attack vehicles (naval = vehicle class)
                    (49, 0),   # repeat until success
                    (0,  1),   # attack anything
                    (49, 0),
                    (6,  0),   # loop
                ]
                script_id = self._get_or_create_script(
                    f'sqd_naval_hunt_{count}', sc_acts,
                    f'Squid Naval Hunt x{count}')

                for difficulty in difficulties:
                    if count > 3 and difficulty == 'Easy':
                        continue

                    team = self._create_team(
                        tf_id_use, script_id, faction, 'Mid',
                        'NAVAL_PARASITE', difficulty,
                        overrides={
                            'Priority':  '6',
                            'Max':       '2',
                            'Aggressive':'yes',
                            'Suicide':   'no',
                        })

                    trigger = self._create_always_trigger(
                        team, faction, difficulty,
                        f"{faction.upper()} {r.name} x{count} Naval {difficulty}")

                    self.teams.append(team)
                    self.triggers.append(trigger)

    def generate_dog_waves(self, difficulties: list[str]):
        """
        Attack Dog harassment waves.

        Dogs (DOG, ADOG, and canine variants) are infantry-parasite units
        that instantly kill enemy infantry on contact using a bite attack
        with InfDeath=1 (instant infantry death regardless of HP).

        They are highly effective against:
          - Infantry-heavy rushes (conscript spam, GI formations)
          - Engineers approaching your base
          - Enemy harassing infantry

        Script: move to enemy base → hunt infantry → repeat.
        Small packs (3-5) sent frequently work better than large groups.
        """
        dog_units = [r for r in self.parser.buildable
                     if getattr(r, 'is_dog', False)
                     and r.faction is not None]

        for r in dog_units:
            faction = r.faction
            for count in [3, 5]:
                units = [(count, r.unit_id)]
                units    = self._scale_units(units)
                _eff_owner = self._effective_owner(units, faction)
                if _eff_owner is None:
                    continue  # contradictory RequiredHouses
                existing = self.tf_reg.find_duplicate(units)
                if not existing:
                    tf_id = self.tf_reg.generate()
                    self.tf_reg.register_composition(tf_id, units)
                    from .taskforce_generator import TaskforceEntry
                    tf_entry = TaskforceEntry(
                        tf_id         = tf_id,
                        name          = f"{count} {r.name}",
                        label_comment = (f"; [{faction.upper()}][{r.tier.upper()}]"
                                         f"[DOG_HARASS][HUNT_INFANTRY]"),
                        units         = units,
                        faction       = faction,
                        phase         = 'Rush',
                        role          = 'DOG_HARASS',
                        difficulty    = 'all',
                    )
                    if tf_entry.tf_id not in self._appended_tf_ids:
                        self._appended_tf_ids.add(tf_entry.tf_id)
                        self.taskforces.append(tf_entry)
                    tf_id_use = tf_id
                else:
                    tf_id_use = existing

                # Hunt infantry specifically
                sc_acts = [
                    (53, 0),   # move to enemy base
                    (0,  3),   # attack infantry (type 3 = infantry)
                    (0,  1),   # attack anything remaining
                    (49, 0),   # repeat
                ]
                script_id = self._get_or_create_script(
                    f'dog_harass_{count}', sc_acts,
                    f'Dog Harassment x{count}')

                for difficulty in difficulties:
                    if count > 3 and difficulty == 'Easy':
                        continue

                    team = self._create_team(
                        tf_id_use, script_id, faction, 'Rush',
                        'DOG_HARASS', difficulty,
                        overrides={
                            'Priority':  '4',
                            'Max':       '3',
                            'Aggressive':'yes',
                            'Suicide':   'no',
                        })

                    trigger = self._create_always_trigger(
                        team, faction, difficulty,
                        f"{faction.upper()} {r.name} x{count} Harass {difficulty}")

                    self.teams.append(team)
                    self.triggers.append(trigger)

    # ------------------------------------------------------------------
    # Items 5 + 6: Defensive/slow/specialist waves
    # ------------------------------------------------------------------

    def generate_defensive_sniper_waves(self, difficulties: list[str]):
        """
        Defensive hunt waves for slow units with long range (SNIPE, VIRUS etc.)
        and pure anti-vehicle specialists (TNKD, GGI).

        These units are poor in a rush (slow speed) but excel from a distance
        or against specific target types. The AI should use them on Hunt mode
        rather than scripted attacks — they find their own targets.

        Long-range criteria: weapon_range >= 1.4× faction average AND slow.
        Anti-vehicle criteria: AG-Anti_Vehicle in roles, no GENERALIST/AG-Multi.

        Script: hunt primary target type → anything → loop.
        """
        ranges = [r.weapon_range for r in self.parser.buildable
                  if r.weapon_range > 0 and r.faction and not r.is_naval_unit]
        speeds = [r.speed for r in self.parser.buildable
                  if r.speed > 0 and r.faction and not r.is_naval_unit
                  and not r.is_transport]
        if not ranges or not speeds:
            return
        avg_range = sum(ranges) / len(ranges)
        avg_speed = sum(speeds) / len(speeds)

        # Long-range slow infantry/vehicles that belong on hunt not rush
        LONG_RANGE_THRESHOLD = avg_range * 1.4
        SLOW_THRESHOLD       = avg_speed * 0.75

        processed = set()
        for r in self.parser.buildable:
            if not r.faction or r.is_naval_unit or r.is_transport:
                continue
            if r.is_utility or r.is_mind_control or r.is_chaos:
                continue  # these have dedicated waves
            if r.grade < 8:
                continue

            is_long_range = r.weapon_range >= LONG_RANGE_THRESHOLD
            is_slow       = r.speed > 0 and r.speed <= SLOW_THRESHOLD
            is_av_spec    = ('AG-Anti_Vehicle' in r.roles and
                             'GENERALIST' not in r.roles and
                             'AG-Multi' not in r.roles)

            if not (is_long_range or is_av_spec):
                continue
            # Skip arty — they already get ARTY waves in main generator
            if 'ARTY' in r.roles and not is_av_spec:
                continue
            if r.unit_id in processed:
                continue
            processed.add(r.unit_id)

            faction = r.faction

            # Determine hunt target: anti-vehicle → vehicles; anti-infantry → infantry
            if is_av_spec:
                hunt_action = (0, 4)  # attack vehicles
                role_label  = 'AV_HUNT'
                note        = f"Anti-vehicle hunt: {r.name}"
            elif 'AG-Anti_Infantry' in r.roles:
                hunt_action = (0, 3)  # attack infantry
                role_label  = 'AI_SNIPE'
                note        = f"Long-range anti-infantry hunt: {r.name}"
            else:
                hunt_action = (0, 1)  # attack anything
                role_label  = 'HUNT'
                note        = f"Hunt: {r.name}"

            for count in [3, 5]:
                units = [(count, r.unit_id)]
                units    = self._scale_units(units)
                _eff_owner = self._effective_owner(units, faction)
                if _eff_owner is None:
                    continue  # contradictory RequiredHouses
                existing = self.tf_reg.find_duplicate(units)
                if not existing:
                    tf_id = self.tf_reg.generate()
                    self.tf_reg.register_composition(tf_id, units)
                    from .taskforce_generator import TaskforceEntry
                    tf_entry = TaskforceEntry(
                        tf_id         = tf_id,
                        name          = f"{count} {r.name}",
                        label_comment = (f"; [{faction.upper()}][{r.tier.upper()}]"
                                         f"[{role_label}][HUNT]"),
                        units         = units,
                        faction       = faction,
                        phase         = r.tier,
                        role          = role_label,
                        difficulty    = 'all',
                    )
                    if tf_entry.tf_id not in self._appended_tf_ids:
                        self._appended_tf_ids.add(tf_entry.tf_id)
                        self.taskforces.append(tf_entry)
                    tf_id_use = tf_id
                else:
                    tf_id_use = existing

                sc_acts = [
                    hunt_action,    # hunt primary target type
                    (49, 0),
                    (0, 1),         # attack anything
                    (49, 0),
                    (6, 0),
                ]
                script_id = self._get_or_create_script(
                    f'{role_label.lower()}_{r.unit_id}_{count}',
                    sc_acts, f'{r.name} x{count} Hunt')

                for difficulty in difficulties:
                    if count > 3 and difficulty == 'Easy':
                        continue

                    team = self._create_team(
                        tf_id_use, script_id, faction, r.tier,
                        role_label, difficulty,
                        overrides={
                            'Priority':   '5',
                            'Max':        '2',
                            'Aggressive': 'yes',
                            'Suicide':    'no',
                        })
                    trigger = self._create_always_trigger(
                        team, faction, difficulty,
                        f"{faction.upper()} {r.name} x{count} Hunt {difficulty}")
                    self.teams.append(team)
                    self.triggers.append(trigger)

    def generate_hero_companion_waves(self, difficulties: list[str]):
        """
        Hero companion waves (BuildLimit > 0 units).
        Heroes (TANY, BORIS, YURIPR) are paired with companions that:
          - Cover the hero's weaknesses (what they can't kill well)
          - Match the hero's speed (within ±2 speed units)
          - Are good at what the hero needs support on

        Composition: 1 hero + 2-3 companions on Hunt.
        The hero is included in the taskforce to ensure it moves with
        the group rather than being recruited separately.
        """
        ini = self.parser.ini
        # Find all hero units
        hero_units = []
        for sect in ('InfantryTypes','VehicleTypes','AircraftTypes'):
            for uid in ini.list_from_section(sect):
                s = ini.section(uid)
                try:
                    bl = int(s.get('buildlimit','0') or '0')
                except:
                    continue
                if bl > 0:
                    r = self.parser.units.get(uid)
                    if r and r.faction and r.grade >= 10:
                        hero_units.append(r)

        for hero in hero_units:
            faction = hero.faction

            # Find what the hero is weak against
            hero_roles = set(hero.roles)
            weak_vs_air  = 'AA' not in hero_roles and 'AA_Secondary' not in hero_roles
            weak_vs_veh  = 'AG-Anti_Vehicle' not in hero_roles and 'AG-Multi' not in hero_roles
            weak_vs_inf  = 'AG-Anti_Infantry' not in hero_roles

            # Find best companions that cover weaknesses and match speed
            companions = []
            speed_min = max(1, hero.speed - 2)
            speed_max = hero.speed + 2

            # Prioritize coverage of weaknesses
            hero_mobility = getattr(hero, 'mobility', 'Land')
            for r in sorted(self.parser.buildable,
                            key=lambda x: -x.grade):
                if r.unit_id == hero.unit_id:
                    continue
                if r.faction != faction:
                    continue
                if r.is_transport or r.is_utility or r.grade < 8:
                    continue
                # Mobility compatibility: a ground hero can't be escorted
                # by aircraft that outpace it and can't be defended by it.
                r_mob = getattr(r, 'mobility', 'Land')
                if r_mob != hero_mobility:
                    # Only allow if both are in a mixable group
                    from .taskforce_generator import (LAND_MIX_GROUP,
                                                      NAVAL_MIX_GROUP)
                    if not (hero_mobility in LAND_MIX_GROUP
                            and r_mob in LAND_MIX_GROUP):
                        if not (hero_mobility in NAVAL_MIX_GROUP
                                and r_mob in NAVAL_MIX_GROUP):
                            continue
                if not (speed_min <= r.speed <= speed_max):
                    continue

                covers_weakness = (
                    (weak_vs_air and 'AA' in r.roles) or
                    (weak_vs_veh and 'AG-Anti_Vehicle' in r.roles) or
                    (weak_vs_inf and 'AG-Anti_Infantry' in r.roles) or
                    'GENERALIST' in r.roles
                )
                if covers_weakness and len(companions) < 3:
                    companions.append(r)
                if len(companions) >= 3:
                    break

            if not companions:
                # Fallback: best generalist units matching speed
                companions = sorted(
                    [r for r in self.parser.buildable
                     if r.faction == faction and r.grade >= 8
                     and 'GENERALIST' in r.roles
                     and speed_min <= r.speed <= speed_max
                     and r.unit_id != hero.unit_id],
                    key=lambda x: -x.grade)[:2]

            if not companions:
                continue

            # Build taskforce: 1 hero + 2 companions (of each type)
            # Total: 1 + 2 + 2 = 5 individuals, 3 unit types.
            # The hero leads; companions cover weaknesses and match pace.
            units = [(1, hero.unit_id)] + [(2, c.unit_id) for c in companions[:2]]

            units    = self._scale_units(units)
            _eff_owner = self._effective_owner(units, faction)
            if _eff_owner is None:
                return  # contradictory RequiredHouses
            existing = self.tf_reg.find_duplicate(units)
            if not existing:
                tf_id = self.tf_reg.generate()
                self.tf_reg.register_composition(tf_id, units)
                from .taskforce_generator import TaskforceEntry
                comp_names = '+'.join(c.name for c in companions[:2])
                tf_entry = TaskforceEntry(
                    tf_id         = tf_id,
                    name          = f"1 {hero.name} + {comp_names}",
                    label_comment = (f"; [{faction.upper()}][HERO_ESCORT]"
                                     f"[{hero.unit_id}]"),
                    units         = units,
                    faction       = faction,
                    phase         = 'Late',
                    role          = 'HERO_ESCORT',
                    difficulty    = 'all',
                )
                if tf_entry.tf_id not in self._appended_tf_ids:
                    self._appended_tf_ids.add(tf_entry.tf_id)
                    self.taskforces.append(tf_entry)
                tf_id_use = tf_id
            else:
                tf_id_use = existing

            # Hunt script — companions follow hero toward enemy
            sc_acts = [
                (53, 0),   # move to enemy base
                (0,  1),   # attack anything
                (49, 0),
                (6,  0),
            ]
            script_id = self._get_or_create_script(
                f'hero_escort_{hero.unit_id}', sc_acts,
                f'{hero.name} Hero Escort')

            for difficulty in difficulties:
                if difficulty == 'Easy':
                    continue  # heroes on Medium/Hard only

                team = self._create_team(
                    tf_id_use, script_id, faction, 'Late',
                    'HERO_ESCORT', difficulty,
                    overrides={
                        'Priority':   '6',
                        'Max':        '2',
                        'Aggressive': 'yes',
                        'Suicide':    'no',
                    })
                trigger = self._create_always_trigger(
                    team, faction, difficulty,
                    f"{faction.upper()} {hero.name} Escort {difficulty}")
                self.teams.append(team)
                self.triggers.append(trigger)

    def generate_magnetron_patrol_waves(self, difficulties: list[str]):
        """
        Magnetron (is_locomotor_weapon) defensive patrol waves.

        TELE lifts vehicles toward ITSELF using the locomotor beam.
        Vehicles are dragged to the Magnetron's position, disrupting
        enemy formation. They land and become hostile again — the value
        is disruption and AA protection (AA guns shoot lifted vehicles).
        Yuri faction benefits most since they have mind control nearby.

        For mods with a naval Magnetron: drags ships toward the Magnetron,
        exposing them to nearby land-based fire.

        The Magnetron also has a secondary artillery weapon (MagneShake)
        effective against buildings — useful as a base-perimeter defender.

        Script: area guard near own base — Magnetrons stay home and
        intercept vehicles that approach within beam range.
        """
        ini = self.parser.ini
        loco_units = [r for r in self.parser.buildable
                      if r.is_locomotor_weapon and r.faction
                      and not r.is_naval_unit]

        for r in loco_units:
            faction = r.faction

            # Check if faction has a grinder — if so, drag toward it
            has_grinder = bool(self.parser.grinders)

            for count in [2, 3]:
                units = [(count, r.unit_id)]
                units    = self._scale_units(units)
                _eff_owner = self._effective_owner(units, faction)
                if _eff_owner is None:
                    continue  # contradictory RequiredHouses
                existing = self.tf_reg.find_duplicate(units)
                if not existing:
                    tf_id = self.tf_reg.generate()
                    self.tf_reg.register_composition(tf_id, units)
                    from .taskforce_generator import TaskforceEntry
                    label = (f"; [{faction.upper()}][DEFENSIVE_PATROL]"
                             f"[MAGNETRON]{'[GRINDER]' if has_grinder else ''}")
                    tf_entry = TaskforceEntry(
                        tf_id         = tf_id,
                        name          = f"{count} {r.name}",
                        label_comment = label,
                        units         = units,
                        faction       = faction,
                        phase         = 'Mid',
                        role          = 'MAGNETRON_PATROL',
                        difficulty    = 'all',
                    )
                    if tf_entry.tf_id not in self._appended_tf_ids:
                        self._appended_tf_ids.add(tf_entry.tf_id)
                        self.taskforces.append(tf_entry)
                    tf_id_use = tf_id
                else:
                    tf_id_use = existing

                # Area guard around base — magnetrons stay near home
                sc_acts = [
                    (11, 5),   # guard area (defend home base)
                    (0,  4),   # attack vehicles (magnetron targets vehicles)
                    (49, 0),
                    (6,  0),
                ]
                script_id = self._get_or_create_script(
                    f'magnetron_patrol_{count}', sc_acts,
                    f'Magnetron Patrol x{count}')

                for difficulty in difficulties:
                    if count > 2 and difficulty == 'Easy':
                        continue

                    team = self._create_team(
                        tf_id_use, script_id, faction, 'Mid',
                        'MAGNETRON_PATROL', difficulty,
                        overrides={
                            'Priority':   '4',
                            'Max':        '2',
                            'Aggressive': 'no',   # defensive, not aggressive
                            'Suicide':    'no',
                            'Recruit':    'yes',
                        })
                    trigger = self._create_always_trigger(
                        team, faction, difficulty,
                        f"{faction.upper()} {r.name} x{count} Patrol {difficulty}")
                    self.teams.append(team)
                    self.triggers.append(trigger)

    def generate_electric_assault_waves(self, difficulties: list[str]):
        """
        ElectricAssault defensive overcharge patrol.

        SHK (Shock Trooper) uses ElectricAssault=yes to overcharge
        Overpowerable=true buildings (e.g. Tesla Coil), making them
        fire faster and deal more damage.

        This is a purely defensive patrol — SHK stays near own Tesla Coil.
        Only generated when the faction has BOTH:
          - An ElectricAssault unit (SHK)
          - An Overpowerable building in their faction (TESLA for NOD)
        """
        ini = self.parser.ini
        ea_units = [r for r in self.parser.buildable
                    if r.is_electric_assault and r.faction]

        for r in ea_units:
            faction = r.faction
            # Check faction has an overpowerable building
            faction_overpowerable = []
            for bid in self.parser.overpowerable_blds:
                bsec = ini.section(bid)
                prereq = bsec.get('prerequisite','')
                bid_faction = self.parser.sides.faction_from_prereq_chain(prereq)
                if bid_faction == faction:
                    faction_overpowerable.append(bid)

            if not faction_overpowerable:
                continue

            units = [(3, r.unit_id)]
            units    = self._scale_units(units)
            _eff_owner = self._effective_owner(units, faction)
            if _eff_owner is None:
                return  # contradictory RequiredHouses
            existing = self.tf_reg.find_duplicate(units)
            if not existing:
                tf_id = self.tf_reg.generate()
                self.tf_reg.register_composition(tf_id, units)
                from .taskforce_generator import TaskforceEntry
                bld_names = '+'.join(faction_overpowerable)
                tf_entry = TaskforceEntry(
                    tf_id         = tf_id,
                    name          = f"3 {r.name}",
                    label_comment = (f"; [{faction.upper()}][OVERCHARGE_PATROL]"
                                     f"[{bld_names}]"),
                    units         = units,
                    faction       = faction,
                    phase         = 'Mid',
                    role          = 'OVERCHARGE_PATROL',
                    difficulty    = 'all',
                )
                if tf_entry.tf_id not in self._appended_tf_ids:
                    self._appended_tf_ids.add(tf_entry.tf_id)
                    self.taskforces.append(tf_entry)
                tf_id_use = tf_id
            else:
                tf_id_use = existing

            # Vanilla "Soviet Tesla Charge" script pattern:
            #   58,waypoint  → Move to waypoint near Tesla Coil
            #   11,radius    → Guard area (stay idle at that spot)
            # We use waypoint 0 as a proxy — the team assembles near base
            # center and guards. In vanilla this is a specific waypoint near
            # the Tesla but we can't know the map layout at generation time.
            # Using guard area so they stay near where they spawn.
            sc_acts = [
                (53, 0),   # move to own base (closest to Tesla Coil area)
                (11, 7),   # guard area, radius 7 cells — stay idle near Tesla
            ]
            script_id = self._get_or_create_script(
                f'overcharge_guard_{faction}', sc_acts,
                f'Tesla Overcharge Guard {faction}')

            for difficulty in difficulties:
                team = self._create_team(
                    tf_id_use, script_id, faction, 'Mid',
                    'OVERCHARGE_PATROL', difficulty,
                    overrides={
                        'Priority':   '3',
                        'Max':        '2',
                        'Aggressive': 'no',
                        'Suicide':    'no',
                    })
                trigger = self._create_always_trigger(
                    team, faction, difficulty,
                    f"{faction.upper()} {r.name} Overcharge {difficulty}")
                self.teams.append(team)
                self.triggers.append(trigger)

    def generate_ifv_passenger_waves(self, difficulties: list[str]):
        """
        IFV/Gunner vehicle passenger waves.

        Uses Phobos script action 10103 (Load Onto Transports) which correctly
        loads all eligible units into all transports in the taskforce.
        Vanilla action 14 is broken for multiple transports — not used.
        Requires Phobos.

        IFV (Gunner=yes, Passengers=1) changes weapon based on passenger.
        The best anti-infantry infantry for this faction fills the seat.
        """
        if not self.phobos:
            return  # Requires Phobos action 72 — skip on vanilla
        ini = self.parser.ini
        gunner_units = [
            r for r in self.parser.buildable
            if ini.section(r.unit_id).get('gunner','no').lower() == 'yes'
            and r.faction
        ]

        for ifv in gunner_units:
            faction    = ifv.faction
            passengers = int(ini.section(ifv.unit_id).get('passengers','0') or '0')
            if passengers < 1:
                continue

            # Find best passenger by their IFV weapon grade (the weapon the
            # Gunner fires with them inside), not their own weapon grade.
            # ifv_weapon_grade=0 means they give no useful weapon (dogs,
            # engineers, animals) — exclude them.
            candidates = [
                r for r in self.parser.buildable
                if r.faction == faction
                and r.category == 'Infantry'
                and not r.is_transport
                and r.ifv_weapon_grade > 0
                and not r.is_utility
            ]
            if not candidates:
                continue
            best_passenger = max(candidates, key=lambda r: r.ifv_weapon_grade)

            # Pack: 3 IFVs + 3 infantry passengers
            units = [(3, ifv.unit_id), (3, best_passenger.unit_id)]
            units = self._scale_units(units)
            units = self._apply_transport_limits(
                units, ifv.unit_id, passengers)
            _eff_owner = self._effective_owner(units, faction)
            if _eff_owner is None:
                return  # contradictory RequiredHouses
            existing = self.tf_reg.find_duplicate(units)
            if not existing:
                tf_id = self.tf_reg.generate()
                self.tf_reg.register_composition(tf_id, units)
                from .taskforce_generator import TaskforceEntry
                tf_entry = TaskforceEntry(
                    tf_id         = tf_id,
                    name          = f"3 {ifv.name} + 3 {best_passenger.name}",
                    label_comment = (f"; [{faction.upper()}][IFV_LOADED]"
                                     f"[{ifv.unit_id}+{best_passenger.unit_id}]"),
                    units         = units,
                    faction       = faction,
                    phase         = 'Mid',
                    role          = 'IFV_LOADED',
                    difficulty    = 'all',
                )
                if tf_entry.tf_id not in self._appended_tf_ids:
                    self._appended_tf_ids.add(tf_entry.tf_id)
                    self.taskforces.append(tf_entry)
                tf_id_use = tf_id
            else:
                tf_id_use = existing

            sc_acts = [
                (10103, 0),  # Load Onto Transports (Phobos action 10103)
                              # Makes all eligible units in the taskforce enter
                              # the transports. Works with multiple vehicles.
                              # In SP missions follow with (43,0) wait-until-loaded
                              # but in skirmish AI that isn't needed.
                (53, 0),   # move to enemy base
                (0,  3),   # attack infantry (IFV fires anti-infantry weapon)
                (0,  1),   # attack anything
                (49, 0),
                (6,  0),
            ]
            script_id = self._get_or_create_script(
                f'ifv_loaded_{faction}', sc_acts,
                f'IFV Loaded Hunt {faction}')

            for difficulty in difficulties:
                team = self._create_team(
                    tf_id_use, script_id, faction, 'Mid',
                    'IFV_LOADED', difficulty,
                    overrides={
                        'Priority':   '5',
                        'Max':        '3',
                        'Aggressive': 'yes',
                        'Suicide':    'no',
                    })
                trigger = self._create_always_trigger(
                    team, faction, difficulty,
                    f"{faction.upper()} IFV+{best_passenger.name} {difficulty}")
                self.teams.append(team)
                self.triggers.append(trigger)

    def generate_opentopped_waves(self, difficulties: list[str]):
        """
        Generic OpenTopped transport passenger composition.

        For any OpenTopped=yes transport (not just BFRT):
          - Reserve ceil(passengers * 0.2, 1) slots for best anti-infantry
          - Fill remaining slots with best overall infantry

        Infantry graded by their OpenTransportWeapon= (OTW) for fired-from-
        transport weapon effectiveness.

        BFRT already has a dedicated wave (generate_bfrt_waves).
        This covers any other OpenTopped unit the mod may define.
        """
        import math
        ini = self.parser.ini
        BOARDING_OK = {'Land', 'Amphibious', 'Tunnel', 'Hover'}

        for r in self.parser.buildable:
            if not r.faction or not r.is_open_transport:
                continue
            if r.unit_id == 'BFRT':
                continue  # BFRT has dedicated wave

            sec        = ini.section(r.unit_id)
            passengers = int(sec.get('passengers','0') or '0')
            size_limit = int(sec.get('sizelimit','99') or '99')
            if passengers < 2:
                continue

            faction = r.faction

            # Grade infantry by anti-infantry for reserved slot(s)
            # and by overall grade for remaining slots
            candidates = [
                inf for inf in self.parser.buildable
                if inf.faction == faction
                and inf.category == 'Infantry'
                and inf.mobility in BOARDING_OK
                and inf.open_transport_weapon is not None
                and not inf.is_utility
                and inf.grade >= 0
                and int(ini.section(inf.unit_id).get('size','1') or '1') <= size_limit
            ]
            if not candidates:
                continue

            reserved = max(1, math.floor(passengers * 0.2))
        # Round DOWN (floor), not up — a 5-passenger transport reserves 1,
        # not 2. Never reserve more than the design calls for.
            ai_slots  = reserved
            gen_slots = passengers - ai_slots

            # Best anti-infantry for reserved slot
            best_ai = max(candidates,
                          key=lambda x: x.grade_breakdown.get('dps_infantry', 0)
                          if x.grade_breakdown else 0)
            # Best overall for remaining slots (different unit if possible)
            best_gen = max((c for c in candidates if c.unit_id != best_ai.unit_id),
                           key=lambda x: x.grade,
                           default=best_ai)

            units = [(1, r.unit_id), (ai_slots, best_ai.unit_id)]
            if gen_slots > 0 and best_gen.unit_id != best_ai.unit_id:
                units.append((gen_slots, best_gen.unit_id))

            units    = self._scale_units(units)
            _eff_owner = self._effective_owner(units, faction)
            if _eff_owner is None:
                return  # contradictory RequiredHouses
            existing = self.tf_reg.find_duplicate(units)
            if not existing:
                tf_id = self.tf_reg.generate()
                self.tf_reg.register_composition(tf_id, units)
                from .taskforce_generator import TaskforceEntry
                tf_entry = TaskforceEntry(
                    tf_id         = tf_id,
                    name          = f"1 {r.name}+{ai_slots} {best_ai.name}",
                    label_comment = (f"; [{faction.upper()}][OPENTOPPED]"
                                     f"[{r.unit_id}]"),
                    units         = units,
                    faction       = faction,
                    phase         = 'Mid',
                    role          = 'OPENTOPPED',
                    difficulty    = 'all',
                )
                if tf_entry.tf_id not in self._appended_tf_ids:
                    self._appended_tf_ids.add(tf_entry.tf_id)
                    self.taskforces.append(tf_entry)
                tf_id_use = tf_id
            else:
                tf_id_use = existing

            sc_acts = [
                (53, 0),
                (0,  1),
                (49, 0),
                (6,  0),
            ]
            script_id = self._get_or_create_script(
                f'opentopped_{r.unit_id}', sc_acts,
                f'OpenTopped {r.name}')

            for difficulty in difficulties:
                team = self._create_team(
                    tf_id_use, script_id, faction, 'Mid',
                    'OPENTOPPED', difficulty,
                    overrides={
                        'Priority':   '5',
                        'Max':        '2',
                        'Aggressive': 'yes',
                        'Suicide':    'no',
                    })
                trigger = self._create_always_trigger(
                    team, faction, difficulty,
                    f"{faction.upper()} {r.name} Loaded {difficulty}")
                self.teams.append(team)
                self.triggers.append(trigger)

    def generate_country_hero_waves(self, difficulties: list[str]):
        """
        Country-specific hero companion waves.

        Heroes (BuildLimit=1) are available to all countries in a faction,
        not just one. This generates per-country waves pairing the hero with
        the best country-exclusive or country-available companions that cover
        the hero's weaknesses and match their speed.

        Avoids duplicating the faction-wide hero companion waves — only
        generates waves that include at least one country-exclusive unit
        alongside the hero, so the country trigger is meaningful.

        Also covers country-exclusive non-hero specials (SEAL=GHOST, SNIPER)
        that aren't heroes but deserve dedicated waves.
        """
        ini = self.parser.ini

        # Collect all heroes
        heroes = []
        for sect in ('InfantryTypes','VehicleTypes','AircraftTypes'):
            for uid in ini.list_from_section(sect):
                s = ini.section(uid)
                try:
                    bl = int(s.get('buildlimit','0') or '0')
                except: continue
                if bl > 0:
                    r = self.parser.units.get(uid)
                    if r and r.faction and r.grade >= 10:
                        heroes.append(r)

        # For each country, find which heroes are available there
        for faction, countries in self.parser.sides.faction_countries.items():
            for country in countries:
                # Heroes available to this country
                country_heroes = []
                for hero in heroes:
                    if hero.faction != faction:
                        continue
                    s = ini.section(hero.unit_id)
                    owner_list = [o.strip() for o in
                                  s.get('owner','').split(',') if o.strip()]
                    if country in owner_list:
                        country_heroes.append(hero)

                # Country-exclusive units (unique to this country)
                exclusive = [
                    r for r in self.parser.buildable
                    if r.faction == faction
                    and r.countries == {country}
                    and r.grade >= 5
                    and not r.is_transport
                ]

                # Country-wide specials: units available to this country but
                # NOT all countries — only include if truly limited (≤2 countries)
                specials = []
                for r in self.parser.buildable:
                    if r.faction != faction or r.is_transport:
                        continue
                    s = ini.section(r.unit_id)
                    owner_list = [o.strip() for o in
                                  s.get('owner','').split(',') if o.strip()]
                    # "Special" = available to at most 2 countries (truly limited)
                    if (1 < len(owner_list) <= 2
                            and country in owner_list
                            and r.grade >= 15
                            and r not in exclusive
                            and not any(r.unit_id == h.unit_id
                                        for h in country_heroes)):
                        specials.append(r)

                if not country_heroes and not (exclusive or specials):
                    continue

                # Only generate country-specific waves if there are actually
                # country-exclusive or country-special units to include.
                # If no exclusives/specials exist, the faction-wide hero
                # companion waves (generate_hero_companion_waves) already cover it.
                if not (exclusive or specials):
                    continue

                for hero in country_heroes:
                    # Find best country-exclusive or country-special companion
                    # that covers hero's weakness and matches speed
                    weak_vs_air = 'AA' not in hero.roles
                    weak_vs_veh = ('AG-Anti_Vehicle' not in hero.roles
                                   and 'AG-Multi' not in hero.roles)
                    speed_min   = max(1, hero.speed - 2)
                    speed_max   = hero.speed + 2

                    # Prefer exclusive units, then specials, then best faction units
                    pool = exclusive + specials
                    companions = []
                    hero_mobility = getattr(hero, 'mobility', 'Land')
                    for r in sorted(pool, key=lambda x: -x.grade):
                        # Mobility compatibility (ground hero cannot escort with aircraft)
                        r_mob = getattr(r, 'mobility', 'Land')
                        if r_mob != hero_mobility:
                            from .taskforce_generator import (LAND_MIX_GROUP,
                                                              NAVAL_MIX_GROUP)
                            if not (hero_mobility in LAND_MIX_GROUP
                                    and r_mob in LAND_MIX_GROUP):
                                if not (hero_mobility in NAVAL_MIX_GROUP
                                        and r_mob in NAVAL_MIX_GROUP):
                                    continue
                        if not (speed_min <= r.speed <= speed_max):
                            continue
                        covers = (
                            (weak_vs_air and 'AA' in r.roles) or
                            (weak_vs_veh and 'AG-Anti_Vehicle' in r.roles) or
                            'GENERALIST' in r.roles
                        )
                        if covers and len(companions) < 2:
                            companions.append(r)
                    if not companions:
                        companions = sorted(pool, key=lambda x: -x.grade)[:1]
                    if not companions:
                        continue

                    units = [(1, hero.unit_id)] + [
                        (2, c.unit_id) for c in companions[:2]]
                    units = self._scale_units(units)
                    # Heroes always 1
                    units = [(1, uid) if uid == hero.unit_id else (cnt, uid)
                             for cnt, uid in units]

                    _eff_owner = self._effective_owner(units, faction)
                    if _eff_owner is None:
                        return  # contradictory RequiredHouses
                    existing = self.tf_reg.find_duplicate(units)
                    if not existing:
                        tf_id = self.tf_reg.generate()
                        self.tf_reg.register_composition(tf_id, units)
                        from .taskforce_generator import TaskforceEntry
                        comp_str = '+'.join(c.name for c in companions[:2])
                        tf_entry = TaskforceEntry(
                            tf_id         = tf_id,
                            name          = f"1 {hero.name}+{comp_str}",
                            label_comment = (
                                f"; [{country.upper()}][HERO_COUNTRY_ESCORT]"
                                f"[{hero.unit_id}]"),
                            units         = units,
                            faction       = faction,
                            phase         = 'Late',
                            role          = 'HERO_ESCORT',
                            difficulty    = 'all',
                        )
                        if tf_entry.tf_id not in self._appended_tf_ids:
                            self._appended_tf_ids.add(tf_entry.tf_id)
                            self.taskforces.append(tf_entry)
                        tf_id_use = tf_id
                    else:
                        tf_id_use = existing

                    sc_acts = [
                        (53, 0),
                        (0,  1),
                        (49, 0),
                        (6,  0),
                    ]
                    script_id = self._get_or_create_script(
                        f'hero_country_{country}_{hero.unit_id}',
                        sc_acts,
                        f'{hero.name} {country} Escort')

                    for difficulty in difficulties:
                        if difficulty == 'Easy':
                            continue

                        team = self._create_team(
                            tf_id_use, script_id, faction, 'Late',
                            'HERO_ESCORT', difficulty,
                            country=country,
                            overrides={
                                'Priority':   '6',
                                'Max':        '2',
                                'Aggressive': 'yes',
                                'Suicide':    'no',
                            })
                        trigger = self._create_always_trigger(
                            team, faction, difficulty,
                            f"{country} {hero.name} Escort {difficulty}")
                        self.teams.append(team)
                        self.triggers.append(trigger)

    def generate_engineer_rush_waves(self, difficulties: list[str]):
        """
        Engineer rush waves — fast/cheap transports loaded with engineers
        to capture enemy buildings.

        Viability criteria for the transport:
          - speed >= average vehicle speed (fast enough to reach enemy base)
          - cost <= 1.5× average vehicle cost (affordable to spam)
          - passengers >= 1, sizelimit >= engineer size (default 1)
          - not a suicide transport (HTK/SAPC with TERROR payload)

        Engineer selection:
          - Engineer=yes infantry matching the AI's faction barracks prereq
          - Each faction has its own engineer type (ENGINEER/SENGINEER/YENGINEER)
          - IFVMode=1 on all engineers → RepairBullet (score=0) so they're
            excluded from IFV weapon waves — but here we explicitly want them

        Composition: N transports + engineers to fill exactly N × capacity.
        Script: move to enemy base → unload → (engineers auto-capture on contact)

        Note: This requires Phobos (action 10103) when multiple transports
        are used. Falls back to 1 transport on vanilla (action 14).
        """
        ini = self.parser.ini

        # Find all engineer unit IDs (Engineer=yes tag).
        # Engineers are NOT in parser.buildable — they have Prerequisite=Barracks
        # which is faction-ambiguous, so they're excluded from grading.
        # Scan InfantryTypes directly and match faction by:
        #  1. Direct prereq chain resolution (concrete building IDs)
        #  2. PrerequisiteBarracks alias expansion from [General]
        #  3. Owner= field if present
        # This handles mods where engineers may not exist, have been removed,
        # or belong to a new faction with different prereq structures.

        # Build barracks alias map: barracks building ID → faction
        gen_sec = ini.section('General') or {}
        barracks_blds = [b.strip() for b in
                         gen_sec.get('prerequisitebarracks','').split(',')
                         if b.strip()]
        barracks_faction = {}
        for bid in barracks_blds:
            f = self.parser.sides.faction_from_prereq_chain(bid)
            if f:
                barracks_faction[bid.upper()] = f

        # Build engineer → faction map using ForbiddenHouses.
        # ForbiddenHouses lists countries that CANNOT build this unit.
        # An engineer belongs to the faction whose countries are NOT forbidden.
        # e.g. ENGINEER forbids Soviets+Yuri → belongs to GDI faction.
        # This correctly handles mods that remove engineers or add new factions.
        all_engineers = {}
        country_to_faction = {}
        for faction, countries in self.parser.sides.faction_countries.items():
            for country in countries:
                country_to_faction[country] = faction

        for uid in ini.list_from_section('InfantryTypes'):
            s = ini.section(uid)
            if s.get('engineer','no').lower() != 'yes':
                continue

            fh_raw   = s.get('forbiddenhouses','')
            forbidden = frozenset(h.strip() for h in fh_raw.split(',') if h.strip())
            owner_raw = s.get('owner','')
            owners    = frozenset(o.strip() for o in owner_raw.split(',') if o.strip())

            # Faction = the faction whose countries can build this unit:
            # allowed countries = owners - forbidden
            allowed = owners - forbidden
            factions_allowed = set(country_to_faction.get(c) for c in allowed
                                   if country_to_faction.get(c))

            if len(factions_allowed) == 1:
                faction = next(iter(factions_allowed))
                all_engineers.setdefault(faction, uid)
            # If multiple factions allowed (ambiguous), skip — handled by
            # faction-specific fallback below

        # Fallback: known vanilla engineer IDs if ForbiddenHouses wasn't enough
        for uid, faction in [('ENGINEER','gdi'),('SENGINEER','nod'),
                              ('YENGINEER','thirdside')]:
            if faction not in all_engineers:
                if uid in [u for u in ini.list_from_section('InfantryTypes')]:
                    all_engineers.setdefault(faction, uid)

        if not all_engineers:
            return  # No engineers found for any faction — skip

        # Average vehicle speed and cost for rush threshold
        vehicles = [r for r in self.parser.buildable
                    if r.category == 'Vehicle' and r.faction and r.speed > 0]
        if not vehicles:
            return
        avg_speed = sum(r.speed for r in vehicles) / len(vehicles)
        avg_cost  = sum(int(ini.section(r.unit_id).get('cost','0') or '0')
                        for r in vehicles) / len(vehicles)
        speed_threshold = avg_speed
        cost_threshold  = avg_cost * 1.5

        # Find viable rush transports — use parser.buildable so blacklisted
        # units (TL=-1 like HIND) are automatically excluded.
        viable = []
        for r in self.parser.buildable:
            if r.category != 'Vehicle' or not r.faction:
                continue
            s   = ini.section(r.unit_id)
            if not (s.get('gunner','no').lower() == 'yes' or
                    s.get('opentopped','no').lower() == 'yes' or
                    int(s.get('passengers','0') or '0') > 0):
                continue
            cost       = int(s.get('cost','0') or '0')
            passengers = int(s.get('passengers','0') or '0')
            sizelimit  = int(s.get('sizelimit','99') or '99')

            # Subtract InitialPayload slots — these are pre-filled passengers
            # that cannot be unloaded or replaced. The transport may have
            # no room left for engineers.
            ip_nums_raw = s.get('initialpayload.nums','')
            ip_total = 0
            if ip_nums_raw.strip():
                try:
                    ip_total = sum(int(n.strip()) for n in ip_nums_raw.split(',')
                                   if n.strip())
                except ValueError:
                    ip_total = 0
            available_slots = max(0, passengers - ip_total)

            if r.speed < speed_threshold:
                continue   # too slow
            if cost > cost_threshold:
                continue   # too expensive
            if available_slots < 1:
                continue   # no room for engineers
            if sizelimit < 1:
                continue   # can't fit infantry (size=1)
            if r.is_suicide and passengers < 1:
                continue

            viable.append(r)

        for transport in viable:
            faction    = transport.faction
            eng_uid    = all_engineers.get(faction)
            if not eng_uid:
                continue

            s          = ini.section(transport.unit_id)
            passengers = int(s.get('passengers','0') or '0')
            ip_nums_raw = s.get('initialpayload.nums','')
            ip_total = 0
            if ip_nums_raw.strip():
                try:
                    ip_total = sum(int(n.strip()) for n in ip_nums_raw.split(',') if n.strip())
                except ValueError:
                    ip_total = 0
            available_slots = max(1, passengers - ip_total)  # min 1 (already filtered)
            load_act   = 10103 if self.phobos else 14
            import math
            MIN_ENGINEERS = 5
            # Use available_slots (after InitialPayload) not total passengers
            if self.phobos:
                n_transport = max(1, math.ceil(MIN_ENGINEERS / available_slots))
            else:
                n_transport = 1
            n_engineers = n_transport * available_slots

            units = [(n_transport, transport.unit_id), (n_engineers, eng_uid)]
            units = self._scale_units(units)
            # Vanilla: cap to 1 transport, engineers = available_slots (not total passengers)
            units = self._apply_transport_limits(
                units, transport.unit_id, available_slots)

            _eff_owner = self._effective_owner(units, faction)
            if _eff_owner is None:
                return  # contradictory RequiredHouses
            existing = self.tf_reg.find_duplicate(units)
            if not existing:
                tf_id = self.tf_reg.generate()
                self.tf_reg.register_composition(tf_id, units)
                from .taskforce_generator import TaskforceEntry
                n_t, n_e = units[0][0], units[1][0]
                tf_entry = TaskforceEntry(
                    tf_id         = tf_id,
                    name          = (f"{n_t} {transport.name} "
                                     f"{n_e} Engineer"),
                    label_comment = (f"; [{faction.upper()}][EARLY]"
                                     f"[ENGINEER_RUSH][CAPTURE]"),
                    units         = units,
                    faction       = faction,
                    phase         = 'Early',
                    role          = 'ENGINEER_RUSH',
                    difficulty    = 'all',
                )
                if tf_entry.tf_id not in self._appended_tf_ids:
                    self._appended_tf_ids.add(tf_entry.tf_id)
                    self.taskforces.append(tf_entry)
                tf_id_use = tf_id
            else:
                tf_id_use = existing

            # Flying transports (SHAD-class) need vanilla's boarding pattern:
            # anchor the whole team at the transport's own factory (58) so
            # the flyer lands at a STATIC rendezvous and the engineers walk
            # in — without the anchor they chase the airborne transport,
            # block every landing cell, and the team softlocks. Vanilla's
            # own Blackhawk scripts all open with 58,<GAWEAP>
            # ("Allied Transport vs Sov Conyard" et al.).
            is_flyer = self._is_flying_transport(transport)
            anchor   = None
            if is_flyer:
                fac = getattr(transport, 'factory_anchor', None)
                fac_idx = self._building_idx(fac) if fac else -1
                if fac_idx < 0:
                    continue   # no anchor building — can't board a flyer safely
                anchor = self.BWP_NEAREST + fac_idx

            # Per-matchup: move engineers TO the enemy CY (47) and unload there
            # so they capture it on contact, rather than dropping at the edge.
            cy_targets = self._conyard_targets() or [(None, None)]
            for cy_id, cy_arg in cy_targets:
                suffix = f" vs {cy_id}" if cy_id else ""
                travel = [(47, cy_arg)] if cy_arg is not None else [(53, 0)]
                if anchor is not None:
                    # vanilla air pattern: rendezvous at own factory → load →
                    # wait fully loaded → fly to target → 8,2 unload (drops
                    # the transport from the team; TransportsReturnOnUnload
                    # sends it home — delivery only, never a combatant)
                    sc_acts = ([(58, anchor), (load_act, 0), (43, 0)]
                               + travel + [(8, 2), (0, 2), (49, 0)])
                    script_key = f'engineer_rush_air{fac_idx}_{faction}_{cy_id or "edge"}'
                else:
                    sc_acts = [(load_act, 0)] + travel + [(8, 0), (0, 2), (49, 0)]
                    script_key = f'engineer_rush_{faction}_{cy_id or "edge"}'
                script_id = self._get_or_create_script(
                    script_key, sc_acts,
                    f'Engineer Rush {faction}{suffix}')

                for difficulty in difficulties:
                    # Engineer rushes are early-game tactics — all difficulties
                    # but weight them lower on Hard (better strategies exist)
                    team_overrides = {
                        'Priority':   '6',
                        'Max':        '2',
                        'Aggressive': 'yes',
                        'Suicide':    'no',
                    }
                    if anchor is not None:
                        # pairs with the 8,2 unload: the flyer goes home
                        team_overrides['TransportsReturnOnUnload'] = 'yes'
                    team = self._create_team(
                        tf_id_use, script_id, faction, 'Early',
                        'ENGINEER_RUSH', difficulty,
                        overrides=team_overrides)
                    trigger = self._create_always_trigger(
                        team, faction, difficulty,
                        f"{faction.upper()} Engineer Rush{suffix} {difficulty}")
                    if cy_id:
                        self._gate_trigger_enemy_building(trigger, cy_id)
                    self.teams.append(team)
                    self.triggers.append(trigger)
