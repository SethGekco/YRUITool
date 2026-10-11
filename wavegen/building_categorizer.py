"""
building_categorizer.py
=======================
Scans rulesmd.ini to detect and categorize all infrastructure buildings
relevant to the dummy prerequisite system.

Detection uses three sources, merged with confidence:
  1. [AI] section keys  (BuildConst=, BuildBarracks=, etc.)
  2. [General] section keys (PrerequisitePower=, PrerequisiteFactory=, etc.)
  3. Building tag analysis (Factory=, Refinery=yes, Radar=yes, etc.)

Categories:
  CONSTRUCTION_YARD   Factory=BuildingType
  POWER_PLANT         Power > 0, no Factory
  BARRACKS            Factory=InfantryType
  REFINERY            Refinery=yes
  WAR_FACTORY         Factory=UnitType, Naval=no
  RADAR               Radar=yes
  SHIPYARD            Factory=UnitType, Naval=yes
  SERVICE_DEPOT       UnitRepair=yes, no Factory
  BATTLE_LAB          BuildCat=Tech (no Factory, high TL)
  PRE_LAB_MISC        BuildCat=Combat/Resource (no Factory, below lab TL)
  POST_LAB_MISC       BuildCat=Combat/Resource (no Factory, above lab TL)
  DUMMY_BUILDING      [AI] BuildDummy= — exclude from prereq system

Buildings in DUMMY_BUILDING are never used in the prereq system.
"""

from dataclasses import dataclass, field
from pathlib import Path


# Category constants
CONSTRUCTION_YARD = "Construction Yards"
POWER_PLANT       = "Power Plants"
BARRACKS          = "Barracks"
REFINERY          = "Refineries"
WAR_FACTORY       = "War Factories"
RADAR             = "Radar"
SHIPYARD          = "Shipyards"
SERVICE_DEPOT     = "Service Depots"
BATTLE_LAB        = "Battle Labs"
PRE_LAB_MISC      = "Pre-Lab Misc"
POST_LAB_MISC     = "Post-Lab Misc"
DUMMY_BUILDING    = "Dummy Buildings"
UNCATEGORIZED     = "Uncategorized"

# Display order
CATEGORY_ORDER = [
    CONSTRUCTION_YARD,
    POWER_PLANT,
    BARRACKS,
    REFINERY,
    WAR_FACTORY,
    RADAR,
    SHIPYARD,
    SERVICE_DEPOT,
    BATTLE_LAB,
    PRE_LAB_MISC,
    POST_LAB_MISC,
    DUMMY_BUILDING,
    UNCATEGORIZED,
]


@dataclass
class BuildingInfo:
    idx:      int          # BuildingTypes list index
    bid:      str          # section ID e.g. GAWEAP
    name:     str          # Name= from rulesmd
    category: str          # one of the category constants
    techlevel: int
    faction:  str          # gdi / nod / thirdside / neutral / unknown
    sources:  list[str] = field(default_factory=list)  # how it was detected


class BuildingCategorizer:
    """
    Scans rulesmd.ini and categorizes all infrastructure buildings.

    Usage:
        cat = BuildingCategorizer('rulesmd.ini')
        cat.scan()
        for cat_name, buildings in cat.by_category().items():
            print(cat_name, [b.bid for b in buildings])
    """

    def __init__(self, rulesmd_path: str | Path):
        from rulesmd_parser import IniParser
        self.path    = Path(rulesmd_path)
        self.ini     = IniParser(str(rulesmd_path))
        self.buildings: list[BuildingInfo] = []
        self._dummy_bids: set[str] = set()
        self._lab_techlevel: int   = 7  # approximate battle lab TL

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> list[BuildingInfo]:
        """Run the full scan. Returns list of BuildingInfo sorted by index."""
        building_list = self.ini.list_from_section('BuildingTypes')

        # Load category hints from [AI] and [General]
        ai_hints  = self._load_ai_hints()
        gen_hints = self._load_general_hints()

        # Detect dummy buildings first (exclude them from everything else)
        self._dummy_bids = set(ai_hints.get(DUMMY_BUILDING, []))

        # Determine faction mapping from [Sides]
        faction_map = self._build_faction_map()

        # Estimate battle lab TL from known labs
        self._estimate_lab_techlevel(building_list, ai_hints)

        results = []
        for idx, bid in enumerate(building_list):
            if not bid or bid.startswith(';'):
                continue
            sec  = self.ini.section(bid)
            name = sec.get('name', bid)
            try:
                tl = int(sec.get('techlevel', '0'))
            except ValueError:
                tl = -1

            # Skip decorative/civilian buildings (TL -1 unless CY)
            is_cy_candidate = bid in ai_hints.get(CONSTRUCTION_YARD, [])
            if tl < 0 and not is_cy_candidate:
                continue

            # Skip walls and defenses
            if sec.get('wall', 'no').lower() == 'yes':
                continue
            if sec.get('isbasedefense', 'no').lower() == 'yes':
                continue

            sources  = []
            category = self._categorize(
                bid, idx, sec, tl, ai_hints, gen_hints, sources)

            faction = faction_map.get(bid, 'unknown')

            results.append(BuildingInfo(
                idx      = idx,
                bid      = bid,
                name     = name,
                category = category,
                techlevel = tl,
                faction  = faction,
                sources  = sources,
            ))

        # Sort by category order then by index
        cat_rank = {c: i for i, c in enumerate(CATEGORY_ORDER)}
        results.sort(key=lambda b: (cat_rank.get(b.category, 99), b.idx))

        self.buildings = results
        return results

    def by_category(self) -> dict[str, list[BuildingInfo]]:
        """Return buildings grouped by category, in display order."""
        from collections import defaultdict
        groups: dict[str, list[BuildingInfo]] = defaultdict(list)
        for b in self.buildings:
            groups[b.category].append(b)
        # Return in display order, skip empty categories
        return {c: groups[c] for c in CATEGORY_ORDER if groups[c]}

    def exclude_dummies(self) -> list[BuildingInfo]:
        """
        Buildings suitable for the dummy prereq system.
        Excludes only actual DUMMY_BUILDING category entries (BuildDummy= list)
        since those are fake/invisible buildings that serve no prereq purpose.
        All other infrastructure buildings including superweapon structures
        (nuke silos, weather, etc.) are included — dummies are for AI logic,
        not human visibility.
        """
        return [b for b in self.buildings
                if b.category != DUMMY_BUILDING
                and b.bid not in self._dummy_bids]

    def print_summary(self):
        """Print categorized building list for user display."""
        by_cat = self.by_category()
        print("Detected infrastructure buildings:")
        print()
        for cat_name in CATEGORY_ORDER:
            blds = by_cat.get(cat_name, [])
            if not blds:
                continue
            ids = ', '.join(b.bid for b in blds)
            print(f"  {cat_name}:")
            for b in blds:
                marker = " [DUMMY - excluded]" if b.category == DUMMY_BUILDING else ""
                print(f"    [{b.idx:>4}] {b.bid:<15} {b.name}{marker}")
            print()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_ai_hints(self) -> dict[str, list[str]]:
        """Load building IDs from [AI] section keys."""
        ai = self.ini.section('AI')
        def _ids(key):
            return [x.strip().split(';')[0].strip()
                    for x in ai.get(key, '').split(',')
                    if x.strip() and not x.strip().startswith(';')]

        return {
            CONSTRUCTION_YARD: _ids('buildconst'),
            POWER_PLANT:       _ids('buildpower'),
            REFINERY:          _ids('buildrefinery'),
            BARRACKS:          _ids('buildbarracks'),
            BATTLE_LAB:        _ids('buildtech'),
            WAR_FACTORY:       _ids('buildweapons'),
            RADAR:             _ids('buildradar'),
            SHIPYARD:          _ids('buildnavalyard'),
            DUMMY_BUILDING:    _ids('builddummy'),
        }

    def _load_general_hints(self) -> dict[str, list[str]]:
        """Load building IDs from [General] prerequisite keys."""
        gen = self.ini.section('General')
        def _ids(key):
            return [x.strip().split(';')[0].strip()
                    for x in gen.get(key, '').split(',')
                    if x.strip() and not x.strip().startswith(';')]

        return {
            POWER_PLANT:  _ids('prerequisitepower'),
            WAR_FACTORY:  _ids('prerequisitefactory'),
            BARRACKS:     _ids('prerequisitebarracks'),
            RADAR:        _ids('prerequisiteradar'),
            BATTLE_LAB:   _ids('prerequisitetech'),
            REFINERY:     _ids('prerequisiteproc'),
        }

    def _estimate_lab_techlevel(self, building_list, ai_hints):
        """Estimate the TechLevel of battle labs from known IDs."""
        lab_ids = set(ai_hints.get(BATTLE_LAB, []))
        levels  = []
        for bid in lab_ids:
            sec = self.ini.section(bid)
            try:
                levels.append(int(sec.get('techlevel', '7')))
            except ValueError:
                pass
        if levels:
            self._lab_techlevel = min(levels)

    def _build_faction_map(self) -> dict[str, str]:
        """Map building IDs to faction via [Sides] countries."""
        faction_map: dict[str, str] = {}

        # Read [Sides] — each side = faction
        in_sides = False
        sides_order = []
        with self.path.open(encoding='latin-1') as f:
            for line in f:
                s = line.strip()
                if s == '[Sides]':
                    in_sides = True
                    continue
                if in_sides and s.startswith('['):
                    break
                if in_sides and '=' in s and not s.startswith(';'):
                    side_name, _, rest = s.partition('=')
                    countries = [c.strip().split(';')[0].strip()
                                 for c in rest.split(',')
                                 if c.strip() and not c.strip().startswith(';')]
                    sides_order.append((side_name.strip().lower(), countries))

        # Map each country's buildings to faction
        for faction_name, countries in sides_order:
            for country in countries:
                sec = self.ini.section(country)
                for key in ['baseunit', 'constructionyard']:
                    # Not the most reliable — use Owner= on buildings instead
                    pass

        # Better: check Owner= on each building
        building_list = self.ini.list_from_section('BuildingTypes')
        for bid in building_list:
            sec   = self.ini.section(bid)
            owner = sec.get('owner', '')
            if not owner:
                continue
            owners = [o.strip() for o in owner.split(',')]
            # Match to faction
            for faction_name, countries in sides_order:
                if any(o in countries for o in owners):
                    faction_map[bid] = faction_name
                    break

        return faction_map

    def _categorize(self, bid: str, idx: int, sec,
                    tl: int, ai_hints: dict, gen_hints: dict,
                    sources: list) -> str:
        """Determine the category for a single building."""

        # Dummy buildings — always first
        if bid in self._dummy_bids:
            sources.append('[AI].BuildDummy')
            return DUMMY_BUILDING

        factory    = sec.get('factory', '')
        power      = sec.get('power', '0')
        refinery   = sec.get('refinery', 'no').lower()
        radar      = sec.get('radar', 'no').lower()
        naval      = sec.get('naval', 'no').lower()
        unitrepair = sec.get('unitrepair', 'no').lower()
        buildcat   = sec.get('buildcat', '').lower()
        try:
            power_int = int(power)
        except ValueError:
            power_int = 0

        # Helper: check if bid in any hint list
        def in_hints(category, hint_dicts):
            for hd in hint_dicts:
                if bid in hd.get(category, []):
                    return True
            return False

        hint_dicts = [ai_hints, gen_hints]

        # Construction Yard — Factory=BuildingType or [AI].BuildConst
        if factory.lower() == 'buildingtype' or in_hints(CONSTRUCTION_YARD, hint_dicts):
            sources.append('Factory=BuildingType' if factory.lower() == 'buildingtype'
                           else '[AI].BuildConst')
            return CONSTRUCTION_YARD

        # Power Plant — positive power, no factory, or [AI].BuildPower
        if in_hints(POWER_PLANT, hint_dicts):
            sources.append('[AI].BuildPower / [General].PrerequisitePower')
            return POWER_PLANT
        if power_int > 0 and not factory:
            sources.append('Power>0, no Factory')
            return POWER_PLANT

        # Refinery — Refinery=yes or [AI]/[General] hints
        if refinery == 'yes' or in_hints(REFINERY, hint_dicts):
            sources.append('Refinery=yes' if refinery == 'yes'
                           else '[AI].BuildRefinery')
            return REFINERY

        # Barracks — Factory=InfantryType or [AI]/[General] hints
        if factory.lower() == 'infantrytype' or in_hints(BARRACKS, hint_dicts):
            sources.append('Factory=InfantryType' if factory.lower() == 'infantrytype'
                           else '[AI].BuildBarracks')
            return BARRACKS

        # Shipyard — Factory=UnitType + Naval=yes or [AI].BuildNavalYard
        if in_hints(SHIPYARD, hint_dicts):
            sources.append('[AI].BuildNavalYard')
            return SHIPYARD
        if factory.lower() == 'unittype' and naval == 'yes':
            sources.append('Factory=UnitType + Naval=yes')
            return SHIPYARD

        # War Factory — Factory=UnitType (non-naval) or [AI]/[General] hints
        if in_hints(WAR_FACTORY, hint_dicts):
            sources.append('[AI].BuildWeapons / [General].PrerequisiteFactory')
            return WAR_FACTORY
        if factory.lower() == 'unittype' and naval != 'yes':
            sources.append('Factory=UnitType')
            return WAR_FACTORY

        # Radar — Radar=yes or [AI]/[General] hints
        if in_hints(RADAR, hint_dicts):
            sources.append('[AI].BuildRadar / [General].PrerequisiteRadar')
            return RADAR
        if radar == 'yes':
            sources.append('Radar=yes')
            return RADAR

        # Service Depot — UnitRepair=yes, no factory
        if unitrepair == 'yes' and not factory:
            sources.append('UnitRepair=yes')
            return SERVICE_DEPOT

        # Battle Lab — [AI].BuildTech or [General].PrerequisiteTech
        if in_hints(BATTLE_LAB, hint_dicts):
            sources.append('[AI].BuildTech / [General].PrerequisiteTech')
            return BATTLE_LAB
        # Fallback: BuildCat=Tech with no factory and high TL
        if buildcat == 'tech' and not factory and tl >= self._lab_techlevel:
            sources.append('BuildCat=Tech, no Factory, TL>=LabTL')
            return BATTLE_LAB

        # Pre/Post Lab Misc — no factory, buildcat=combat or resource
        if not factory and buildcat in ('combat', 'resource') and tl > 0:
            if tl < self._lab_techlevel:
                sources.append(f'BuildCat={buildcat}, TL<LabTL')
                return PRE_LAB_MISC
            else:
                sources.append(f'BuildCat={buildcat}, TL>=LabTL')
                return POST_LAB_MISC

        # Aircraft factory — treat as War Factory variant
        if factory.lower() == 'aircrafttype':
            sources.append('Factory=AircraftType')
            return WAR_FACTORY

        return UNCATEGORIZED
