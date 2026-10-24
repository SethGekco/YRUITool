"""
dummy_prereq_generator.py
=========================
Generates dummy prerequisite buildings and their associated superweapons
for YR / YR-mod rulesmd.ini files.

== PURPOSE ==
AITriggerTypes can only specify one building as a condition. This system
allows the AI to track whether MULTIPLE buildings exist simultaneously by
using invisible limbo-delivered dummy buildings as proxies.

== SYSTEM OVERVIEW ==
For each target building B at BuildingTypes index N:
  DMPR{N:03d}       — dummy building, limboed when B exists
  DMPRSW{N:03d}     — spawner SW: fires when B exists, delivers DMPR{N}
  DMPRKSW{N:03d}    — killer SW: fires when DMPR{N} exists but B does not

For each combination (A, B) at indices (i, j), i < j:
  DMPR{i}n{j}       — dummy representing "A AND B both exist"
  DMPRSW{i}n{j}     — spawner: listed on DMPR{i}, fires when DMPR{j} exists
  DMPRKSW{i}n{j}    — killer: fires when DMPR{i}n{j} exists but DMPR{i} gone
  DMPRKSW{j}n{i}    — killer: fires when DMPR{i}n{j} exists but DMPR{j} gone

For N-way combinations, the binary tree split is:
  left  = first N-1 indices (as a group)
  right = highest index (singleton DMPR)
  Spawner listed on left child, AuxBuildings = right child
  Two killers on the combo: one per child

== NAMING CONVENTIONS ==
  dmpr_id(indices)   → DMPR{a}n{b}n{c}...  (sorted, 3-digit padded)
  limbo_id(indices)  → 00019{a}0{b}0{c}... (sorted, 3-digit padded)
  sw_id(indices)     → DMPRSW{a}n{b}...
  ksw_id(combo, missing) → DMPRKSW{missing}n{rest...}

== OUTPUT ==
Sections written to rulesmd.ini:
  [BuildingTypes]     — AIPREQ000=DMPR001, etc.
  [SuperWeaponTypes]  — AIPREQ000D=DMPRSW001, AIPREQ000K=DMPRKSW001
  [DMPR###...]        — dummy building sections
  [DMPRSW###...]      — spawner superweapon sections
  [DMPRKSW###...]     — killer superweapon sections
"""

from ..engine.ini_model import guard_ini_text
from itertools import combinations
from pathlib import Path
from .rulesmd_parser import IniParser


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------

def _fmt(indices) -> list[str]:
    """Sort indices and format each as 3+ digit string."""
    return [f"{i:03d}" if i < 1000 else str(i) for i in sorted(indices)]


def dmpr_id(indices) -> str:
    return "DMPR" + "n".join(_fmt(indices))

def limbo_id(indices) -> str:
    return "00019" + "0".join(_fmt(indices))

def sw_id(indices) -> str:
    return "DMPRSW" + "n".join(_fmt(indices))

def ksw_id(combo_indices, missing_child_indices) -> str:
    """
    Kill SW ID. Missing child comes first, then the rest of the combo.
    missing_child_indices: the child that is being watched for absence
    combo_indices: the full combo this SW is listed on
    """
    missing_part = "n".join(_fmt(missing_child_indices))
    rest = sorted(set(combo_indices) -
                  set(missing_child_indices if hasattr(missing_child_indices, '__iter__')
                      else [missing_child_indices]))
    rest_part = "n".join(_fmt(rest))
    if rest_part:
        return f"DMPRKSW{missing_part}n{rest_part}"
    return f"DMPRKSW{missing_part}"


# ---------------------------------------------------------------------------
# Binary tree split for N-way combinations
# ---------------------------------------------------------------------------

def split_combo(indices: tuple) -> tuple[tuple, tuple]:
    """
    Split a sorted N-tuple into (left, right) children.
    left  = first N-1 elements (as a group)
    right = last element (singleton)
    """
    s = sorted(indices)
    if len(s) < 2:
        raise ValueError("Cannot split a singleton")
    return tuple(s[:-1]), (s[-1],)


# ---------------------------------------------------------------------------
# Building/SW entry dataclasses
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field



def _list_insert_point(lines: list[str], section_start: int) -> tuple[int, int]:
    """
    Find the correct insertion point for new list entries in an INI section.

    Returns (last_idx, insert_at) where:
      last_idx  = index of the last numbered entry in the list
      insert_at = index to insert new entries (right after last numbered entry)

    This correctly handles sections that have trailing blank lines and
    comments before the next section header — new entries go immediately
    after the last numbered key=value line, not before the comments.
    """
    import re
    numbered = re.compile(r"^\d+=|^AIPREQ\d+[DK]?=", re.IGNORECASE)
    highest_entry_line = section_start  # fallback: insert right after header

    i = section_start + 1
    while i < len(lines):
        s = lines[i].strip()
        # Stop at next section header
        if s.startswith('[') and not s.startswith('[;'):
            break
        if numbered.match(s):
            highest_entry_line = i
        i += 1

    # Insert immediately after the last numbered entry
    insert_at = highest_entry_line + 1
    return highest_entry_line, insert_at


def _find_highest_key(lines: list[str], section_start: int,
                       prefix: str = "") -> int:
    """
    Find the highest numeric key in a list section.
    If prefix is given (e.g. 'AIPREQ'), only counts keys starting with prefix.
    """
    import re
    highest = -1
    i = section_start + 1
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith('[') and not s.startswith('[;'):
            break
        if '=' in s and not s.startswith(';'):
            key = s.split('=')[0].strip()
            if prefix:
                if key.upper().startswith(prefix.upper()):
                    try:
                        n = int(re.sub(r"[^0-9]", "", key[len(prefix):]))
                        highest = max(highest, n)
                    except ValueError:
                        pass
            else:
                try:
                    highest = max(highest, int(key))
                except ValueError:
                    pass
        i += 1
    return highest


@dataclass
class DummyBuilding:
    indices:       tuple          # sorted tuple of BuildingType indices
    bld_ids:       list[str]      # corresponding building IDs
    section_id:    str            # e.g. DMPR002n007
    limbo_id_str:  str            # e.g. 000190020007
    spawn_sw:      str            # spawner SW section ID
    kill_sws:      list[str]      # all kill SW section IDs listed on this building
    # kill_sws are the SWs that watch for THIS building's children to disappear


@dataclass
class SpawnerSW:
    indices:       tuple          # combo this SW spawns
    section_id:    str            # e.g. DMPRSW002n007
    aux_buildings: str            # SW.AuxBuildings= (right child DMPR or real building)
    neg_buildings: str            # SW.NegBuildings= (the dummy being spawned)
    limbo_types:   str            # LimboDelivery.Types=
    limbo_ids:     str            # LimboDelivery.IDs=
    always_granted: bool          # True for singles and right-child spawners
    listed_on:     str            # which DMPR section lists this in SuperWeapons=


@dataclass
class KillerSW:
    combo_indices:   tuple        # the combo being killed
    missing_child:   tuple        # which child's absence triggers this kill
    section_id:      str          # e.g. DMPRKSW002n007
    aux_buildings:   str          # SW.AuxBuildings= (the combo dummy)
    neg_buildings:   str          # SW.NegBuildings= (the missing child)
    limbo_kill_ids:  str          # LimboKill.IDs=
    listed_on:       str          # which DMPR section lists this in SuperWeapons=


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class DummyPrereqGenerator:
    """
    Generates the complete dummy prerequisite system for a set of
    target buildings (factories, radars, labs, depots, refineries, CYs).

    Generates singles + all pairs + all triples (up to max_combo_size).
    """

    def __init__(self, rulesmd_path: str | Path,
                 max_combo_size: int = 3):
        self.rules_path    = Path(rulesmd_path)
        self.ini           = IniParser(str(rulesmd_path))
        self.max_combo     = max_combo_size
        self.buildings     = self.ini.list_from_section('BuildingTypes')
        self.id_to_idx     = {bid: i for i, bid in enumerate(self.buildings)}
        self.target_blds:  list[tuple[int, str]] = []   # (index, id) sorted

        self.dummy_blds:   list[DummyBuilding] = []
        self.spawner_sws:  list[SpawnerSW]     = []
        self.killer_sws:   list[KillerSW]      = []

        self._dmpr_super_weapons: dict[str, list[str]] = {}  # dmpr_id -> [sw_ids]

    # ------------------------------------------------------------------
    # Step 1: collect target buildings
    # ------------------------------------------------------------------

    def collect_targets(self,
                        extra_ids: list[str] | None = None) -> list[tuple[int,str]]:
        """
        Auto-detect key infrastructure buildings + any extras provided.
        Returns sorted (index, id) list.
        """
        candidates = set()

        for idx, bid in enumerate(self.buildings):
            sec  = self.ini.section(bid)
            tech = sec.get('techlevel', '0')
            try:
                tech_int = int(tech)
            except ValueError:
                tech_int = -1

            # Construction yards: TL=-1 but critical
            name = sec.get('name', '').lower()
            if 'construction yard' in name:
                candidates.add((idx, bid))
                continue

            # Skip TL=-1 (hidden/civilian) except CYs above
            if tech_int < 0:
                continue
            # Skip defenses
            if sec.get('isbasedefense', 'no').lower() == 'yes':
                continue
            # Skip walls
            if sec.get('wall', 'no').lower() == 'yes':
                continue
            factory = sec.get('factory', '')
            storage = sec.get('storage', '0')

            is_factory  = bool(factory)
            is_refinery = storage not in ('0', '') or 'refin' in bid.lower()
            is_lab      = any(x in name for x in ['battle', 'lab', 'tech'])
            is_radar    = any(x in name for x in ['radar', 'dome', 'dish'])
            is_service  = any(x in name for x in ['service', 'depot'])
            is_port     = any(x in name for x in ['shipyard', 'pen', 'port'])

            # Skip pure superweapon structures (nuke silos, weather, etc.)
            # but NOT battle labs or radars which may also grant a SW
            has_sw = bool(sec.get('superweapon', ''))
            is_key = any([is_factory, is_refinery, is_lab,
                          is_radar, is_service, is_port])
            if has_sw and not is_key:
                continue

            if any([is_factory, is_refinery, is_lab, is_radar,
                    is_service, is_port]):
                candidates.add((idx, bid))

        if extra_ids:
            for bid in extra_ids:
                if bid in self.id_to_idx:
                    candidates.add((self.id_to_idx[bid], bid))

        self.target_blds = sorted(candidates)
        return self.target_blds

    # ------------------------------------------------------------------
    # Step 2: build all combinations
    # ------------------------------------------------------------------

    def generate_all(self):
        """
        Generate all dummy buildings and superweapons for singles,
        pairs, and triples (up to max_combo_size).
        """
        if not self.target_blds:
            self.collect_targets()

        indices_list = [idx for idx, _ in self.target_blds]

        for size in range(1, self.max_combo + 1):
            for combo in combinations(indices_list, size):
                self._generate_combo(combo)

    def _bld_ids_for(self, indices) -> list[str]:
        return [self.buildings[i] for i in sorted(indices)
                if i < len(self.buildings)]

    def _generate_combo(self, combo: tuple):
        """Generate all entries for one combination."""
        combo    = tuple(sorted(combo))
        bld_ids  = self._bld_ids_for(combo)
        dmpr     = dmpr_id(combo)
        lim_id   = limbo_id(combo)
        spawn_sw = sw_id(combo)

        # Determine kill SWs for this combo (one per child)
        kill_sw_ids = []
        if len(combo) == 1:
            # Single: one kill SW watching the real building
            ksw = ksw_id(combo, combo)
            kill_sw_ids = [ksw]
        else:
            # N-way: two kill SWs watching left and right children
            left, right = split_combo(combo)
            ksw_left  = ksw_id(combo, left)   # fires when left child gone
            ksw_right = ksw_id(combo, right)   # fires when right child gone
            kill_sw_ids = [ksw_left, ksw_right]

        # Register dummy building
        db = DummyBuilding(
            indices      = combo,
            bld_ids      = bld_ids,
            section_id   = dmpr,
            limbo_id_str = lim_id,
            spawn_sw     = spawn_sw,
            kill_sws     = kill_sw_ids,
        )
        self.dummy_blds.append(db)
        self._dmpr_super_weapons[dmpr] = kill_sw_ids[:]

        # Spawner SW
        if len(combo) == 1:
            # Single: AuxBuildings = real building, NegBuildings = dummy
            real_bld  = self.buildings[combo[0]]
            spawner   = SpawnerSW(
                indices       = combo,
                section_id    = spawn_sw,
                aux_buildings = real_bld,
                neg_buildings = dmpr,
                limbo_types   = dmpr,
                limbo_ids     = lim_id,
                always_granted= True,   # no parent DMPR needed
                listed_on     = "",     # added to BuildingTypes superweapons externally
            )
        else:
            # N-way: listed on left child DMPR, AuxBuildings = right child DMPR
            left, right = split_combo(combo)
            left_dmpr   = dmpr_id(left)
            right_dmpr  = dmpr_id(right)
            spawner     = SpawnerSW(
                indices       = combo,
                section_id    = spawn_sw,
                aux_buildings = right_dmpr,
                neg_buildings = dmpr,
                limbo_types   = dmpr,
                limbo_ids     = lim_id,
                always_granted= False,  # listed on left DMPR → implicitly requires it
                listed_on     = left_dmpr,
            )
            # Add this spawner to the left child's SuperWeapons list
            if left_dmpr not in self._dmpr_super_weapons:
                self._dmpr_super_weapons[left_dmpr] = []
            self._dmpr_super_weapons[left_dmpr].append(spawn_sw)

        self.spawner_sws.append(spawner)

        # Killer SWs
        if len(combo) == 1:
            # Single killer: watches for real building absence
            real_bld = self.buildings[combo[0]]
            ksw_sec  = kill_sw_ids[0]
            killer   = KillerSW(
                combo_indices  = combo,
                missing_child  = combo,
                section_id     = ksw_sec,
                aux_buildings  = dmpr,
                neg_buildings  = real_bld,
                limbo_kill_ids = lim_id,
                listed_on      = dmpr,
            )
            self.killer_sws.append(killer)
        else:
            left, right = split_combo(combo)
            # Killer watching left child
            ksw_left_sec = ksw_id(combo, left)
            self.killer_sws.append(KillerSW(
                combo_indices  = combo,
                missing_child  = left,
                section_id     = ksw_left_sec,
                aux_buildings  = dmpr,
                neg_buildings  = dmpr_id(left),
                limbo_kill_ids = lim_id,
                listed_on      = dmpr,
            ))
            # Killer watching right child
            ksw_right_sec = ksw_id(combo, right)
            self.killer_sws.append(KillerSW(
                combo_indices  = combo,
                missing_child  = right,
                section_id     = ksw_right_sec,
                aux_buildings  = dmpr,
                neg_buildings  = dmpr_id(right),
                limbo_kill_ids = lim_id,
                listed_on      = dmpr,
            ))

    # ------------------------------------------------------------------
    # Step 3: write to rulesmd.ini
    # ------------------------------------------------------------------

    def _multiplay_countries(self) -> str:
        """Get all Multiplay=yes countries for Owner= field."""
        countries = []
        for section_name in self.ini.sections:
            sec = self.ini.section(section_name)
            if sec.get('multiplay', 'no').lower() == 'yes':
                countries.append(section_name)
        return ','.join(countries) if countries else 'Americans'

    def write_to_rules(self,
                       rulesmd_path: str | Path,
                       output_path:  str | Path | None = None) -> Path:
        """
        Append all dummy buildings and superweapons to rulesmd.ini.
        Updates [BuildingTypes] and [SuperWeaponTypes] lists.
        """
        rulesmd_path = Path(rulesmd_path)
        output_path  = Path(output_path) if output_path else rulesmd_path

        with rulesmd_path.open(encoding='latin-1') as f:
            lines = f.readlines()

        owner_str = self._multiplay_countries()

        # ------------------------------------------------------------------
        # 1. Update [BuildingTypes] list
        # ------------------------------------------------------------------
        bt_idx = next(i for i, l in enumerate(lines)
                      if l.strip() == '[BuildingTypes]')
        _, bt_insert = _list_insert_point(lines, bt_idx)
        highest_aipreq = _find_highest_key(lines, bt_idx, prefix='AIPREQ')

        new_bt_entries = []
        for i, db in enumerate(self.dummy_blds):
            n = highest_aipreq + 1 + i
            new_bt_entries.append(
                f"AIPREQ{n:03d}={db.section_id}  ; YR-AI-Tool\r\n")
        lines[bt_insert:bt_insert] = new_bt_entries

        # ------------------------------------------------------------------
        # 2. Update [SuperWeaponTypes] list
        # ------------------------------------------------------------------
        swt_idx = next((i for i, l in enumerate(lines)
                        if l.strip() == '[SuperWeaponTypes]'), None)
        if swt_idx is None:
            lines.append('\r\n[SuperWeaponTypes]\r\n')
            swt_idx = len(lines) - 1

        _, swt_insert = _list_insert_point(lines, swt_idx)
        highest_sw_aipreq = _find_highest_key(lines, swt_idx, prefix='AIPREQ')

        new_sw_entries = []
        for i, sw in enumerate(self.spawner_sws):
            n = highest_sw_aipreq + 1 + i * 2
            new_sw_entries.append(
                f"AIPREQ{n:03d}D={sw.section_id}  ; YR-AI-Tool\r\n")
        for i, ksw in enumerate(self.killer_sws):
            n = highest_sw_aipreq + 1 + len(self.spawner_sws) * 2 + i * 2
            new_sw_entries.append(
                f"AIPREQ{n:03d}K={ksw.section_id}  ; YR-AI-Tool\r\n")
        lines[swt_insert:swt_insert] = new_sw_entries

        # ------------------------------------------------------------------
        # 3. Append dummy building sections
        # ------------------------------------------------------------------
        # Remove trailing semicolon
        while lines and lines[-1].strip() in (';', ''):
            lines.pop()

        lines.append('\r\n; === YR-AI-Tool Dummy Prerequisite Buildings ===\r\n\r\n')

        for db in self.dummy_blds:
            bld_names = ', '.join(
                self.ini.section(bid).get('name', bid)
                for bid in db.bld_ids)
            all_sws = self._dmpr_super_weapons.get(db.section_id, [])
            bld_comment = ', '.join(
                self.ini.section(bid).get('name', bid) or bid
                for bid in db.bld_ids)
            lines.append(f"[{db.section_id}]  ; w/ {bld_comment}\r\n")
            lines.append(f"UIName=NOSTR:\r\n")
            lines.append(f"Name=DUMMY PREREQUISITE: {','.join(db.bld_ids)}\r\n")
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
            lines.append(f"BuildLimit=0\r\n")
            lines.append(f"Prerequisite=\r\n")
            lines.append(f"Owner={owner_str}\r\n")
            lines.append(f"Image=\r\n")
            if all_sws:
                lines.append(f"SuperWeapons={','.join(all_sws)}\r\n")
            lines.append(f"; Combo: {bld_names}\r\n")
            lines.append('\r\n')

        # ------------------------------------------------------------------
        # 4. Append spawner SW sections
        # ------------------------------------------------------------------
        lines.append('; === YR-AI-Tool Dummy Prerequisite Spawner Superweapons ===\r\n\r\n')

        for sw in self.spawner_sws:
            bld_cmt = self._bld_comment(self._bld_ids_for(sw.indices))
            lines.append(f"[{sw.section_id}]  ; spawns: {bld_cmt}\r\n")
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
            lines.append(f"SW.AuxBuildings={sw.aux_buildings}\r\n")
            lines.append(f"LimboDelivery.Types={sw.limbo_types}\r\n")
            lines.append(f"LimboDelivery.IDs={sw.limbo_ids}\r\n")
            lines.append(f"SW.AlwaysGranted={'yes' if sw.always_granted else 'no'}\r\n")
            lines.append(f"SW.InitialReady=yes\r\n")
            lines.append(f"SW.NegBuildings={sw.neg_buildings}\r\n")
            lines.append('\r\n')

        # ------------------------------------------------------------------
        # 5. Append killer SW sections
        # ------------------------------------------------------------------
        lines.append('; === YR-AI-Tool Dummy Prerequisite Killer Superweapons ===\r\n\r\n')

        for ksw in self.killer_sws:
            bld_cmt = self._bld_comment(self._bld_ids_for(ksw.combo_indices))
            lines.append(f"[{ksw.section_id}]  ; kills: {bld_cmt}\r\n")
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
            lines.append(f"SW.AuxBuildings={ksw.aux_buildings}\r\n")
            lines.append(f"LimboKill.Affects=owner\r\n")
            lines.append(f"LimboKill.IDs={ksw.limbo_kill_ids}\r\n")
            lines.append(f"SW.AlwaysGranted=yes\r\n")
            lines.append(f"SW.InitialReady=yes\r\n")
            lines.append(f"SW.NegBuildings={ksw.neg_buildings}\r\n")
            lines.append('\r\n')

        lines.append(';\r\n')

        with output_path.open('w', encoding='latin-1', errors='replace') as f:
            f.write(guard_ini_text(lines))

        return output_path

    def summary(self) -> str:
        return (f"Dummy prerequisite system:\n"
                f"  Target buildings:  {len(self.target_blds)}\n"
                f"  Dummy buildings:   {len(self.dummy_blds)}\n"
                f"  Spawner SWs:       {len(self.spawner_sws)}\n"
                f"  Killer SWs:        {len(self.killer_sws)}\n"
                f"  Total sections:    "
                f"{len(self.dummy_blds)+len(self.spawner_sws)+len(self.killer_sws)}")


# ---------------------------------------------------------------------------
# Phase 1 Extension: Absence Dummies + Mixed + User Mode + Ares #include
# ---------------------------------------------------------------------------

# Absence dummy prefix/limbo prefix
ABSENCE_PREFIX   = "DMPRX"
ABSENCE_SW_PFX   = "DMPRSWX"   # spawner for absence
ABSENCE_KSW_PFX  = "DMPRKSWX"  # killer for absence
ABSENCE_LIMBO_PFX = "00029"     # vs 00019 for presence


def _fmt_x(indices) -> list[str]:
    """Format absence indices for section IDs — each gets an X prefix."""
    return [f"X{i:03d}" if i < 1000 else f"X{i}" for i in sorted(indices)]


def _fmt_9(indices) -> list[str]:
    """Format absence indices for limbo IDs — each gets a 9 prefix (X→9)."""
    return [f"9{i:03d}" if i < 1000 else f"9{i}" for i in sorted(indices)]


def dmprx_id(indices) -> str:
    """Absence dummy ID: DMPRX{a}nX{b}nX{c}...
    Every index gets an X prefix so DMPRX357nX358 clearly means
    both 357 AND 358 are absent. Single: DMPRX002.
    """
    parts = _fmt_x(indices)
    return "DMPR" + "n".join(parts)

def limbo_x_id(indices) -> str:
    """Absence dummy limbo ID: 00029{9a}0{9b}...
    Uses 9 as the substitute for X (parallel to 0 substituting n).
    Single: 000299002  Pair: 000299006090070
    """
    return ABSENCE_LIMBO_PFX + "0".join(_fmt_9(indices))

def sw_x_id(indices) -> str:
    """Absence spawner SW ID: DMPRSWX{a}nX{b}nX{c}..."""
    parts = _fmt_x(indices)
    return "DMPRSW" + "n".join(parts)

def ksw_x_id(combo_indices, missing_child_indices) -> str:
    """Absence killer SW ID: DMPRKSWX{missing}nX{rest}...
    Missing child indices come first (all X-prefixed), then remaining
    combo indices (also X-prefixed since this is an absence killer).
    """
    if not hasattr(missing_child_indices, '__iter__'):
        missing_child_indices = (missing_child_indices,)
    missing_set  = set(missing_child_indices)
    rest         = sorted(set(combo_indices) - missing_set)
    missing_part = "n".join(_fmt_x(sorted(missing_child_indices)))
    rest_part    = "n".join(_fmt_x(rest))
    if rest_part:
        return f"DMPRKSW{missing_part}n{rest_part}"
    return f"DMPRKSW{missing_part}"


@dataclass
class AbsenceDummyBuilding:
    """Tracks when a set of buildings are ALL ABSENT."""
    indices:       tuple
    bld_ids:       list[str]
    section_id:    str    # DMPRX{...}
    limbo_id_str:  str    # 00029{...}
    spawn_sw:      str    # SW that delivers this when buildings missing
    kill_sws:      list[str]  # SWs that kill this when buildings reappear


@dataclass
class AbsenceSpawnerSW:
    """Fires when buildings are absent, delivers absence dummy."""
    indices:        tuple
    section_id:     str
    neg_buildings:  str   # real buildings + the dummy itself
    limbo_types:    str
    limbo_ids:      str
    always_granted: bool
    listed_on:      str   # for N-way: listed on left child absence dummy


@dataclass
class AbsenceKillerSW:
    """Fires when absence dummy exists but the building has reappeared."""
    combo_indices:   tuple
    missing_child:   tuple
    section_id:      str
    aux_buildings:   str    # needs absence dummy to exist
    neg_buildings:   str    # empty — fires when building reappears
    limbo_kill_ids:  str
    patch_on_bld:    str    # real building ID to add to SuperWeapons=
    listed_on:       str    # which DMPRX section lists this


# ---------------------------------------------------------------------------
# Extended Generator with all modes
# ---------------------------------------------------------------------------

class FullDummyPrereqGenerator(DummyPrereqGenerator):
    """
    Extends DummyPrereqGenerator with:
      - Absence dummies (DMPRX###)
      - Mixed presence+absence combos
      - User mode selection
      - Ares #include file split
      - Patching real building SuperWeapons=

    Modes:
      0 = none
      1 = presence only
      2 = absence only
      3 = both, no mixed
      4 = full mixed (presence + absence + presence/absence combos)

    Mixed scope (only relevant when mode=4):
      'none'      = no mixed combos (same as mode 3)
      'auto'      = only generate mixed combos explicitly requested via
                    request_mixed_combo() — used by trigger generator
      'selected'  = generate mixed combos for user-selected buildings only
      'all'       = all possible mixed combinations (potentially enormous)

    selected_for_mixed: list of building IDs that should get mixed treatment.
      When mixed_scope='selected', only combos involving these buildings
      are generated as mixed. Others get presence/absence only.
    """

    # Mixed dummy limbo prefix (distinct from 00019 presence, 00029 absence)
    MIXED_LIMBO_PFX = "00039"

    def __init__(self, rulesmd_path: str | Path,
                 max_combo_size:     int  = 3,
                 mode:               int  = 1,
                 use_ares_include:   bool = True,
                 mixed_scope:        str  = 'auto',
                 selected_for_mixed: list[str] | None = None):
        super().__init__(rulesmd_path, max_combo_size)
        self.mode              = mode
        self.use_ares_include  = use_ares_include
        self.mixed_scope       = mixed_scope          # 'none','auto','selected','all'
        self.selected_for_mixed = set(selected_for_mixed or [])
        self.absence_dummies:  list[AbsenceDummyBuilding]  = []
        self.absence_spawners: list[AbsenceSpawnerSW]      = []
        self.absence_killers:  list[AbsenceKillerSW]       = []
        self._absence_super_weapons: dict[str, list[str]]  = {}
        self._real_bld_patches: dict[str, list[str]]       = {}
        # Mixed dummies: keyed by (presence_indices_tuple, absence_indices_tuple)
        self._mixed_dummies:   list[dict]                  = []
        self._mixed_spawners:  list[dict]                  = []
        self._mixed_killers:   list[dict]                  = []
        self._mixed_sw_map:    dict[str, list[str]]        = {}
        self._requested_mixed: set[tuple]                  = set()  # auto requests

    def generate_all(self):
        """Generate presence and/or absence dummies based on mode."""
        if not self.target_blds:
            self.collect_targets()

        if self.mode in (1, 3, 4):
            super().generate_all()  # presence dummies

        if self.mode in (2, 3, 4):
            self._generate_absence_all()

        if self.mode == 4 and self.mixed_scope != 'none':
            self._generate_mixed_all()

    # ------------------------------------------------------------------
    # Mixed combo generation
    # ------------------------------------------------------------------

    def request_mixed_combo(self, present_ids: list[str],
                             absent_ids: list[str]):
        """
        Request a specific mixed combo be generated (auto mode).
        Called by the trigger generator when it needs a specific condition.
        present_ids: building IDs that must EXIST
        absent_ids:  building IDs that must be ABSENT
        """
        p_idx = tuple(sorted(self.id_to_idx[b] for b in present_ids
                              if b in self.id_to_idx))
        a_idx = tuple(sorted(self.id_to_idx[b] for b in absent_ids
                              if b in self.id_to_idx))
        if p_idx and a_idx:
            self._requested_mixed.add((p_idx, a_idx))

    def request_absence_combo(self, absent_ids: list[str]):
        """
        Request a specific absence combo be generated.
        The resulting dummy fires when ALL listed buildings are absent.
        Used for category-level absence conditions (e.g. no CY of any faction).
        """
        if not hasattr(self, '_requested_absence'):
            self._requested_absence = set()
        a_idx = tuple(sorted(self.id_to_idx[b] for b in absent_ids
                              if b in self.id_to_idx))
        if a_idx:
            self._requested_absence.add(a_idx)
            # Ensure base presence/absence dummies exist for these
            # (they'll be generated when generate_all is called)

    def _generate_mixed_all(self):
        """Generate mixed presence+absence combos based on mixed_scope."""
        from itertools import combinations, product

        indices = [idx for idx, _ in self.target_blds]

        if self.mixed_scope == 'auto':
            # Only generate explicitly requested combos
            for p_idx, a_idx in self._requested_mixed:
                self._generate_mixed_combo(p_idx, a_idx)

        elif self.mixed_scope == 'selected':
            # Generate mixed combos where at least one building is in selected set
            selected_indices = {self.id_to_idx[b] for b in self.selected_for_mixed
                                 if b in self.id_to_idx}
            for size in range(2, self.max_combo + 1):
                # All ways to split 'size' buildings into present+absent groups
                for combo in combinations(indices, size):
                    # At least one must be selected
                    if not any(i in selected_indices for i in combo):
                        continue
                    # Generate all splits: at least 1 present, at least 1 absent
                    for n_present in range(1, size):
                        for p_part in combinations(combo, n_present):
                            a_part = tuple(i for i in combo if i not in p_part)
                            self._generate_mixed_combo(
                                tuple(sorted(p_part)),
                                tuple(sorted(a_part)))

        elif self.mixed_scope == 'all':
            # Every possible mixed combination — potentially enormous
            for size in range(2, self.max_combo + 1):
                for combo in combinations(indices, size):
                    for n_present in range(1, size):
                        for p_part in combinations(combo, n_present):
                            a_part = tuple(i for i in combo if i not in p_part)
                            self._generate_mixed_combo(
                                tuple(sorted(p_part)),
                                tuple(sorted(a_part)))

    def _mixed_section_id(self, present_idx: tuple, absent_idx: tuple) -> str:
        """
        Build the section ID for a mixed dummy.
        Format: DMPR{p0}n{p1}...nX{a0}nX{a1}...
        All presence indices first (sorted), then absence indices (sorted, X-prefixed).
        Example: DMPR007nX005 = GAWEAP present, GADEPT absent
        """
        p_parts = [f"{i:03d}" for i in sorted(present_idx)]
        a_parts  = [f"X{i:03d}" for i in sorted(absent_idx)]
        return "DMPR" + "n".join(p_parts + a_parts)

    def _mixed_limbo_id(self, present_idx: tuple, absent_idx: tuple) -> str:
        """
        Limbo ID for a mixed dummy.
        Format: 00039 + 0-joined present indices + 0 + 0-joined 9-prefixed absent indices.
        Uses 9 as the substitute for X (parallel to 0 substituting n).
        Example: GAWEAP(7) present + GADEPT(5) absent → 0003900709005
        """
        p_parts = _fmt(list(present_idx))
        a_parts  = _fmt_9(list(absent_idx))
        return self.MIXED_LIMBO_PFX + "0".join(p_parts) + "0" + "0".join(a_parts)

    def _generate_mixed_combo(self, present_idx: tuple, absent_idx: tuple):
        """
        Generate a mixed dummy: requires present_idx buildings to EXIST
        and absent_idx buildings to be ABSENT simultaneously.

        Binary tree split: left = presence side, right = absence side.
        The spawner is listed on the left child (presence DMPR), uses
        SW.AuxBuildings = right child (absence DMPRX), AlwaysGranted=no.

        Killers watch each child — if presence dummy disappears OR absence
        dummy disappears (meaning building was built), kill the mixed dummy.
        """
        key = (present_idx, absent_idx)
        if key in {(tuple(m['present_idx']), tuple(m['absent_idx']))
                   for m in self._mixed_dummies}:
            return  # already generated

        section_id = self._mixed_section_id(present_idx, absent_idx)
        limbo_id_s = self._mixed_limbo_id(present_idx, absent_idx)

        # Resolve child dummies
        # Left child: presence combo (or single)
        left_id  = dmpr_id(present_idx)
        # Right child: absence combo (or single)
        right_id = dmprx_id(absent_idx)

        # Spawner SW: listed on left (presence) dummy
        spawn_sw_id = f"DMPRSW{'n'.join(_fmt(present_idx))}nX{'n'.join(_fmt(absent_idx))}"

        # Killer SWs listed on the mixed dummy itself
        # One kills when left (presence) child disappears
        ksw_left_id  = f"DMPRKSW{'n'.join(_fmt(present_idx))}nX{'n'.join(_fmt(absent_idx))}"
        # One kills when right (absence) child disappears
        ksw_right_id = f"DMPRKSWx{'n'.join(_fmt(absent_idx))}n{'n'.join(_fmt(present_idx))}"

        # Register mixed dummy
        entry = {
            'present_idx': present_idx,
            'absent_idx':  absent_idx,
            'section_id':  section_id,
            'limbo_id':    limbo_id_s,
            'spawn_sw':    spawn_sw_id,
            'kill_sws':    [ksw_left_id, ksw_right_id],
            'left_child':  left_id,
            'right_child': right_id,
        }
        self._mixed_dummies.append(entry)
        self._mixed_sw_map[section_id] = [ksw_left_id, ksw_right_id]

        # Spawner: listed on left presence dummy
        spawner = {
            'section_id':    spawn_sw_id,
            'aux_buildings': right_id,
            'neg_buildings': section_id,
            'limbo_types':   section_id,
            'limbo_ids':     limbo_id_s,
            'always_granted': False,
            'listed_on':     left_id,
            'present_idx':   present_idx,
            'absent_idx':    absent_idx,
        }
        self._mixed_spawners.append(spawner)

        # Add spawner to left presence dummy's SuperWeapons
        if left_id not in self._dmpr_super_weapons:
            self._dmpr_super_weapons[left_id] = []
        self._dmpr_super_weapons[left_id].append(spawn_sw_id)

        # Killer watching left child (presence dummy gone = building destroyed)
        self._mixed_killers.append({
            'section_id':     ksw_left_id,
            'aux_buildings':  section_id,
            'neg_buildings':  left_id,
            'limbo_kill_ids': limbo_id_s,
            'listed_on':      section_id,
            'present_idx':    present_idx,
            'absent_idx':     absent_idx,
        })

        # Killer watching right child (absence dummy gone = building was built)
        self._mixed_killers.append({
            'section_id':     ksw_right_id,
            'aux_buildings':  section_id,
            'neg_buildings':  right_id,
            'limbo_kill_ids': limbo_id_s,
            'listed_on':      section_id,
            'present_idx':    present_idx,
            'absent_idx':     absent_idx,
        })

    def _generate_absence_all(self):
        """Generate absence dummies for all combinations up to max_combo size,
        plus any explicitly requested combos (e.g. category-level all-absent)."""
        indices_list = [idx for idx, _ in self.target_blds]
        from itertools import combinations
        generated = set()
        for size in range(1, self.max_combo + 1):
            for combo in combinations(indices_list, size):
                c = tuple(sorted(combo))
                if c not in generated:
                    self._generate_absence_combo(c)
                    generated.add(c)
        # Also generate explicitly requested absence combos (any size)
        for a_idx in getattr(self, '_requested_absence', set()):
            if a_idx not in generated:
                self._generate_absence_combo(a_idx)
                generated.add(a_idx)

    def _generate_absence_combo(self, combo: tuple):
        """Generate absence dummy and its superweapons for one combination."""
        bld_ids   = self._bld_ids_for(combo)
        dmprx     = dmprx_id(combo)
        lim_id    = limbo_x_id(combo)
        spawn_sw  = sw_x_id(combo)

        # Kill SWs (same binary tree logic as presence)
        if len(combo) == 1:
            kill_sw_ids = [ksw_x_id(combo, combo)]
        else:
            left, right = split_combo(combo)
            kill_sw_ids = [ksw_x_id(combo, left), ksw_x_id(combo, right)]

        # Register absence dummy
        ab = AbsenceDummyBuilding(
            indices      = combo,
            bld_ids      = bld_ids,
            section_id   = dmprx,
            limbo_id_str = lim_id,
            spawn_sw     = spawn_sw,
            kill_sws     = kill_sw_ids,
        )
        self.absence_dummies.append(ab)
        self._absence_super_weapons[dmprx] = kill_sw_ids[:]

        # Absence spawner
        if len(combo) == 1:
            # Single: NegBuildings = real building + the absence dummy itself
            real_bld = self.buildings[combo[0]]
            neg      = f"{real_bld},{dmprx}"
            spawner  = AbsenceSpawnerSW(
                indices        = combo,
                section_id     = spawn_sw,
                neg_buildings  = neg,
                limbo_types    = dmprx,
                limbo_ids      = lim_id,
                always_granted = True,
                listed_on      = "",
            )
        else:
            left, right   = split_combo(combo)
            left_dmprx    = dmprx_id(left)
            right_dmprx   = dmprx_id(right)
            neg           = f"{right_dmprx},{dmprx}"
            spawner       = AbsenceSpawnerSW(
                indices        = combo,
                section_id     = spawn_sw,
                neg_buildings  = neg,
                limbo_types    = dmprx,
                limbo_ids      = lim_id,
                always_granted = False,  # listed on left absence dummy
                listed_on      = left_dmprx,
            )
            if left_dmprx not in self._absence_super_weapons:
                self._absence_super_weapons[left_dmprx] = []
            self._absence_super_weapons[left_dmprx].append(spawn_sw)

        self.absence_spawners.append(spawner)

        # Absence killers
        if len(combo) == 1:
            real_bld = self.buildings[combo[0]]
            ksw_sec  = kill_sw_ids[0]
            killer   = AbsenceKillerSW(
                combo_indices  = combo,
                missing_child  = combo,
                section_id     = ksw_sec,
                aux_buildings  = dmprx,
                neg_buildings  = "",     # empty: fires when building reappears
                limbo_kill_ids = lim_id,
                patch_on_bld   = real_bld,  # add to real building SuperWeapons=
                listed_on      = dmprx,
            )
            self.absence_killers.append(killer)
            # Schedule patch on real building
            if real_bld not in self._real_bld_patches:
                self._real_bld_patches[real_bld] = []
            self._real_bld_patches[real_bld].append(ksw_sec)
        else:
            left, right = split_combo(combo)
            # Killer watching left child (absence dummy)
            ksw_l = ksw_x_id(combo, left)
            self.absence_killers.append(AbsenceKillerSW(
                combo_indices  = combo,
                missing_child  = left,
                section_id     = ksw_l,
                aux_buildings  = dmprx,
                neg_buildings  = dmprx_id(left),  # neg = left child
                limbo_kill_ids = lim_id,
                patch_on_bld   = "",
                listed_on      = dmprx,
            ))
            # Killer watching right child
            ksw_r = ksw_x_id(combo, right)
            self.absence_killers.append(AbsenceKillerSW(
                combo_indices  = combo,
                missing_child  = right,
                section_id     = ksw_r,
                aux_buildings  = dmprx,
                neg_buildings  = dmprx_id(right),  # neg = right child
                limbo_kill_ids = lim_id,
                patch_on_bld   = "",
                listed_on      = dmprx,
            ))

    def full_summary(self) -> str:
        total = (len(self.dummy_blds) + len(self.spawner_sws) +
                 len(self.killer_sws) + len(self.absence_dummies) +
                 len(self.absence_spawners) + len(self.absence_killers) +
                 len(self._mixed_dummies) + len(self._mixed_spawners) +
                 len(self._mixed_killers))
        lines = [
            f"Mode: {self.mode} | Mixed scope: {self.mixed_scope} "
            f"| Ares include: {self.use_ares_include}",
            f"Presence dummies:  {len(self.dummy_blds)}",
            f"Presence spawners: {len(self.spawner_sws)}",
            f"Presence killers:  {len(self.killer_sws)}",
            f"Absence dummies:   {len(self.absence_dummies)}",
            f"Absence spawners:  {len(self.absence_spawners)}",
            f"Absence killers:   {len(self.absence_killers)}",
            f"Mixed dummies:     {len(self._mixed_dummies)}",
            f"Mixed spawners:    {len(self._mixed_spawners)}",
            f"Mixed killers:     {len(self._mixed_killers)}",
            f"Real bld patches:  {len(self._real_bld_patches)}",
            f"Total sections:    {total}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Write with Ares #include support
    # ------------------------------------------------------------------

    def write_all(self,
                  rulesmd_path: str | Path,
                  output_path:  str | Path | None = None,
                  output_dir:   str | Path | None = None) -> dict[str, Path]:
        """
        Write everything. Returns dict of written file paths.

        rulesmd_path: source rulesmd.ini to read from
        output_path:  destination rulesmd.ini (defaults to rulesmd_path)
        output_dir:   directory for include files (defaults to output_path's dir)

        If use_ares_include=True:
          - Lists go in output_path (rulesmd)
          - Sections go in separate include files in output_dir
          - [#include] added to output_path

        If use_ares_include=False:
          - Everything goes inline in output_path
        """
        from pathlib import Path as P
        rulesmd_path = P(rulesmd_path)
        output_path  = P(output_path) if output_path else rulesmd_path
        output_dir   = P(output_dir)  if output_dir  else output_path.parent

        written = {}

        if self.use_ares_include:
            # Write sections to include files
            if self.mode in (1, 3, 4):
                p = output_dir / "ai_prereq_present.ini"
                self._write_presence_sections(p)
                written['presence'] = p

            if self.mode in (2, 3, 4):
                p = output_dir / "ai_prereq_absent.ini"
                self._write_absence_sections(p)
                written['absence'] = p

            if self.mode == 4 and self._mixed_dummies:
                p = output_dir / "ai_prereq_mixed.ini"
                self._write_mixed_sections(p)
                written['mixed'] = p
            elif self._mixed_dummies:
                # Mixed dummies requested via request_mixed_combo even in
                # mode 1/2/3 — write them alongside absence sections
                p = output_dir / "ai_prereq_mixed.ini"
                self._write_mixed_sections(p)
                written['mixed'] = p

            # Write lists + #include to output_path
            self._write_rulesmd_lists(rulesmd_path, output_path, written)
            written['rulesmd'] = output_path
        else:
            # Everything inline
            self.write_to_rules(rulesmd_path, output_path)
            if self.mode in (2, 3, 4):
                self._append_absence_sections(output_path)
            if self._mixed_dummies:
                self._append_mixed_sections(output_path)
            written['rulesmd'] = output_path

        # Patch real buildings with absence killer SWs
        if self._real_bld_patches:
            self._patch_real_buildings(written['rulesmd'])

        return written

    def _write_presence_sections(self, path: Path):
        """Write presence dummy sections to a separate include file."""
        lines = ['; YR-AI-Tool -- Presence Dummy Prerequisites\r\n',
                 '; Generated automatically. Do not edit manually.\r\n\r\n']
        owner_str = self._multiplay_countries()

        lines.append('; === Presence Dummy Buildings ===\r\n\r\n')
        for db in self.dummy_blds:
            all_sws = self._dmpr_super_weapons.get(db.section_id, [])
            lines += self._building_section_lines(
                db.section_id, db.bld_ids,
                self._bld_comment(db.bld_ids),
                all_sws, owner_str,
                header_comment=self._bld_comment(db.bld_ids))

        lines.append('; === Presence Spawner Superweapons ===\r\n\r\n')
        for sw in self.spawner_sws:
            cmt = self._bld_comment(self._bld_ids_for(sw.indices))
            lines += self._spawner_lines(sw.section_id, sw.aux_buildings,
                                          sw.limbo_types, sw.limbo_ids,
                                          sw.always_granted, sw.neg_buildings,
                                          comment=cmt)

        lines.append('; === Presence Killer Superweapons ===\r\n\r\n')
        for ksw in self.killer_sws:
            cmt = self._bld_comment(self._bld_ids_for(ksw.combo_indices))
            lines += self._killer_lines(ksw.section_id, ksw.aux_buildings,
                                         ksw.neg_buildings, ksw.limbo_kill_ids,
                                         presence=True, comment=cmt)

        with path.open('w', encoding='latin-1', errors='replace') as f:
            f.write(guard_ini_text(lines))

    def _write_absence_sections(self, path: Path):
        """Write absence dummy sections to a separate include file."""
        lines = ['; YR-AI-Tool -- Absence Dummy Prerequisites\r\n',
                 '; Generated automatically. Do not edit manually.\r\n\r\n']
        lines += self._absence_section_lines()
        with path.open('w', encoding='latin-1', errors='replace') as f:
            f.write(guard_ini_text(lines))

    def _write_mixed_sections(self, path: Path):
        """Write mixed dummy sections to a separate include file."""
        lines = ['; YR-AI-Tool -- Mixed Presence+Absence Dummy Prerequisites\r\n',
                 '; Generated automatically. Do not edit manually.\r\n\r\n']
        owner_str = self._multiplay_countries()

        lines.append('; === Mixed Dummy Buildings ===\r\n\r\n')
        for m in self._mixed_dummies:
            p_bids  = [self.buildings[i] for i in m['present_idx']]
            a_bids  = [self.buildings[i] for i in m['absent_idx']]
            all_sws = self._mixed_sw_map.get(m['section_id'], [])
            mix_comment = (self._bld_comment(p_bids, 'w/') + ' | ' +
                           self._bld_comment(a_bids, 'wo/'))
            lines += self._building_section_lines(
                m['section_id'],
                p_bids + a_bids,
                mix_comment,
                all_sws, owner_str,
                header_comment=mix_comment)

        lines.append('; === Mixed Spawner Superweapons ===\r\n\r\n')
        for sw in self._mixed_spawners:
            p_bids = [self.buildings[i] for i in sw['present_idx']]
            a_bids = [self.buildings[i] for i in sw['absent_idx']]
            cmt = self._bld_comment(p_bids, 'w/') + ' | ' + self._bld_comment(a_bids, 'wo/')
            lines += self._spawner_lines(
                sw['section_id'], sw['aux_buildings'],
                sw['limbo_types'], sw['limbo_ids'],
                sw['always_granted'], sw['neg_buildings'], comment=cmt)

        lines.append('; === Mixed Killer Superweapons ===\r\n\r\n')
        for ksw in self._mixed_killers:
            p_bids = [self.buildings[i] for i in ksw['present_idx']]
            a_bids = [self.buildings[i] for i in ksw['absent_idx']]
            cmt = self._bld_comment(p_bids, 'w/') + ' | ' + self._bld_comment(a_bids, 'wo/')
            lines += self._killer_lines(
                ksw['section_id'], ksw['aux_buildings'],
                ksw['neg_buildings'], ksw['limbo_kill_ids'],
                presence=True, comment=cmt)

        with path.open('w', encoding='latin-1', errors='replace') as f:
            f.write(guard_ini_text(lines))
        """Generate presence dummy building and SW section lines."""
        lines = []
        owner_str = self._multiplay_countries()

        for db in self.dummy_blds:
            all_sws = self._dmpr_super_weapons.get(db.section_id, [])
            lines += self._building_section_lines(
                db.section_id, db.bld_ids,
                self._bld_comment(db.bld_ids),
                all_sws, owner_str,
                header_comment=self._bld_comment(db.bld_ids))

        lines.append('; === Presence Spawner Superweapons ===\r\n\r\n')
        for sw in self.spawner_sws:
            cmt = self._bld_comment(self._bld_ids_for(sw.indices))
            lines += self._spawner_lines(sw.section_id, sw.aux_buildings,
                                          sw.limbo_types, sw.limbo_ids,
                                          sw.always_granted, sw.neg_buildings,
                                          comment=cmt)

        lines.append('; === Presence Killer Superweapons ===\r\n\r\n')
        for ksw in self.killer_sws:
            cmt = self._bld_comment(self._bld_ids_for(ksw.combo_indices))
            lines += self._killer_lines(ksw.section_id, ksw.aux_buildings,
                                         ksw.neg_buildings, ksw.limbo_kill_ids,
                                         presence=True, comment=cmt)
        return lines

    def _absence_section_lines(self) -> list[str]:
        """Generate absence dummy building and SW section lines."""
        lines = []
        owner_str = self._multiplay_countries()

        for ab in self.absence_dummies:
            all_sws = self._absence_super_weapons.get(ab.section_id, [])
            lines += self._building_section_lines(
                ab.section_id, ab.bld_ids,
                self._bld_comment(ab.bld_ids, 'wo/'),
                all_sws, owner_str,
                is_absence=True,
                header_comment=self._bld_comment(ab.bld_ids, 'wo/'))

        lines.append('; === Absence Spawner Superweapons ===\r\n\r\n')
        for sw in self.absence_spawners:
            lines += self._absence_spawner_lines(sw)

        lines.append('; === Absence Killer Superweapons ===\r\n\r\n')
        for ksw in self.absence_killers:
            cmt = self._bld_comment(self._bld_ids_for(ksw.combo_indices), 'wo/')
            lines += self._killer_lines(ksw.section_id, ksw.aux_buildings,
                                         ksw.neg_buildings, ksw.limbo_kill_ids,
                                         presence=False, comment=cmt)
        return lines

    def _bld_comment(self, bld_ids: list[str],
                      prefix: str = 'w/') -> str:
        """Build a readable 'w/ Name1, Name2' comment string for building IDs."""
        names = ', '.join(
            self.ini.section(bid).get('name', bid) or bid
            for bid in bld_ids)
        return f"{prefix} {names}"

    def _building_section_lines(self, section_id, bld_ids, bld_names,
                                  all_sws, owner_str,
                                  is_absence=False,
                                  header_comment: str = '') -> list[str]:
        prefix = "ABSENT: " if is_absence else "DUMMY PREREQUISITE: "
        lines  = []
        header = f"[{section_id}]"
        if header_comment:
            header += f"  ; {header_comment}"
        lines.append(header + "\r\n")
        lines.append(f"UIName=NOSTR:\r\n")
        lines.append(f"Name={prefix}{','.join(bld_ids)}\r\n")
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
        if all_sws:
            lines.append(f"SuperWeapons={','.join(all_sws)}\r\n")
        lines.append('\r\n')
        return lines

    def _spawner_lines(self, section_id, aux_buildings, limbo_types,
                        limbo_ids, always_granted, neg_buildings,
                        comment: str = '') -> list[str]:
        lines = []
        header = f"[{section_id}]"
        if comment:
            header += f"  ; spawns: {comment}"
        lines.append(header + "\r\n")
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
        if aux_buildings:
            lines.append(f"SW.AuxBuildings={aux_buildings}\r\n")
        lines.append(f"LimboDelivery.Types={limbo_types}\r\n")
        lines.append(f"LimboDelivery.IDs={limbo_ids}\r\n")
        lines.append(f"SW.AlwaysGranted={'yes' if always_granted else 'no'}\r\n")
        lines.append(f"SW.InitialReady=yes\r\n")
        lines.append(f"SW.NegBuildings={neg_buildings}\r\n")
        lines.append('\r\n')
        return lines

    def _absence_spawner_lines(self, sw: AbsenceSpawnerSW) -> list[str]:
        comment = self._bld_comment(self._bld_ids_for(sw.indices), 'wo/')
        return self._spawner_lines(
            sw.section_id, "",
            sw.limbo_types, sw.limbo_ids,
            sw.always_granted, sw.neg_buildings,
            comment=comment)

    def _killer_lines(self, section_id, aux_buildings, neg_buildings,
                       limbo_kill_ids, presence=True,
                       comment: str = '') -> list[str]:
        lines = []
        header = f"[{section_id}]"
        if comment:
            header += f"  ; kills: {comment}"
        lines.append(header + "\r\n")
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
        lines.append(f"SW.AuxBuildings={aux_buildings}\r\n")
        lines.append(f"LimboKill.Affects=owner\r\n")
        lines.append(f"LimboKill.IDs={limbo_kill_ids}\r\n")
        lines.append(f"SW.AlwaysGranted=yes\r\n")
        lines.append(f"SW.InitialReady=yes\r\n")
        if neg_buildings:
            lines.append(f"SW.NegBuildings={neg_buildings}\r\n")
        lines.append('\r\n')
        return lines


    def _append_absence_sections(self, path: Path):
        """Append absence sections inline to rulesmd.ini (no Ares)."""
        lines = self._absence_section_lines()
        with path.open('a', encoding='latin-1', errors='replace') as f:
            f.write('; === YR-AI-Tool Absence Dummy Prerequisites ===\r\n\r\n')
            f.write(guard_ini_text(lines))

    def _append_mixed_sections(self, path: Path):
        """Append mixed presence+absence sections inline to rulesmd.ini."""
        if not self._mixed_dummies:
            return
        owner_str = self._multiplay_countries()
        lines = ['; === YR-AI-Tool Mixed Presence+Absence Dummy Prerequisites ===\r\n\r\n']

        for m in self._mixed_dummies:
            p_bids  = [self.buildings[i] for i in m['present_idx']]
            a_bids  = [self.buildings[i] for i in m['absent_idx']]
            all_sws = self._mixed_sw_map.get(m['section_id'], [])
            mix_comment = (self._bld_comment(p_bids, 'w/') + ' | ' +
                           self._bld_comment(a_bids, 'wo/'))
            lines += self._building_section_lines(
                m['section_id'],
                p_bids + a_bids,
                mix_comment,
                all_sws, owner_str,
                header_comment=mix_comment)

        lines.append('; === Mixed Spawner Superweapons ===\r\n\r\n')
        for sw in self._mixed_spawners:
            p_bids = [self.buildings[i] for i in sw['present_idx']]
            a_bids = [self.buildings[i] for i in sw['absent_idx']]
            cmt = self._bld_comment(p_bids, 'w/') + ' | ' + self._bld_comment(a_bids, 'wo/')
            lines += self._spawner_lines(
                sw['section_id'], sw['aux_buildings'],
                sw['limbo_types'], sw['limbo_ids'],
                sw['always_granted'], sw['neg_buildings'], comment=cmt)

        lines.append('; === Mixed Killer Superweapons ===\r\n\r\n')
        for ksw in self._mixed_killers:
            p_bids = [self.buildings[i] for i in ksw['present_idx']]
            a_bids = [self.buildings[i] for i in ksw['absent_idx']]
            cmt = self._bld_comment(p_bids, 'w/') + ' | ' + self._bld_comment(a_bids, 'wo/')
            lines += self._killer_lines(
                ksw['section_id'], ksw['aux_buildings'],
                ksw['neg_buildings'], ksw['limbo_kill_ids'],
                presence=True, comment=cmt)

        with path.open('a', encoding='latin-1', errors='replace') as f:
            f.write(guard_ini_text(lines))

    def _write_rulesmd_lists(self, source_path: Path, output_path: Path,
                               include_files: dict):
        """
        Write list entries to rulesmd.ini and add [#include] section.
        Sections stay in separate include files.
        """
        with source_path.open(encoding='latin-1') as f:
            lines = f.readlines()

        while lines and lines[-1].strip() in (';', ''):
            lines.pop()

        # Add/update [#include] section
        inc_idx = next((i for i, l in enumerate(lines)
                        if l.strip() == '[#include]'), None)
        if inc_idx is None:
            lines.insert(0, '[#include]\r\n')
            inc_idx = 0

        # Find end of [#include] section using clean insert point
        _, inc_insert = _list_insert_point(lines, inc_idx)
        highest_inc   = _find_highest_key(lines, inc_idx)
        # [#include] entries are 1-based per Ares documentation
        next_inc = max(1, highest_inc + 1)

        new_includes = []
        for key, filepath in include_files.items():
            if key in ('presence', 'absence', 'mixed'):
                n = next_inc + len(new_includes)
                new_includes.append(
                    f"{n}={filepath.name}  ; YR-AI-Tool\r\n")
        lines[inc_insert:inc_insert] = new_includes

        # Write list entries (same as write_to_rules but skip sections)
        # BuildingTypes list
        bt_idx = next(i for i, l in enumerate(lines)
                      if l.strip() == '[BuildingTypes]')
        _, bt_insert = _list_insert_point(lines, bt_idx)
        highest_bt_aipreq = _find_highest_key(lines, bt_idx, prefix='AIPREQ')

        all_dummies = list(self.dummy_blds) if self.mode in (1,3,4) else []
        all_absence = list(self.absence_dummies) if self.mode in (2,3,4) else []
        all_mixed   = self._mixed_dummies if self._mixed_dummies else []

        new_bt = []
        for i, db in enumerate(all_dummies):
            n = highest_bt_aipreq + 1 + i
            new_bt.append(f"AIPREQ{n:03d}={db.section_id}  ; YR-AI-Tool\r\n")
        for i, ab in enumerate(all_absence):
            n = highest_bt_aipreq + 1 + len(all_dummies) + i
            new_bt.append(f"AIPREQ{n:03d}={ab.section_id}  ; YR-AI-Tool\r\n")
        for i, mx in enumerate(all_mixed):
            n = highest_bt_aipreq + 1 + len(all_dummies) + len(all_absence) + i
            new_bt.append(f"AIPREQ{n:03d}={mx['section_id']}  ; YR-AI-Tool\r\n")
        lines[bt_insert:bt_insert] = new_bt

        # SuperWeaponTypes list
        swt_idx = next((i for i, l in enumerate(lines)
                        if l.strip() == '[SuperWeaponTypes]'), None)
        if swt_idx is None:
            lines.append('\r\n[SuperWeaponTypes]\r\n')
            swt_idx = len(lines) - 1

        _, swt_insert = _list_insert_point(lines, swt_idx)
        highest_sw = _find_highest_key(lines, swt_idx, prefix='AIPREQ')

        def _sid(obj):
            """Get section_id from either a dataclass or dict."""
            return obj.section_id if hasattr(obj, 'section_id') else obj['section_id']

        new_sw = []
        all_spawners = (list(self.spawner_sws) if self.mode in (1,3,4) else []) + \
                       (list(self.absence_spawners) if self.mode in (2,3,4) else []) + \
                       (self._mixed_spawners if self._mixed_spawners else [])
        all_killers  = (list(self.killer_sws) if self.mode in (1,3,4) else []) + \
                       (list(self.absence_killers) if self.mode in (2,3,4) else []) + \
                       (self._mixed_killers if self._mixed_killers else [])

        for i, sw in enumerate(all_spawners):
            n = highest_sw + 1 + i * 2
            new_sw.append(f"AIPREQ{n:03d}D={_sid(sw)}  ; YR-AI-Tool\r\n")
        for i, ksw in enumerate(all_killers):
            n = highest_sw + 1 + len(all_spawners)*2 + i*2
            new_sw.append(f"AIPREQ{n:03d}K={_sid(ksw)}  ; YR-AI-Tool\r\n")
        lines[swt_insert:swt_insert] = new_sw

        lines.append(';\r\n')
        with output_path.open('w', encoding='latin-1', errors='replace') as f:
            f.write(guard_ini_text(lines))

    def _patch_real_buildings(self, rulesmd_path: Path):
        """
        Patch existing building sections to add absence killer SWs
        to their SuperWeapons= entries.
        """
        if not self._real_bld_patches:
            return

        with rulesmd_path.open(encoding='latin-1') as f:
            lines = f.readlines()

        import re
        section_pattern = re.compile(r"^\[([^\]]+)\]")
        current_section = None

        for i, line in enumerate(lines):
            m = section_pattern.match(line.strip())
            if m:
                current_section = m.group(1)
                continue

            if current_section in self._real_bld_patches:
                s = line.strip()
                if s.lower().startswith('superweapons='):
                    existing = s[13:].strip()
                    new_sws  = self._real_bld_patches[current_section]
                    if existing:
                        lines[i] = f"SuperWeapons={existing},{','.join(new_sws)}  ; YR-AI-Tool\r\n"
                    else:
                        lines[i] = f"SuperWeapons={','.join(new_sws)}  ; YR-AI-Tool\r\n"
                    del self._real_bld_patches[current_section]

        # For buildings that had no SuperWeapons= line, insert one after Name=
        if self._real_bld_patches:
            current_section = None
            for i, line in enumerate(lines):
                m = section_pattern.match(line.strip())
                if m:
                    current_section = m.group(1)
                    continue
                if current_section in self._real_bld_patches:
                    s = line.strip().lower()
                    if s.startswith('name=') or s.startswith('techlevel='):
                        new_sws = self._real_bld_patches[current_section]
                        lines.insert(i + 1,
                            f"SuperWeapons={','.join(new_sws)}  ; YR-AI-Tool\r\n")
                        del self._real_bld_patches[current_section]
                        break

        with rulesmd_path.open('w', encoding='latin-1', errors='replace') as f:
            f.write(guard_ini_text(lines))


# ---------------------------------------------------------------------------
# Ares/Phobos detection
# ---------------------------------------------------------------------------

def detect_ares(rulesmd_path: str | Path) -> bool:
    """
    Detect Ares by checking for Ares-specific tags:
      - [#include] section (Ares INI inclusion system)
      - SW.AuxBuildings= or SW.NegBuildings= (Ares 0.9 superweapon tags)
    LimboDelivery is NOT an Ares feature — it is Phobos.
    """
    path = Path(rulesmd_path)
    if not path.exists():
        return False
    with path.open(encoding='latin-1') as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith(';'):
                continue
            if s == '[#include]':
                return True
            if s.startswith('SW.AuxBuildings') or s.startswith('SW.NegBuildings'):
                return True
    return False


def detect_phobos(aimd_path: str | Path,
                  rulesmd_path: str | Path | None = None) -> bool:
    """
    Detect Phobos by checking for Phobos-specific tags:
      - Script action numbers >= 10000 in aimd.ini (Phobos-only script actions)
      - LimboDelivery= or LimboKill= in rulesmd.ini (Phobos limbo building system)
    The dummy prerequisite system requires BOTH Ares (SW.AuxBuildings/NegBuildings)
    AND Phobos (LimboDelivery/LimboKill) to function.
    """
    import re
    pattern = re.compile(r"^\d+=(\d+),")
    path = Path(aimd_path)
    if path.exists():
        with path.open(encoding='latin-1') as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith(';'):
                    continue
                m = pattern.match(s)
                if m and int(m.group(1)) >= 10000:
                    return True
    # Also check rulesmd for LimboDelivery tags
    if rulesmd_path:
        rpath = Path(rulesmd_path)
        if rpath.exists():
            with rpath.open(encoding='latin-1') as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith(';'):
                        continue
                    if s.startswith('LimboDelivery') or s.startswith('LimboKill'):
                        return True
    return False


def ask_mode() -> int:
    """Interactive mode selection."""
    print("\nDummy Prerequisite Mode:")
    print("  0. None — skip entirely")
    print("  1. Present only — track buildings that EXIST")
    print("  2. Missing only — track buildings that are ABSENT")
    print("  3. Both, no mixed — presence + absence, no combinations")
    print("  4. Full mixed — all combinations including present+absent")
    while True:
        raw = input("\nSelect mode [1]: ").strip()
        if not raw:
            return 1
        try:
            n = int(raw)
            if 0 <= n <= 4:
                return n
        except ValueError:
            pass
        print("  Please enter 0-4.")


def ask_mixed_scope(buildings_by_category: dict) -> tuple[str, list[str]]:
    """
    Interactive mixed scope selection (only called when mode=4).
    Returns (scope, selected_ids) where scope is one of:
      'none', 'auto', 'selected', 'all'
    and selected_ids is a list of building IDs (only relevant for 'selected').
    """
    print("\nMixed Prerequisites Scope:")
    print("  A. None     — no mixed combos (same as mode 3)")
    print("  B. Auto     — only what AI triggers actually need (recommended)")
    print("  C. Selected — pick specific buildings to mix")
    print("  D. All      — every possible combination (warning: can be enormous)")

    while True:
        raw = input("\nSelect scope [B]: ").strip().upper()
        if not raw:
            raw = 'B'
        if raw == 'A':
            return 'none', []
        if raw == 'B':
            return 'auto', []
        if raw == 'C':
            break
        if raw == 'D':
            confirm = input("  Warning: 'All' can generate millions of sections. "
                            "Continue? [y/N]: ").strip().lower()
            if confirm == 'y':
                return 'all', []
            print("  Cancelled.")
        else:
            print("  Please enter A, B, C, or D.")

    # Selected scope: show building list by category
    print("\nSelect buildings to include in mixed combos.")
    print("Enter building IDs separated by commas, or 'all' for a category.")
    print()
    all_ids = []
    for cat_name, blds in buildings_by_category.items():
        ids = [b.bid for b in blds]
        all_ids.extend(ids)
        print(f"  {cat_name}: {', '.join(ids)}")

    selected = []
    raw = input("\nEnter building IDs [none]: ").strip()
    if raw:
        for token in raw.split(','):
            bid = token.strip().upper()
            if bid in all_ids:
                selected.append(bid)
            else:
                print(f"  Unknown building ID: {bid} — skipped")

    return 'selected', selected
