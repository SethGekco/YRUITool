"""
cli.py
======
Main entry point for the YR AI Wave Generator.

Usage:
    python cli.py --rules rulesmd.ini --aimd aimd.ini [options]

Orchestration order:
    1.  Parse rulesmd.ini + detect Ares/Phobos
    2.  Categorize buildings
    3.  User menus (wave counts, dummy mode, building scope, etc.)
    4.  Initialize registries from existing aimd.ini
    5.  Optionally strip existing YR-AI-Tool entries
    6.  Generate dummy prerequisites (present/absent/mixed)
    7.  Generate defense dummies (DMPRNODEF / DMPRNODEFAA)
    8.  Generate strategic triggers (no-AA, no-defense, MCV, etc.)
    9.  Generate general + country-exclusive wave triggers
    10. Write rulesmd.ini output (dummy buildings, #include)
    11. Write aimd.ini output (taskforces, scripts, teams, triggers)
    12. Print summary
"""

TOOL_VERSION = "v39"  # Update with each release

import argparse
import sys
import shutil
from pathlib import Path


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="YR AI Wave Generator — generates AI attack waves for "
                    "Red Alert 2: Yuri's Revenge and mods.")
    p.add_argument("--rules",
                   help="Path to rulesmd.ini (default: rulesmd.ini or "
                        "vanilla_rulesmd.ini next to cli.py)")
    p.add_argument("--aimd",
                   help="Path to aimd.ini (default: aimd.ini or "
                        "vanilla_aimd.ini next to cli.py)")
    p.add_argument("--out-rules",
                   help="Output rulesmd.ini path (default: output/rulesmd.ini)")
    p.add_argument("--out-aimd",
                   help="Output aimd.ini path (default: output/aimd.ini)")
    p.add_argument("--out-dir",
                   help="Output directory for include files (default: same "
                        "directory as --out-rules)")
    p.add_argument("--seed", type=int, default=None,
                   help="Random seed for reproducible output")
    p.add_argument("--scale-rush",      type=float, default=1.0, metavar="F",
                   help="Extra multiplier for rush-type waves (on top of base scale).")
    p.add_argument("--scale-general",   type=float, default=1.0, metavar="F",
                   help="Extra multiplier for general attack waves.")
    p.add_argument("--scale-artillery", type=float, default=1.0, metavar="F",
                   help="Extra multiplier for artillery/bombard waves.")
    p.add_argument("--scale-aerial",    type=float, default=1.0, metavar="F",
                   help="Extra multiplier for aerial waves.")
    p.add_argument("--scale-naval",     type=float, default=1.0, metavar="F",
                   help="Extra multiplier for naval waves.")
    p.add_argument("--scale-defense",   type=float, default=1.0, metavar="F",
                   help="Extra multiplier for pool/base-defense teams.")
    p.add_argument("--cap-infantry",    type=int, default=0, metavar="N",
                   help="Max infantry per taskforce (0 = uncapped).")
    p.add_argument("--cap-vehicle",     type=int, default=0, metavar="N",
                   help="Max vehicles per taskforce (0 = uncapped).")
    p.add_argument("--cap-aircraft",    type=int, default=0, metavar="N",
                   help="Max aircraft per taskforce (0 = uncapped).")
    p.add_argument("--oversized-threshold", type=int, default=10, metavar="N",
                   help="Team size at which the no-gather LARGE_TEAM scripts "
                        "are used (default 10).")
    p.add_argument("--scale-flatten", type=float, default=0.0, metavar="F",
                   help="0.0-1.0: blend scaling from uniform (0, keeps vanilla "
                        "ratios) toward equal-share (1, all lines end equal). "
                        "Community rebalances use ~0.35 so specialist lines "
                        "get boosted toward parity.")
    p.add_argument("--no-diversify", action="store_true",
                   help="Disable splitting single-type taskforces 50/50 with "
                        "a complementary unit type.")
    p.add_argument("--no-patrols", action="store_true",
                   help="Disable procedural base-patrol defense teams.")
    p.add_argument("--no-capture", action="store_true",
                   help="Disable neutral tech-structure capture teams.")
    p.add_argument("--no-destroy-tech", action="store_true",
                   help="Disable mid/late-game destroy-tech triggers. When "
                        "enabled (default), the AI shifts from capturing "
                        "tech buildings to destroying them after "
                        "--destroy-tech-delay seconds of elapsed game time.")
    p.add_argument("--destroy-tech-delay", type=int, default=300,
                   help="Seconds of elapsed game time before destroy-tech "
                        "triggers become active (default 300 = 5 min).")
    p.add_argument("--debug-messages", action="store_true",
                   help="Populate every generated AIExt trigger with debug "
                        "MessageDisplay/LogMessage/ValueDisplay/LogWrite quads "
                        "for every emitted tag. Requires AITriggerTypeExt DLL "
                        "with the debug system enabled via "
                        "[Debug].DisplayAIWaveMessages=both in rulesmd.ini.")
    p.add_argument("--no-overview", action="store_true",
                   help="Skip writing overview.txt alongside output files.")
    p.add_argument("--no-coverage", action="store_true",
                   help="Skip the coverage pass (silent trigger dedup + "
                        "one filler wave per unused buildable unit).")
    p.add_argument("--no-gunner", action="store_true",
                   help="Disable pairing Hard late-game infantry waves with "
                        "Gunner=yes vehicles (IFVs).")
    p.add_argument("--taskforce-scale", type=float, default=1.0,
                   metavar="SCALE",
                   help="Multiply taskforce unit counts by this factor (default 1.0). "
                        "E.g. 2.0 doubles all counts: 3xMTNK becomes 6xMTNK. "
                        "BuildLimit=1 units are always kept at exactly 1 regardless. "
                        "Counts are always rounded down (floor) and minimum 1.")
    p.add_argument("--wave-scale", type=float, default=1.0,
                   metavar="N",
                   help="Integer multiplier for wave sizes (1-10, default 1). "
                        "Scales taskforce counts AND updates [AI].RelaxedStray in "
                        "rules output proportionally so larger teams stay cohesive. "
                        "Values above 5 are not recommended for performance reasons.")
    p.add_argument("--general-waves", type=int, default=20,
                   help="General attack waves per faction per difficulty (default 20)")
    p.add_argument("--country-waves", type=int, default=10,
                   help="Country-exclusive waves per country per difficulty (default 10)")
    p.add_argument("--role-pool",
                   nargs="+",
                   metavar="ROLE",
                   default=None,
                   help="Custom role pool for wave generation, space-separated. "
                        "Roles are cycled to fill --general-waves slots. "
                        "Available: RUSH GENERAL_ATTACK ANTI_DEFENSES MINER_KILLER "
                        "FACTORY_KILLER CY_KILLER AA AERIAL NAVAL. "
                        "Example: --role-pool RUSH RUSH GENERAL_ATTACK GENERAL_ATTACK MINER_KILLER")
    p.add_argument("--role-counts",
                   nargs="+",
                   metavar="ROLE:N",
                   default=None,
                   help="Exact counts per role per faction per difficulty. "
                        "Format: ROLE:N. Example: --role-counts RUSH:4 MINER_KILLER:3 "
                        "GENERAL_ATTACK:8 AERIAL:2. Total should equal --general-waves.")
    p.add_argument("--phase-pool",
                   nargs="+",
                   metavar="PHASE",
                   default=None,
                   help="Custom phase pool, cycled across waves. "
                        "Available: Rush Early Mid Late EndGame. "
                        "Default: Rush Early Mid Mid Late Late EndGame")
    p.add_argument("--patrol-ticks", type=int, default=5,
                   help="Guard ticks per patrol stop in emergency defensive "
                        "teams (default 5 = 30 seconds)")
    p.add_argument("--dummy-mode", type=int, default=None, choices=[0,1,2,3,4],
                   help="Dummy prerequisite mode: 0=none 1=present 2=absent "
                        "3=both 4=full mixed. If omitted, prompts interactively.")
    p.add_argument("--max-combo-size", type=int, default=3,
                   help="Maximum building combination size for dummy prerequisites. "
                        "E.g. 3 generates all single, pair, and triple combos. "
                        "Higher values create exponentially more dummies but cover "
                        "more cross-faction scenarios. Default: 3. "
                        "WARNING: 9 buildings at size 9 = thousands of dummies.")
    p.add_argument("--strat-max-combo-size", type=int, default=1,
                   help="Maximum combo size for strategic building dummies "
                        "(CYs/warfactories/shipyards). Default 1 generates only "
                        "single-building dummies plus explicit category combos. "
                        "Set higher for all cross-category combinations. "
                        "WARNING: 9 buildings at size 9 is very slow.")
    p.add_argument("--mixed-scope", choices=['none','auto','selected','all'],
                   default=None,
                   help="Mixed dummy scope (only relevant with --dummy-mode 4). "
                        "If omitted, prompts interactively.")
    p.add_argument("--require-isbasedefense", action="store_true",
                   help="Only include IsBaseDefense=yes buildings in defense "
                        "dummy system (default: include all buildings with Primary=)")
    p.add_argument("--delete-tool", action="store_true",
                   help="Delete all previously generated YR-AI-Tool entries "
                        "before regenerating. If not specified, prompts interactively.")
    p.add_argument("--vanilla-aimd", type=str, default=None,
                   metavar="PATH",
                   help="Path to vanilla YR 1.001 aimd.ini for entry classification "
                        "(determines Vanilla vs Tool vs Mod-added in the entry manager). "
                        "Defaults to vanilla_aimd.ini in the tool directory or CWD. "
                        "Not used as generation input — your mod's aimd.ini is the input.")
    p.add_argument("--skip-cleanup", action="store_true",
                   help="Skip the entry cleanup/deletion step entirely.")
    p.add_argument("--no-strategic", action="store_true",
                   help="Skip strategic conditional triggers (no-AA, no-defense, MCV, etc.)")
    p.add_argument("--no-waves", action="store_true",
                   help="Skip general and country-exclusive wave generation.")
    p.add_argument("--no-dummy", action="store_true",
                   help="Skip all dummy prerequisite generation.")
    p.add_argument("--legacy", action="store_true",
                   help="Enable prompts for the legacy dummy-prerequisite "
                        "system (Ares/Phobos fake-building AI gating). Off "
                        "by default — the AITriggerTypeExt DLL supersedes it. "
                        "--dummy-mode also implies --legacy.")
    p.add_argument("--difficulties", nargs="+",
                   default=["Easy", "Medium", "Hard"],
                   choices=["Easy", "Medium", "Hard"],
                   help="Which difficulty levels to generate for.")
    p.add_argument("--cost-ratio-tolerance", type=float, default=0.05,
                   metavar="PCT",
                   help="Fraction of waves allowed to have cost imbalance "
                        "(default: 0.05 = 5%%). Set to 1.0 to disable enforcement.")
    p.add_argument("--cost-ratio-strict", action="store_true",
                   help="Force all taskforces to have balanced cost distribution. "
                        "Rejects any composition where one unit costs >5x another.")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Skip all interactive prompts, use defaults.")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def detect_extensions(rules_path: Path, aimd_path: Path) -> tuple[bool, bool]:
    """
    Return (ares_detected, phobos_detected).
    Ares: [#include] or SW.AuxBuildings/SW.NegBuildings in rulesmd.ini
    Phobos: script actions >= 10000 in aimd.ini OR LimboDelivery in rulesmd.ini
    The dummy prereq system requires BOTH Ares AND Phobos.
    """
    from .dummy_prereq_generator import detect_ares, detect_phobos
    return detect_ares(rules_path), detect_phobos(aimd_path, rules_path)


# ---------------------------------------------------------------------------
# Entry deletion
# ---------------------------------------------------------------------------

def strip_tool_entries(path: Path, tag: str = "; YR-AI-Tool") -> int:
    """
    Remove all sections and list entries tagged with '; YR-AI-Tool'.
    Returns count of lines removed.
    Works by:
      1. Removing list entries (key=value lines containing the tag)
      2. Removing entire sections whose header line is immediately
         preceded by a '; YR-AI-Tool' comment or whose first entry has the tag
    """
    with path.open(encoding='latin-1') as f:
        lines = f.readlines()

    import re
    section_pat = re.compile(r"^\[([^\]]+)\]")
    result      = []
    i           = 0
    removed     = 0

    while i < len(lines):
        line = lines[i]
        s    = line.strip()

        # Check if this is a section header
        m = section_pat.match(s)
        if m:
            # Look ahead: check if this entire section was generated by tool
            # (first non-blank non-comment line has the tag, or comment before header)
            is_tool_section = False
            # Check line before header
            if result and tag in result[-1]:
                is_tool_section = True
            # Check first entry in section
            if not is_tool_section:
                j = i + 1
                while j < len(lines) and not lines[j].strip().startswith('['):
                    sl = lines[j].strip()
                    if sl and not sl.startswith(';'):
                        if tag in lines[j]:
                            is_tool_section = True
                        break
                    j += 1

            if is_tool_section:
                # Skip the section header
                i += 1
                # Skip all lines until next section
                while i < len(lines) and not section_pat.match(lines[i].strip()):
                    removed += 1
                    i += 1
                # Remove the comment line before header if it was ours
                if result and tag in result[-1]:
                    result.pop()
                    removed += 1
                continue

        # For list entries: remove lines with the tag
        if tag in line and '=' in s and not s.startswith('['):
            removed += 1
            i += 1
            continue

        result.append(line)
        i += 1

    with path.open('w', encoding='latin-1', errors='replace') as f:
        f.writelines(result)

    return removed


def strip_vanilla_entries(aimd_path: Path) -> int:
    """
    Remove all vanilla AI entries from aimd.ini.
    Vanilla entries are those WITHOUT the '; YR-AI-Tool' tag.
    Only removes entries from the AI-specific sections.
    """
    AI_SECTIONS = {
        'TaskForces', 'ScriptTypes', 'TeamTypes',
        'AITriggerTypes', 'AITriggerTypesEnable',
    }
    with aimd_path.open(encoding='latin-1') as f:
        lines = f.readlines()

    import re
    section_pat    = re.compile(r"^\[([^\]]+)\]")
    current_section = None
    result         = []
    removed        = 0

    for line in lines:
        s = line.strip()
        m = section_pat.match(s)
        if m:
            current_section = m.group(1)
            result.append(line)
            continue

        if (current_section in AI_SECTIONS
                and '=' in s
                and not s.startswith(';')
                and '; YR-AI-Tool' not in line):
            removed += 1
            continue

        result.append(line)

    with aimd_path.open('w', encoding='latin-1', errors='replace') as f:
        f.writelines(result)

    return removed


# ---------------------------------------------------------------------------
# Interactive menus
# ---------------------------------------------------------------------------

def prompt_dummy_mode(args) -> int:
    if args.no_dummy:
        return 0
    if args.dummy_mode is not None:
        return args.dummy_mode
    if args.yes:
        return 1  # default: presence only

    print("\nDummy Prerequisite Mode:")
    print("  0. None — skip entirely")
    print("  1. Present only — track buildings that EXIST  [default]")
    print("  2. Missing only — track buildings that are ABSENT")
    print("  3. Both, no mixed")
    print("  4. Full mixed — all combinations including present+absent")
    while True:
        raw = input("Select [1]: ").strip()
        if not raw: return 1
        try:
            n = int(raw)
            if 0 <= n <= 4: return n
        except ValueError:
            pass
        print("  Enter 0-4.")


def prompt_mixed_scope(args, cat) -> tuple[str, list[str]]:
    if args.mixed_scope:
        return args.mixed_scope, []
    if args.yes:
        return 'auto', []

    print("\nMixed Prerequisites Scope:")
    print("  A. None     — no mixed combos")
    print("  B. Auto     — only what triggers need  [default]")
    print("  C. Selected — pick specific buildings")
    print("  D. All      — every combination (WARNING: can be enormous)")
    while True:
        raw = input("Select [B]: ").strip().upper() or 'B'
        if raw == 'A': return 'none', []
        if raw == 'B': return 'auto', []
        if raw == 'C':
            by_cat = cat.by_category()
            from .building_categorizer import DUMMY_BUILDING
            by_cat = {k:v for k,v in by_cat.items() if k != DUMMY_BUILDING}
            print("\nBuildings (enter IDs separated by commas):")
            all_ids = []
            for cname, blds in by_cat.items():
                ids = [b.bid for b in blds]
                all_ids.extend(ids)
                print(f"  {cname}: {', '.join(ids)}")
            raw2 = input("\nIDs [none]: ").strip()
            selected = [t.strip().upper() for t in raw2.split(',')
                        if t.strip().upper() in all_ids]
            return 'selected', selected
        if raw == 'D':
            confirm = input("WARNING: may generate millions of sections. Continue? [y/N]: ")
            if confirm.strip().lower() == 'y':
                return 'all', []
        print("  Enter A, B, C, or D.")


def prompt_wave_counts(args) -> tuple[int, int]:
    if args.yes:
        return args.general_waves, args.country_waves

    # Estimate total triggers: general_waves × difficulties × factions
    # Vanilla aimd.ini has 165 triggers total. YR has hardcoded AI limits.
    # Beyond ~800-1000 total triggers the game's AI evaluator slows down
    # noticeably; beyond ~2000 the AI may effectively stop acting.
    # Recommended: 20-50 general waves per faction per difficulty.
    # With 3 factions × 3 difficulties, 30 waves = 270 total triggers.
    n_factions    = 3   # gdi, nod, thirdside (typical)
    n_difficulties = 3  # Easy, Medium, Hard
    vanilla_total  = 165

    print(f"\nWave counts")
    print(f"  Vanilla aimd.ini has {vanilla_total} total triggers.")
    print(f"  YR's AI evaluator runs every tick — too many triggers causes the")
    print(f"  AI to stop attacking entirely (CPU bottleneck in condition checks).")
    print(f"  Recommended: 20-50 general waves per faction per difficulty.")
    print(f"  (3 factions × 3 difficulties × 30 = 270 total triggers)")

    raw = input(f"  General waves per faction per difficulty [{args.general_waves}]: ").strip()
    general = int(raw) if raw.isdigit() else args.general_waves

    raw = input(f"  Country waves per country per difficulty [{args.country_waves}]: ").strip()
    country = int(raw) if raw.isdigit() else args.country_waves

    estimated = general * n_factions * n_difficulties
    print(f"  Estimated general triggers: ~{estimated:,}")
    if estimated > 1000:
        print(f"  WARNING: {estimated:,} triggers is very high. The AI will likely")
        print(f"  not attack. Recommended max is ~500 total. Consider reducing to")
        print(f"  {max(1, 500 // (n_factions * n_difficulties))} waves per faction per difficulty.")

    return general, country


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def _update_relaxed_stray(rules_path: str, scale: int) -> None:
    """
    Scale [AI].RelaxedStray by the given multiplier.
    RelaxedStray=25.0;50.0 format: two values (normal;charging).
    Both are multiplied by scale. This keeps larger taskforces cohesive
    by allowing units to stray further before being called back.
    """
    import re
    with open(rules_path, 'r', encoding='latin-1') as f:
        content = f.read()

    def scale_val(v: str) -> str:
        try:
            return f'{float(v.strip()) * scale:.1f}'
        except ValueError:
            return v.strip()

    def replace_match(m):
        raw = m.group(1)
        if ';' in raw:
            parts = raw.split(';')
            return 'RelaxedStray=' + ';'.join(scale_val(p) for p in parts)
        return 'RelaxedStray=' + scale_val(raw)

    # Only match RelaxedStray= not on a comment line
    new_content = re.sub(
        r'(?m)^(?![ \t]*;).*RelaxedStray=([^\r\n]+)',
        lambda m: m.group(0).replace(
            'RelaxedStray=' + m.group(1),
            replace_match(m)),
        content
    )
    with open(rules_path, 'w', encoding='latin-1') as f:
        f.write(new_content)


def _inject_superweapons(rules_path: str, inject: dict) -> None:
    """
    Add SuperWeapons= entries to existing INI sections.
    inject: {section_id: [sw_id, ...]}
    Reads the file, finds each section, appends to its SuperWeapons= line
    (or adds one if absent).
    """
    with open(rules_path, 'r', encoding='latin-1') as f:
        lines = f.readlines()

    # Build index: section header line number
    import re
    section_line: dict[str, int] = {}
    for i, line in enumerate(lines):
        m = re.match(r'^\[(\w+)\]', line)
        if m and not line.strip().startswith(';'):
            section_line[m.group(1)] = i

    for section_id, sw_ids in inject.items():
        if section_id not in section_line:
            continue
        start = section_line[section_id]
        # Find end of this section
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if re.match(r'^\[', lines[i]):
                end = i
                break

        # Look for existing SuperWeapons= in this section
        sw_line_idx = None
        for i in range(start, end):
            if lines[i].lower().startswith('superweapons='):
                sw_line_idx = i
                break

        sw_add = ','.join(sw_ids)
        if sw_line_idx is not None:
            # Append to existing
            existing = lines[sw_line_idx].rstrip('\r\n')
            lines[sw_line_idx] = f'{existing},{sw_add}\r\n'
        else:
            # Insert after the section header line
            lines.insert(start + 1, f'SuperWeapons={sw_add}\r\n')
            # Update section_line indices
            for k in section_line:
                if section_line[k] > start:
                    section_line[k] += 1

    with open(rules_path, 'w', encoding='latin-1') as f:
        f.writelines(lines)


def reorder_aimd(path):
    """Rewrite the output aimd.ini in vanilla section order:

        [TaskForces] list      → TaskForce sections
        [ScriptTypes] list     → Script sections
        [TeamTypes] list       → TeamType sections
        [AITriggerTypes]       (inline entries)
        [ID.AIExt] sidecars    (after AITriggerTypes)

    Multiple generators append their sections at EOF in call order, so the
    finished file interleaves types. This pass parses the completed file
    and reassembles it. Label comments directly above a section travel
    with it. Unknown sections are preserved at the end.
    """
    import re as _re
    path = Path(path)
    text = path.read_text(encoding='latin-1')
    lines = text.splitlines(keepends=True)

    HEX_RE   = _re.compile(r'^\[([0-9A-Fa-f]{8}-G)\]\s*(?:;.*)?$')
    AIEXT_RE = _re.compile(r'^\[([0-9A-Fa-f]{8}-G)\.AIExt\]', _re.IGNORECASE)
    LIST_RE  = _re.compile(r'^\[(TaskForces|ScriptTypes|TeamTypes|AITriggerTypes)\]\s*$',
                           _re.IGNORECASE)

    # Split file into blocks: (kind, key, lines)
    # kind: 'preamble', 'list', 'section', 'aiext', 'other'
    blocks = []
    cur = []            # accumulated lines of current block
    cur_kind = 'preamble'
    cur_key = None
    pending_comments = []   # comment/blank lines waiting to attach to next header

    def flush():
        nonlocal cur, cur_kind, cur_key
        if cur:
            blocks.append((cur_kind, cur_key, cur))
        cur = []; cur_kind = None; cur_key = None

    for line in lines:
        s = line.strip()
        m_list  = LIST_RE.match(s)
        m_aiext = AIEXT_RE.match(s)
        m_hex   = HEX_RE.match(s) if not m_aiext else None
        is_header = bool(m_list or m_aiext or m_hex)

        if is_header:
            flush()
            if m_list:
                cur_kind = 'list'; cur_key = m_list.group(1).lower()
            elif m_aiext:
                cur_kind = 'aiext'; cur_key = m_aiext.group(1).upper()
            else:
                cur_kind = 'section'; cur_key = m_hex.group(1).upper()
            cur = pending_comments + [line]
            pending_comments = []
        elif s.startswith('[') and s.endswith(']'):
            # Unknown section type — keep as its own block
            flush()
            cur_kind = 'other'; cur_key = s
            cur = pending_comments + [line]
            pending_comments = []
        elif not s or s.startswith(';'):
            # Comments/blanks between blocks may belong to the NEXT header.
            # Hold them; if content follows instead, they join current block.
            pending_comments.append(line)
        else:
            if pending_comments:
                cur.extend(pending_comments)
                pending_comments = []
            if cur_kind is None:
                cur_kind = 'preamble'
            cur.append(line)
    if pending_comments:
        cur.extend(pending_comments)
    flush()

    # Index blocks
    preamble  = [b for b in blocks if b[0] == 'preamble']
    lists     = {b[1]: b for b in blocks if b[0] == 'list'}
    sections  = [b for b in blocks if b[0] == 'section']
    aiexts    = [b for b in blocks if b[0] == 'aiext']
    others    = [b for b in blocks if b[0] == 'other']

    # Determine each section's type from list membership
    def list_ids(name):
        blk = lists.get(name)
        if not blk:
            return set()
        ids = set()
        for l in blk[2][1:]:
            sl = l.strip()
            if '=' in sl and not sl.startswith(';'):
                v = sl.split('=', 1)[1].split(';')[0].strip().upper()
                if _re.match(r'^[0-9A-F]{8}-G$', v):
                    ids.add(v)
        return ids

    tf_ids   = list_ids('taskforces')
    sc_ids   = list_ids('scripttypes')
    team_ids = list_ids('teamtypes')

    tf_secs, sc_secs, tm_secs, unknown_secs = [], [], [], []
    for b in sections:
        if   b[1] in tf_ids:   tf_secs.append(b)
        elif b[1] in sc_ids:   sc_secs.append(b)
        elif b[1] in team_ids: tm_secs.append(b)
        else:                  unknown_secs.append(b)

    # Dedup list-entry lines (N=ID) so a taskforce/script/team ID appears
    # at most once in its list. Renumbers to keep indexes contiguous.
    for lname in ('taskforces', 'scripttypes', 'teamtypes'):
        blk = lists.get(lname)
        if not blk:
            continue
        seen_ids: set[str] = set()
        header_lines: list[str] = []
        tail_comments: list[str] = []
        kept_ids: list[tuple[str, str]] = []   # (id, trailing_comment_or_empty)
        past_header = False
        for ln in blk[2]:
            s = ln.strip()
            if not past_header and s.lower() == f'[{lname}]':
                header_lines.append(ln)
                past_header = True
                continue
            if not past_header:
                header_lines.append(ln)
                continue
            if not s or s.startswith(';'):
                if not kept_ids:
                    header_lines.append(ln)
                else:
                    tail_comments.append(ln)
                continue
            if '=' in s:
                v = s.split('=', 1)[1]
                trail = ''
                if ';' in v:
                    trail = ' ; ' + v.split(';', 1)[1].strip()
                    v = v.split(';', 1)[0]
                v = v.strip().upper()
                if _re.match(r'^[0-9A-F]{8}-G$', v):
                    if v in seen_ids:
                        continue
                    seen_ids.add(v)
                    kept_ids.append((v, trail))
                    continue
            # unknown line — keep verbatim
            tail_comments.append(ln)
        rebuilt = list(header_lines)
        for i, (tid, trail) in enumerate(kept_ids):
            rebuilt.append(f"{i}={tid}{trail}\r\n")
        rebuilt.extend(tail_comments)
        lists[lname] = ('list', lname, rebuilt)

    # Dedup section blocks by ID (keep first). Multiple writers can each
    # append the same section to the file; we only need one copy.
    def _dedup(secs):
        seen: set[str] = set()
        out = []
        for b in secs:
            if b[1] in seen:
                continue
            seen.add(b[1])
            out.append(b)
        return out
    tf_secs      = _dedup(tf_secs)
    sc_secs      = _dedup(sc_secs)
    tm_secs      = _dedup(tm_secs)
    aiexts       = _dedup(aiexts)
    unknown_secs = _dedup(unknown_secs)

    # Dedup AITriggerTypes inline entries too (same ID = same trigger)
    trig_blk = lists.get('aitriggertypes')
    if trig_blk:
        seen_trigs: set[str] = set()
        kept: list[str] = []
        header_written = False
        for ln in trig_blk[2]:
            s = ln.strip()
            if s.lower().startswith('[aitriggertypes]'):
                if not header_written:
                    kept.append(ln)
                    header_written = True
                continue
            m = _re.match(r'^([0-9A-Fa-f]{8}-G)=', s)
            if m:
                tid = m.group(1).upper()
                if tid in seen_trigs:
                    continue
                seen_trigs.add(tid)
            kept.append(ln)
        lists['aitriggertypes'] = ('list', 'aitriggertypes', kept)

    def emit(out, block_list):
        for _, _, blines in block_list:
            out.extend(blines)
            if blines and not blines[-1].endswith('\n'):
                out.append('\r\n')
            out.append('\r\n') if (blines and blines[-1].strip()) else None

    out: list[str] = []
    for _, _, blines in preamble:
        out.extend(blines)

    for list_name, secs in (('taskforces', tf_secs),
                             ('scripttypes', sc_secs),
                             ('teamtypes', tm_secs)):
        blk = lists.get(list_name)
        if blk:
            out.extend(blk[2])
            if blk[2] and blk[2][-1].strip():
                out.append('\r\n')
        emit(out, secs)

    trig = lists.get('aitriggertypes')
    if trig:
        out.extend(trig[2])
        if trig[2] and trig[2][-1].strip():
            out.append('\r\n')

    emit(out, aiexts)
    emit(out, unknown_secs)
    # [Digest] and any other non-standard sections are intentionally
    # dropped. Digest is a checksum that the game recomputes; leaving
    # a stale value forces YR to reject the file with certain builds.
    # Other 'unknown' hex-form sections are preserved above; here we
    # only skip literal named blocks like [Digest].

    path.write_text(''.join(out), encoding='latin-1')


def main():
    args = parse_args()

    tool_dir = Path(__file__).resolve().parent

    # ── Input file resolution ──────────────────────────────────────────
    # If --rules / --aimd not given, search for them in order:
    #   1. CWD (user's mod directory)
    #   2. Tool directory (vanilla files shipped with the tool)
    def _find_input(arg_val: str | None, *candidates: str) -> Path | None:
        if arg_val:
            return Path(arg_val)
        for name in candidates:
            for base in [Path.cwd() / 'input', Path.cwd(), tool_dir]:
                p = base / name
                if p.exists():
                    return p
        return None

    rules_path = _find_input(args.rules, 'rulesmd.ini', 'vanilla_rulesmd.ini')
    aimd_path  = _find_input(args.aimd,  'aimd.ini',    'vanilla_aimd.ini')

    if rules_path is None or not rules_path.exists():
        print("ERROR: rulesmd.ini not found.")
        print("  Place rulesmd.ini in the current directory, or pass --rules PATH")
        sys.exit(1)
    if aimd_path is None or not aimd_path.exists():
        print("ERROR: aimd.ini not found.")
        print("  Place aimd.ini in the current directory, or pass --aimd PATH")
        sys.exit(1)

    rules_path = rules_path.resolve()
    aimd_path  = aimd_path.resolve()

    # ── Output path resolution ─────────────────────────────────────────
    # Outputs go to <mod_dir>/output/rulesmd.ini and aimd.ini by default.
    # All paths resolved to absolute.
    # SPECIAL CASE: if inputs live under a folder named "input" (i.e. the
    # standard ./input/ ./output/ layout), the output should go to the
    # SIBLING "output" folder, not a nested one. Without this check,
    # ./input/rulesmd.ini would produce ./input/output/rulesmd.ini which
    # is confusing.
    mod_dir = rules_path.parent
    if mod_dir.name.lower() == "input":
        default_out = mod_dir.parent / "output"
    else:
        default_out = mod_dir / "output"

    out_dir   = Path(args.out_dir).resolve()   if args.out_dir   else default_out
    out_rules = Path(args.out_rules).resolve() if args.out_rules else out_dir / "rulesmd.ini"
    out_aimd  = Path(args.out_aimd).resolve()  if args.out_aimd  else out_dir / "aimd.ini"

    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"  YR AI Wave Generator {TOOL_VERSION}")
    print("=" * 60)
    print(f"  Rules:   {rules_path}")
    print(f"  AIMD:    {aimd_path}")
    print(f"  Output:  {out_rules} / {out_aimd}")

    # Warn loudly if the tool is using its bundled vanilla_*.ini as input
    # (i.e. modder placed rules in the wrong location and we silently fell
    # back to the vanilla template — output will be based on vanilla).
    if rules_path.name.startswith('vanilla_'):
        print(f"  ⚠  WARNING: using bundled vanilla rulesmd.ini as input.")
        print(f"     Your modded rulesmd.ini was NOT found in ./input/, ./,")
        print(f"     or the tool directory. Place it at ./input/rulesmd.ini")
        print(f"     or pass --rules PATH to use it. Output will be vanilla-based.")
    if aimd_path.name.startswith('vanilla_'):
        print(f"  ⚠  WARNING: using bundled vanilla aimd.ini as input.")
        print(f"     Same fix — place aimd.ini at ./input/aimd.ini or pass --aimd PATH.")

    # ------------------------------------------------------------------
    # Step 1: Extension detection
    # ------------------------------------------------------------------
    print("\n[1/10] Detecting Ares / Phobos...")
    ares, phobos = detect_extensions(rules_path, aimd_path)
    print(f"  Ares detected:   {'YES' if ares else 'NO'}")
    print(f"  Phobos detected: {'YES' if phobos else 'NO'}")

    # Feature control based on Ares/Phobos detection
    # If either is missing, inform the user and ask per feature group.
    if not ares or not phobos:
        print(f"\n  The following extensions were not detected in your mod files:")
        feature_groups = []
        if not ares:
            print(f"    Ares  — not detected")
            print(f"      Affects: SW.AuxBuildings/NegBuildings (meta-dummies, absence dummies)")
            print(f"               PrerequisiteXxxAlternate (unit-as-prerequisite)")
            feature_groups.append(('ares', 'Ares features (meta-dummies, absence dummies, unit prereqs)'))
        if not phobos:
            print(f"    Phobos — not detected")
            print(f"      Affects: LimboDelivery/LimboKill (entire dummy prereq system)")
            print(f"               Advanced script actions 10000+ (multi-transport loading)")
            feature_groups.append(('phobos', 'Phobos features (dummy prereqs, advanced scripts)'))

        print()
        if args.yes:
            print(f"  Continuing with undetected extensions enabled (--yes mode).")
            print(f"  If your mod does not use them, generated output may be invalid.")
        else:
            for key, label in feature_groups:
                try:
                    raw = input(f"  Enable {label} anyway? [Y/n]: ").strip().lower()
                    if raw == 'n':
                        if key == 'ares':   ares   = False
                        if key == 'phobos': phobos = False
                        print(f"    Disabled.")
                    else:
                        if key == 'ares':   ares   = True
                        if key == 'phobos': phobos = True
                        print(f"    Enabled — ensure your mod supports this.")
                except EOFError:
                    pass

    # ------------------------------------------------------------------
    # Step 2: Parse rulesmd.ini
    # ------------------------------------------------------------------
    print("\n[2/10] Parsing rulesmd.ini...")
    from .rulesmd_parser import RulesMdParser
    parser = RulesMdParser(str(rules_path)).parse()
    factions = list(parser.sides.faction_countries.keys())
    print(f"  Factions: {factions}")
    print(f"  Buildable units: {len(parser.buildable)}")
    print(f"  Naval transports: {len(parser.naval_transports)}")

    # ------------------------------------------------------------------
    # Step 3: Categorize buildings
    # ------------------------------------------------------------------
    print("\n[3/10] Categorizing buildings...")
    from .building_categorizer import BuildingCategorizer
    cat = BuildingCategorizer(str(rules_path))
    cat.scan()
    eligible = cat.exclude_dummies()
    print(f"  Eligible buildings for dummy system: {len(eligible)}")
    if not args.yes:
        cat.print_summary()

    # ------------------------------------------------------------------
    # Step 3b: AI-only unit scan
    # ------------------------------------------------------------------
    print("\n[3b] Scanning for AI-only units...")
    from .ai_unit_scanner import (scan_candidates, prompt_user, _bypass_build_limit,
                                  _analyze_type_list_groups, _prompt_type_list_groups)
    from .rulesmd_parser import IniParser as _IniScan
    _ini_scan    = _IniScan(str(rules_path))
    bypass       = _bypass_build_limit(_ini_scan)

    # Type-list group analysis: detect attachment pools and dummy-only groups
    print("\n[3a] Scanning type-list groups for attachment/dummy patterns...")
    auto_exclude, suspect_groups = _analyze_type_list_groups(
        _ini_scan, str(rules_path))
    group_exclude = _prompt_type_list_groups(suspect_groups, yes_mode=args.yes)
    group_exclude = group_exclude | auto_exclude
    if auto_exclude:
        print(f"  Skipped {len(auto_exclude)} units (visual/mechanical attachments).")
    if group_exclude - auto_exclude:
        print(f"  Skipped {len(group_exclude - auto_exclude)} additional units per your selection.")

    ai_candidates = scan_candidates(str(rules_path), parser, bypass=bypass,
                                    group_exclude=group_exclude)
    ai_unit_ids, exclude_crate_goodies = prompt_user(
        ai_candidates, bypass, yes_mode=args.yes, ini=_ini_scan)

    # Also always include stolen tech units (they ARE buildable when spy infiltrates)
    STOLEN_FLAGS = ['requiresstolenalliedtech','requiresstolensoviettech',
                    'requiresstolenthirdtech']
    for section in ('InfantryTypes','VehicleTypes','AircraftTypes'):
        for uid in _ini_scan.list_from_section(section):
            if not uid: continue
            sec = _ini_scan.section(uid)
            if (any(sec.get(f,'no').lower()=='yes' for f in STOLEN_FLAGS)
                    and uid not in ai_unit_ids
                    and uid not in {r.unit_id for r in parser.buildable}):
                ai_unit_ids.append(uid)

    if ai_unit_ids:
        print(f"  Including {len(ai_unit_ids)} AI-only units.")
        # ScannedUnit doesn't pre-compute faction — let add_ai_units resolve it
        added = parser.add_ai_units(ai_unit_ids, faction_hints={})
        print(f"  Added {len(added)} units to buildable pool with grades recomputed.")
        # TODO: Stolen tech waves (CCOMAND, CIVAN, PTROOP)
        # These are faction-neutral units unlocked when an AI spy infiltrates a
        # Battle Lab. The AI cannot currently trigger the spy mechanic, but a
        # Phobos/Ares dummy prerequisite approach could work:
        #   1. Create a dummy building representing "stolen allied/soviet/third tech"
        #   2. A superweapon fires (triggered by a spy script or randomly) to
        #      spawn the dummy into limbo
        #   3. AITriggerTypes conditioned on owning the dummy unlock stolen tech waves
        # For now, stolen tech units are graded and visible but no waves are
        # generated for them (faction=None means they skip the wave generator pools).

    # ------------------------------------------------------------------
    # Step 4: User menus
    # ------------------------------------------------------------------
    print("\n[4/10] Configuration...")

    # Wave counts
    general_waves, country_waves = prompt_wave_counts(args)

    # Wave scale
    wave_scale = args.wave_scale

    # Per-category scale multipliers + per-type caps (optional)
    scale_by_group = {
        'rush':      args.scale_rush,
        'general':   args.scale_general,
        'artillery': args.scale_artillery,
        'aerial':    args.scale_aerial,
        'naval':     args.scale_naval,
        'defense':   args.scale_defense,
    }
    type_caps = {
        'Infantry': args.cap_infantry,
        'Vehicle':  args.cap_vehicle,
        'Aircraft': args.cap_aircraft,
    }

    if not args.yes and wave_scale == 1.0:
        # Ask MODE first. Global → ask one scale number.
        # Custom → skip the global number and go straight to per-category.
        # None (n) → skip scaling entirely, use default 1.0 for all.
        try:
            raw = input("\n  Scaling mode: (G)lobal, (C)ustom per wave type, or (N)one? [G/c/n]: ").strip().lower()
        except EOFError:
            raw = 'g'

        if raw == 'n':
            # None: keep all wave scales at default 1.0, skip both prompts entirely.
            print("  Scaling disabled — all wave types keep default 1.0 scale.")
        elif raw == 'c':
            print("  Extra multiplier per category. Press Enter to keep 1.0.")
            for key, label in [('rush',      'Rush waves (rush / suicide / infantry rush)'),
                               ('general',   'General attack waves'),
                               ('artillery', 'Artillery / bombard waves'),
                               ('aerial',    'Aerial waves'),
                               ('naval',     'Naval waves'),
                               ('defense',   'Defense / recruitable pool teams')]:
                try:
                    v = input(f"    {label} [{scale_by_group[key]}]: ").strip()
                except EOFError:
                    v = ''
                if v:
                    try:
                        scale_by_group[key] = max(0.1, min(20.0, float(v)))
                    except ValueError:
                        pass
        else:
            # Global: ask ONE scale number. Floats accepted so players can
            # scale DOWN (0.5 = half-size) as well as up. Counts round UP
            # on scale-down so no line vanishes.
            print("  Taskforce size scale (0.1-10, default 1.0 = vanilla-sized).")
            print("  Higher values make larger attack groups.")
            print("  Values below 1 shrink waves; 0.5 = roughly half-size.")
            print("  Values above 5 are not recommended.")
            try:
                raw = input("  Scale [1.0]: ").strip()
                if raw:
                    wave_scale = max(0.1, min(10.0, float(raw)))
            except (ValueError, EOFError):
                wave_scale = 1.0

    if not args.yes:
        # Specialist boost — only meaningful when scaling > 1.
        effective_scale = wave_scale * max(scale_by_group.values(), default=1.0)
        if effective_scale > 1:
            print("")
            print("  A vanilla wave might have '4 Grizzly + 1 Prism' — the Grizzly")
            print("  is the main muscle and the Prism is a specialist support unit.")
            print("  When you scale the wave up, do you want the ratio kept the same,")
            print("  or should specialist units get proportionally more of the wave?")
            print("")
            print("     0.0 = keep vanilla ratio    → 12 Grizzly + 3 Prism   (at 3x)")
            print("     0.35 = slight boost         → 11 Grizzly + 4 Prism   (at 3x)")
            print("     1.0 = equal counts          → 8 Grizzly + 8 Prism    (at 3x)")
            print("")
            try:
                v = input(f"  Boost specialists in scaled waves [{args.scale_flatten}]: ").strip()
            except EOFError:
                v = ''
            if v:
                try:
                    args.scale_flatten = max(0.0, min(1.0, float(v)))
                except ValueError:
                    pass
        try:
            raw = input("  Set per-type unit caps per taskforce? [y/N]: ").strip().lower()
        except EOFError:
            raw = 'n'
        if raw == 'y':
            print("  Hard cap on total units of each type per taskforce (0 = uncapped).")
            for key in ('Infantry', 'Vehicle', 'Aircraft'):
                try:
                    v = input(f"    Max {key} [{type_caps[key]}]: ").strip()
                except EOFError:
                    v = ''
                if v.isdigit():
                    type_caps[key] = int(v)
    # Drop no-op entries so the fast path in _apply_taskforce_scale stays hot
    scale_by_group = {k: v for k, v in scale_by_group.items() if v != 1.0}
    type_caps      = {k: v for k, v in type_caps.items() if v > 0}

    # Deletion selection — done HERE (before generation) so the user
    # specifies what to clean out before new content is added, not after.
    # We store the selection now and apply it in step 5 after copying files.
    _pending_delete_all  = False   # True = delete all (V+T+M)
    _pending_delete_cats = None    # set of category names, or None = skip
    delete_tool = args.delete_tool

    if not args.yes and not args.skip_cleanup:
        print(f"\n  Remove existing AI entries from output files?")
        print(f"  Recommended when re-running on the same aimd.ini.")
        print(f"  (V=Vanilla  T=Tool-generated  M=Mod-added  all=everything)")
        try:
            raw = input("  Categories to delete [all/V/T/M/none]: ").strip().upper()
        except EOFError:
            raw = "ALL"
        if raw in ("", "ALL"):
            _pending_delete_all = True
            delete_tool = True
        elif raw not in ("NONE", "N", "NO"):
            cats = set()
            if "V" in raw: cats.add("VANILLA")
            if "T" in raw: cats.add("TOOL")
            if "M" in raw: cats.add("MOD")
            if cats:
                _pending_delete_cats = cats
                delete_tool = bool("TOOL" in cats)
    elif args.yes:
        delete_tool = True
        _pending_delete_all = True

    # Debug messages prompt — asks once whether to populate every generated
    # AIExt trigger with debug MessageDisplay/LogMessage/ValueDisplay/LogWrite
    # quads. Requires AITriggerTypeExt DLL with debug support enabled via
    # [Debug].DisplayAIWaveMessages in rulesmd.ini.
    if not args.yes and not args.debug_messages:
        try:
            raw = input(
                "\n  Populate generated AIExt triggers with debug messages? "
                "[y/N]: ").strip().lower()
        except EOFError:
            raw = 'n'
        if raw in ('y', 'yes'):
            args.debug_messages = True
            print("  Debug messages will be added. Requires the "
                  "AITriggerTypeExt DLL and [Debug].DisplayAIWaveMessages=both "
                  "in rulesmd.ini.")

    # Dummy prerequisite mode — legacy system, off by default.
    # Only shown when the user opts in via --legacy or --dummy-mode.
    dummy_mode = 0
    mixed_scope = 'auto'
    selected_blds = []
    user_declined_dummy = False   # True when user explicitly answered "n"
    legacy_active = args.legacy or (args.dummy_mode is not None)
    if legacy_active and not args.no_dummy:
        if args.dummy_mode is not None:
            dummy_mode = args.dummy_mode
        elif not args.yes:
            print(f"\n  Dummy Prerequisite System (legacy):")
            print(f"  Older method of gating AI triggers via fake buildings.")
            print(f"  Requires rulesmd.ini modification and Ares + Phobos.")
            print(f"  The AITriggerTypeExt .dll handles this more cleanly.")
            print(f"  Do you want to use the dummy prerequisite system? [y/N]: ", end='')
            try:
                raw = input().strip().lower()
                if raw == 'y':
                    dummy_mode = prompt_dummy_mode(args)
                    if dummy_mode == 4:
                        mixed_scope, selected_blds = prompt_mixed_scope(args, cat)
                else:
                    user_declined_dummy = True   # explicit "n" or enter
            except EOFError:
                user_declined_dummy = True

    print(f"\n  Configuration summary:")
    if legacy_active:
        print(f"    Dummy mode:     {dummy_mode}")
        print(f"    Mixed scope:    {mixed_scope}")
    print(f"    General waves:  {general_waves}/faction/difficulty")
    print(f"    Country waves:  {country_waves}/country/difficulty")
    print(f"    Wave scale:     {wave_scale}×")
    print(f"    Difficulties:   {args.difficulties}")
    print(f"    Patrol ticks:   {args.patrol_ticks}")
    print(f"    Clean previous: {delete_tool}")
    if wave_scale > 5:
        print(f"    WARNING: Wave scale {wave_scale} is above 5 — may impact performance.")

    if not args.yes:
        raw = input("\n  Proceed? [Y/n]: ").strip().lower()
        if raw == 'n':
            sys.exit(0)

    # ------------------------------------------------------------------
    # Step 5: Copy input files to output, manage existing entries
    # ------------------------------------------------------------------
    print("\n[5/10] Preparing output files...")
    shutil.copy(rules_path, out_rules)
    shutil.copy(aimd_path,  out_aimd)

    # Prepend version stamp to output aimd so it's visible in the file
    # and in debug.log (game logs the first line of aimd.ini on load)
    import datetime
    stamp = (f"; YR AI Wave Generator {TOOL_VERSION} - "
             f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\r\n")
    with open(out_aimd, 'r+', encoding='latin-1') as f:
        original = f.read()
        f.seek(0)
        f.write(stamp + original)

    if not args.skip_cleanup:
        # Locate vanilla baseline files
        # Locate vanilla baseline file for entry classification.
        # Searches tool directory (where cli.py lives) then CWD.

        def _find_vanilla(arg_val, filename):
            if arg_val:
                return Path(arg_val)
            for candidate in [tool_dir / filename, Path.cwd() / filename]:
                if candidate.exists():
                    return candidate
            return tool_dir / filename   # return expected path even if missing

        vanilla_aimd_path  = _find_vanilla(args.vanilla_aimd,  'vanilla_aimd.ini')

        if not vanilla_aimd_path.exists():
            print(f"  NOTE: vanilla_aimd.ini not found.")
            print(f"        Searched: {tool_dir / 'vanilla_aimd.ini'}")
            print(f"                  {Path.cwd() / 'vanilla_aimd.ini'}")
            print(f"        Without it, ALL entries will appear as 'Mod-added'.")
            print(f"        Copy vanilla_aimd.ini alongside cli.py to enable")
            print(f"        vanilla detection.")
            vanilla_fp = {}
        else:
            from .entry_manager import _fingerprint
            vanilla_fp = _fingerprint(str(vanilla_aimd_path))
            print(f"  Vanilla baseline: {len(vanilla_fp)} entries fingerprinted")

        from .entry_manager import (parse_entries, resolve_orphans, apply_deletions)
        entries = parse_entries(str(out_aimd), vanilla_fp)

        v = sum(1 for e in entries if e.category.name == 'VANILLA')
        t = sum(1 for e in entries if e.category.name == 'TOOL')
        m = sum(1 for e in entries if e.category.name == 'MOD')
        print(f"  Classified: {v} vanilla  {t} tool-generated  {m} mod-added")

        # Apply the deletion selection made during configuration
        to_del: set[str] = set()
        if _pending_delete_all:
            to_del = {e.section_id for e in entries}
        elif _pending_delete_cats:
            to_del = {e.section_id for e in entries
                      if e.category.name in _pending_delete_cats}

        if to_del:
            to_com = resolve_orphans(to_del, entries)
            nd, nc = apply_deletions(str(out_aimd), to_del, to_com)
            print(f"  Deleted {nd} entries, commented out {nc} orphans")
        else:
            print(f"  No entries deleted.")


    # ------------------------------------------------------------------
    # Step 6: Initialize registries
    # ------------------------------------------------------------------
    print("\n[6/10] Initializing registries...")
    from .id_registry import GlobalIDRegistry
    from .taskforce_generator import IDRegistry
    from .script_generator import ScriptRegistry
    from .team_generator import TeamRegistry
    from .trigger_generator import TriggerRegistry, build_side_map

    global_reg = GlobalIDRegistry.from_aimd(str(out_aimd))
    tf_reg     = IDRegistry.from_aimd(str(out_aimd), global_reg)
    sc_reg     = ScriptRegistry.from_aimd(str(out_aimd), global_reg)
    team_reg   = TeamRegistry.from_aimd(str(out_aimd), global_reg)
    trig_reg   = TriggerRegistry.from_aimd(str(out_aimd), global_reg)
    side_map   = build_side_map(str(rules_path))
    print(f"  Registered {global_reg.count} existing IDs")

    # ------------------------------------------------------------------
    # Step 7: Dummy prerequisites
    # ------------------------------------------------------------------
    dummy_gen   = None
    def_gen     = None
    strat_dummy = None   # category-level dummies for strategic triggers

    # Strategic building dummies — skipped if user declined dummy system
    # or passed --no-dummy / --no-strategic.
    if legacy_active and not args.no_strategic and not user_declined_dummy and not args.no_dummy:
        from .dummy_prereq_generator import FullDummyPrereqGenerator
        from .building_categorizer import BuildingCategorizer

        _bc       = BuildingCategorizer(str(rules_path))
        _bldgs    = _bc.scan()
        STRAT_CATS = {'Construction Yards', 'War Factories', 'Shipyards',
                      'Power Plants'}
        _strat_blds = [(b.idx, b.bid) for b in _bldgs
                       if b.category in STRAT_CATS]

        # Also include buildings that power PoweredUnit= units (e.g. GAROBO→ROBO).
        # These need presence dummies so waves containing powered units can
        # condition on the AI owning the building that activates them.
        _ini_tmp = parser.ini
        _powered_bld_ids = set()
        for uid, bld_ids in parser.powered_unit_map.items():
            for bid in bld_ids:
                _powered_bld_ids.add(bid)
        # Assign stable indices for any new buildings not already in _strat_blds
        _existing_ids = {bid for _, bid in _strat_blds}
        _next_idx     = max((idx for idx, _ in _strat_blds), default=-1) + 1
        for bid in sorted(_powered_bld_ids - _existing_ids):
            _strat_blds.append((_next_idx, bid))
            _next_idx += 1

        if _strat_blds:
            from .dummy_prereq_generator import DummyPrereqGenerator
            strat_dummy = FullDummyPrereqGenerator(
                str(rules_path),
                max_combo_size   = args.strat_max_combo_size,
                mode             = 3,     # presence + absence
                use_ares_include = phobos,
                mixed_scope      = 'auto',
            )
            strat_dummy.target_blds = sorted(_strat_blds)

            cy_ids   = [b.bid for b in _bldgs if b.category == 'Construction Yards']
            wfac_ids = [b.bid for b in _bldgs if b.category == 'War Factories']
            sy_ids   = [b.bid for b in _bldgs if b.category == 'Shipyards']
            pp_ids   = [b.bid for b in _bldgs if b.category == 'Power Plants']

            # Category-level absence combos (faction-neutral)
            if len(cy_ids) > 1:
                strat_dummy.request_absence_combo(cy_ids)
            if len(sy_ids) > 1:
                strat_dummy.request_absence_combo(sy_ids)
            # "enemy has no power" → all Powered=yes defenses are blind
            if len(pp_ids) > 1:
                strat_dummy.request_absence_combo(pp_ids)

            # Mixed combos for enemy_no_cy_attack
            for wf in wfac_ids:
                strat_dummy.request_mixed_combo(
                    present_ids=[wf], absent_ids=cy_ids)

            # First pass: single-building presence + absence dummies,
            # plus explicitly requested category-level absence combos.
            # Strategic trigger generator needs these for _dummy_exists() checks.
            DummyPrereqGenerator.generate_all(strat_dummy)
            strat_dummy._generate_absence_all()

    if dummy_mode > 0 and not args.no_dummy and legacy_active:
        print("\n[7/10] Generating dummy prerequisites...")
        from .dummy_prereq_generator import FullDummyPrereqGenerator
        dummy_gen = FullDummyPrereqGenerator(
            str(rules_path),
            max_combo_size   = args.max_combo_size,
            mode             = dummy_mode,
            use_ares_include = phobos,
            mixed_scope      = mixed_scope,
            selected_for_mixed = selected_blds,
        )
        dummy_gen.target_blds = sorted(
            (b.idx, b.bid) for b in eligible)

        from .defense_dummy_generator import DefenseDummyGenerator
        def_gen = DefenseDummyGenerator(
            str(rules_path),
            require_isbasedefense = args.require_isbasedefense)
        def_gen.scan()
        def_gen.generate()
        print(f"  Defense dummies: {len(def_gen.buildings)} buildings, "
              f"{len(def_gen.spawners)} spawners, "
              f"{len(def_gen.killers)} killers")

    else:
        if legacy_active:
            print("\n[7/10] Skipping dummy prerequisites.")

    # ------------------------------------------------------------------
    # Step 8: Strategic triggers
    # ------------------------------------------------------------------
    # Ensure strat_dummy is used as dummy_gen if no other dummy_gen exists
    if strat_dummy and not dummy_gen:
        dummy_gen = strat_dummy

    # Meta-dummy system for units with Prerequisite.Negative
    # Runs after dummy_gen is finalized (needs bld_to_dmpr mapping)
    meta_gen = None
    if dummy_gen and ares and phobos:
        from .meta_prereq_generator import MetaPrereqGenerator
        meta_gen = MetaPrereqGenerator(
            str(rules_path), parser, dummy_gen, global_reg)
        meta_count = meta_gen.generate(opt_in=not args.yes)
        if meta_count:
            print(f"  Meta-dummies: {meta_count} generated "
                  f"({len(meta_gen.neg_presence_dummies)} neg presence dummies)")

    strat_gen = None
    if legacy_active and not args.no_strategic and not user_declined_dummy and not args.no_dummy:
        from .strategic_trigger_generator import (
            StrategicTriggerGenerator)
        strat_gen = StrategicTriggerGenerator(
            parser, global_reg, tf_reg, sc_reg, team_reg, trig_reg,
            side_map,
            dummy_gen    = dummy_gen,
            defense_gen  = def_gen,
            patrol_ticks = args.patrol_ticks,
            seed         = args.seed,
            ares         = ares,
        )
        strat_gen.generate(
            factions     = factions,
            difficulties = args.difficulties,
            phobos       = phobos,
        )

        # Second pass: generate only mixed combos (after trigger generator
        # has requested them via request_mixed_combo).
        # Presence and absence were already generated in the first pass.
        if strat_dummy:
            strat_dummy._generate_mixed_all()
            mixed_count = len(getattr(strat_dummy, '_mixed_dummies', []))
            print(f"  [Strategic dummies] "
                  f"{len(strat_dummy.dummy_blds)} presence, "
                  f"{len(strat_dummy.absence_dummies)} absence, "
                  f"{mixed_count} mixed")

        # Merge strategic dummies into dummy_gen for writing
        # (only if they are separate objects — strat_dummy may already be dummy_gen)
        if strat_dummy and dummy_gen and strat_dummy is not dummy_gen:
            existing_bids = {d.section_id for d in dummy_gen.dummy_blds} | \
                            {d.section_id for d in dummy_gen.absence_dummies}
            for ad in strat_dummy.absence_dummies:
                if ad.section_id not in existing_bids:
                    dummy_gen.absence_dummies.append(ad)
            for asp in strat_dummy.absence_spawners:
                dummy_gen.absence_spawners.append(asp)
            for ak in strat_dummy.absence_killers:
                dummy_gen.absence_killers.append(ak)
            for pd in strat_dummy.dummy_blds:
                if pd.section_id not in existing_bids:
                    dummy_gen.dummy_blds.append(pd)
            for sp in strat_dummy.spawner_sws:
                dummy_gen.spawner_sws.append(sp)
            for ks in strat_dummy.killer_sws:
                dummy_gen.killer_sws.append(ks)
        print(f"  {strat_gen.summary()}")
    else:
        print("\n[8/10] Skipping strategic triggers.")

    # ------------------------------------------------------------------
    # Step 9: Wave triggers
    # ------------------------------------------------------------------
    wave_gen = None
    if not args.no_waves:
        print("\n[9/10] Generating wave triggers...")

        # Run mod scanners for AIExt condition generation
        print("  Scanning mod for AIExt conditions (AA, infrastructure, spies)...")
        from .aiext_generator import scan_mod, AIExtConfig
        scan_result = scan_mod(args.rules, parser=parser,
                               sides_registry=parser.sides)
        print(f"    AA structures detected:  {len(scan_result.aa.aa_structure_ids)}")
        print(f"    ConYards detected:       {len(scan_result.rebuild.conyard_ids)}")
        print(f"    Spy units detected:      {len(scan_result.spies.spies)}")
        print(f"    HVT tier3 buildings:     {len(scan_result.hvt.tier3)}")

        aiext_cfg = AIExtConfig()   # use defaults; TODO: expose to CLI args

        from .wave_generator import WaveGenerator, WaveGeneratorConfig
        wave_gen = WaveGenerator(
            parser, global_reg, tf_reg, sc_reg, team_reg, trig_reg,
            side_map, phobos=phobos, seed=args.seed,
            meta_map=meta_gen.unit_to_meta if meta_gen else {},
            scan_result=scan_result)
        # Parse role pool / role counts / phase pool
        from .wave_generator import DEFAULT_ROLE_POOL, DEFAULT_PHASE_POOL
        role_pool  = args.role_pool  if args.role_pool  else list(DEFAULT_ROLE_POOL)
        phase_pool = args.phase_pool if args.phase_pool else list(DEFAULT_PHASE_POOL)
        role_counts = {}
        if args.role_counts:
            for item in args.role_counts:
                try:
                    role, n = item.split(':')
                    role_counts[role.upper()] = int(n)
                except ValueError:
                    print(f"  WARNING: ignoring malformed --role-counts entry '{item}' "
                          f"(expected ROLE:N)")
        # NOTE: CrateGoodie=yes does not gate buildability — it only controls
        # whether a unit can be granted as a crate reward. It is never used
        # to exclude units from taskforce composition.
        crate_goodie_ids: frozenset = frozenset()

        wave_config = WaveGeneratorConfig(
            general_waves_per_difficulty = general_waves,
            country_waves_per_difficulty = country_waves,
            difficulties                 = args.difficulties,
            role_pool                    = role_pool,
            phase_pool                   = phase_pool,
            role_counts                  = role_counts,
            cost_ratio_tolerance         = args.cost_ratio_tolerance,
            cost_ratio_strict            = args.cost_ratio_strict,
            taskforce_scale              = args.taskforce_scale * wave_scale,
            scale_by_group               = scale_by_group,
            type_caps                    = type_caps,
            oversized_threshold          = args.oversized_threshold,
            gunner_hard_infantry         = not args.no_gunner,
            scale_flatten                = args.scale_flatten,
            diversify_mono               = not args.no_diversify,
            aiext_config                 = aiext_cfg,
            exclude_unit_ids             = crate_goodie_ids,
            debug_messages               = args.debug_messages,
        )
        # Enable debug-tag population BEFORE generate() runs so every
        # collect() call gets its debug quads populated.
        wave_gen.aiext_writer.set_debug_messages(args.debug_messages)
        wave_gen.generate(wave_config)
        aiext_count = wave_gen.aiext_writer.count
        print(f"  {wave_gen.summary()}")
        if aiext_count > 0:
            print(f"  AIExt conditions attached: {aiext_count} triggers")

        # Transport compositions (BFRT, HTK+TERROR, naval)
        from .wave_generator import TransportCompositionGenerator
        transport_gen = TransportCompositionGenerator(
            parser, tf_reg, sc_reg, team_reg, trig_reg,
            side_map, phobos=phobos, seed=args.seed,
            taskforce_scale=args.taskforce_scale * wave_scale
                            * scale_by_group.get('general', 1.0))
        # Transport-composition triggers (deployfire, suicide, naval, engineer,
        # ...) previously got NO AIExt conditions — this generator's
        # _aiext_builder was always None. Share the WaveGenerator's builder +
        # writer so build() runs for these roles too and the conditions land in
        # the writer WaveGenerator flushes (transport.write_all doesn't emit
        # AIExt, so no duplicate sections).
        if wave_gen is not None and wave_gen._aiext_builder is not None:
            transport_gen._aiext_builder = wave_gen._aiext_builder
            transport_gen.aiext_writer   = wave_gen.aiext_writer

        transport_gen.generate_bfrt_waves(args.difficulties)
        transport_gen.generate_htk_terror_waves(args.difficulties)
        transport_gen.generate_naval_transport_waves(args.difficulties)
        transport_gen.generate_naval_general_waves(args.difficulties)
        transport_gen.generate_dtruck_waves(args.difficulties)
        transport_gen.generate_suicide_rush_waves(args.difficulties)
        transport_gen.generate_deployfire_waves(args.difficulties)
        transport_gen.generate_parasite_waves(args.difficulties)
        transport_gen.generate_mind_control_waves(args.difficulties)
        transport_gen.generate_chaos_waves(args.difficulties)
        transport_gen.generate_sqd_naval_waves(args.difficulties)
        transport_gen.generate_dog_waves(args.difficulties)
        transport_gen.generate_defensive_sniper_waves(args.difficulties)
        transport_gen.generate_hero_companion_waves(args.difficulties)
        transport_gen.generate_country_hero_waves(args.difficulties)
        transport_gen.generate_magnetron_patrol_waves(args.difficulties)
        transport_gen.generate_electric_assault_waves(args.difficulties)
        transport_gen.generate_ifv_passenger_waves(args.difficulties)
        transport_gen.generate_opentopped_waves(args.difficulties)
        transport_gen.generate_engineer_rush_waves(args.difficulties)
        print(f"  {transport_gen.summary()}")

        # Pool teams — base defense / recruitment reserve. Without these,
        # the AI has no idle recruitable units sitting in Guard mode and
        # must build every attack team's composition from scratch, which
        # slows down how quickly taskforces form. Per DeeZire: pool teams
        # use Guard (11,5) not Area Guard, so members stay recruitable.
        print("  Generating pool teams (base defense / recruitment reserve)...")
        from .taskforce_generator import TaskforceEntry
        team_gen = wave_gen.team_gen   # reuse the instance WaveGenerator already created
        active_factions = sorted(parser.sides.faction_countries.keys())
        pool_teams = team_gen.generate_pool_teams(
            active_factions, tf_reg, sc_reg, str(out_aimd),
            taskforce_scale=args.taskforce_scale * wave_scale
                            * scale_by_group.get('defense', 1.0))
        if pool_teams:
            print(f"    Pool teams: {len(pool_teams)} "
                  f"({len(active_factions)} factions x 3 tiers x 2 categories)")
            # NOTE: pool taskforce sections and the pool Guard script are
            # written by team_gen.write_to_ini() itself (invoked from
            # wave_gen.write_all via the shared team_gen instance). Do NOT
            # also append them to wave_gen.taskforces/scripts — that caused
            # every pool taskforce + the pool script to be written twice.
            wave_gen.teams.extend(pool_teams)
            # Generate triggers for pool teams — without triggers the engine
            # never spawns them regardless of Autocreate=yes.
            pool_triggers = wave_gen.trig_gen.generate_for_teams(pool_teams)
            wave_gen.triggers.extend(pool_triggers)
        else:
            print(f"    No pool teams generated (no eligible units per faction/tier).")

        # Procedural extras: base patrols + neutral tech capture + destroy-tech
        if (not args.no_patrols or not args.no_capture
                or not args.no_destroy_tech):
            from .procedural_scripts import generate_procedural_extras
            extra = generate_procedural_extras(
                wave_gen, parser,
                patrols=not args.no_patrols,
                capture=not args.no_capture,
                destroy_tech=not args.no_destroy_tech,
                destroy_tech_delay_seconds=args.destroy_tech_delay)
            if (extra['patrol_teams'] or extra['capture_teams']
                    or extra.get('destroy_tech_teams', 0)):
                print(f"  Procedural extras: {extra['patrol_teams']} patrol teams, "
                      f"{extra['capture_teams']} tech-capture teams, "
                      f"{extra.get('destroy_tech_teams', 0)} destroy-tech teams")

        # Coverage pass:
        # 1) silently drop trigger duplicates that require MORE prerequisite
        #    buildings than another trigger producing the same composition
        # 2) ensure every legally-buildable unit shows up in at least one
        #    taskforce (low-priority "coverage filler" waves)
        if not args.no_coverage:
            try:
                from .coverage_tracker import apply_coverage
                cov = apply_coverage(wave_gen, parser)
                if cov['silent_dedup'] or cov['coverage_fillers']:
                    print(f"  Coverage: dropped {cov['silent_dedup']} redundant "
                          f"triggers, added {cov['coverage_fillers']} filler waves")
            except Exception as e:
                print(f"  WARNING: coverage pass failed: {e}")
    else:
        print("\n[9/10] Skipping wave triggers.")
        transport_gen = None

    # ------------------------------------------------------------------
    # Step 10: Write output
    # ------------------------------------------------------------------
    print("\n[10/10] Writing output files...")

    # Dummy prereqs: write output
    # Note: strat_dummy was already generated and merged into dummy_gen above.
    # For the main dummy_gen (mode > 0), generate_all() still needs to run
    # for the non-naval buildings. If dummy_gen IS strat_dummy (mode=0 case),
    # generate_all() was already called — skip.
    if dummy_gen:
        if dummy_gen is not strat_dummy:
            # Main dummy_gen for non-naval buildings, not yet generated
            dummy_gen.generate_all()
        written = dummy_gen.write_all(
            str(rules_path),
            output_path = str(out_rules),
            output_dir  = str(out_dir),
        )
        print(f"  Dummy prereq files:")
        for key, path in written.items():
            import os
            size = os.path.getsize(path) // 1024
            print(f"    {key}: {Path(path).name} ({size} KB)")

    if def_gen:
        def_ini = out_dir / "ai_prereq_defenses.ini"
        def_gen.write_to_file(str(def_ini), str(out_rules))
        print(f"  Defense dummies: {def_ini.name}")

    if meta_gen and meta_gen.meta_dummies:
        meta_ini = out_dir / "ai_prereq_meta.ini"
        meta_gen.write(str(meta_ini))
        print(f"  Meta dummies: {meta_ini.name} "
              f"({len(meta_gen.meta_dummies)} meta + "
              f"{len(meta_gen.neg_presence_dummies)} neg presence)")
        # Inject chain SWs onto DMPR dummy sections in rules_output.ini
        inject = meta_gen.get_superweapons_to_inject()
        if inject:
            _inject_superweapons(str(out_rules), inject)
            print(f"  Injected chain SWs into {len(inject)} DMPR dummy sections")

    # Strategic triggers
    if strat_gen:
        strat_gen.write_all(str(out_aimd), str(out_aimd))

    # Wave triggers
    if wave_gen:
        wave_gen.write_all(str(out_aimd), str(out_aimd))
    if transport_gen:
        transport_gen.write_all(str(out_aimd), str(out_aimd))

    # Cleanup: remove orphaned [XYZ.AIExt] sections whose base trigger XYZ
    # no longer exists in the file (leftover from previous tool runs).
    from .entry_manager import cleanup_orphan_sidecars
    n_orphans = cleanup_orphan_sidecars(str(out_aimd))
    if n_orphans > 0:
        print(f"  Cleaned up {n_orphans} orphaned .AIExt sidecar section(s)")

    # Trailing semicolon (game never reads last line)
    for path in [out_aimd, out_rules]:
        with path.open('a', encoding='latin-1') as f:
            f.write(';\r\n')

    # Update RelaxedStray if wave-scale > 1
    if wave_scale > 1:
        if wave_scale > 5:
            print(f"\n  WARNING: --wave-scale {wave_scale} is above 5. "
                  f"Large taskforces may cause performance issues in-game.")
        _update_relaxed_stray(str(out_rules), wave_scale)
        print(f"  RelaxedStray scaled ×{wave_scale} in {out_rules.name}")

    # Reassemble output aimd in vanilla section order:
    # TaskForces list+sections → Scripts → Teams → AITriggerTypes → .AIExt
    try:
        reorder_aimd(out_aimd)
        print(f"  Reordered {out_aimd.name} to vanilla section layout")
    except Exception as e:
        print(f"  WARNING: could not reorder {out_aimd.name}: {e}")

    # Human-readable overview: what units the tool detected, why they scored
    # the way they did, and where each shows up in the generated waves.
    if not args.no_overview:
        try:
            from .overview_report import write_overview, collect_trigger_counts
            overview_path = out_aimd.parent / "overview.txt"
            tc = collect_trigger_counts(wave_gen)
            write_overview(overview_path, parser,
                           scan_result=getattr(wave_gen, 'scan_result', None),
                           gunner_hard_infantry=not args.no_gunner,
                           trigger_counts=tc,
                           wave_gen=wave_gen)
            print(f"  Overview written: {overview_path.name}")
        except Exception as e:
            print(f"  WARNING: could not write overview: {e}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("  Complete!")
    print("=" * 60)
    print(f"  {out_rules.name}")
    print(f"  {out_aimd.name}")

    total_triggers = 0
    if strat_gen:  total_triggers += len(strat_gen.triggers)
    if wave_gen:   total_triggers += wave_gen._generated_count['general'] + \
                                     wave_gen._generated_count['country']
    print(f"\n  Total triggers generated: {total_triggers}")
    if wave_gen:
        print(f"    General:  {wave_gen._generated_count['general']}")
        print(f"    Country:  {wave_gen._generated_count['country']}")
    if strat_gen:
        print(f"    Strategic: {len(strat_gen.triggers)}")
    print()

    # ── Move input files to input/ subfolder ──────────────────────────
    # Only move if:
    #   - File is in the mod directory (not the tool dir)
    #   - File is not one of the vanilla system files (vanilla_aimd.ini,
    #     vanilla_rulesmd.ini) — those stay alongside the tool scripts
    #   - File is not the output file itself
    vanilla_names = {'vanilla_aimd.ini', 'vanilla_rulesmd.ini'}
    input_dir = mod_dir / "input"
    for src, label in [(rules_path, 'rulesmd.ini'), (aimd_path, 'aimd.ini')]:
        if (src.parent == mod_dir
                and src.name not in vanilla_names
                and src.resolve() != out_rules.resolve()
                and src.resolve() != out_aimd.resolve()):
            input_dir.mkdir(parents=True, exist_ok=True)
            dest = input_dir / src.name
            try:
                shutil.move(str(src), str(dest))
                print(f"  Moved {label}: {src.name} → input/{src.name}")
            except Exception as e:
                print(f"  NOTE: Could not move {label} to input/: {e}")


if __name__ == "__main__":
    main()
