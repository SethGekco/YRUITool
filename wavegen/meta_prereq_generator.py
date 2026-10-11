"""
meta_prereq_generator.py

Generates the meta-dummy prerequisite system for units that have both
Prerequisite= and Prerequisite.Negative= (Ares extension).

Problem:
  AITriggerType has one condition slot. A unit gated on
  Prerequisite=A,B AND Prerequisite.Negative=D,E cannot be expressed
  in a single trigger. The AI ignores prerequisites entirely, so without
  this system it would recruit units it shouldn't be able to build.

Solution — meta-dummy buildings:
  For each unit (or unique prereq profile), generate:

  1. Presence dummies for negative-prereq buildings
     (upgrade dummies like BNINJASoulAmericans0 that the existing
     infrastructure dummy system doesn't cover).
     Each gets a SW that fires when the building is present.

  2. A meta-dummy building representing the full prereq state
     (positive conditions satisfied AND no negative conditions active).

  3. A delivery chain for the meta-dummy:
     - Single positive prereq token → SW hosted on that token's
       existing DMPR dummy, delivers meta-dummy directly.
     - Multiple positive prereq tokens → chained SWs:
       SW1 on DMPR_A → delivers intermediate1
       SW2 on intermediate1 + SW.AuxBuildings=DMPR_B → delivers meta
     - All delivery SWs also have SW.NegBuildings=<neg_presence_dummies>
       so delivery is blocked when any negative condition is active.

  4. A kill SW that removes the meta-dummy when any negative condition
     becomes active:
     SW.AuxBuildings=NEG_DUMMY1,NEG_DUMMY2,...  (OR: any one triggers it)
     LimboKill.IDs=<meta_id>,<intermediate_ids>

  The AITriggerType uses CondType=1 (AI owns) + CondObj=meta-dummy.

Output: ai_prereq_meta.ini (separate from presence/absence/mixed files)

Requirements:
  - Ares: SW.AuxBuildings, SW.NegBuildings
  - Phobos: LimboDelivery, LimboKill

TODO: Country restriction on meta-dummy SWs (SW.RequiredHouses).
      Currently treats all meta-dummies as available to all countries.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from rulesmd_parser import IniParser
from ai_unit_scanner import (
    _fully_expand_token, _generic_prereqs,
    _is_building_impossible, _playable_countries,
    _player_deliverable_buildings,
)


# ---------------------------------------------------------------------------
# ID allocation
# ---------------------------------------------------------------------------

_LIMBO_ID_BASE_NEG  = 81_600_000   # presence dummies for neg-prereq buildings
_LIMBO_ID_BASE_META = 81_700_000   # meta-dummies
_LIMBO_ID_BASE_INT  = 81_800_000   # intermediates in delivery chains


def _limbo_str(n: int) -> str:
    return str(n).zfill(8)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class NegPresenceDummy:
    """Presence dummy for a negative-prereq building."""
    building_id:  str    # the real building (e.g. BNINJASoulAmericans0)
    section_id:   str    # e.g. DMPRNEG00001
    limbo_id:     int
    spawn_sw_id:  str    # SW that delivers this dummy when building exists
    kill_sw_id:   str    # SW that kills this dummy when building is gone


@dataclass
class MetaDummy:
    """Meta-dummy for a unit's full prerequisite profile."""
    unit_id:        str
    unit_name:      str
    section_id:     str          # e.g. AIMETADMY00001
    limbo_id:       int
    # Delivery chain: list of (sw_section_id, hosted_on_section_id, aux_building, delivers)
    chain_sws:      list[dict]   # chain SW descriptors
    intermediate_ids: list[int]  # limbo IDs of intermediate buildings
    intermediate_sections: list[str]
    kill_sw_id:     str
    neg_dummy_ids:  list[str]    # NegPresenceDummy.section_ids used by this meta


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class MetaPrereqGenerator:
    """
    Generates meta-dummy buildings and their SW chains for units with
    Prerequisite.Negative= entries.
    """

    def __init__(self, rules_path: str,
                 parser,
                 dmpr_gen,           # DummyPrereqGenerator (already generated)
                 global_id_registry):
        self.rules_path = rules_path
        self.parser     = parser
        self.ini        = IniParser(rules_path)
        self.dmpr_gen   = dmpr_gen
        self.id_reg     = global_id_registry

        self.gp_upper   = _generic_prereqs(self.ini)
        self.playable   = _playable_countries(self.ini)
        self.player_del = _player_deliverable_buildings(self.ini)

        # bld_id → existing single DMPR dummy section_id
        self.bld_to_dmpr: dict[str, str] = {
            d.bld_ids[0]: d.section_id
            for d in dmpr_gen.dummy_blds
            if len(d.indices) == 1
        }

        # Results
        self.neg_presence_dummies: dict[str, NegPresenceDummy] = {}  # bld_id → dummy
        self.meta_dummies:         list[MetaDummy]              = []

        # Section ID counters
        self._neg_counter  = 0
        self._meta_counter = 0
        self._int_counter  = 0

        # Limbo ID counters
        self._neg_limbo  = _LIMBO_ID_BASE_NEG
        self._meta_limbo = _LIMBO_ID_BASE_META
        self._int_limbo  = _LIMBO_ID_BASE_INT

        # Unit-to-meta mapping for trigger generator
        self.unit_to_meta: dict[str, MetaDummy] = {}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def generate(self, opt_in: bool = True) -> int:
        """
        Scan all buildable units for Prerequisite.Negative and generate
        meta-dummies. Returns count of meta-dummies generated.
        If opt_in=True (default), prompt user for confirmation.
        """
        candidates = self._find_candidates()
        if not candidates:
            return 0

        if opt_in:
            print(f"\n  [Meta-dummy system]")
            print(f"  {len(candidates)} units have Prerequisite.Negative (Ares).")
            print(f"  Generating meta-dummy buildings so AI respects negative prerequisites.")
            print(f"  This adds ~{len(candidates)*4 + len(self._collect_neg_buildings(candidates))} INI sections.")
            try:
                ans = input("  Generate meta-dummies? [Y/n] > ").strip().lower()
            except EOFError:
                ans = 'y'
            if ans == 'n':
                return 0

        # Pass 1: generate presence dummies for all neg-prereq buildings
        neg_buildings = self._collect_neg_buildings(candidates)
        for bld_id in sorted(neg_buildings):
            self._get_or_create_neg_dummy(bld_id)

        # Pass 2: generate meta-dummies
        for uid, name, pos_tokens, neg_buildings_set in candidates:
            meta = self._generate_meta(uid, name, pos_tokens, neg_buildings_set)
            if meta:
                self.meta_dummies.append(meta)
                self.unit_to_meta[uid] = meta

        return len(self.meta_dummies)

    def _find_candidates(self) -> list[tuple]:
        """Find all buildable units with Prerequisite.Negative."""
        result = []
        for r in self.parser.buildable:
            if not r.faction:
                continue
            s = self.ini.section(r.unit_id)
            if not s:
                continue
            neg_raw = s.get('prerequisite.negative', '').strip()
            if not neg_raw:
                continue
            pos_raw = s.get('prerequisite', '').strip()

            # Expand positive tokens to existing DMPR dummies
            pos_tokens = []
            for tok in pos_raw.split(','):
                tok = tok.strip()
                if not tok:
                    continue
                dmpr = self._resolve_pos_token(tok)
                if dmpr:
                    pos_tokens.append((tok, dmpr))

            # Expand negative tokens to concrete buildings
            neg_blds = set()
            for tok in neg_raw.split(','):
                tok = tok.strip()
                if not tok:
                    continue
                for bld in self._expand_token(tok):
                    if self._is_player_accessible(bld):
                        neg_blds.add(bld)

            if not neg_blds:
                continue  # no actionable negative prereqs

            result.append((r.unit_id, r.name, pos_tokens, neg_blds))
        return result

    def _collect_neg_buildings(self, candidates) -> set[str]:
        return set().union(*(neg for _, _, _, neg in candidates))

    # ------------------------------------------------------------------
    # Token resolution helpers
    # ------------------------------------------------------------------

    def _expand_token(self, token: str) -> frozenset[str]:
        upper = token.upper()
        if upper in self.gp_upper:
            return _fully_expand_token(token, self.gp_upper)
        return frozenset({token})

    def _resolve_pos_token(self, token: str) -> str | None:
        """
        Return the DMPR section ID for a positive prereq token, or None
        if the token doesn't map to an existing infrastructure dummy.
        """
        for bld in self._expand_token(token):
            if bld in self.bld_to_dmpr:
                return self.bld_to_dmpr[bld]
        return None

    def _is_player_accessible(self, bld_id: str) -> bool:
        """True if a player can own this building (SW-deliverable or buildable)."""
        if bld_id in self.player_del:
            return True
        return not _is_building_impossible(bld_id, self.ini, self.playable)

    # ------------------------------------------------------------------
    # Neg presence dummy
    # ------------------------------------------------------------------

    def _get_or_create_neg_dummy(self, bld_id: str) -> NegPresenceDummy:
        if bld_id in self.neg_presence_dummies:
            return self.neg_presence_dummies[bld_id]

        self._neg_counter += 1
        n = self._neg_counter
        section_id  = f'DMPRNEG{n:05d}'
        spawn_sw_id = f'DMPRNEGSP{n:05d}'
        kill_sw_id  = f'DMPRNEGKL{n:05d}'
        limbo_id    = self._neg_limbo + n

        dummy = NegPresenceDummy(
            building_id = bld_id,
            section_id  = section_id,
            limbo_id    = limbo_id,
            spawn_sw_id = spawn_sw_id,
            kill_sw_id  = kill_sw_id,
        )
        self.neg_presence_dummies[bld_id] = dummy
        return dummy

    # ------------------------------------------------------------------
    # Meta-dummy generation
    # ------------------------------------------------------------------

    def _generate_meta(self, uid: str, name: str,
                        pos_tokens: list[tuple[str, str]],
                        neg_buildings: set[str]) -> MetaDummy | None:
        self._meta_counter += 1
        n = self._meta_counter
        section_id = f'AIMETADMY{n:05d}'
        limbo_id   = self._meta_limbo + n
        kill_sw_id = f'AIMETAKL{n:05d}'

        # Collect neg presence dummy section IDs
        neg_dummy_sections = [
            self.neg_presence_dummies[b].section_id
            for b in sorted(neg_buildings)
            if b in self.neg_presence_dummies
        ]

        # Build delivery chain
        chain_sws, int_ids, int_sections = self._build_chain(
            uid, n, pos_tokens, neg_dummy_sections, section_id, limbo_id)

        return MetaDummy(
            unit_id        = uid,
            unit_name      = name,
            section_id     = section_id,
            limbo_id       = limbo_id,
            chain_sws      = chain_sws,
            intermediate_ids = int_ids,
            intermediate_sections = int_sections,
            kill_sw_id     = kill_sw_id,
            neg_dummy_ids  = neg_dummy_sections,
        )

    def _build_chain(self, uid: str, meta_n: int,
                      pos_tokens: list[tuple[str, str]],
                      neg_dummy_sections: list[str],
                      final_section: str, final_limbo: int,
                      ) -> tuple[list[dict], list[int], list[str]]:
        """
        Build the SW delivery chain for a meta-dummy.

        No positive prereqs → single always-granted SW delivers meta directly.
        One positive prereq → SW hosted on its DMPR dummy, delivers meta.
        N positive prereqs → chain of N SWs, each hosted on the previous
                             intermediate dummy.

        All final delivery SWs include SW.NegBuildings=<neg dummies> so
        they stop firing when a negative condition becomes active.

        Returns (chain_sw_descriptors, intermediate_limbo_ids, intermediate_section_ids)
        """
        neg_str = ','.join(neg_dummy_sections) if neg_dummy_sections else ''
        chain   = []
        int_ids = []
        int_sections = []

        if not pos_tokens:
            # No positive prereqs — deliver meta directly, always granted
            sw_id = f'AIMETASP{meta_n:05d}A'
            chain.append({
                'section_id':    sw_id,
                'hosted_on':     None,       # AlwaysGranted=yes, no host
                'always_granted': True,
                'aux_buildings': '',
                'neg_buildings': f'{final_section},{neg_str}' if neg_str else final_section,
                'delivers':      final_section,
                'limbo_id':      _limbo_str(final_limbo),
            })
            return chain, int_ids, int_sections

        if len(pos_tokens) == 1:
            # Single positive prereq — SW hosted on that DMPR dummy
            _, dmpr_id = pos_tokens[0]
            sw_id = f'AIMETASP{meta_n:05d}A'
            neg_full = f'{final_section},{neg_str}' if neg_str else final_section
            chain.append({
                'section_id':    sw_id,
                'hosted_on':     dmpr_id,
                'always_granted': False,
                'aux_buildings': '',
                'neg_buildings': neg_full,
                'delivers':      final_section,
                'limbo_id':      _limbo_str(final_limbo),
            })
            return chain, int_ids, int_sections

        # Multiple positive prereqs — build chain
        # Step 1: SW on first DMPR delivers intermediate1
        # Step i: SW on intermediate(i-1) + AuxBuildings=DMPR_i delivers intermediate_i
        # Last:   SW on intermediate(n-2) + AuxBuildings=DMPR_(n-1) delivers meta

        prev_host = None
        for i, (tok, dmpr_id) in enumerate(pos_tokens):
            self._int_counter += 1
            sw_id = f'AIMETASP{meta_n:05d}{chr(65+i)}'  # A, B, C...
            is_last = (i == len(pos_tokens) - 1)

            if is_last:
                delivers   = final_section
                limbo_id_s = _limbo_str(final_limbo)
                neg_full   = f'{final_section},{neg_str}' if neg_str else final_section
            else:
                int_id  = self._int_limbo + self._int_counter
                int_sec = f'AIMETAINT{meta_n:05d}{chr(65+i)}'
                delivers   = int_sec
                limbo_id_s = _limbo_str(int_id)
                neg_full   = f'{int_sec},{neg_str}' if neg_str else int_sec
                int_ids.append(int_id)
                int_sections.append(int_sec)

            if i == 0:
                # First in chain: hosted on first DMPR, no AuxBuildings needed
                chain.append({
                    'section_id':    sw_id,
                    'hosted_on':     dmpr_id,
                    'always_granted': False,
                    'aux_buildings': '',
                    'neg_buildings': neg_full,
                    'delivers':      delivers,
                    'limbo_id':      limbo_id_s,
                })
            else:
                # Subsequent: hosted on previous intermediate, AuxBuildings=current DMPR
                chain.append({
                    'section_id':    sw_id,
                    'hosted_on':     prev_host,   # previous intermediate section
                    'always_granted': False,
                    'aux_buildings': dmpr_id,
                    'neg_buildings': neg_full,
                    'delivers':      delivers,
                    'limbo_id':      limbo_id_s,
                })

            prev_host = delivers if not is_last else None

        return chain, int_ids, int_sections

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def write(self, out_path: str | Path) -> None:
        """Write ai_prereq_meta.ini."""
        lines = [
            "; ai_prereq_meta.ini\r\n",
            "; Meta-dummy prerequisite system for units with Prerequisite.Negative\r\n",
            "; Generated by YR-AI-Tool — DO NOT EDIT MANUALLY\r\n",
            ";\r\n",
            "; Requires: Ares (SW.AuxBuildings, SW.NegBuildings)\r\n",
            ";           Phobos (LimboDelivery, LimboKill)\r\n",
            "\r\n",
        ]

        # ── Neg presence dummies ──────────────────────────────────────
        lines.append("; ════════════════════════════════════════════════\r\n")
        lines.append("; Presence dummies for negative-prereq buildings\r\n")
        lines.append("; ════════════════════════════════════════════════\r\n\r\n")

        for bld_id, nd in sorted(self.neg_presence_dummies.items(),
                                  key=lambda x: x[1].section_id):
            # Dummy building
            lines += [
                f"; Presence dummy for [{bld_id}]\r\n",
                f"[{nd.section_id}]\r\n",
                f"Name=AI Neg Prereq Dummy ({bld_id})\r\n",
                f"TechLevel=-1\r\n",
                f"Strength=1\r\n",
                f"AIBuildThis=no\r\n",
                f"LegalTarget=yes\r\n",
                f"KeepAlive=no\r\n",
                f"Prerequisite=IMPOSSIBLE\r\n",
                f"SuperWeapons={nd.spawn_sw_id},{nd.kill_sw_id}\r\n",
                "\r\n",
            ]
            # Spawn SW: AlwaysGranted + AuxBuildings=real_building
            # (limbo buildings can't host SWs, so we use always-granted
            # with AuxBuildings to check if the building exists in limbo)
            lines += [
                f"[{nd.spawn_sw_id}]\r\n",
                f"Type=UnitDelivery\r\n",
                f"LimboDelivery.Types={nd.section_id}\r\n",
                f"LimboDelivery.IDs={_limbo_str(nd.limbo_id)}\r\n",
                f"SW.AlwaysGranted=yes\r\n",
                f"SW.AutoFire=yes\r\n",
                f"SW.InitialReady=yes\r\n",
                f"SW.AllowPlayer=yes\r\n",
                f"SW.AllowAI=yes\r\n",
                f"SW.AuxBuildings={bld_id}\r\n",           # fires when bld exists
                f"SW.NegBuildings={nd.section_id}\r\n",   # idempotent
                f"SW.ShowCameo=no\r\n",
                f"SW.ManualFire=no\r\n",
                f"RechargeTime=0.01\r\n",
                f"IsPowered=false\r\n",
                "\r\n",
            ]
            # Kill SW: AlwaysGranted + AuxBuildings=the dummy (fires when dummy exists)
            # + NegBuildings=real_building (fires when real building is GONE)
            lines += [
                f"[{nd.kill_sw_id}]\r\n",
                f"Type=UnitDelivery\r\n",
                f"LimboKill.IDs={_limbo_str(nd.limbo_id)}\r\n",
                f"LimboKill.Affects=owner\r\n",
                f"SW.AlwaysGranted=yes\r\n",
                f"SW.AutoFire=yes\r\n",
                f"SW.InitialReady=yes\r\n",
                f"SW.AllowPlayer=yes\r\n",
                f"SW.AllowAI=yes\r\n",
                f"SW.AuxBuildings={nd.section_id}\r\n",   # dummy exists (something to kill)
                f"SW.NegBuildings={bld_id}\r\n",          # real building gone → fire
                f"SW.ShowCameo=no\r\n",
                f"SW.ManualFire=no\r\n",
                f"RechargeTime=0.01\r\n",
                f"IsPowered=false\r\n",
                "\r\n",
            ]

        # ── Meta-dummies ──────────────────────────────────────────────
        lines.append("; ════════════════════════════════════════════════\r\n")
        lines.append("; Meta-dummy buildings\r\n")
        lines.append("; ════════════════════════════════════════════════\r\n\r\n")

        for md in self.meta_dummies:
            lines += [
                f"; Meta-dummy for [{md.unit_id}] {md.unit_name}\r\n",
                f"[{md.section_id}]\r\n",
                f"Name=AI Meta Prereq ({md.unit_id})\r\n",
                f"TechLevel=-1\r\n",
                f"Strength=1\r\n",
                f"AIBuildThis=no\r\n",
                f"LegalTarget=yes\r\n",
                f"KeepAlive=no\r\n",
                f"Prerequisite=IMPOSSIBLE\r\n",
                "\r\n",
            ]

            # Intermediate dummy buildings
            for sec_id in md.intermediate_sections:
                lines += [
                    f"[{sec_id}]\r\n",
                    f"Name=AI Meta Prereq Intermediate ({md.unit_id})\r\n",
                    f"TechLevel=-1\r\n",
                    f"Strength=1\r\n",
                    f"AIBuildThis=no\r\n",
                    f"LegalTarget=yes\r\n",
                    f"KeepAlive=no\r\n",
                    f"Prerequisite=IMPOSSIBLE\r\n",
                    "\r\n",
                ]

            # Chain SWs
            for sw in md.chain_sws:
                lines += [f"[{sw['section_id']}]\r\n"]
                lines += [f"Type=UnitDelivery\r\n"]
                lines += [f"LimboDelivery.Types={sw['delivers']}\r\n"]
                lines += [f"LimboDelivery.IDs={sw['limbo_id']}\r\n"]
                if sw['always_granted']:
                    lines += [f"SW.AlwaysGranted=yes\r\n"]
                else:
                    lines += [f"SW.AlwaysGranted=no\r\n"]
                lines += [f"SW.AutoFire=yes\r\n"]
                lines += [f"SW.InitialReady=yes\r\n"]
                lines += [f"SW.AllowPlayer=yes\r\n"]
                lines += [f"SW.AllowAI=yes\r\n"]
                if sw['aux_buildings']:
                    lines += [f"SW.AuxBuildings={sw['aux_buildings']}\r\n"]
                if sw['neg_buildings']:
                    lines += [f"SW.NegBuildings={sw['neg_buildings']}\r\n"]
                lines += [
                    f"SW.ShowCameo=no\r\n",
                    f"SW.ManualFire=no\r\n",
                    f"RechargeTime=0.01\r\n",
                    f"IsPowered=false\r\n",
                    "\r\n",
                ]
                if sw['hosted_on']:
                    lines += [
                        f"; NOTE: Add SuperWeapons={sw['section_id']} "
                        f"to [{sw['hosted_on']}]\r\n",
                        "\r\n",
                    ]

            # Kill SW
            all_kill_ids = [_limbo_str(md.limbo_id)] + [
                _limbo_str(i) for i in md.intermediate_ids]
            neg_aux = ','.join(md.neg_dummy_ids) if md.neg_dummy_ids else ''
            lines += [
                f"[{md.kill_sw_id}]\r\n",
                f"Type=UnitDelivery\r\n",
                f"LimboKill.IDs={','.join(all_kill_ids)}\r\n",
                f"LimboKill.Affects=owner\r\n",
                f"SW.AlwaysGranted=yes\r\n",
                f"SW.AutoFire=yes\r\n",
                f"SW.InitialReady=yes\r\n",
                f"SW.AllowPlayer=yes\r\n",
                f"SW.AllowAI=yes\r\n",
            ]
            if neg_aux:
                lines += [f"SW.AuxBuildings={neg_aux}\r\n"]
            lines += [
                f"SW.NegBuildings={md.section_id}\r\n",  # only kill if meta exists
                f"SW.ShowCameo=no\r\n",
                f"SW.ManualFire=no\r\n",
                f"RechargeTime=0.01\r\n",
                f"IsPowered=false\r\n",
                "\r\n",
            ]

        with open(out_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)

    def get_superweapons_to_inject(self) -> dict[str, list[str]]:
        """
        Return {building_section_id: [sw_ids_to_add_to_SuperWeapons=]}.
        These SWs need to be listed on DMPR dummy buildings in the output.
        (Neg presence spawn SWs are now always-granted, no injection needed.)
        """
        inject: dict[str, list[str]] = {}

        # Chain SWs go on their host DMPR dummy or intermediate section
        for md in self.meta_dummies:
            for sw in md.chain_sws:
                if sw['hosted_on']:
                    inject.setdefault(sw['hosted_on'], []).append(sw['section_id'])

        return inject
