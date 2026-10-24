"""
aiext_generator.py
==================
Generates [TriggerID.AIExt] sidecar sections for AITriggerTypeExt.dll.

Provides:
  - Scanner systems that analyse the mod's rulesmd.ini to produce
    game-state conditions (AA threat, ConYard presence, spy viability,
    high-value targets, etc.)
  - AIExtConditions dataclass that encodes one trigger's .AIExt block
  - AIExtWriter that serialises conditions to [TriggerID.AIExt] sections
    in aimd.ini
  - AIExtConfig that controls which trigger families are active and
    what thresholds to use

All scanners are mod-agnostic — they derive everything from INI tags,
never from hardcoded unit IDs.
"""

from __future__ import annotations
from ..engine.ini_model import guard_ini_text

import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# AIExt condition dataclass
# ---------------------------------------------------------------------------

@dataclass
class AIExtConditions:
    """
    All optional .AIExt prerequisites for one trigger.
    None means "no constraint on this field".
    -1 on a Max means uncapped.
    TargetHouseMode defaults to 'Any' for all enemy/neutral checks since
    that is more reliable than 'Current' in the vanilla engine.
    """
    # Owner (AI itself)
    owner_buildings:      list[str] = field(default_factory=list)
    owner_buildings_min:  list[int] = field(default_factory=list)
    owner_buildings_max:  list[int] = field(default_factory=list)

    owner_credits_min:    Optional[int] = None
    owner_credits_max:    Optional[int] = None

    owner_power_min:      Optional[int] = None   # net surplus
    owner_power_max:      Optional[int] = None

    # Enemy
    enemy_buildings:      list[str] = field(default_factory=list)
    enemy_buildings_min:  list[int] = field(default_factory=list)
    enemy_buildings_max:  list[int] = field(default_factory=list)

    enemy_units:          list[str] = field(default_factory=list)
    enemy_units_min:      list[int] = field(default_factory=list)
    enemy_units_max:      list[int] = field(default_factory=list)

    enemy_credits_min:    Optional[int] = None
    enemy_credits_max:    Optional[int] = None

    enemy_power_min:      Optional[int] = None
    enemy_power_max:      Optional[int] = None

    enemy_power_production_min: Optional[int] = None
    enemy_power_production_max: Optional[int] = None

    # Threat: this trigger's own team range as a percentage of the enemy's
    # (100 = matched; Min=100 = "don't dispatch if my team is outranged").
    team_range_ratio_min:  Optional[int] = None
    team_range_ratio_lock: Optional[str] = None   # 'AA' | 'AG' | None

    # Neutral / civilian house (tech buildings, oil derricks, etc.)
    neutral_buildings:      list[str] = field(default_factory=list)
    neutral_buildings_min:  list[int] = field(default_factory=list)
    neutral_buildings_max:  list[int] = field(default_factory=list)

    target_house_mode:    str = 'Current'   # Matches DLL default; only emit when overridden
    allies_mode:          Optional[str] = None

    # Elapsed game-time gate (AITriggerTypeExt DLL feature).
    # elapsed_seconds_min: trigger cannot fire until this many seconds have
    #   elapsed since the game started. Used to shift AI from early-game
    #   patterns (capture, defensive) to mid/late-game patterns (destroy,
    #   aggressive). Default None = no minimum.
    # elapsed_seconds_max: trigger cannot fire after this many seconds.
    #   Rarely used but supports fading-out early-game-only triggers.
    #
    # NOTE: the exact INI tag name depends on the DLL. Currently emitting
    # `RequiredElapsedTimeMin=` / `RequiredElapsedTimeMax=` following the
    # existing Required*Min/Max convention. If the DLL uses a different
    # tag name (e.g. `ElapsedSecondsMin=` or `MinElapsedTime=`), update
    # the two `write_if` calls in _serialise below to match.
    elapsed_seconds_min:  Optional[int] = None
    elapsed_seconds_max:  Optional[int] = None

    # Debug messages — Priority 1 in the DLL roadmap. When set, the DLL
    # displays the given CSF string on the corresponding lifecycle event
    # (build-start / cancel / deploy). Guarded by [Debug].DisplayAIWaveMessages
    # at the top level so shipping builds are silent by default.
    #
    # DLL tag names emitted:
    #   DebugMessageDisplay.Consider=STT:CSFKey
    #   DebugMessageDisplay.Cancel=STT:CSFKey
    #   DebugMessageDisplay.Finish=STT:CSFKey
    # ============================================================================
    # Debug configuration — unified per-tag "debug quad" system.
    #
    # Each generated AIExt gate can attach up to four debug sub-fields:
    #   Debug.MessageDisplay   — CSF/NOSTR text shown on the game HUD
    #   Debug.ValueDisplay     — bool; append the computed value after the message
    #   Debug.LogMessage       — plain string written to debug.log
    #   Debug.LogWrite         — bool; automatic detailed pass/fail line to log
    #
    # Plus two lifecycle scope fields per tag:
    #   Debug.TriggerDisplay   — list from [start, cancel, finish, never];
    #                            defaults to 'finish'; empty or 'never' = silent
    #   Debug.TriggerLog       — same value shape; defaults to 'finish'
    #
    # Sub-tag inheritance (Option A): if a sub-tag (e.g. RequiredOwnerBuildingsMin)
    # doesn't declare TriggerDisplay/TriggerLog, it inherits from the parent
    # (RequiredOwnerBuildings). If the parent is also silent, the sub-tag is silent.
    #
    # Trigger-level lifecycle (fires once per state transition regardless of gates):
    #   DebugMessageDisplay.Consider / Cancel / Finish   (CSF/NOSTR)
    #   DebugLog.Consider / Cancel / Finish              (raw text)
    # ============================================================================

    # Trigger lifecycle overlay messages
    debug_message_consider:  Optional[str] = None   # DebugMessageDisplay.Consider
    debug_message_cancel: Optional[str] = None
    debug_message_finish: Optional[str] = None
    # Live-hooked lifecycle events (real DLL hooks, unlike Finish which is a
    # no-op scaffold). Start fires at winner-draw; Destroyed/Deleted fire once
    # from the TeamClass destructor (failure / success).
    debug_message_start: Optional[str] = None
    debug_message_destroyed: Optional[str] = None
    debug_message_deleted: Optional[str] = None

    # Trigger lifecycle log messages
    debug_log_consider:  Optional[str] = None
    debug_log_cancel: Optional[str] = None
    debug_log_finish: Optional[str] = None
    debug_log_start: Optional[str] = None
    debug_log_destroyed: Optional[str] = None
    debug_log_deleted: Optional[str] = None

    # Per-tag debug quads. Keyed by the AIExt tag name (e.g.
    # 'RequiredOwnerBuildings', 'RequiredOwnerBuildingsMin', 'TargetHouseMode').
    # Each value is a dict with keys:
    #   'message_display': str or None
    #   'value_display':   bool
    #   'log_message':     str or None
    #   'log_write':       bool
    #   'trigger_display': str or None  (e.g. 'start,finish' — inherits if None)
    #   'trigger_log':     str or None  (inherits from trigger_display if None)
    debug_per_tag: dict = field(default_factory=dict)

    def is_empty(self) -> bool:
        """True if no conditions are set — no .AIExt section needed."""
        return (
            not self.owner_buildings
            and not self.neutral_buildings
            and self.owner_credits_min is None
            and self.owner_credits_max is None
            and self.owner_power_min is None
            and self.owner_power_max is None
            and not self.enemy_buildings
            and not self.enemy_units
            and self.enemy_credits_min is None
            and self.enemy_credits_max is None
            and self.enemy_power_min is None
            and self.enemy_power_max is None
            and self.enemy_power_production_min is None
            and self.enemy_power_production_max is None
            and self.team_range_ratio_min is None
            and self.elapsed_seconds_min is None
            and self.elapsed_seconds_max is None
            and self.debug_message_consider is None
            and self.debug_message_cancel is None
            and self.debug_message_finish is None
            and self.debug_message_start is None
            and self.debug_message_destroyed is None
            and self.debug_message_deleted is None
            and self.debug_log_consider is None
            and self.debug_log_cancel is None
            and self.debug_log_finish is None
            and self.debug_log_start is None
            and self.debug_log_destroyed is None
            and self.debug_log_deleted is None
            and not self.debug_per_tag
        )


# ---------------------------------------------------------------------------
# AA DPS Scanner
# ---------------------------------------------------------------------------

@dataclass
class AAWeaponInfo:
    unit_id:   str
    is_building: bool
    is_base_defense: bool
    dps:       float   # AA damage per second
    strength:  int     # hp of the unit — used as threat weight

    @property
    def weighted_dps(self) -> float:
        """DPS weighted by unit HP as a rough threat proxy."""
        return self.dps * math.log1p(self.strength)


@dataclass
class AAThreatResult:
    """Results of AA scanning for a mod."""
    aa_structure_ids:    list[str]   # building IDs with AA weapons
    aa_unit_ids:         list[str]   # non-building unit IDs with AA weapons
    total_structure_dps: float       # sum of base defense AA DPS
    total_unit_dps:      float       # sum of mobile unit AA DPS
    detail:              list[AAWeaponInfo] = field(default_factory=list)

    def has_aa_structures(self) -> bool:
        return bool(self.aa_structure_ids)

    def has_significant_aa_units(self, dps_threshold: float = 150.0) -> bool:
        """True if mobile AA DPS exceeds threshold."""
        return self.total_unit_dps >= dps_threshold


def scan_aa_threat(ini) -> AAThreatResult:
    """
    Scan all TechnoTypes for anti-air capability.

    AA detection chain:
      TechnoType.Primary= / Secondary=  →  WeaponType
      WeaponType.Projectile=            →  BulletType
      BulletType.AA=yes                 →  this weapon has AA
      DPS = WeaponType.Damage / WeaponType.ROF * 15  (15 frames/sec)

    Both Primary and Secondary are checked.
    Returns AAThreatResult with structure and unit AA info.
    """
    weapon_cache: dict[str, Optional[tuple[float, bool]]] = {}

    def weapon_aa_dps(weapon_id: str) -> Optional[float]:
        """Returns DPS if weapon has AA, else None. Cached."""
        if weapon_id in weapon_cache:
            res = weapon_cache[weapon_id]
            return res[0] if res else None

        wsec = ini.section(weapon_id)
        if not wsec:
            weapon_cache[weapon_id] = None
            return None

        proj_id = wsec.get('projectile', '')
        if not proj_id:
            weapon_cache[weapon_id] = None
            return None

        psec = ini.section(proj_id)
        if not psec:
            weapon_cache[weapon_id] = None
            return None

        if psec.get('aa', 'no').strip().lower() != 'yes':
            weapon_cache[weapon_id] = None
            return None

        try:
            damage = float(wsec.get('damage', '0') or '0')
            rof    = float(wsec.get('rof', '45') or '45')
            if rof <= 0:
                rof = 45
        except ValueError:
            weapon_cache[weapon_id] = None
            return None

        dps = damage / rof * 15.0
        weapon_cache[weapon_id] = (dps, True)
        return dps

    def unit_aa_dps(sec: dict) -> float:
        """Sum AA DPS from all weapon slots of a unit."""
        total = 0.0
        # Vanilla slots
        for slot in ('primary', 'secondary'):
            wid = sec.get(slot, '').strip()
            if wid and wid.lower() not in ('none', ''):
                d = weapon_aa_dps(wid)
                if d is not None:
                    total += d
        # Ares/Phobos multi-weapon slots: Weapon1..WeaponN, EliteWeapon1..N
        # Scan until two consecutive misses
        for prefix in ('weapon', 'eliteweapon'):
            i = 1
            misses = 0
            while misses < 2:
                wid = sec.get(f'{prefix}{i}', '').strip()
                if not wid or wid.lower() == 'none':
                    misses += 1
                else:
                    misses = 0
                    d = weapon_aa_dps(wid)
                    if d is not None:
                        total += d
                i += 1
                if i > 20:   # hard cap — no unit has more than 20 weapon slots
                    break
        return total

    aa_structures: list[AAWeaponInfo] = []
    aa_units:      list[AAWeaponInfo] = []

    # Scan buildings
    for bid in (ini.list_from_section('BuildingTypes') or []):
        if not bid or bid.startswith(';'):
            continue
        sec = ini.section(bid)
        if not sec:
            continue
        dps = unit_aa_dps(sec)
        if dps <= 0:
            continue
        try:
            strength = int(sec.get('strength', '100') or '100')
        except ValueError:
            strength = 100
        is_bd = sec.get('isbasedefense', 'no').strip().lower() == 'yes'
        aa_structures.append(AAWeaponInfo(
            unit_id=bid, is_building=True,
            is_base_defense=is_bd, dps=dps, strength=strength))

    # Scan mobile units (Infantry, Vehicle, Aircraft)
    for list_key in ('InfantryTypes', 'VehicleTypes', 'AircraftTypes'):
        for uid in (ini.list_from_section(list_key) or []):
            if not uid or uid.startswith(';'):
                continue
            sec = ini.section(uid)
            if not sec:
                continue
            dps = unit_aa_dps(sec)
            if dps <= 0:
                continue
            try:
                strength = int(sec.get('strength', '100') or '100')
            except ValueError:
                strength = 100
            aa_units.append(AAWeaponInfo(
                unit_id=uid, is_building=False,
                is_base_defense=False, dps=dps, strength=strength))

    return AAThreatResult(
        aa_structure_ids=[x.unit_id for x in aa_structures],
        aa_unit_ids=[x.unit_id for x in aa_units],
        total_structure_dps=sum(x.dps for x in aa_structures),
        total_unit_dps=sum(x.dps for x in aa_units),
        detail=aa_structures + aa_units,
    )


# ---------------------------------------------------------------------------
# Construction Yard / Rebuild Capability Scanner
# ---------------------------------------------------------------------------

@dataclass
class RebuildCapResult:
    """What building types can rebuild the player's base."""
    conyard_ids:      list[str]   # Factory=BuildingType
    warfactory_ids:   list[str]   # Factory=UnitType, Naval=no
    barracks_ids:     list[str]   # Factory=InfantryType
    airfield_ids:     list[str]   # Factory=AircraftType
    shipyard_ids:     list[str]   # Factory=UnitType, Naval=yes
    service_depot_ids: list[str]  # UnitRepair=yes, no Factory
    mcv_ids:          list[str]   # Deployer=yes, DeploysInto → conyard
    # SW types that can deliver an MCV (UnitDelivery/LimboDelivery)
    mcv_sw_building_ids: list[str]


def scan_rebuild_capability(ini) -> RebuildCapResult:
    """
    Identify all building types relevant to base rebuilding.

    ConYard detection: Factory=BuildingType
    MCV detection:     Deployer=yes AND DeploysInto= points at a ConYard
    MCV-via-SW:        SuperWeaponTypes where Type=UnitDelivery or
                       LimboDelivery and Deliver.Types contains an MCV
    """
    # Build a set of ConYard IDs first
    conyard_ids     = []
    warfactory_ids  = []
    barracks_ids    = []
    airfield_ids    = []
    shipyard_ids    = []
    service_depot_ids = []

    for bid in (ini.list_from_section('BuildingTypes') or []):
        if not bid or bid.startswith(';'):
            continue
        sec = ini.section(bid)
        if not sec:
            continue
        factory   = sec.get('factory', '').strip().lower()
        naval     = sec.get('naval', 'no').strip().lower() == 'yes'
        repair    = sec.get('unitrepair', 'no').strip().lower() == 'yes'

        if factory == 'buildingtype':
            conyard_ids.append(bid)
        elif factory == 'unittype' and naval:
            shipyard_ids.append(bid)
        elif factory == 'unittype':
            warfactory_ids.append(bid)
        elif factory == 'infantrytype':
            barracks_ids.append(bid)
        elif factory == 'aircrafttype':
            airfield_ids.append(bid)
        elif repair and not factory:
            service_depot_ids.append(bid)

    conyard_set = frozenset(c.upper() for c in conyard_ids)

    # MCV detection
    mcv_ids = []
    for list_key in ('VehicleTypes', 'AircraftTypes'):
        for uid in (ini.list_from_section(list_key) or []):
            if not uid or uid.startswith(';'):
                continue
            sec = ini.section(uid)
            if not sec:
                continue
            # MCVs: DeploysInto= pointing to a ConYard
            # Note: vanilla MCVs do NOT have Deployer=yes — that tag is for
            # units that can toggle deploy freely. MCVs just have DeploysInto.
            deploys_into = sec.get('deploysinto', '').strip().upper()
            if deploys_into in conyard_set:
                mcv_ids.append(uid)

    # SW-delivered MCV buildings
    mcv_set = frozenset(m.upper() for m in mcv_ids)
    mcv_sw_building_ids = []

    for swid in (ini.list_from_section('SuperWeaponTypes') or []):
        if not swid or swid.startswith(';'):
            continue
        sec = ini.section(swid)
        if not sec:
            continue
        sw_type = sec.get('type', '').strip().lower()
        if sw_type not in ('unitdelivery', 'limbodelivery'):
            continue
        # Check Deliver.Types for any MCV
        deliver = sec.get('deliver.types', '') or sec.get('limbodelivery.types', '')
        if not deliver:
            continue
        for uid in deliver.split(','):
            uid = uid.strip().upper()
            if uid in mcv_set:
                # Find what building provides this SW
                # Scan all buildings for SuperWeapon= pointing to swid
                for bid in (ini.list_from_section('BuildingTypes') or []):
                    if not bid:
                        continue
                    bsec = ini.section(bid)
                    if not bsec:
                        continue
                    for sw_slot in ('superweapon', 'superweapon2'):
                        if bsec.get(sw_slot, '').strip().upper() == swid.upper():
                            if bid not in mcv_sw_building_ids:
                                mcv_sw_building_ids.append(bid)
                break

    return RebuildCapResult(
        conyard_ids=conyard_ids,
        warfactory_ids=warfactory_ids,
        barracks_ids=barracks_ids,
        airfield_ids=airfield_ids,
        shipyard_ids=shipyard_ids,
        service_depot_ids=service_depot_ids,
        mcv_ids=mcv_ids,
        mcv_sw_building_ids=mcv_sw_building_ids,
    )


# ---------------------------------------------------------------------------
# Spy / Thief Feasibility Scanner
# ---------------------------------------------------------------------------

@dataclass
class SpyInfo:
    unit_id:  str
    name:     str
    factions: list[str]   # factions that can build this unit
    can_steal_money:  bool   # infiltrates refineries
    can_reset_sw:     bool   # infiltrates SW buildings
    can_steal_tech:   bool   # infiltrates tech centers / battle labs
    can_steal_vet:    bool   # infiltrates unit factories


@dataclass
class SpyFeasibilityResult:
    spies:            list[SpyInfo]
    detector_unit_ids: list[str]   # units with DetectDisguise=yes
    detector_bld_ids:  list[str]   # buildings with DetectDisguise=yes (unlikely but scan anyway)

    def has_spies(self) -> bool:
        return bool(self.spies)

    def has_money_spies(self) -> bool:
        return any(s.can_steal_money for s in self.spies)

    def has_sw_reset_spies(self) -> bool:
        return any(s.can_reset_sw for s in self.spies)

    def has_detectors(self) -> bool:
        return bool(self.detector_unit_ids or self.detector_bld_ids)


def scan_spy_feasibility(ini, sides_registry) -> SpyFeasibilityResult:
    """
    Find all functional spy units (Infiltrate=yes AND Agent=yes).
    Also find detector units (DetectDisguise=yes).
    """
    spies: list[SpyInfo] = []
    detector_units: list[str] = []
    detector_blds:  list[str] = []

    # Build faction membership map
    faction_map: dict[str, str] = {}
    if sides_registry:
        for faction, countries in sides_registry.faction_countries.items():
            for country in countries:
                faction_map[country.upper()] = faction

    def unit_factions(sec: dict) -> list[str]:
        """Which factions can build this unit."""
        owner_raw = sec.get('owner', '') or ''
        result = set()
        for country in owner_raw.split(','):
            country = country.strip().upper()
            if country in faction_map:
                result.add(faction_map[country])
        return sorted(result)

    for uid in (ini.list_from_section('InfantryTypes') or []):
        if not uid or uid.startswith(';'):
            continue
        sec = ini.section(uid)
        if not sec:
            continue

        infiltrate = sec.get('infiltrate', 'no').strip().lower() == 'yes'
        agent      = sec.get('agent', 'no').strip().lower() == 'yes'
        detect     = sec.get('detectdisguise', 'no').strip().lower() == 'yes'

        if detect:
            detector_units.append(uid)

        if not (infiltrate and agent):
            continue

        factions = unit_factions(sec)
        name     = sec.get('name', uid)

        # What can this spy do?
        # YR spy logic: infiltrates buildings based on building type
        # Money steal: refineries (Refinery=yes)
        # SW reset:    SW buildings (SuperWeapon= present)
        # Tech steal:  battle labs / tech centers (BuildCat=Tech or TechLevel high)
        # Vet steal:   unit factories
        # In practice ALL spies can do all of these — the game decides
        # based on what building they enter. We tag all as capable.
        spies.append(SpyInfo(
            unit_id=uid,
            name=name,
            factions=factions,
            can_steal_money=True,
            can_reset_sw=True,
            can_steal_tech=True,
            can_steal_vet=True,
        ))

    # Also scan vehicles for disguise detectors (some mods put detect on vehicles)
    for uid in (ini.list_from_section('VehicleTypes') or []):
        if not uid or uid.startswith(';'):
            continue
        sec = ini.section(uid)
        if not sec:
            continue
        if sec.get('detectdisguise', 'no').strip().lower() == 'yes':
            detector_units.append(uid)

    # Buildings with DetectDisguise (rare but possible in mods)
    for bid in (ini.list_from_section('BuildingTypes') or []):
        if not bid or bid.startswith(';'):
            continue
        sec = ini.section(bid)
        if not sec:
            continue
        if sec.get('detectdisguise', 'no').strip().lower() == 'yes':
            detector_blds.append(bid)

    return SpyFeasibilityResult(
        spies=spies,
        detector_unit_ids=detector_units,
        detector_bld_ids=detector_blds,
    )


# ---------------------------------------------------------------------------
# High-Value Target Scanner
# ---------------------------------------------------------------------------

@dataclass
class HVTEntry:
    bid:        str
    name:       str
    cost:       int
    strength:   int
    tier:       int    # 1=essential only, 2=expensive only, 3=essential+expensive
    categories: list[str]   # why it qualifies


@dataclass
class HVTResult:
    tier3: list[HVTEntry]   # expensive AND essential — attack first
    tier2: list[HVTEntry]   # expensive only
    tier1: list[HVTEntry]   # essential only (factories etc.)
    mean_cost: float
    std_cost:  float


def scan_high_value_targets(ini, rebuild_result: RebuildCapResult) -> HVTResult:
    """
    Identify high-value building targets using a two-axis system:
      Axis 1: Cost= above mean + 1 stddev (expensive)
      Axis 2: Strategic essentialness (ConYard, SW, tech unlock)

    Returns HVTResult with three tiers.
    """
    # Collect essential building sets
    essential_ids = frozenset(
        rebuild_result.conyard_ids
        + rebuild_result.warfactory_ids
        + rebuild_result.barracks_ids
        + rebuild_result.airfield_ids
        + rebuild_result.shipyard_ids
        + rebuild_result.service_depot_ids
    )

    # Identify SW buildings and tech buildings
    sw_building_ids:   set[str] = set()
    tech_building_ids: set[str] = set()

    for bid in (ini.list_from_section('BuildingTypes') or []):
        if not bid or bid.startswith(';'):
            continue
        sec = ini.section(bid)
        if not sec:
            continue
        # SW building
        if sec.get('superweapon', '').strip() or sec.get('superweapon2', '').strip():
            sw_building_ids.add(bid)
        # Tech / research building (high TL, no factory, BuildCat hint)
        build_cat = sec.get('buildcat', '').strip().lower()
        if build_cat == 'tech':
            tech_building_ids.add(bid)

    all_essential = essential_ids | sw_building_ids | tech_building_ids

    # Gather costs for all buildings
    costs: list[int] = []
    entries: list[tuple[str, str, int, int]] = []   # (bid, name, cost, strength)

    for bid in (ini.list_from_section('BuildingTypes') or []):
        if not bid or bid.startswith(';'):
            continue
        sec = ini.section(bid)
        if not sec:
            continue
        try:
            cost = int(sec.get('cost', '0') or '0')
        except ValueError:
            cost = 0
        if cost <= 0:
            continue
        try:
            strength = int(sec.get('strength', '100') or '100')
        except ValueError:
            strength = 100
        name = sec.get('name', bid)
        costs.append(cost)
        entries.append((bid, name, cost, strength))

    if not costs:
        return HVTResult(tier3=[], tier2=[], tier1=[], mean_cost=0, std_cost=0)

    mean_cost = statistics.mean(costs)
    std_cost  = statistics.stdev(costs) if len(costs) > 1 else 0
    threshold = mean_cost + std_cost

    tier3: list[HVTEntry] = []
    tier2: list[HVTEntry] = []
    tier1: list[HVTEntry] = []

    for bid, name, cost, strength in entries:
        is_expensive  = cost >= threshold
        is_essential  = bid in all_essential
        categories    = []

        if bid in rebuild_result.conyard_ids:
            categories.append('ConYard')
        if bid in rebuild_result.warfactory_ids:
            categories.append('WarFactory')
        if bid in rebuild_result.barracks_ids:
            categories.append('Barracks')
        if bid in rebuild_result.airfield_ids:
            categories.append('Airfield')
        if bid in rebuild_result.shipyard_ids:
            categories.append('Shipyard')
        if bid in rebuild_result.service_depot_ids:
            categories.append('ServiceDepot')
        if bid in sw_building_ids:
            categories.append('SuperWeapon')
        if bid in tech_building_ids:
            categories.append('TechLab')

        if is_expensive and is_essential:
            tier3.append(HVTEntry(bid, name, cost, strength, 3, categories))
        elif is_expensive:
            tier2.append(HVTEntry(bid, name, cost, strength, 2, categories))
        elif is_essential:
            tier1.append(HVTEntry(bid, name, cost, strength, 1, categories))

    # Sort each tier by cost descending
    for t in (tier3, tier2, tier1):
        t.sort(key=lambda e: e.cost, reverse=True)

    return HVTResult(
        tier3=tier3,
        tier2=tier2,
        tier1=tier1,
        mean_cost=mean_cost,
        std_cost=std_cost,
    )


# ---------------------------------------------------------------------------
# Full mod scan result
# ---------------------------------------------------------------------------

@dataclass
class ModScanResult:
    """Aggregated results from all scanner systems."""
    aa:     AAThreatResult
    rebuild: RebuildCapResult
    spies:  SpyFeasibilityResult
    hvt:    HVTResult
    mc_threat: 'MCThreatResult | None' = None
    _ini:   object = field(default=None, repr=False)  # IniParser reference


@dataclass
class MCThreatResult:
    """Item 56: mind-control attackers detected in the mod.

    - `unit_ids`   : enemy unit IDs whose primary weapon has MindControl=yes
                     (e.g. MIND yuri, YURIPR, PSICOM, DUKE, YAPRPR, etc.).
    - `building_ids`: mind-control-projecting structures (YAPSYT psychic
                       tower, etc.), typically with `PsychicDetectionRadius=`
                       or a similar Ares-flavored psychic tag.
    - `immune_units`: buildable unit IDs with ImmuneToPsionics=yes — these
                       are what an MC-counter wave should be composed of.
    """
    unit_ids:     list[str] = field(default_factory=list)
    building_ids: list[str] = field(default_factory=list)
    immune_units: list[str] = field(default_factory=list)


def scan_mc_threat(parser, ini) -> MCThreatResult:
    """Scan the mod for mind-control units, mind-control-projecting
    buildings, and MC-immune units."""
    result = MCThreatResult()

    # MC-attacker units: primary weapon warhead has MindControl=yes
    for section_name in ('InfantryTypes', 'VehicleTypes'):
        for uid in ini.list_from_section(section_name):
            if not uid or uid.startswith(';'):
                continue
            sec = ini.section(uid)
            if not sec:
                continue
            primary = (sec.get('primary', '') or '').split(';')[0].strip()
            if not primary:
                continue
            wsec = ini.section(primary)
            if not wsec:
                continue
            wh = (wsec.get('warhead', '') or '').split(';')[0].strip()
            whsec = ini.section(wh) if wh else None
            if whsec and whsec.get('mindcontrol', 'no').strip().lower() == 'yes':
                result.unit_ids.append(uid)

    # MC-projecting buildings: PsychicDetectionRadius= or Sensors + specific
    # SuperWeapon types are the common patterns. Vanilla YAPSYT is the
    # Psychic Tower — has MindControl=yes on its primary weapon warhead
    # OR is called out with PsychicRadius=.
    for uid in ini.list_from_section('BuildingTypes'):
        if not uid or uid.startswith(';'):
            continue
        sec = ini.section(uid)
        if not sec:
            continue
        if (sec.get('psychicdetectionradius', '').strip() or
                sec.get('psychicradius', '').strip()):
            result.building_ids.append(uid)
            continue
        primary = (sec.get('primary', '') or '').split(';')[0].strip()
        if not primary:
            continue
        wsec = ini.section(primary)
        if not wsec:
            continue
        wh = (wsec.get('warhead', '') or '').split(';')[0].strip()
        whsec = ini.section(wh) if wh else None
        if whsec and whsec.get('mindcontrol', 'no').strip().lower() == 'yes':
            result.building_ids.append(uid)

    # MC-immune buildable units
    if parser is not None:
        for rec in getattr(parser, 'buildable', []):
            if getattr(rec, 'is_mc_immune', False):
                result.immune_units.append(rec.unit_id)

    return result


# ---------------------------------------------------------------------------
# Prerequisite-to-buildings resolver
# ---------------------------------------------------------------------------

def resolve_team_prerequisites(unit_ids: list[str], ini,
                                gp: dict | None = None,
                                faction: str | None = None,
                                sides=None) -> list[str]:
    """
    Walk unit Prerequisite= chains, return concrete building IDs required.
    When faction is provided, prerequisite alias expansion (e.g. RADAR →
    GAAIRC,NARADR,AMRADR,NAPSIS) is filtered to only buildings owned by
    that faction's countries, preventing impossible multi-faction requirements.
    """
    if gp is None:
        gp_raw = ini.section('GenericPrerequisites') or {}
        gp = {k.upper(): v for k, v in gp_raw.items()}

    # Faction countries for alias filtering
    faction_countries: set[str] = set()
    if faction and sides:
        countries = getattr(sides, 'faction_countries', {}).get(faction, [])
        faction_countries = {c.upper() for c in countries}

    gen = ini.section('General') or {}
    alias_map: dict[str, list[str]] = {}
    for alias in ('power', 'factory', 'barracks', 'radar', 'tech',
                  'infantry', 'vehicle', 'aircraft', 'naval', 'ally'):
        key = f'prerequisite{alias}'
        val = gen.get(key, '').strip()
        if val:
            alias_map[alias.upper()] = [b.strip() for b in val.split(',') if b.strip()]

    def _faction_owns(bid: str) -> bool:
        """True if this building is buildable by the faction's countries."""
        if not faction_countries:
            return True
        sec = ini.section(bid)
        if not sec:
            return False
        owners = {o.strip().upper() for o in sec.get('owner', '').split(',') if o.strip()}
        forbidden = {o.strip().upper() for o in sec.get('forbiddenhouses', '').split(',') if o.strip()}
        return bool(owners & faction_countries) and not bool(forbidden & faction_countries)

    def expand_token(token: str, visited: frozenset = frozenset()) -> set[str]:
        upper = token.strip().upper()
        if not upper or upper == 'NONE':
            return set()
        if upper in visited:
            return set()
        if upper in alias_map:
            candidates: set[str] = set()
            for b in alias_map[upper]:
                candidates |= expand_token(b, visited | {upper})
            # Filter alias expansion to faction-appropriate buildings
            if faction_countries and len(candidates) > 1:
                filtered = {b for b in candidates if _faction_owns(b)}
                return filtered if filtered else candidates
            return candidates
        if upper in gp:
            val = gp[upper]
            result: set[str] = set()
            for b in val.split(','):
                result |= expand_token(b.strip(), visited | {upper})
            return result
        return {token.strip()}

    all_buildings: set[str] = set()
    for uid in unit_ids:
        if not uid:
            continue
        sec = ini.section(uid)
        if not sec:
            continue
        prereq_raw = sec.get('prerequisite', '').strip()
        if not prereq_raw or prereq_raw.upper() == 'NONE':
            continue
        for token in prereq_raw.split(','):
            token = token.strip()
            if token:
                all_buildings |= expand_token(token)

    def is_human_buildable(bid: str) -> bool:
        sec = ini.section(bid)
        if not sec:
            return False
        try:
            tl = int(sec.get('techlevel', '-1') or '-1')
        except ValueError:
            tl = -1
        if tl < 1 or tl >= 11:
            return False
        return bool(sec.get('owner', '').strip())

    result = sorted(b for b in all_buildings
                    if b and not b.isdigit() and len(b) <= 12
                    and is_human_buildable(b))
    return result


def scan_mod(rulesmd_path: str | Path, parser=None, sides_registry=None) -> ModScanResult:
    """
    Run all scanner systems against a mod's rulesmd.ini.
    If parser is provided, reuses its IniParser to avoid re-reading the file.
    """
    if parser is not None:
        ini = parser.ini
    else:
        from .rulesmd_parser import IniParser
        ini = IniParser(str(rulesmd_path))

    aa      = scan_aa_threat(ini)
    rebuild = scan_rebuild_capability(ini)
    spies   = scan_spy_feasibility(ini, sides_registry)
    hvt     = scan_high_value_targets(ini, rebuild)
    mc      = scan_mc_threat(parser, ini)

    return ModScanResult(aa=aa, rebuild=rebuild, spies=spies, hvt=hvt,
                          mc_threat=mc, _ini=ini)


# ---------------------------------------------------------------------------
# AIExt configuration
# ---------------------------------------------------------------------------

@dataclass
class AIExtConfig:
    """
    Controls which trigger families and conditions are generated.

    Thresholds are configurable — defaults are conservative and intended
    as starting points for playtesting.
    """
    enabled: bool = True

    # TargetHouseMode for all enemy checks
    target_house_mode: str = 'Current'

    # --- Economy thresholds ---
    enemy_rich_threshold:  int = 8000
    enemy_poor_threshold:  int = 3000

    # --- AA DPS bands for aerial role selection ---
    # Below aa_dps_rush:  AERIAL_RUSH (no/zero AA)
    # aa_dps_rush to aa_dps_wave: AERIAL_WAVE (low AA)
    # aa_dps_wave to aa_dps_assault: AERIAL_ASSAULT (moderate AA)
    # Above aa_dps_assault: suppress aerial entirely
    aa_dps_rush:    float = 0.0     # zero structures = rush
    aa_dps_wave:    float = 80.0    # low AA = medium wave
    aa_dps_assault: float = 250.0   # moderate AA = overwhelming assault

    # Frames considered "early game" for infantry rush (15 frames/sec)
    # Default: 6000 frames = ~6.6 minutes at normal speed
    early_game_frames: int = 6000

    # Max base defense buildings for a "light defense" infantry rush
    # 0 = no defenses at all (small rush)
    # 1-2 = light defenses (medium rush, more units required)
    infantry_rush_max_defenses: int = 2

    # --- Difficulty gates ---
    easy_uses_aiext:   bool = False
    medium_uses_aiext: bool = True
    hard_uses_aiext:   bool = True

    # --- Active families ---
    family_economy:        bool = True
    family_aerial:         bool = True
    family_infantry_rush:  bool = True
    family_infrastructure: bool = True
    family_defense_gap:    bool = True
    family_escalation:     bool = True
    family_spy:            bool = True

    # --- Dummy prerequisite mode (legacy, demoted) ---
    use_dummy_prereqs: bool = False


# ---------------------------------------------------------------------------
# Condition builder
# ---------------------------------------------------------------------------

class AIExtConditionBuilder:
    """
    Builds AIExtConditions for a trigger based on its role, faction,
    difficulty, and the mod scan results.
    """

    def __init__(self, scan: ModScanResult, config: AIExtConfig, sides=None):
        self.scan   = scan
        self.config = config
        self._sides = sides   # SidesRegistry for faction→country lookup
        self._aa_structure_dps = scan.aa.total_structure_dps
        self._base_defense_ids = self._find_base_defense_ids()

    def _find_base_defense_ids(self) -> list[str]:
        """
        Ground-capable base defenses (pillboxes, tesla coils, prism towers...).

        Previously this pulled from the AA scan's detail list, which meant an
        INFANTRY_RUSH gate listed AA structures (NASAM/NAFLAK/YAGGUN) — checking
        anti-air when the wave is ground infantry. Now walks [BuildingTypes]
        directly: IsBaseDefense=yes buildings whose primary weapon's projectile
        can hit ground targets (projectile AG defaults to yes in YR; only
        AG=no marks a pure-AA defense).
        """
        ini = getattr(self.scan, '_ini', None)
        if ini is None:
            # Fallback to the old (AA-derived) behaviour if no INI available
            return [x.unit_id for x in self.scan.aa.detail
                    if x.is_building and x.is_base_defense]

        result: list[str] = []
        bt = ini.section('BuildingTypes') or {}
        seen: set[str] = set()
        for bid in bt.values():
            bid = (bid or '').split(';')[0].strip()
            if not bid or bid in seen:
                continue
            seen.add(bid)
            sec = ini.section(bid)
            if not sec:
                continue
            if sec.get('isbasedefense', 'no').strip().lower() != 'yes':
                continue
            # Must be buildable by someone (skip TechLevel<1 dummies)
            try:
                tl = int(sec.get('techlevel', '-1') or '-1')
            except ValueError:
                tl = -1
            if tl < 1 or tl >= 11:
                continue
            # Resolve primary weapon → projectile → AG capability
            weapon = sec.get('primary', '').split(';')[0].strip()
            ag_capable = True   # no weapon = e.g. bunkers; treat as ground-relevant
            if weapon:
                wsec = ini.section(weapon)
                if wsec:
                    proj = wsec.get('projectile', '').split(';')[0].strip()
                    if proj:
                        psec = ini.section(proj)
                        if psec:
                            ag = psec.get('ag', 'yes').strip().lower()
                            ag_capable = (ag != 'no')
            if ag_capable:
                result.append(bid)
        return result

    def aerial_band(self) -> str:
        """Return AERIAL_RUSH / AERIAL_WAVE / AERIAL_ASSAULT / SUPPRESS based on AA DPS."""
        cfg = self.config
        dps = self._aa_structure_dps
        if dps <= cfg.aa_dps_rush:
            return 'AERIAL_RUSH'
        elif dps <= cfg.aa_dps_wave:
            return 'AERIAL_WAVE'
        elif dps <= cfg.aa_dps_assault:
            return 'AERIAL_ASSAULT'
        else:
            return 'SUPPRESS'

    def build(self, role: str, faction: str, difficulty: str,
              country: str | None = None,
              unit_ids: list[str] | None = None) -> AIExtConditions:
        cfg = self.config
        if not cfg.enabled:
            return AIExtConditions()
        if difficulty == 'Easy'   and not cfg.easy_uses_aiext:
            return AIExtConditions()
        if difficulty == 'Medium' and not cfg.medium_uses_aiext:
            return AIExtConditions()

        cond = AIExtConditions(target_house_mode=cfg.target_house_mode)
        role_upper = role.upper()

        # --- Prerequisite-based owner gate (replaces escalation gate) ---
        # Walk the actual unit prerequisites and require those buildings,
        # filtered to only those buildable by THIS faction.
        if unit_ids and cfg.family_escalation:
            self._apply_prerequisite_gate(cond, unit_ids, faction)

        if cfg.family_aerial and self._is_aerial_role(role_upper):
            self._apply_aerial_conditions(cond, role_upper)

        if cfg.family_infantry_rush and 'INFANTRY_RUSH_EARLY' in role_upper:
            self._apply_infantry_rush_conditions(cond, difficulty)

        if cfg.family_spy and self._is_spy_role(role_upper):
            self._apply_spy_conditions(cond)

        if cfg.family_infrastructure and self._is_infrastructure_role(role_upper):
            self._apply_infrastructure_conditions(cond, role_upper)

        if cfg.family_economy:
            self._apply_economy_conditions(cond, role_upper)

        # Item 56: MC-immune wave — fires when enemy has MC attackers.
        # Composition should be MC-immune units only (checked upstream);
        # here we just gate the trigger on the presence of enemy MC threats.
        if 'MC_IMMUNE' in role_upper or 'ANTI_MC' in role_upper:
            self._apply_mc_conditions(cond)

        if self._is_siege_role(role_upper):
            self._apply_siege_conditions(cond)

        return cond

    # ------------------------------------------------------------------
    # Role classifiers
    # ------------------------------------------------------------------

    def _is_aerial_role(self, role: str) -> bool:
        return any(k in role for k in ('AERIAL_RUSH', 'AERIAL_WAVE',
                                        'AERIAL_ASSAULT', 'AERIAL',
                                        'PARADROP', 'BOMBER'))

    def _is_spy_role(self, role: str) -> bool:
        return any(k in role for k in ('SPY', 'INFILTRAT', 'THIEF', 'AGENT'))

    def _is_infrastructure_role(self, role: str) -> bool:
        return any(k in role for k in ('ENGINEER', 'CAPTURE',
                                        'FACTORY', 'CONYARD', 'CY_KILLER'))

    def _is_siege_role(self, role: str) -> bool:
        # Long-range teams that should out-range what they engage.
        return any(k in role for k in ('DEPLOYFIRE', 'ARTILLERY', 'ARTY', 'SIEGE'))

    # ------------------------------------------------------------------
    # Condition applicators
    # ------------------------------------------------------------------

    def _apply_aerial_conditions(self, cond: AIExtConditions,
                                  role: str) -> None:
        aa = self.scan.aa

        if 'AERIAL_RUSH' in role:
            # Zero AA structures — clean sweep
            if aa.aa_structure_ids:
                cond.enemy_buildings.extend(aa.aa_structure_ids)
                cond.enemy_buildings_min.extend([0] * len(aa.aa_structure_ids))
                cond.enemy_buildings_max.extend([0] * len(aa.aa_structure_ids))

        elif 'AERIAL_WAVE' in role:
            # Low AA — cap at 1 structure of any type
            if aa.aa_structure_ids:
                cond.enemy_buildings.extend(aa.aa_structure_ids)
                cond.enemy_buildings_min.extend([0] * len(aa.aa_structure_ids))
                cond.enemy_buildings_max.extend([1] * len(aa.aa_structure_ids))

        elif 'AERIAL_ASSAULT' in role:
            # Designed to overpower AA — no building max gate needed
            # The large mixed composition handles it at the taskforce level
            pass

    def _apply_infantry_rush_conditions(self, cond: AIExtConditions,
                                         difficulty: str) -> None:
        """Early infantry rush: gates on base defense count."""
        cfg = self.config
        if self._base_defense_ids:
            max_def = cfg.infantry_rush_max_defenses if difficulty == 'Hard' else 0
            cond.enemy_buildings.extend(self._base_defense_ids)
            cond.enemy_buildings_min.extend([0] * len(self._base_defense_ids))
            cond.enemy_buildings_max.extend(
                [max_def] * len(self._base_defense_ids))

    def _apply_siege_conditions(self, cond: AIExtConditions) -> None:
        """Siege/artillery should out-range what it fights. Only dispatch when
        the team is NOT outranged on the ground (team AG range >= enemy AG
        range). Prevents deploying slow artillery into a range disadvantage
        where it dies without firing. Conservative: for a team that genuinely
        out-ranges (as artillery normally does) this passes; it only vetoes
        against longer-range enemy defenses (Prism, V3).
        """
        cond.team_range_ratio_min = 100
        cond.team_range_ratio_lock = 'AG'
        if cond.debug_log_cancel is None:
            cond.debug_log_cancel = 'held back (outranged on the ground)'

    def _apply_mc_conditions(self, cond: AIExtConditions) -> None:
        """Fire an MC-immune wave when the enemy has any mind-control
        attackers (units or towers) still standing. Composition-side is
        responsible for using MC-immune units; here we gate on threat
        presence.
        """
        mc = getattr(self.scan, 'mc_threat', None)
        if mc is None:
            return
        if mc.unit_ids:
            cond.enemy_units.extend(mc.unit_ids)
            cond.enemy_units_min.extend([1] * len(mc.unit_ids))
            cond.enemy_units_max.extend([-1] * len(mc.unit_ids))
        if mc.building_ids:
            cond.enemy_buildings.extend(mc.building_ids)
            cond.enemy_buildings_min.extend([1] * len(mc.building_ids))
            cond.enemy_buildings_max.extend([-1] * len(mc.building_ids))

    def _apply_spy_conditions(self, cond: AIExtConditions) -> None:
        spies = self.scan.spies
        if spies.detector_bld_ids:
            cond.enemy_buildings.extend(spies.detector_bld_ids)
            cond.enemy_buildings_min.extend([0] * len(spies.detector_bld_ids))
            cond.enemy_buildings_max.extend([0] * len(spies.detector_bld_ids))
        cond.enemy_power_min = 100

    def _apply_infrastructure_conditions(self, cond: AIExtConditions,
                                          role: str) -> None:
        rebuild = self.scan.rebuild
        if any(k in role for k in ('ENGINEER', 'CAPTURE', 'CY_KILLER')):
            if rebuild.conyard_ids:
                cond.enemy_buildings.extend(rebuild.conyard_ids)
                cond.enemy_buildings_min.extend([1] * len(rebuild.conyard_ids))
                cond.enemy_buildings_max.extend([-1] * len(rebuild.conyard_ids))

    def _apply_economy_conditions(self, cond: AIExtConditions,
                                   role: str) -> None:
        cfg = self.config
        if any(k in role for k in ('MINER', 'REFINER', 'HARVEST')):
            cond.enemy_credits_max = cfg.enemy_poor_threshold
        if any(k in role for k in ('SPY', 'THIEF')):
            cond.enemy_credits_min = cfg.enemy_rich_threshold

    def _apply_prerequisite_gate(self, cond: AIExtConditions,
                                  unit_ids: list[str],
                                  faction: str | None = None) -> None:
        """
        Walk actual unit prerequisites and require those buildings,
        filtered to only buildings this faction can actually build.
        When a prerequisite alias (like RADAR) expands to multiple buildings,
        only those owned by the faction's countries are included.
        """
        buildings = resolve_team_prerequisites(
            unit_ids,
            self.scan._ini if hasattr(self.scan, '_ini') else self._ini,
            faction=faction,
            sides=self._sides)
        if not buildings:
            return
        cond.owner_buildings.extend(buildings)
        cond.owner_buildings_min.extend([1] * len(buildings))
        cond.owner_buildings_max.extend([-1] * len(buildings))


# ---------------------------------------------------------------------------
# AIExt section writer
# ---------------------------------------------------------------------------

def populate_default_debug(cond: 'AIExtConditions', trigger_id: str,
                           name: Optional[str] = None) -> None:
    """When the user opts in to debug messages, walk every actually-emitted
    tag on `cond` and give it a default debug quad.

    Design (per Rex):
    - Every gate with a value gets Debug.MessageDisplay=NOSTR:... and
      Debug.LogMessage=... with sensible generic text.
    - ValueDisplay=yes and LogWrite=yes so the DLL appends the computed
      value and writes detailed pass/fail to the log automatically.
    - TriggerDisplay/TriggerLog omitted — they default to 'finish' at the
      DLL level. Modders can override per-tag if they want start/cancel
      visibility.

    Also populates the three trigger-lifecycle overlay + log messages.
    """
    def add_quad(tag_name: str, human_label: str,
                  details_types: str | None = None) -> None:
        """Emit the six-field debug quad for one tag.

        details_types: optional comma-separated field list for the Details
        display (e.g. 'Type, Minimum, Maximum, Current' for lists, or
        'Minimum, Maximum, Current' for scalars). When set, emits
        Debug.DetailsDisplay=yes and Debug.DetailsTypes=... alongside the
        standard six. DetailsTrigger defaults to 'finish' at the DLL level.
        """
        entry = {
            # Include ": " suffix so when the DLL appends the computed value
            # via ValueDisplay, it reads as "Label: values" cleanly.
            'message_display': f'NOSTR:{human_label}: ',
            'value_display':   True,
            'log_message':     f'{human_label}: ',
            'log_write':       True,
        }
        if details_types:
            entry['details_display'] = True
            entry['details_types']   = details_types
        cond.debug_per_tag[tag_name] = entry

    def add_list_family(base: str, human_root: str) -> None:
        """List-family condition (buildings, units, superweapons): emit
        four debug tags per family — the base list, Min, Max, and Current.
        The base tag also gets a Details display with all four fields."""
        add_quad(base,             f'{human_root}',
                 details_types='Type, Minimum, Maximum, Current')
        add_quad(base + 'Min',     f'{human_root} min')
        add_quad(base + 'Max',     f'{human_root} max')
        add_quad(base + 'Current', f'{human_root} current')

    def add_scalar_family(base_root: str, human_root: str,
                           has_min: bool, has_max: bool) -> None:
        """Scalar-family condition (credits, power, techlevel): emit
        debug tags for whichever thresholds are set, plus Current, and
        put a Details display on Current (since scalars have no 'Type'
        field to hang Details off, Current owns it).

        Details for scalars = 'Minimum, Maximum, Current' — Type omitted.
        """
        if has_min:
            add_quad(base_root + 'Min', f'{human_root} min')
        if has_max:
            add_quad(base_root + 'Max', f'{human_root} max')
        add_quad(base_root + 'Current', f'{human_root} current',
                 details_types='Minimum, Maximum, Current')

    # Trigger lifecycle debug output. Consider/Cancel are SPAMMY (fire every
    # AI evaluation tick) so they stay LOG-only / opt-in. The three real hooked
    # once-per-team events all go to BOTH the HUD overlay AND the log, using the
    # trigger's readable NAME, so the player can watch the AI act live:
    #   Start     — a team was ACTUALLY created (the DLL fires this at real team
    #               creation now, not the winner-draw, so it is NOT spammy — it
    #               is exactly "the AI just dispatched this wave").
    #   Destroyed — the team died before finishing its script.
    #   Deleted   — the team completed its script.
    # `label` is the human-readable trigger name when available (falls back to the
    # GUID). Prefixes: >> dispatched, XX wiped, OK done — quick to scan on-screen.
    label = name or trigger_id
    cond.debug_message_start = f'NOSTR:>> {label}'
    cond.debug_log_start = f'{label} dispatched (team created)'
    cond.debug_message_destroyed = f'NOSTR:XX {label} wiped'
    cond.debug_log_destroyed = f'{label} team destroyed (script failed)'
    cond.debug_message_deleted = f'NOSTR:OK {label} done'
    cond.debug_log_deleted = f'{label} team completed (script success)'

    # Owner (tool tracks buildings, credits, power — no owner_units)
    if cond.owner_buildings:
        add_list_family('RequiredOwnerBuildings', 'Owner buildings')
    if cond.owner_credits_min is not None or cond.owner_credits_max is not None:
        add_scalar_family('RequiredOwnerCredits', 'Owner credits',
                           has_min=cond.owner_credits_min is not None,
                           has_max=cond.owner_credits_max is not None)
    if cond.owner_power_min is not None or cond.owner_power_max is not None:
        add_scalar_family('RequiredOwnerPower', 'Owner net power',
                           has_min=cond.owner_power_min is not None,
                           has_max=cond.owner_power_max is not None)

    # Enemy (buildings, units, credits, power)
    if cond.enemy_buildings:
        add_list_family('RequiredEnemyBuildings', 'Enemy buildings')
    if cond.enemy_units:
        add_list_family('RequiredEnemyUnits', 'Enemy units')
    if cond.enemy_credits_min is not None or cond.enemy_credits_max is not None:
        add_scalar_family('RequiredEnemyCredits', 'Enemy credits',
                           has_min=cond.enemy_credits_min is not None,
                           has_max=cond.enemy_credits_max is not None)
    if cond.enemy_power_min is not None or cond.enemy_power_max is not None:
        add_scalar_family('RequiredEnemyPower', 'Enemy net power',
                           has_min=cond.enemy_power_min is not None,
                           has_max=cond.enemy_power_max is not None)
    if (cond.enemy_power_production_min is not None
            or cond.enemy_power_production_max is not None):
        add_scalar_family('RequiredEnemyPowerOutput', 'Enemy power output',
                           has_min=cond.enemy_power_production_min is not None,
                           has_max=cond.enemy_power_production_max is not None)

    # Neutral (buildings only in tool)
    if cond.neutral_buildings:
        add_list_family('RequiredNeutralBuildings', 'Neutral buildings')

    # Elapsed time — scalar family. Current is game clock (frames) at
    # evaluation time, super useful for debugging timing gates.
    if (cond.elapsed_seconds_min is not None
            or cond.elapsed_seconds_max is not None):
        add_scalar_family('RequiredElapsedTime', 'Elapsed time',
                           has_min=cond.elapsed_seconds_min is not None,
                           has_max=cond.elapsed_seconds_max is not None)

    # TargetHouseMode when overridden — enum, no Min/Max/Current concept
    if cond.target_house_mode and cond.target_house_mode != 'Current':
        add_quad('TargetHouseMode', 'Target house mode')


class AIExtWriter:
    """
    Writes [TriggerID.AIExt] sections to aimd.ini.

    Call collect() for each trigger that has conditions, then
    write_to_ini() to append all sections to the output file.
    """

    def __init__(self, debug_messages: bool = False):
        """
        debug_messages: when True, every collected trigger gets a full
        debug quad populated for every actually-emitted tag, plus the
        three lifecycle overlay + log messages. Modder can still hand-
        edit any tag's Debug.* sub-fields in the generated INI.
        """
        self._entries: list[tuple[str, AIExtConditions]] = []
        self._debug_messages: bool = debug_messages

    def collect(self, trigger_id: str, conditions: AIExtConditions,
                name: Optional[str] = None) -> None:
        """Register a trigger's conditions for writing. `name` is the trigger's
        readable name, used in the on-screen/log lifecycle messages."""
        if self._debug_messages:
            populate_default_debug(conditions, trigger_id, name)
        if not conditions.is_empty():
            self._entries.append((trigger_id, conditions))

    def set_debug_messages(self, enabled: bool) -> None:
        """Toggle debug population. Call before collect() calls happen."""
        self._debug_messages = enabled

    def write_to_ini(self, aimd_path: str | Path,
                     output_path: str | Path | None = None) -> Path:
        """
        Append all [TriggerID.AIExt] sections to aimd.ini.
        Inserts before [Digest] if present, otherwise appends at end.
        """
        if not self._entries:
            return Path(aimd_path)

        aimd_path   = Path(aimd_path)
        output_path = Path(output_path) if output_path else aimd_path

        lines: list[str] = []
        if aimd_path.exists():
            with aimd_path.open(encoding='latin-1') as f:
                lines = f.readlines()

        # Find insertion point: before [Digest] or end of file
        insert_at = len(lines)
        for i, line in enumerate(lines):
            if line.strip() == '[Digest]':
                insert_at = i
                break

        new_lines: list[str] = ['\r\n']
        for trigger_id, cond in self._entries:
            new_lines.append(f'[{trigger_id}.AIExt]  ; YR-AI-Tool\r\n')
            new_lines.extend(self._serialise(cond))
            new_lines.append('\r\n')

        lines[insert_at:insert_at] = new_lines

        with output_path.open('w', encoding='latin-1') as f:
            f.write(guard_ini_text(lines))

        return output_path

    def _serialise(self, cond: AIExtConditions) -> list[str]:
        """Serialise one AIExtConditions to INI lines."""
        lines: list[str] = []

        def write_if(key: str, val) -> None:
            if val is not None:
                lines.append(f'{key}={val}\r\n')

        def write_list(key: str, lst: list) -> None:
            if lst:
                lines.append(f'{key}={",".join(str(v) for v in lst)}\r\n')

        # Owner buildings
        write_list('RequiredOwnerBuildings',    cond.owner_buildings)
        write_list('RequiredOwnerBuildingsMin', cond.owner_buildings_min)
        write_list('RequiredOwnerBuildingsMax', cond.owner_buildings_max)

        # Owner economy
        write_if('RequiredOwnerCreditsMin', cond.owner_credits_min)
        write_if('RequiredOwnerCreditsMax', cond.owner_credits_max)
        write_if('RequiredOwnerPowerMin', cond.owner_power_min)
        write_if('RequiredOwnerPowerMax', cond.owner_power_max)

        # Enemy buildings
        write_list('RequiredEnemyBuildings',    cond.enemy_buildings)
        write_list('RequiredEnemyBuildingsMin', cond.enemy_buildings_min)
        write_list('RequiredEnemyBuildingsMax', cond.enemy_buildings_max)

        # Enemy units
        write_list('RequiredEnemyUnits',    cond.enemy_units)
        write_list('RequiredEnemyUnitsMin', cond.enemy_units_min)
        write_list('RequiredEnemyUnitsMax', cond.enemy_units_max)

        write_list('RequiredNeutralBuildings',    cond.neutral_buildings)
        write_list('RequiredNeutralBuildingsMin', cond.neutral_buildings_min)
        write_list('RequiredNeutralBuildingsMax', cond.neutral_buildings_max)

        # Enemy economy
        write_if('RequiredEnemyCreditsMin', cond.enemy_credits_min)
        write_if('RequiredEnemyCreditsMax', cond.enemy_credits_max)
        write_if('RequiredEnemyPowerMin', cond.enemy_power_min)
        write_if('RequiredEnemyPowerMax', cond.enemy_power_max)
        write_if('RequiredEnemyPowerOutputMin', cond.enemy_power_production_min)
        write_if('RequiredEnemyPowerOutputMax', cond.enemy_power_production_max)

        # Threat: don't dispatch when this trigger's team is outranged.
        write_if('RequiredTeamRangeRatioMin', cond.team_range_ratio_min)
        write_if('RequiredTeamRangeRatioLock', cond.team_range_ratio_lock)
        if cond.team_range_ratio_min is not None:
            # observability quad (DLL only logs it under DisplayAIWaveMessages)
            write_if('RequiredTeamRangeRatio.Debug.LogMessage', 'team-vs-enemy range pct:')
            write_if('RequiredTeamRangeRatio.Debug.LogWrite', 'yes')
            write_if('RequiredTeamRangeRatio.Debug.DetailsTypes', 'Current')

        # Elapsed game-time (AITriggerTypeExt DLL feature).
        # Confirm the exact tag name matches the DLL's expectation.
        # ElapsedTime — DLL expects FRAMES (15 frames = 1 second regardless of
        # game speed). The tool stores seconds internally for readability;
        # convert at emit time.
        if cond.elapsed_seconds_min is not None:
            write_if('RequiredElapsedTimeMin', cond.elapsed_seconds_min * 15)
        if cond.elapsed_seconds_max is not None:
            write_if('RequiredElapsedTimeMax', cond.elapsed_seconds_max * 15)

        # Debug lifecycle overlay messages
        write_if('DebugMessageDisplay.Consider',  cond.debug_message_consider)
        write_if('DebugMessageDisplay.Cancel', cond.debug_message_cancel)
        write_if('DebugMessageDisplay.Finish', cond.debug_message_finish)
        write_if('DebugMessageDisplay.Start', cond.debug_message_start)
        write_if('DebugMessageDisplay.Destroyed', cond.debug_message_destroyed)
        write_if('DebugMessageDisplay.Deleted', cond.debug_message_deleted)

        # Debug lifecycle log messages
        write_if('DebugLog.Consider',  cond.debug_log_consider)
        write_if('DebugLog.Cancel', cond.debug_log_cancel)
        write_if('DebugLog.Finish', cond.debug_log_finish)
        write_if('DebugLog.Start', cond.debug_log_start)
        write_if('DebugLog.Destroyed', cond.debug_log_destroyed)
        write_if('DebugLog.Deleted', cond.debug_log_deleted)

        # Per-tag debug quads. Each entry is one AIExt tag name → dict of
        # debug sub-fields. Emit in tag-name order for deterministic output.
        for tag_name in sorted(cond.debug_per_tag.keys()):
            quad = cond.debug_per_tag[tag_name]
            md = quad.get('message_display')
            vd = quad.get('value_display', False)
            lm = quad.get('log_message')
            lw = quad.get('log_write', False)
            td = quad.get('trigger_display')   # str or None (inherits)
            tl = quad.get('trigger_log')       # str or None (inherits)
            # Details sub-fields (list-form bracket display)
            dd  = quad.get('details_display', False)
            dt  = quad.get('details_types')    # comma-list, e.g. "Type, Minimum, Maximum, Current"
            dtg = quad.get('details_trigger')  # str or None (inherits default 'finish')
            if md:
                lines.append(f'{tag_name}.Debug.MessageDisplay={md}\r\n')
            if vd:
                lines.append(f'{tag_name}.Debug.ValueDisplay=yes\r\n')
            if lm:
                lines.append(f'{tag_name}.Debug.LogMessage={lm}\r\n')
            if lw:
                lines.append(f'{tag_name}.Debug.LogWrite=yes\r\n')
            if td:
                lines.append(f'{tag_name}.Debug.TriggerDisplay={td}\r\n')
            if tl:
                lines.append(f'{tag_name}.Debug.TriggerLog={tl}\r\n')
            if dd:
                lines.append(f'{tag_name}.Debug.DetailsDisplay=yes\r\n')
            if dt:
                lines.append(f'{tag_name}.Debug.DetailsTypes={dt}\r\n')
            if dtg:
                lines.append(f'{tag_name}.Debug.DetailsTrigger={dtg}\r\n')

        # Mode
        # Only emit TargetHouseMode when overridden — DLL default is Current
        if cond.target_house_mode and cond.target_house_mode != 'Current':
            lines.append(f'TargetHouseMode={cond.target_house_mode}\r\n')
        if cond.allies_mode:
            lines.append(f'RequiredAlliesMode={cond.allies_mode}\r\n')

        return lines

    @property
    def count(self) -> int:
        return len(self._entries)
