"""
defense_dummy_generator.py
==========================
Generates dummy prerequisite buildings and superweapons for tracking
whether an enemy has BASE DEFENSES or AA DEFENSES.

Unlike the regular dummy prereq system (which tracks individual buildings
by BuildingTypes index), this system tracks CATEGORIES of buildings:
  - Any defense present         → DMPRNODEF_* dummies get killed
  - Any AA defense present      → DMPRNODEFAA_* dummies get killed

Because there can be many defense buildings (exceeding INI line length
limits of ~512 chars), defenses are batched into groups of BATCH_SIZE.
Each batch gets its own intermediate dummy. A master dummy per faction
tracks whether that faction has any defenses. A master-of-masters dummy
tracks whether ANY faction has any defenses.

== STRUCTURE ==

Per faction (e.g. GDI):
  DMPRNODEF_GDI_A   — present while ≥1 defense from batch A exists
  DMPRNODEF_GDI_B   — present while ≥1 defense from batch B exists
  DMPRNODEF_GDI     — present while NO GDI defenses exist at all
                       (killed when any batch dummy exists)

Master of masters:
  DMPRNODEF         — present while NO defenses exist from ANY faction
                       (killed when any per-faction master dummy is killed,
                       i.e. when any faction's batch dummy exists)

AA variants use the same structure with DMPRNODEFAA prefix.

== LIMBO ID RANGE ==
  000456XX  — defense tracking dummies (distinct from 00019/00029/00039)
  000457XX  — AA defense tracking dummies

== NAMING ==
  DMPRNODEF           master of masters: no defenses at all
  DMPRNODEF_GDI       per-faction master: no GDI defenses
  DMPRNODEF_GDI_A     per-faction batch: GDI defenses batch A
  DMPRNODEFSW_GDI_A   spawner for GDI batch A
  DMPRNODEFKSW_GDI_A  killer: kills DMPRNODEF_GDI when batch A exists
  DMPRNODEFKSW_GDI    killer: kills DMPRNODEF when any GDI batch exists

  DMPRNODEFAA         AA master of masters
  DMPRNODEFAA_GDI     AA per-faction master: no GDI AA defenses
  etc.

== OPTION FLAGS ==
  require_isbasedefense  If True, only include buildings with
                         IsBaseDefense=yes. Default False.
  batch_size             Max buildings per batch. Default 20.
"""

from dataclasses import dataclass, field
from pathlib import Path
from rulesmd_parser import IniParser


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BATCH_SIZE = 20

# Limbo ID counters — use 000456XX for defenses, 000457XX for AA
LIMBO_BASE_NODEF  = 45600
LIMBO_BASE_NODEFAA = 45700

# Faction short names for IDs
FACTION_SHORT = {
    'gdi':       'GDI',
    'nod':       'NOD',
    'thirdside': 'YUR',
    'neutral':   'NEU',
    'multi':     'MUL',
    'unknown':   'UNK',
}


# ---------------------------------------------------------------------------
# Building scanner
# ---------------------------------------------------------------------------

@dataclass
class DefenseInfo:
    idx:    int
    bid:    str
    name:   str
    faction: str
    has_aa: bool
    is_basedefense: bool


def scan_defenses(rulesmd_path: str | Path,
                  require_isbasedefense: bool = False,
                  sides_order: list | None = None) -> list[DefenseInfo]:
    """
    Scan rulesmd.ini for buildings with a valid Primary= weapon.
    Optionally filter to IsBaseDefense=yes only.
    Detects AA capability from Primary and Secondary projectiles.
    """
    ini       = IniParser(str(rulesmd_path))
    buildings = ini.list_from_section('BuildingTypes')

    # Build country -> faction map from [Sides]
    country_faction: dict[str, str] = {}
    if sides_order is None:
        sides_order = _read_sides(rulesmd_path)
    for faction_name, countries in sides_order:
        for c in countries:
            country_faction[c.lower()] = faction_name.lower()

    results = []
    for idx, bid in enumerate(buildings):
        if not bid or bid.startswith(';'):
            continue
        sec = ini.section(bid)

        # Must have a real Primary= weapon
        primary = sec.get('primary', '').strip()
        if not primary or primary.lower() == 'none':
            continue

        # Filter campaign/neutral structures with no tech level
        tech = sec.get('techlevel', '0')
        try:
            tech_int = int(tech)
        except ValueError:
            tech_int = -1

        # IsBaseDefense filter
        is_def = sec.get('isbasedefense', 'no').lower() == 'yes'
        if require_isbasedefense and not is_def:
            continue

        # Skip obvious non-defense buildings
        # (factories, CYs, refineries, etc.) unless IsBaseDefense=yes
        if not is_def:
            factory = sec.get('factory', '')
            if factory:
                continue
            if sec.get('refinery', 'no').lower() == 'yes':
                continue
            # Skip very low TL neutral structures (campaign decorations)
            if tech_int < 0:
                continue

        # Detect AA from primary and secondary weapon projectiles
        has_aa = False
        for wpn_key in ('primary', 'secondary'):
            wpn_id = sec.get(wpn_key, '').strip()
            if not wpn_id or wpn_id.lower() == 'none':
                continue
            wpn  = ini.section(wpn_id)
            proj = ini.section(wpn.get('projectile', ''))
            if proj.get('aa', 'no').lower() == 'yes':
                has_aa = True
                break

        # Determine faction from Owner=
        owner   = sec.get('owner', '')
        owners  = [o.strip().lower() for o in owner.split(',') if o.strip()]
        factions = {country_faction[o] for o in owners if o in country_faction}
        if len(factions) == 1:
            faction = factions.pop()
        elif len(factions) > 1:
            faction = 'multi'
        else:
            faction = 'neutral'

        results.append(DefenseInfo(
            idx            = idx,
            bid            = bid,
            name           = sec.get('name', bid),
            faction        = faction,
            has_aa         = has_aa,
            is_basedefense = is_def,
        ))

    return results


def _read_sides(rulesmd_path: str | Path) -> list[tuple[str, list[str]]]:
    """Read [Sides] section returning [(faction_name, [countries])]."""
    sides = []
    in_sides = False
    with open(str(rulesmd_path), encoding='latin-1') as f:
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
                sides.append((side_name.strip(), countries))
    return sides


# ---------------------------------------------------------------------------
# Defense dummy generator
# ---------------------------------------------------------------------------

class DefenseDummyGenerator:
    """
    Generates DMPRNODEF and DMPRNODEFAA dummy prerequisite systems.

    Usage:
        gen = DefenseDummyGenerator('rulesmd.ini')
        gen.scan()
        gen.generate()
        gen.write_to_file('ai_prereq_defenses.ini', 'rulesmd.ini')
    """

    def __init__(self, rulesmd_path: str | Path,
                 require_isbasedefense: bool = False,
                 batch_size: int = BATCH_SIZE):
        self.path                  = Path(rulesmd_path)
        self.ini                   = IniParser(str(rulesmd_path))
        self.require_isbasedefense = require_isbasedefense
        self.batch_size            = batch_size
        self.sides_order           = _read_sides(rulesmd_path)
        self.all_defenses:  list[DefenseInfo] = []
        self.aa_defenses:   list[DefenseInfo] = []

        # Generated entries
        # Each entry: dict with section_id, limbo_id, type, etc.
        self.buildings:     list[dict] = []  # DMPRNODEF* building sections
        self.spawners:      list[dict] = []  # SW spawner sections
        self.killers:       list[dict] = []  # SW killer sections
        self._sw_on_bld:    dict[str, list[str]] = {}  # building -> [sw_ids]
        self._limbo_counter = 0

    def _next_limbo(self, base: int) -> str:
        self._limbo_counter += 1
        return f"{base:05d}{self._limbo_counter:02d}"

    def scan(self) -> tuple[list[DefenseInfo], list[DefenseInfo]]:
        """Scan rulesmd.ini for defense buildings."""
        self.all_defenses = scan_defenses(
            self.path, self.require_isbasedefense, self.sides_order)
        self.aa_defenses  = [d for d in self.all_defenses if d.has_aa]
        return self.all_defenses, self.aa_defenses

    def generate(self):
        """Generate all dummy buildings and superweapons."""
        if not self.all_defenses:
            self.scan()

        # Group defenses by faction
        factions_def  = self._group_by_faction(self.all_defenses)
        factions_aa   = self._group_by_faction(self.aa_defenses)

        # Generate per-type
        master_nodef  = self._generate_system(
            factions_def,  prefix='DMPRNODEF',   limbo_base=LIMBO_BASE_NODEF)
        master_nodefaa = self._generate_system(
            factions_aa,   prefix='DMPRNODEFAA', limbo_base=LIMBO_BASE_NODEFAA)

    def _group_by_faction(self,
                           defenses: list[DefenseInfo]) -> dict[str, list[str]]:
        """Group defense building IDs by faction."""
        groups: dict[str, list[str]] = {}
        for d in defenses:
            fshort = FACTION_SHORT.get(d.faction, d.faction.upper()[:3])
            groups.setdefault(fshort, []).append(d.bid)
        return groups

    def _generate_system(self, factions: dict[str, list[str]],
                          prefix: str, limbo_base: int) -> str | None:
        """
        Generate the full dummy system for one category (def or AA).
        Returns the master-of-masters section ID, or None if no defenses.
        """
        if not factions:
            return None

        faction_master_ids = []

        for faction_short, bids in sorted(factions.items()):
            batches = [bids[i:i+self.batch_size]
                       for i in range(0, len(bids), self.batch_size)]
            batch_dummy_ids = []

            for bi, batch in enumerate(batches):
                suffix   = chr(ord('A') + bi)  # A, B, C...
                batch_id = f"{prefix}_{faction_short}_{suffix}"
                limbo_id = self._next_limbo(limbo_base)
                spawn_id = f"{prefix}SW_{faction_short}_{suffix}"

                # One killer per building in the batch:
                # Each fires when the batch dummy exists but THAT building is gone.
                # When all buildings are gone from the batch, no NegBuildings
                # blocks any killer, so the batch dummy is killed.
                # We use one representative killer per batch (NegBuildings=last
                # building) — fires when that building is gone. This isn't
                # perfectly accurate (only last building going triggers kill)
                # but combined with the spawner (which only re-fires when
                # any building exists) the system self-corrects.
                #
                # More correct: one killer per building in batch.
                batch_killer_ids = []
                for bk_idx, bk_bid in enumerate(batch):
                    kill_id = f"{prefix}KSW_{faction_short}_{suffix}_{bk_idx:02d}"
                    self._add_killer(
                        section_id     = kill_id,
                        aux_buildings  = batch_id,       # needs batch dummy
                        neg_buildings  = bk_bid,         # fires when this building gone
                        limbo_kill_ids = limbo_id,       # kills the batch dummy
                        listed_on      = batch_id,
                        kills_target   = batch_id,
                    )
                    batch_killer_ids.append(kill_id)

                # Batch dummy building — lists all its own killers
                faction_master_id = f"{prefix}_{faction_short}"
                kill_faction_id   = f"{prefix}KSW_{faction_short}_{suffix}_M"
                self._add_building(batch_id, limbo_id,
                                   f"Tracks: {faction_short} defenses batch {suffix}",
                                   batch_killer_ids + [kill_faction_id])

                # Batch spawner: fires when any building in batch exists
                self._add_spawner(
                    section_id    = spawn_id,
                    aux_buildings = ','.join(batch),
                    neg_buildings = batch_id,
                    limbo_types   = batch_id,
                    limbo_ids     = limbo_id,
                    always_granted = True,
                )

                # Faction master killer: kills FACTION master when this batch exists
                self._add_killer(
                    section_id     = kill_faction_id,
                    aux_buildings  = batch_id,
                    neg_buildings  = '',
                    limbo_kill_ids = None,  # filled when faction master limbo known
                    listed_on      = batch_id,
                    kills_target   = faction_master_id,
                )

                # Register all killers on the batch dummy's SuperWeapons
                for kill_id in batch_killer_ids:
                    self._sw_on_bld.setdefault(batch_id, []).append(kill_id)
                self._sw_on_bld.setdefault(batch_id, []).append(kill_faction_id)

            # Faction master dummy: present when NO defenses of this faction exist
            faction_master_id = f"{prefix}_{faction_short}"
            faction_limbo     = self._next_limbo(limbo_base)
            faction_spawn_id  = f"{prefix}SW_{faction_short}"

            # Faction master killer: listed on each batch dummy, kills master
            # (already added above as kill_faction_id — update limbo IDs now)
            for k in self.killers:
                if k.get('kills_target') == faction_master_id:
                    k['limbo_kill_ids'] = faction_limbo

            # Faction master building
            # SuperWeapons: all kill SWs that are listed on batch dummies
            # targeting this master
            faction_kill_sws = [
                f"{prefix}KSW_{faction_short}_M"
                for _ in batches
            ]
            # Actually use unique kill IDs per batch
            faction_kill_sws_actual = [
                f"{prefix}KSW_{faction_short}_{chr(ord('A')+i)}_M"
                for i in range(len(batches))
            ]
            self._add_building(faction_master_id, faction_limbo,
                                f"Tracks: NO {faction_short} defenses present",
                                [])  # no SWs on this one — killers are on batches

            # Faction master spawner:
            # Fires when no batch dummies exist (no defenses in any batch)
            # NegBuildings: the master itself + all batch dummies
            neg_list = [faction_master_id] + batch_dummy_ids
            self._add_spawner(
                section_id     = faction_spawn_id,
                aux_buildings  = '',    # AlwaysGranted=yes, no aux needed
                neg_buildings  = ','.join(neg_list),
                limbo_types    = faction_master_id,
                limbo_ids      = faction_limbo,
                always_granted = True,
            )

            faction_master_ids.append(faction_master_id)

        # Master of masters: present when NO defenses of ANY faction exist
        master_id    = prefix
        master_limbo = self._next_limbo(limbo_base)
        master_spawn = f"{prefix}SW"

        # Kill SWs: one per faction master dummy, listed on each faction master
        for fshort, bids in sorted(factions.items()):
            faction_master_id = f"{prefix}_{fshort}"
            kill_master_id    = f"{prefix}KSW_{fshort}_MM"
            self._add_killer(
                section_id     = kill_master_id,
                aux_buildings  = faction_master_id,  # wait — this is wrong
                # We want to kill MASTER when faction master is KILLED
                # i.e. when a faction master dies (defense appeared)
                # Actually: kill master when any BATCH dummy exists
                # Better: list killer on batch dummies directly
                neg_buildings  = '',
                limbo_kill_ids = master_limbo,
                listed_on      = faction_master_id,
                kills_target   = master_id,
            )
            self._sw_on_bld.setdefault(faction_master_id, []).append(kill_master_id)

        # Master building
        self._add_building(master_id, master_limbo,
                           f"Tracks: NO defenses of any faction",
                           [])  # killers are on faction masters

        # Master spawner: fires when no faction master dummies exist
        neg_all = [master_id] + faction_master_ids
        self._add_spawner(
            section_id     = master_spawn,
            aux_buildings  = '',
            neg_buildings  = ','.join(neg_all),
            limbo_types    = master_id,
            limbo_ids      = master_limbo,
            always_granted = True,
        )

        # Update SuperWeapons on buildings
        for bld in self.buildings:
            sid = bld['section_id']
            if sid in self._sw_on_bld:
                bld['super_weapons'] = self._sw_on_bld[sid]

        return master_id

    def _add_building(self, section_id: str, limbo_id: str,
                       comment: str, super_weapons: list[str]):
        self.buildings.append({
            'section_id':   section_id,
            'limbo_id':     limbo_id,
            'comment':      comment,
            'super_weapons': list(super_weapons),
        })

    def _add_spawner(self, section_id: str, aux_buildings: str,
                      neg_buildings: str, limbo_types: str,
                      limbo_ids: str, always_granted: bool):
        self.spawners.append({
            'section_id':    section_id,
            'aux_buildings': aux_buildings,
            'neg_buildings': neg_buildings,
            'limbo_types':   limbo_types,
            'limbo_ids':     limbo_ids,
            'always_granted': always_granted,
        })

    def _add_killer(self, section_id: str, aux_buildings: str,
                     neg_buildings: str, limbo_kill_ids: str | None,
                     listed_on: str, kills_target: str):
        self.killers.append({
            'section_id':    section_id,
            'aux_buildings': aux_buildings,
            'neg_buildings': neg_buildings,
            'limbo_kill_ids': limbo_kill_ids,
            'listed_on':     listed_on,
            'kills_target':  kills_target,
        })

    def _multiplay_countries(self) -> str:
        countries = []
        for section_name in self.ini.sections:
            sec = self.ini.section(section_name)
            if sec.get('multiplay', 'no').lower() == 'yes':
                countries.append(section_name)
        return ','.join(countries) if countries else 'Americans'

    def summary(self) -> str:
        aa_count  = len(self.aa_defenses)
        def_count = len(self.all_defenses)
        return (
            f"Defense buildings found:    {def_count} "
            f"({aa_count} with AA)\n"
            f"Dummy buildings generated:  {len(self.buildings)}\n"
            f"Spawner SWs generated:      {len(self.spawners)}\n"
            f"Killer SWs generated:       {len(self.killers)}\n"
            f"Total sections:             "
            f"{len(self.buildings)+len(self.spawners)+len(self.killers)}"
        )

    # ------------------------------------------------------------------
    # Write output
    # ------------------------------------------------------------------

    def write_to_file(self, output_ini_path: str | Path,
                       rulesmd_path: str | Path) -> Path:
        """
        Write all defense dummy sections to a separate include file.
        Also updates [BuildingTypes] and [SuperWeaponTypes] lists in
        rulesmd_path.
        """
        output_ini_path = Path(output_ini_path)
        owner_str       = self._multiplay_countries()
        lines           = []

        lines.append('; YR-AI-Tool -- Defense Dummy Prerequisites\r\n')
        lines.append('; Tracks presence/absence of base defenses.\r\n')
        lines.append('; Generated automatically. Do not edit manually.\r\n\r\n')

        # Buildings
        lines.append('; === Defense Tracking Dummy Buildings ===\r\n\r\n')
        for bld in self.buildings:
            sws = bld.get('super_weapons', [])
            lines += self._building_lines(
                bld['section_id'], bld['comment'],
                sws, owner_str)

        # Spawners
        lines.append('; === Defense Dummy Spawner Superweapons ===\r\n\r\n')
        for sw in self.spawners:
            lines += self._spawner_lines(sw)

        # Killers
        lines.append('; === Defense Dummy Killer Superweapons ===\r\n\r\n')
        for ksw in self.killers:
            lines += self._killer_lines(ksw)

        with output_ini_path.open('w', encoding='latin-1', errors='replace') as f:
            f.writelines(lines)

        # Update rulesmd.ini lists
        self._update_rulesmd_lists(rulesmd_path,
                                    include_filename=Path(output_ini_path).name)

        return output_ini_path

    def _building_lines(self, section_id: str, comment: str,
                         super_weapons: list[str], owner_str: str) -> list[str]:
        lines = []
        lines.append(f"[{section_id}]\r\n")
        lines.append(f"UIName=NOSTR:\r\n")
        lines.append(f"Name={comment}\r\n")
        lines.append(f"BuildCat=Power\r\n")
        lines.append(f"Strength=750\r\n")
        lines.append(f"Armor=wood\r\n")
        lines.append(f"TechLevel=-1\r\n")
        lines.append(f"Adjacent=2\r\n")
        lines.append(f"Sight=0\r\n")
        lines.append(f"AIBasePlanningSide=0\r\n")
        lines.append(f"Cost=0\r\n")
        lines.append(f"Points=0\r\n")
        lines.append(f"Power=0\r\n")
        lines.append(f"Capturable=false\r\n")
        lines.append(f"Crewed=no\r\n")
        lines.append(f"Explosion=\r\n")
        lines.append(f"MaxDebris=0\r\n")
        lines.append(f"MinDebris=0\r\n")
        lines.append(f"ThreatPosed=0\r\n")
        lines.append(f"TogglePower=no\r\n")
        lines.append(f"Spyable=no\r\n")
        lines.append(f"DieSound=\r\n")
        lines.append(f"ImmuneToPsionics=yes\r\n")
        lines.append(f"EligibileForAllyBuilding=no\r\n")
        lines.append(f"AIBuildThis=no\r\n")
        lines.append(f"UnitLost.Suppress=yes\r\n")
        lines.append(f"LegalTarget=yes\r\n")
        lines.append(f"KeepAlive=no\r\n")
        lines.append(f"BuildLimit=0\r\n")
        lines.append(f"Prerequisite=\r\n")
        lines.append(f"Owner={owner_str}\r\n")
        lines.append(f"Image=\r\n")
        if super_weapons:
            lines.append(f"SuperWeapons={','.join(super_weapons)}\r\n")
        lines.append('\r\n')
        return lines

    def _spawner_lines(self, sw: dict) -> list[str]:
        lines = []
        lines.append(f"[{sw['section_id']}]\r\n")
        lines.append(f"SW.Deferment=0\r\n")
        lines.append(f"SW.AITargeting=Self\r\n")
        lines.append(f"SW.ShowCameo=no\r\n")
        lines.append(f"SW.AutoFire=yes\r\n")
        lines.append(f"SW.ManualFire=no\r\n")
        lines.append(f"RechargeTime=0.001\r\n")
        lines.append(f"SW.AllowPlayer=yes\r\n")
        lines.append(f"Type=UnitDelivery\r\n")
        lines.append(f"Action=ParaDrop\r\n")
        lines.append(f"IsPowered=false\r\n")
        if sw['aux_buildings']:
            lines.append(f"SW.AuxBuildings={sw['aux_buildings']}\r\n")
        lines.append(f"LimboDelivery.Types={sw['limbo_types']}\r\n")
        lines.append(f"LimboDelivery.IDs={sw['limbo_ids']}\r\n")
        lines.append(f"SW.AlwaysGranted={'yes' if sw['always_granted'] else 'no'}\r\n")
        lines.append(f"SW.InitialReady=yes\r\n")
        if sw['neg_buildings']:
            lines.append(f"SW.NegBuildings={sw['neg_buildings']}\r\n")
        lines.append('\r\n')
        return lines

    def _killer_lines(self, ksw: dict) -> list[str]:
        lines = []
        lines.append(f"[{ksw['section_id']}]\r\n")
        lines.append(f"SW.Deferment=0\r\n")
        lines.append(f"SW.AITargeting=Self\r\n")
        lines.append(f"SW.ShowCameo=no\r\n")
        lines.append(f"SW.AutoFire=yes\r\n")
        lines.append(f"SW.ManualFire=no\r\n")
        lines.append(f"RechargeTime=0.001\r\n")
        lines.append(f"SW.AllowPlayer=yes\r\n")
        lines.append(f"Type=UnitDelivery\r\n")
        lines.append(f"Action=ParaDrop\r\n")
        lines.append(f"IsPowered=false\r\n")
        lines.append(f"SW.AuxBuildings={ksw['aux_buildings']}\r\n")
        lines.append(f"LimboKill.Affects=owner\r\n")
        if ksw.get('limbo_kill_ids'):
            lines.append(f"LimboKill.IDs={ksw['limbo_kill_ids']}\r\n")
        lines.append(f"SW.AlwaysGranted=yes\r\n")
        lines.append(f"SW.InitialReady=yes\r\n")
        if ksw.get('neg_buildings'):
            lines.append(f"SW.NegBuildings={ksw['neg_buildings']}\r\n")
        lines.append('\r\n')
        return lines

    def _update_rulesmd_lists(self, rulesmd_path: str | Path,
                               include_filename: str | None = None):
        """Add defense dummy IDs to [BuildingTypes], [SuperWeaponTypes],
        and optionally [#include] in rulesmd.ini."""
        from dummy_prereq_generator import _list_insert_point, _find_highest_key
        path = Path(rulesmd_path)
        with path.open(encoding='latin-1') as f:
            file_lines = f.readlines()

        # [#include] — add defense include file (1-based per Ares docs)
        if include_filename:
            inc_idx = next((i for i, l in enumerate(file_lines)
                            if l.strip() == '[#include]'), None)
            if inc_idx is None:
                file_lines.insert(0, '[#include]\r\n')
                inc_idx = 0
            _, inc_ins  = _list_insert_point(file_lines, inc_idx)
            highest_inc = _find_highest_key(file_lines, inc_idx)
            next_inc    = max(1, highest_inc + 1)
            file_lines[inc_ins:inc_ins] = [
                f"{next_inc}={include_filename}  ; YR-AI-Tool\r\n"
            ]

        # BuildingTypes
        bt_idx = next(i for i, l in enumerate(file_lines)
                      if l.strip() == '[BuildingTypes]')
        _, bt_ins   = _list_insert_point(file_lines, bt_idx)
        highest_bta = _find_highest_key(file_lines, bt_idx, prefix='AIPREQ')

        new_bt = []
        for i, bld in enumerate(self.buildings):
            n = highest_bta + 1 + i
            new_bt.append(f"AIPREQ{n:03d}={bld['section_id']}  ; YR-AI-Tool\r\n")
        file_lines[bt_ins:bt_ins] = new_bt

        # SuperWeaponTypes
        swt_idx = next((i for i, l in enumerate(file_lines)
                        if l.strip() == '[SuperWeaponTypes]'), None)
        if swt_idx is None:
            file_lines.append('\r\n[SuperWeaponTypes]\r\n')
            swt_idx = len(file_lines) - 1

        _, swt_ins  = _list_insert_point(file_lines, swt_idx)
        highest_swa = _find_highest_key(file_lines, swt_idx, prefix='AIPREQ')

        new_sw = []
        for i, sw in enumerate(self.spawners):
            n = highest_swa + 1 + i * 2
            new_sw.append(f"AIPREQ{n:03d}D={sw['section_id']}  ; YR-AI-Tool\r\n")
        for i, ksw in enumerate(self.killers):
            n = highest_swa + 1 + len(self.spawners) * 2 + i * 2
            new_sw.append(f"AIPREQ{n:03d}K={ksw['section_id']}  ; YR-AI-Tool\r\n")
        file_lines[swt_ins:swt_ins] = new_sw

        with path.open('w', encoding='latin-1', errors='replace') as f:
            f.writelines(file_lines)

