"""
sw_chain_walker.py

Walks the superweapon delivery chain to determine whether an "impossible"
building (TechLevel < 1, TechLevel >= 11, or nonexistent) can be obtained
by a human player through any SW delivery mechanism.

Chain walking covers:
  - LimboDelivery SWs (directly delivers buildings into player's limbo)
  - SW firing conditions: AlwaysGranted, host building, Designators,
    RequiredHouses, AllowPlayer
  - Unit obtainability for designator checks:
    ParaDrop aircraft, SpyPlane aircraft, Spawns=, FreeUnit=,
    UnitDelivery SW, Enslaves=, Crewed=

The walker is depth-limited (default 10 steps). When it cannot resolve a
chain within the limit, it returns an UNKNOWN result and the caller should
prompt the user.

TODO:
  - Anim-to-Unit chains (requires artmd.ini parsing)
  - MakeInfantry warhead chains
  - CrateGoodie units
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class Reachability(Enum):
    POSSIBLE    = "possible"     # player can obtain this building
    IMPOSSIBLE  = "impossible"   # no delivery path found
    UNKNOWN     = "unknown"      # chain depth exceeded or ambiguous


@dataclass
class ChainResult:
    reachability: Reachability
    countries:    frozenset[str] = field(default_factory=frozenset)
    # Empty frozenset means "all countries" (no restriction found)
    depth:        int            = 0
    reason:       str            = ""


MAX_DEPTH = 10


class SWChainWalker:
    """
    Walks superweapon delivery chains to determine building reachability.
    """

    def __init__(self, ini, playable_countries: frozenset[str]):
        self.ini      = ini
        self.playable = playable_countries

        # Build indexes at construction time — expensive but done once
        self._sw_ids         = self._collect_sw_ids()
        self._delivery_index = self._build_delivery_index()
        self._host_index     = self._build_host_index()

    # ------------------------------------------------------------------
    # Index builders
    # ------------------------------------------------------------------

    def _collect_sw_ids(self) -> list[str]:
        """All SW IDs from SuperWeaponTypes (all keys, not just numeric)."""
        sec = self.ini.section('SuperWeaponTypes') or {}
        seen, result = set(), []
        for v in sec.values():
            swid = v.strip() if v else ''
            if swid and swid not in seen:
                seen.add(swid)
                result.append(swid)
        return result

    def _build_delivery_index(self) -> dict[str, list[str]]:
        """
        {building_id_upper: [sw_id, ...]} — SWs that deliver this unit/building.

        Covers three mechanisms:
          1. LimboDelivery.Types — Phobos limbo delivery
          2. Deliver.Types on Type=UnitDelivery SWs — Ares unit delivery
          3. ParaDrop.Types on Type=ParaDrop / AmerParaDrop SWs — vanilla paradrop
        """
        index: dict[str, list[str]] = {}
        paradrop_types = {'paradrop', 'amerparadrop', 'spyplane'}
        for swid in self._sw_ids:
            s = self.ini.section(swid)
            if not s:
                continue
            sw_type = s.get('type', '').strip().lower()
            # LimboDelivery (Phobos)
            for bid in (s.get('limbodelivery.types', '') or '').split(','):
                bid = bid.strip()
                if bid:
                    index.setdefault(bid.upper(), []).append(swid)
            # Deliver.Types (Ares UnitDelivery)
            if sw_type == 'unitdelivery':
                for bid in (s.get('deliver.types', '') or '').split(','):
                    bid = bid.strip()
                    if bid:
                        index.setdefault(bid.upper(), []).append(swid)
            # ParaDrop.Types (vanilla ParaDrop / AmerParaDrop)
            if sw_type in paradrop_types:
                pd_num_raw = (s.get('paradrop.num', '') or '').strip()
                try:
                    nums = [int(n.strip()) for n in pd_num_raw.split(',')
                            if n.strip()]
                except ValueError:
                    nums = []
                for i, bid in enumerate(
                        (s.get('paradrop.types', '') or '').split(',')):
                    bid = bid.strip()
                    if not bid:
                        continue
                    # ParaDrop.Num must be > 0 to actually deliver anything
                    num = nums[i] if i < len(nums) else 0
                    if num > 0:
                        index.setdefault(bid.upper(), []).append(swid)
        return index

    def _build_host_index(self) -> dict[str, list[str]]:
        """
        {sw_id: [building_id, ...]} — buildings that host this SW
        via SuperWeapon=, SuperWeapon2=, SuperWeapons=.
        """
        index: dict[str, list[str]] = {}
        for bid in self.ini.list_from_section('BuildingTypes'):
            s = self.ini.section(bid)
            if not s:
                continue
            hosted = set()
            for tag in ('superweapon', 'superweapon2'):
                v = (s.get(tag) or '').strip()
                if v:
                    hosted.add(v)
            for v in (s.get('superweapons') or '').split(','):
                v = v.strip()
                if v:
                    hosted.add(v)
            for swid in hosted:
                index.setdefault(swid, []).append(bid)
        return index

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def can_player_obtain(self, bid: str,
                           visited_blds: frozenset[str] = frozenset(),
                           depth: int = 0) -> ChainResult:
        """
        Can any human player obtain building `bid` through any SW chain?
        Returns ChainResult with reachability and optional country restriction.
        """
        if depth > MAX_DEPTH:
            return ChainResult(Reachability.UNKNOWN, depth=depth,
                               reason=f"depth limit {MAX_DEPTH} reached")

        if bid.upper() in {b.upper() for b in visited_blds}:
            return ChainResult(Reachability.IMPOSSIBLE, depth=depth,
                               reason="circular reference")

        delivering_sws = self._delivery_index.get(bid.upper(), [])
        if not delivering_sws:
            return ChainResult(Reachability.IMPOSSIBLE, depth=depth,
                               reason=f"no SW delivers [{bid}]")

        new_visited = visited_blds | {bid}

        for swid in delivering_sws:
            result = self._can_sw_fire(swid, new_visited, depth + 1)
            if result.reachability == Reachability.POSSIBLE:
                return result
            if result.reachability == Reachability.UNKNOWN:
                # Keep as candidate — prefer POSSIBLE but return UNKNOWN
                # if no POSSIBLE found
                best_unknown = result

        # No POSSIBLE path found
        try:
            return best_unknown   # type: ignore
        except UnboundLocalError:
            return ChainResult(Reachability.IMPOSSIBLE, depth=depth,
                               reason=f"all delivery SWs for [{bid}] failed")

    def _can_sw_fire(self, swid: str,
                      visited_blds: frozenset[str],
                      depth: int) -> ChainResult:
        """
        Can this SW fire for at least one human player?
        Checks: AllowPlayer, AlwaysGranted / host building,
                Designators, RequiredHouses.
        """
        if depth > MAX_DEPTH:
            return ChainResult(Reachability.UNKNOWN, depth=depth,
                               reason=f"depth limit at SW [{swid}]")

        s = self.ini.section(swid)
        if not s:
            return ChainResult(Reachability.IMPOSSIBLE, depth=depth,
                               reason=f"SW [{swid}] has no section")

        # AllowPlayer check
        if s.get('sw.allowplayer', 'yes').lower() == 'no':
            return ChainResult(Reachability.IMPOSSIBLE, depth=depth,
                               reason=f"SW [{swid}] AllowPlayer=no")

        # RequiredHouses — restricts which countries can fire
        req_houses_raw = s.get('sw.requiredhouses', '')
        req_countries: frozenset[str] = frozenset()
        if req_houses_raw.strip():
            houses = frozenset(h.strip() for h in req_houses_raw.split(',')
                               if h.strip())
            # Filter to playable countries only
            req_countries = houses & self.playable
            if not req_countries:
                return ChainResult(Reachability.IMPOSSIBLE, depth=depth,
                                   reason=f"SW [{swid}] RequiredHouses has "
                                          f"no playable countries")

        # AlwaysGranted check
        always = s.get('sw.alwaysgranted', 'no').lower() == 'yes'
        auto_fire = s.get('sw.autofire', 'no').lower() == 'yes'
        if not always:
            # SW must be hosted on a building OR be an AutoFire SW with
            # SW.AuxBuildings (fires when condition is met, no physical host)
            host_blds = self._host_index.get(swid, [])
            aux_blds_raw = s.get('sw.auxbuildings', '')
            aux_blds = [b.strip() for b in aux_blds_raw.split(',')
                        if b.strip()] if aux_blds_raw else []

            if not host_blds and not (auto_fire and aux_blds):
                return ChainResult(Reachability.IMPOSSIBLE, depth=depth,
                                   reason=f"SW [{swid}] not always-granted "
                                          f"and not hosted on any building")

            if host_blds:
                host_reachable = False
                for hbid in host_blds:
                    h_result = self._is_building_obtainable(
                        hbid, visited_blds, depth + 1)
                    if h_result.reachability == Reachability.POSSIBLE:
                        host_reachable = True
                        if not req_countries:
                            req_countries = h_result.countries
                        else:
                            req_countries = req_countries & h_result.countries
                        break
                if not host_reachable:
                    return ChainResult(Reachability.IMPOSSIBLE, depth=depth,
                                       reason=f"SW [{swid}] host buildings "
                                              f"not obtainable")

            elif auto_fire and aux_blds:
                # AutoFire SW triggered by AuxBuildings condition —
                # check if at least one AuxBuilding is player-obtainable
                aux_reachable = False
                for abid in aux_blds:
                    a_result = self._is_building_obtainable(
                        abid, visited_blds, depth + 1)
                    if a_result.reachability == Reachability.POSSIBLE:
                        aux_reachable = True
                        if not req_countries:
                            req_countries = a_result.countries
                        else:
                            req_countries = req_countries & a_result.countries
                        break
                if not aux_reachable:
                    return ChainResult(Reachability.IMPOSSIBLE, depth=depth,
                                       reason=f"SW [{swid}] AuxBuildings "
                                              f"not obtainable")

        # Designator check
        # For LimboDelivery SWs: SW.Designators is the paradrop aircraft that
        # the game spawns automatically to carry the delivery — it does NOT need
        # to be a player-buildable unit. Skip the designator check in this case.
        has_limbo = bool((s.get('limbodelivery.types','') or '').strip())
        desig_raw = s.get('sw.designators', '')
        if desig_raw.strip() and not has_limbo:
            desig_types = [d.strip() for d in desig_raw.split(',')
                           if d.strip()]
            desig_ok = False
            for dtype in desig_types:
                u_result = self._is_unit_obtainable(dtype, visited_blds,
                                                     depth + 1)
                if u_result.reachability == Reachability.POSSIBLE:
                    desig_ok = True
                    # Intersect country restrictions from designator
                    if u_result.countries:
                        if not req_countries:
                            req_countries = u_result.countries
                        else:
                            req_countries = req_countries & u_result.countries
                    break
                if u_result.reachability == Reachability.UNKNOWN:
                    # Prompt user — for now treat as potentially possible
                    desig_ok = True
                    break
            if not desig_ok:
                return ChainResult(Reachability.IMPOSSIBLE, depth=depth,
                                   reason=f"SW [{swid}] designators "
                                          f"{desig_types} not obtainable")

        # SW.AnyDesignator — any unit can designate, but only if
        # at least one unit can exist in range. We don't assume the
        # AI always has units (per spec), so we check if any buildable
        # unit has a DesignatorRange tag set.
        if s.get('sw.anydesignator', 'no').lower() == 'yes':
            any_desig = self._any_designator_unit_exists()
            if not any_desig:
                return ChainResult(Reachability.UNKNOWN, depth=depth,
                                   reason=f"SW [{swid}] AnyDesignator=yes "
                                          f"but no designator units found")

        # Inhibitors — enemy-side, don't block player path
        # (per spec: assume no inhibitors for "can player obtain?" check)

        return ChainResult(Reachability.POSSIBLE,
                           countries=req_countries,
                           depth=depth)

    def _is_building_obtainable(self, bid: str,
                                  visited_blds: frozenset[str],
                                  depth: int) -> ChainResult:
        """
        Is building `bid` obtainable by a human player through normal build
        or SW delivery?

        A building is considered player-obtainable if:
          - TL 1-10 with valid Owner= / ForbiddenHouses= (normal buildable)
          - TL < 1 but NOT impossible (starting buildings like construction yards
            that players begin the game with — they have valid Owner= but
            aren't on the tech slider because you always start with them)
          - Delivered via a reachable SW chain (any TL)
        """
        if depth > MAX_DEPTH:
            return ChainResult(Reachability.UNKNOWN, depth=depth)

        s = self.ini.section(bid)
        if not s:
            return self.can_player_obtain(bid, visited_blds, depth)

        try:
            tl = int(s.get('techlevel', '-1') or '-1')
        except ValueError:
            tl = -1

        # Check if any playable country can own this building
        owner_raw = s.get('owner', '')
        fh_raw    = s.get('forbiddenhouses', '')
        owners    = {o.strip() for o in owner_raw.split(',') if o.strip()}
        forbidden = {f.strip() for f in fh_raw.split(',') if f.strip()}
        allowed   = (owners - forbidden) & self.playable

        if allowed and tl != 11:
            # Has valid owners and isn't TL=11 (hard impossible)
            # TL=-1 buildings with valid owners are starting buildings
            return ChainResult(Reachability.POSSIBLE,
                               countries=frozenset(allowed), depth=depth)

        # Not normally obtainable — try SW delivery
        return self.can_player_obtain(bid, visited_blds, depth)

    def _is_unit_obtainable(self, uid: str,
                              visited_blds: frozenset[str],
                              depth: int) -> ChainResult:
        """
        Can a player obtain/spawn unit `uid`?
        Checks: buildable, ParaDrop aircraft, SpyPlane aircraft,
                Spawns= on buildable units, FreeUnit= on buildings,
                UnitDelivery SW.
        """
        if depth > MAX_DEPTH:
            return ChainResult(Reachability.UNKNOWN, depth=depth)

        s = self.ini.section(uid)
        if not s:
            return ChainResult(Reachability.IMPOSSIBLE, depth=depth,
                               reason=f"unit [{uid}] has no section")

        try:
            tl = int(s.get('techlevel', '-1') or '-1')
        except ValueError:
            tl = -1

        # Normally buildable (TL 1-10, valid prereqs)
        if 1 <= tl <= 10:
            owner_raw = s.get('owner', '')
            fh_raw    = s.get('forbiddenhouses', '')
            owners    = {o.strip() for o in owner_raw.split(',') if o.strip()}
            forbidden = {f.strip() for f in fh_raw.split(',') if f.strip()}
            allowed   = (owners - forbidden) & self.playable
            if allowed:
                return ChainResult(Reachability.POSSIBLE,
                                   countries=frozenset(allowed), depth=depth)

        # ParaDrop aircraft: any ParaDrop SW where ParaDrop.Aircraft=uid
        # or any country/side where ParaDrop.Aircraft=uid
        result = self._check_paradrop_aircraft(uid, depth)
        if result.reachability == Reachability.POSSIBLE:
            return result

        # SpyPlane aircraft: any SW with Type=SpyPlane and SpyPlane.Type=uid
        result = self._check_spyplane_aircraft(uid, depth)
        if result.reachability == Reachability.POSSIBLE:
            return result

        # Spawns= on a buildable unit
        result = self._check_spawned_from(uid, depth)
        if result.reachability == Reachability.POSSIBLE:
            return result

        # FreeUnit= on a buildable building
        result = self._check_free_unit(uid, depth)
        if result.reachability == Reachability.POSSIBLE:
            return result

        # UnitDelivery SW delivers this unit directly
        result = self._check_unit_delivery_sw(uid, visited_blds, depth)
        if result.reachability != Reachability.IMPOSSIBLE:
            return result

        # Enslaves= (unit is a slave produced by a buildable unit)
        result = self._check_enslaved(uid, depth)
        if result.reachability == Reachability.POSSIBLE:
            return result

        return ChainResult(Reachability.IMPOSSIBLE, depth=depth,
                           reason=f"no path to obtain unit [{uid}]")

    # ------------------------------------------------------------------
    # Unit obtainability sub-checks
    # ------------------------------------------------------------------

    def _check_paradrop_aircraft(self, uid: str, depth: int) -> ChainResult:
        """
        Check if uid appears as a ParaDrop aircraft in any SW or country.
        Resolution order (simplified): SW tag > country tag > side tag > General.
        """
        countries_with_access: set[str] = set()

        # Check SWs
        for swid in self._sw_ids:
            s = self.ini.section(swid)
            if not s: continue
            sw_type = s.get('type', '').lower()
            if sw_type not in ('paradrop', 'amerparadrop'):
                continue
            if s.get('sw.allowplayer', 'yes').lower() == 'no':
                continue
            # Check ParaDrop.Aircraft and country/side variants
            if self._sw_has_aircraft(s, uid):
                req = s.get('sw.requiredhouses', '')
                if req.strip():
                    houses = {h.strip() for h in req.split(',') if h.strip()}
                    countries_with_access |= (houses & self.playable)
                else:
                    # No restriction — all playable countries
                    return ChainResult(Reachability.POSSIBLE,
                                       countries=frozenset(), depth=depth)

        # Check country sections
        sides = self.ini.section('Sides') or {}
        for faction, val in sides.items():
            for country in (c.strip() for c in val.split(',') if c.strip()):
                cs = self.ini.section(country)
                if not cs: continue
                aircraft = (cs.get('paradrop.aircraft') or '').strip()
                if aircraft.upper() == uid.upper():
                    if cs.get('multiplay', 'no').lower() == 'yes':
                        countries_with_access.add(country)

        # Check side sections
        for faction in (self.ini.section('Sides') or {}):
            fs = self.ini.section(faction)
            if not fs: continue
            aircraft = (fs.get('paradrop.aircraft') or '').strip()
            if aircraft.upper() == uid.upper():
                # All countries in this side
                val = (self.ini.section('Sides') or {}).get(faction, '')
                for c in val.split(','):
                    c = c.strip()
                    if c:
                        countries_with_access.add(c)

        if countries_with_access:
            return ChainResult(Reachability.POSSIBLE,
                               countries=frozenset(countries_with_access & self.playable),
                               depth=depth)
        return ChainResult(Reachability.IMPOSSIBLE, depth=depth)

    def _sw_has_aircraft(self, sw_sec: dict, uid: str) -> bool:
        """Check if a SW section references uid as any of its paradrop aircraft."""
        for k, v in sw_sec.items():
            if 'aircraft' in k.lower() and (v or '').strip().upper() == uid.upper():
                return True
        return False

    def _check_spyplane_aircraft(self, uid: str, depth: int) -> ChainResult:
        """Check if uid appears as SpyPlane.Type in any player-accessible SW."""
        for swid in self._sw_ids:
            s = self.ini.section(swid)
            if not s: continue
            if s.get('type', '').lower() != 'spyplane':
                continue
            if s.get('sw.allowplayer', 'yes').lower() == 'no':
                continue
            if (s.get('spyplane.type') or '').strip().upper() == uid.upper():
                req = s.get('sw.requiredhouses', '')
                countries = frozenset()
                if req.strip():
                    houses = {h.strip() for h in req.split(',') if h.strip()}
                    countries = houses & self.playable
                return ChainResult(Reachability.POSSIBLE,
                                   countries=countries, depth=depth)
        return ChainResult(Reachability.IMPOSSIBLE, depth=depth)

    def _check_spawned_from(self, uid: str, depth: int) -> ChainResult:
        """Check if uid is spawned by a buildable unit (Spawns= tag)."""
        # Check VehicleTypes and AircraftTypes for Spawns=uid
        for sect in ('VehicleTypes', 'AircraftTypes'):
            for parent_id in self.ini.list_from_section(sect):
                ps = self.ini.section(parent_id)
                if not ps: continue
                spawns = (ps.get('spawns') or '').strip().upper()
                if spawns == uid.upper():
                    # Is the parent buildable?
                    try:
                        ptl = int(ps.get('techlevel', '-1') or '-1')
                    except ValueError:
                        ptl = -1
                    if 1 <= ptl <= 10:
                        owner_raw = ps.get('owner', '')
                        fh_raw    = ps.get('forbiddenhouses', '')
                        owners    = {o.strip() for o in owner_raw.split(',')
                                     if o.strip()}
                        forbidden = {f.strip() for f in fh_raw.split(',')
                                     if f.strip()}
                        allowed   = (owners - forbidden) & self.playable
                        if allowed:
                            return ChainResult(Reachability.POSSIBLE,
                                               countries=frozenset(allowed),
                                               depth=depth)
        return ChainResult(Reachability.IMPOSSIBLE, depth=depth)

    def _check_free_unit(self, uid: str, depth: int) -> ChainResult:
        """Check if uid is a FreeUnit= on any buildable building."""
        for bid in self.ini.list_from_section('BuildingTypes'):
            bs = self.ini.section(bid)
            if not bs: continue
            if (bs.get('freeunit') or '').strip().upper() != uid.upper():
                continue
            try:
                btl = int(bs.get('techlevel', '-1') or '-1')
            except ValueError:
                btl = -1
            if 1 <= btl <= 10:
                owner_raw = bs.get('owner', '')
                fh_raw    = bs.get('forbiddenhouses', '')
                owners    = {o.strip() for o in owner_raw.split(',')
                             if o.strip()}
                forbidden = {f.strip() for f in fh_raw.split(',')
                             if f.strip()}
                allowed   = (owners - forbidden) & self.playable
                if allowed:
                    return ChainResult(Reachability.POSSIBLE,
                                       countries=frozenset(allowed), depth=depth)
        return ChainResult(Reachability.IMPOSSIBLE, depth=depth)

    def _check_unit_delivery_sw(self, uid: str,
                                  visited_blds: frozenset[str],
                                  depth: int) -> ChainResult:
        """
        Check if uid is delivered by a SW the player can fire.
        Checks LimboDelivery.Types, Deliver.Types (UnitDelivery),
        and ParaDrop.Types (ParaDrop / AmerParaDrop).
        """
        uid_upper = uid.upper()
        for swid in self._sw_ids:
            s = self.ini.section(swid)
            if not s: continue
            sw_type = s.get('type', '').lower()
            if s.get('sw.allowplayer', 'yes').lower() == 'no':
                continue

            delivers = set()
            # LimboDelivery.Types
            for t in (s.get('limbodelivery.types', '') or '').split(','):
                t = t.strip()
                if t: delivers.add(t.upper())
            # Deliver.Types (UnitDelivery)
            if sw_type == 'unitdelivery':
                for t in (s.get('deliver.types', '') or '').split(','):
                    t = t.strip()
                    if t: delivers.add(t.upper())
            # ParaDrop.Types (with Num > 0)
            if sw_type in ('paradrop', 'amerparadrop'):
                pd_num_raw = (s.get('paradrop.num', '') or '').strip()
                try:
                    nums = [int(n.strip()) for n in pd_num_raw.split(',')
                            if n.strip()]
                except ValueError:
                    nums = []
                for i, t in enumerate(
                        (s.get('paradrop.types', '') or '').split(',')):
                    t = t.strip()
                    if t:
                        num = nums[i] if i < len(nums) else 0
                        if num > 0:
                            delivers.add(t.upper())

            if uid_upper not in delivers:
                continue
            result = self._can_sw_fire(swid, visited_blds, depth + 1)
            if result.reachability != Reachability.IMPOSSIBLE:
                return result
        return ChainResult(Reachability.IMPOSSIBLE, depth=depth)

    def _check_enslaved(self, uid: str, depth: int) -> ChainResult:
        """Check if uid is a slave unit produced by a buildable enslaver."""
        for sect in ('VehicleTypes', 'InfantryTypes'):
            for parent_id in self.ini.list_from_section(sect):
                ps = self.ini.section(parent_id)
                if not ps: continue
                enslaves = (ps.get('enslaves') or '').strip().upper()
                if enslaves == uid.upper():
                    try:
                        ptl = int(ps.get('techlevel', '-1') or '-1')
                    except ValueError:
                        ptl = -1
                    if 1 <= ptl <= 10:
                        owner_raw = ps.get('owner', '')
                        fh_raw    = ps.get('forbiddenhouses', '')
                        owners    = {o.strip() for o in owner_raw.split(',')
                                     if o.strip()}
                        forbidden = {f.strip() for f in fh_raw.split(',')
                                     if f.strip()}
                        allowed   = (owners - forbidden) & self.playable
                        if allowed:
                            return ChainResult(Reachability.POSSIBLE,
                                               countries=frozenset(allowed),
                                               depth=depth)
        return ChainResult(Reachability.IMPOSSIBLE, depth=depth)

    def _any_designator_unit_exists(self) -> bool:
        """
        For SW.AnyDesignator=yes: check if any unit has DesignatorRange= set.
        If none do, the SW can never fire since no valid designators exist.
        """
        for sect in ('VehicleTypes', 'InfantryTypes', 'AircraftTypes',
                     'BuildingTypes'):
            for uid in self.ini.list_from_section(sect):
                s = self.ini.section(uid)
                if s and s.get('designatorrange', ''):
                    return True
        return False
