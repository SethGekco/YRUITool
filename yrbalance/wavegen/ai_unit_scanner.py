"""
ai_unit_scanner.py

Scans InfantryTypes, VehicleTypes, AircraftTypes for units that are
not in parser.buildable but may be intended for AI use.

Three detection buckets presented to the user:

  Bucket A — TechLevel < 1
    Units with TechLevel=0 or negative are excluded from the tech slider.
    Includes civilians, props, scripted units, and some AI-only units.

  Bucket B — BuildLimit=0 (only when AllowBypassBuildLimit allows it)
    BuildLimit=0 means nobody can build the unit normally (limit of zero).
    If AllowBypassBuildLimit=yes for any difficulty, AI at that difficulty
    can bypass this and build them anyway.
    If AllowBypassBuildLimit=no,no,no (all difficulties), these units are
    silently blacklisted — the AI cannot build them under any circumstance.

  Bucket C — Impossible prerequisites
    Units whose Prerequisite= contains at least one token that no human
    player can ever satisfy:
      - Nonexistent building (no section in INI)
      - TechLevel=-1 building
      - Building owned by no playable country (Owner - ForbiddenHouses
        has no intersection with playable countries)
    GenericPrerequisites groups are expanded: a group is impossible only
    if ALL buildings in its expansion are individually impossible.
    Standard vanilla aliases (RADAR, POWER, etc.) are also expanded.
    AI ignores prerequisites entirely, so these units are AI-reachable
    while being human-unreachable.

The user sees approximate counts and sample names from each bucket,
then chooses which buckets (and optionally which units) to include.

TODO: Support Ares Prerequisite extensions:
  - Prerequisite.Negative (exclusion prereqs)
  - Prerequisite.List# (alternative prerequisite lists)
  - List index range filtering (allow user to specify e.g. WP1–WP500 only)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from .rulesmd_parser import PREREQ_ALIAS_KEYS

# Units that are never usable in skirmish regardless of TL or prereqs.
# These are hardcoded vanilla IDs that have no useful combat role.
VANILLA_BLACKLIST = {
    # Animals
    "COW","ALL","CAML","POLARB","JOSH","SLAV",
    # Civilians
    "CIV1","CIV2","CIV3","CIVA","CIVB","CIVC","CIVP",
    "CIVBBP","CIVBF","CIVBFM","CIVBTM","CIVBM",
    "CIVSF","CIVSFM","CIVSTM",
    # Campaign-only / placeholder
    "HOWI","UTNK","VLAD","XCOMET","YDUM","APACHE",
    # Misc non-combat
    "PENTGEN","PRES","SSRV","VLADIMIR","CLNT","ARND","STLN",
}


@dataclass
class ScannedUnit:
    unit_id:   str
    name:      str
    category:  str        # 'Infantry' | 'Vehicle' | 'Aircraft'
    tech_level: int
    build_limit: int
    prereq:    str
    bucket_a:  bool = False   # TL < 1
    bucket_b:  bool = False   # BuildLimit=0
    bucket_c:  bool = False   # impossible prereq
    is_crate_goodie: bool = False  # CrateGoodie=yes — only obtainable via crates
    suspicious: bool = False  # possible dummy/clone — needs user review
    suspicious_reason: str = ''
    cost_override: int | None = None  # cost from detected clone original
    faction:   str | None = None


def _playable_countries(ini) -> frozenset[str]:
    """Return all countries belonging to any non-engine faction."""
    skip = {'civilian', 'mutant'}
    result = set()
    for faction, val in (ini.section('Sides') or {}).items():
        if faction.lower() in skip:
            continue
        for c in val.split(','):
            c = c.strip()
            if c:
                result.add(c)
    return frozenset(result)


def _generic_prereqs(ini) -> dict[str, str]:
    """Return {GROUP_NAME_UPPER: raw_value} from [GenericPrerequisites]."""
    gp = ini.section('GenericPrerequisites') or {}
    return {k.upper(): v for k, v in gp.items()}


def _fully_expand_token(token: str, gp_upper: dict,
                         visited: frozenset = frozenset()) -> frozenset:
    """
    Recursively expand a prereq token through GenericPrerequisites groups
    until only concrete building IDs remain.
    Handles circular references via visited set.
    Returns frozenset of concrete building IDs.
    """
    upper = token.upper()
    if upper in visited:
        return frozenset()   # circular
    if upper not in gp_upper:
        return frozenset({token})   # concrete building ID
    val  = gp_upper[upper]
    blds = [b.strip() for b in val.split(',') if b.strip()]
    if not blds:
        return frozenset()
    new_visited = visited | {upper}
    result = set()
    for bid in blds:
        result |= _fully_expand_token(bid, gp_upper, new_visited)
    return frozenset(result)


def _is_building_impossible(bid: str, ini, playable: frozenset[str],
                             player_delivered: frozenset[str] = frozenset()) -> bool:
    """
    True if a human player can never own this building.

    TechLevel rules:
      < 1   → below human tech slider range; AI-only
      1–10  → normal; human and AI can build
      >= 11 → engine hard cap; impossible for everyone including AI

    Also impossible if no section defined, or no playable country can own it.
    Player-deliverable (SW.AllowPlayer=yes) buildings are never impossible.
    """
    if bid in player_delivered:
        return False
    s = ini.section(bid)
    if not s:
        return True
    try:
        tl = int(s.get('techlevel', '-1') or '-1')
    except ValueError:
        tl = -1
    if tl < 1 or tl >= 11:
        return True
    owner_raw = s.get('owner', '')
    fh_raw    = s.get('forbiddenhouses', '')
    owners    = {o.strip() for o in owner_raw.split(',') if o.strip()}
    forbidden = {f.strip() for f in fh_raw.split(',') if f.strip()}
    allowed   = owners - forbidden
    return not bool(allowed & playable)


def _prereq_alternates(ini) -> dict[str, list[str]]:
    """
    Load Ares PrerequisiteXxxAlternate entries from [General].

    Format: Prerequisite<Token>Alternate=<unit_id>[,<unit_id>...]
    These specify that owning the listed unit(s) satisfies the prerequisite
    token in addition to the buildings in [GenericPrerequisites].

    Returns {TOKEN_UPPER: [unit_id, ...]}
    """
    gen = ini.section('General') or {}
    alts: dict[str, list[str]] = {}
    for k, v in gen.items():
        kl = k.lower()
        if not (kl.startswith('prerequisite') and kl.endswith('alternate')):
            continue
        token = k[len('prerequisite'):-len('alternate')].upper()
        if not token or not v:
            continue
        units = [u.strip() for u in v.split(',') if u.strip()]
        if units:
            alts[token] = units
    return alts


def _is_token_impossible(token: str, ini, gp: dict,
                          playable: frozenset[str],
                          player_delivered: frozenset[str] = frozenset(),
                          prereq_alts: dict | None = None) -> bool:
    """
    Check a single prereq token. Expansion order:
      1. Standard vanilla aliases → expand via [General].PrerequisiteX
      2. GenericPrerequisites groups → recursively expand to concrete IDs
      3. Literal building ID

    Additionally checks Ares PrerequisiteXxxAlternate entries — if owning
    a unit satisfies this token, and that unit is player-deliverable, the
    token is NOT impossible.

    A group/alias is impossible only if ALL concrete expansions are impossible
    AND no alternate unit is player-obtainable.
    """
    upper = token.upper()

    # Check PrerequisiteXxxAlternate first — Ares feature that allows a unit
    # to satisfy a prerequisite token in addition to buildings.
    # If any alternate unit is player-obtainable, this token is satisfiable.
    if prereq_alts is None:
        prereq_alts = _prereq_alternates(ini)
    if upper in prereq_alts:
        from .sw_chain_walker import SWChainWalker, Reachability
        _walker = SWChainWalker(ini, playable)
        for alt_uid in prereq_alts[upper]:
            if alt_uid in player_delivered:
                return False   # satisfiable via alternate unit (building)
            # Check via SW chain walker — handles units delivered by SWs,
            # paradropped, or otherwise player-obtainable
            result = _walker._is_unit_obtainable(alt_uid, frozenset(), 0)
            if result.reachability == Reachability.POSSIBLE:
                return False   # alternate unit is obtainable

    if upper in PREREQ_ALIAS_KEYS:
        gen_key = PREREQ_ALIAS_KEYS[upper].lower()
        gen_val = (ini.section('General') or {}).get(gen_key, '')
        expansion = [b.strip() for b in gen_val.split(',') if b.strip()]
        if not expansion:
            return True
        return all(_is_building_impossible(b, ini, playable, player_delivered)
                   for b in expansion)

    if upper in gp:
        concrete = _fully_expand_token(token, gp)
        if not concrete:
            return True
        return all(_is_building_impossible(b, ini, playable, player_delivered)
                   for b in concrete)

    return _is_building_impossible(token, ini, playable, player_delivered)


def _has_impossible_prereq(prereq_str: str, ini, gp: dict,
                             playable: frozenset[str],
                             player_delivered: frozenset[str] = frozenset(),
                             prereq_alts: dict | None = None) -> bool:
    """True if any prereq token is impossible for a human to satisfy."""
    if not prereq_str.strip():
        return False
    if prereq_alts is None:
        prereq_alts = _prereq_alternates(ini)
    for token in prereq_str.split(','):
        token = token.strip()
        if not token:
            continue
        if _is_token_impossible(token, ini, gp, playable, player_delivered,
                                 prereq_alts):
            return True
    return False


def _bypass_build_limit(ini) -> tuple[bool, bool, bool]:
    """
    Read [GlobalControls].AllowBypassBuildLimit=hard,medium,easy.
    Returns (hard_bypass, medium_bypass, easy_bypass).

    If the tag is absent entirely, assume AI CAN bypass (all True) —
    the restriction only applies when the tag is explicitly present and
    set to 'no'. If present but a difficulty entry is missing, that
    difficulty also defaults to True (bypass allowed).
    """
    gc  = ini.section('GlobalControls') or {}
    raw = gc.get('allowbypassbuildlimit', None)

    # Tag absent entirely → AI CANNOT bypass on any difficulty.
    # Only an explicit AllowBypassBuildLimit= entry grants bypass.
    if raw is None or raw.strip() == '':
        return False, False, False

    parts = [p.strip().lower() for p in raw.split(',')]

    def to_bool(s):
        # Explicit 'yes'/'true'/'1' → True; anything else (including missing) → False
        return s in ('yes', 'true', '1')

    hard   = to_bool(parts[0]) if len(parts) > 0 else False
    medium = to_bool(parts[1]) if len(parts) > 1 else False
    easy   = to_bool(parts[2]) if len(parts) > 2 else False
    return hard, medium, easy


def _attachment_unit_ids(ini) -> frozenset:
    """
    Collect all unit IDs referenced in AttachmentX.TechnoType= tags
    (X = 0, 1, 2, ...) by scanning the raw INI file.

    The IniParser strips text after ';' as comments, but some mods
    use ';' as a separator inside these values. Raw scan handles both.

    Units listed as attachments are visual/mechanical add-ons that
    spawn on a host unit — they are never independently buildable
    or meaningful combat units. Silently excluded from all buckets.
    """
    import re
    attachment_ids = set()
    try:
        with ini.path.open(encoding='latin-1', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(';'):
                    continue
                # Match Attachment0.TechnoType= ... AttachmentN.TechnoType=
                m = re.match(r'Attachment\d+[.]TechnoType\s*=\s*(.+)', line, re.IGNORECASE)
                if m:
                    val = m.group(1)
                    # Split on ; (used both as comment and separator)
                    for part in val.split(';'):
                        part = part.strip()
                        if part:
                            attachment_ids.add(part)
    except (OSError, IOError):
        pass
    return frozenset(attachment_ids)


# Locomotor GUID that marks attachment/stationary units (Phobos attachment system)
_ATTACHMENT_LOCOMOTOR_GUID = '{C5D54B98-8C98-4275-8CE4-EF75CB0CBE3E}'


def _is_hard_blacklisted(uid: str, ini) -> bool:
    """
    True if a unit should be silently excluded from ALL scanner buckets.
    Only uses objective, non-name-based signals:
      - Spawned=yes: spawn-child units (created by parent, not built)
      - Speed=0: immobile units (mines, decorative vehicles, scenery)
      - Locomotor = attachment GUID: Phobos attachment locomotor
    Explicit AttachmentX.TechnoType= references are handled separately
    via _attachment_unit_ids(). Name/ID pattern matching is NOT used
    since naming conventions vary by mod.
    """
    s = ini.section(uid)
    if not s:
        return False
    if s.get('spawned', 'no').lower() == 'yes':
        return True
    try:
        speed = int(s.get('speed', '1') or '1')
        if speed == 0:
            return True
    except ValueError:
        pass
    loco = s.get('locomotor', '').strip()
    if loco.upper() == _ATTACHMENT_LOCOMOTOR_GUID.upper():
        return True
    return False


def _player_deliverable_buildings(ini) -> frozenset:
    """
    Buildings a human player can receive via LimboDelivery SW
    (SW.AllowPlayer=yes). These are NOT impossible for humans to own.
    Buildings delivered ONLY via SW.AllowPlayer=no remain impossible.
    """
    result = set()
    sw_sec = ini.section('SuperWeaponTypes') or {}
    for k, swid in sw_sec.items():
        swid = swid.strip() if swid else ''
        if not swid:
            continue
        s = ini.section(swid)
        if not s:
            continue
        types_raw = s.get('limbodelivery.types', '')
        if not types_raw:
            continue
        if s.get('sw.allowplayer', 'yes').lower() != 'yes':
            continue
        for bid in types_raw.split(','):
            bid = bid.strip()
            if bid:
                result.add(bid)
    return frozenset(result)


def _find_clone_original(uid: str, ini,
                          buildable_ids: frozenset) -> tuple[str, int] | None:
    """
    Detect if uid is a Cost=0 clone of a real unit by matching Image= and stats.

    A unit is a suspected clone if:
      - It has Cost=0
      - It has Image=X where X is a known buildable unit's section ID
      - Its Speed, Strength, and Sight are within 15% of unit X

    Returns (original_uid, original_cost) or None if not a clone.
    The original's cost is used for taskforce cost-ratio balancing.

    Note: real units use their section ID as the implicit image (no Image= tag
    needed) — e.g. HTNK uses the HTNK voxel by default. So Image=HTNK on
    another unit is a clear signal it's copying HTNK.
    """
    s = ini.section(uid)
    if not s:
        return None
    try:
        cost = int(s.get('cost', '1') or '1')
    except ValueError:
        cost = 1
    if cost != 0:
        return None

    image = s.get('image', '').strip()
    if not image or image.upper() == uid.upper():
        return None  # no Image= or self-referencing

    orig_id = image
    if orig_id not in buildable_ids:
        return None  # Image target isn't a real buildable unit

    orig_sec = ini.section(orig_id)
    if not orig_sec:
        return None

    # Stat similarity check (within 15%)
    def close(a_raw, b_raw):
        try:
            a, b = int(a_raw or '0'), int(b_raw or '0')
            if a == 0 and b == 0:
                return True
            if a == 0 or b == 0:
                return False
            return abs(a - b) / max(a, b) <= 0.15
        except (ValueError, ZeroDivisionError):
            return False

    stats_match = (
        close(s.get('speed','0'),    orig_sec.get('speed','0'))    and
        close(s.get('strength','0'), orig_sec.get('strength','0')) and
        close(s.get('sight','0'),    orig_sec.get('sight','0'))
    )
    if not stats_match:
        return None

    try:
        orig_cost = int(orig_sec.get('cost', '0') or '0')
    except ValueError:
        orig_cost = 0

    return orig_id, orig_cost


def _is_attachment_dummy_by_properties(uid: str, ini) -> tuple[bool, str]:
    """
    Detect units that are likely attachment dummies by objective properties,
    NOT by name. Returns (is_suspicious, reason).

    A unit is suspicious (not hard-blacklisted) if it has:
      - Literal Prerequisite=IMPOSSIBLE AND (cost ≤ 1 OR no primary weapon)

    This catches attachment-style dummies that aren't explicitly listed in
    AttachmentX.TechnoType= tags. Presented to user for confirmation.
    """
    s = ini.section(uid)
    if not s:
        return False, ''

    prereq = s.get('prerequisite', '').strip().upper()
    if prereq != 'IMPOSSIBLE':
        return False, ''

    try:
        cost = int(s.get('cost', '1') or '1')
    except ValueError:
        cost = 1

    has_weapon = bool(s.get('primary', '').strip())

    if cost <= 1:
        return True, f'Prerequisite=IMPOSSIBLE and cost={cost}'
    if not has_weapon:
        return True, 'Prerequisite=IMPOSSIBLE and no primary weapon'

    return False, ''


def _analyze_type_list_groups(ini, rules_path: str
                               ) -> tuple[frozenset, list[dict]]:
    """
    Scan VehicleTypes/InfantryTypes/AircraftTypes for structural patterns
    in their index keys. Groups entries by shared letter prefix and detects
    clusters separated by large numeric gaps.

    For each group:
      - AUTO-EXCLUDE: has ≥1 confirmed attachment AND zero human-buildable units
        → silently add all group units to the exclude set
      - PROMPT: has ≥1 confirmed attachment AND ≥1 human-buildable unit (mixed),
        OR zero human-buildable AND zero confirmed (pure AI/dummy pool)
        → return as a suspect entry for user review

    Returns:
      (auto_exclude_ids, suspect_groups)
      auto_exclude_ids: frozenset of unit IDs to silently exclude
      suspect_groups:   list of dicts for user-facing prompts
    """
    import re

    # Raw scan for confirmed attachment IDs (handles semicolon separators)
    confirmed_attach = set()
    try:
        with open(rules_path, encoding='latin-1', errors='replace') as f:
            raw = f.read()
        # Strip comment lines before scanning to avoid false positives
        clean_lines = [l for l in raw.splitlines()
                       if l.strip() and not l.strip().startswith(';')]
        clean_raw = '\n'.join(clean_lines)
        for m in re.finditer(
                r'Attachment\d+[.]TechnoType\s*=\s*([^\r\n]+)', clean_raw, re.IGNORECASE):
            for part in m.group(1).split(';'):
                p = part.strip()
                if p:
                    confirmed_attach.add(p)
    except (OSError, IOError):
        pass

    playable = _playable_countries(ini)
    ATTACH_LOCO = '{C5D54B98-8C98-4275-8CE4-EF75CB0CBE3E}'

    def _hard_bl(uid):
        s = ini.section(uid)
        if not s: return False
        if s.get('spawned', 'no').lower() == 'yes': return True
        try:
            if int(s.get('speed', '1') or '1') == 0: return True
        except ValueError:
            pass
        return s.get('locomotor', '').strip().upper() == ATTACH_LOCO.upper()

    def _human_buildable(uid):
        s = ini.section(uid)
        if not s: return False
        try:
            tl = int(s.get('techlevel', '-1') or '-1')
        except ValueError:
            tl = -1
        if tl < 1 or tl >= 11: return False
        return not _is_building_impossible(uid, ini, playable)

    def _key_prefix(k):
        m = re.match(r'^([a-zA-Z]+)', k)
        return m.group(1).lower() if m else ''

    def _key_num(k):
        m = re.match(r'^[a-zA-Z]*(\d+)', k)
        return int(m.group(1)) if m else -1

    auto_exclude  = set()
    suspect_groups = []

    for sect in ('VehicleTypes', 'InfantryTypes', 'AircraftTypes'):
        sec = ini.section(sect) or {}
        if not sec:
            continue

        # Group by letter prefix
        groups: dict[str, list[tuple[str, str]]] = {}
        for k, v in sec.items():
            uid = v.strip() if v else ''
            if not uid:
                continue
            prefix = _key_prefix(k)
            groups.setdefault(prefix, []).append((k, uid))

        for prefix, entries in groups.items():
            if len(entries) < 2:
                continue  # single-entry groups not worth analyzing

            n_confirm  = sum(1 for _, u in entries if u in confirmed_attach)
            n_blacklist = sum(1 for _, u in entries if _hard_bl(u))
            n_human    = sum(1 for _, u in entries if _human_buildable(u))
            n_total    = len(entries)

            # Detect numeric gaps (may indicate sub-groups)
            nums = sorted(_key_num(k) for k, _ in entries if _key_num(k) >= 0)
            gaps = ([nums[i+1] - nums[i] for i in range(len(nums) - 1)]
                    if len(nums) > 1 else [])
            max_gap = max(gaps) if gaps else 0

            if n_confirm > 0 and n_human == 0:
                # All confirmed or impossible — auto-exclude entire group
                for _, uid in entries:
                    auto_exclude.add(uid)
                suspect_groups.append({
                    'section':    sect,
                    'prefix':     prefix,
                    'total':      n_total,
                    'confirmed':  n_confirm,
                    'human':      n_human,
                    'ai_only':    n_total - n_confirm - n_blacklist - n_human,
                    'action':     'auto_exclude',  # visual/mechanical attachments
                    'units':      [uid for _, uid in entries],
                    'max_gap':    max_gap,
                })

            elif n_confirm > 0 and n_human > 0:
                # Mixed — prompt user
                suspect_groups.append({
                    'section':    sect,
                    'prefix':     prefix,
                    'total':      n_total,
                    'confirmed':  n_confirm,
                    'human':      n_human,
                    'ai_only':    n_total - n_confirm - n_blacklist - n_human,
                    'action':     'prompt',
                    'units':      [uid for _, uid in entries],
                    'human_units':[uid for _, uid in entries if _human_buildable(uid)],
                    'max_gap':    max_gap,
                })

            elif n_confirm == 0 and n_human == 0 and n_total >= 5:
                # No confirmed attachments, no human-buildable — pure dummy pool
                # Flag if it looks like a structured list (low gap, tight grouping)
                if max_gap <= 10:
                    suspect_groups.append({
                        'section':    sect,
                        'prefix':     prefix,
                        'total':      n_total,
                        'confirmed':  n_confirm,
                        'human':      n_human,
                        'ai_only':    n_total - n_confirm - n_blacklist - n_human,
                        'action':     'prompt_dummy',  # non-player-buildable group
                        'units':      [uid for _, uid in entries],
                        'max_gap':    max_gap,
                    })

    return frozenset(auto_exclude), suspect_groups


def _prompt_type_list_groups(suspect_groups: list[dict],
                              yes_mode: bool = False) -> frozenset:
    """
    Present suspect type-list groups to the user and ask whether to include
    them in wave generation. Returns frozenset of unit IDs to exclude.
    """
    if not suspect_groups:
        return frozenset()

    # Auto-excluded groups: report quietly
    auto    = [g for g in suspect_groups if g['action'] == 'auto_exclude']
    prompts = [g for g in suspect_groups
               if g['action'] in ('prompt', 'prompt_dummy')]

    if auto:
        print(f"\n  [Unit list scanner] Skipping {len(auto)} group(s) "
              f"(confirmed visual/mechanical attachments, not combat units):")
        for g in auto:
            print(f"    [{g['section']}] key prefix '{g['prefix']}': "
                  f"{g['total']} units → not included in waves")

    if not prompts:
        return frozenset()

    print(f"\n  [Unit list scanner] {len(prompts)} group(s) need your input.")
    print(f"  These groups share a common key prefix in the type list and")
    print(f"  contain units not normally buildable by any player.")
    print(f"  You can choose whether to include them in AI attack waves.")
    excluded = set()

    for i, g in enumerate(prompts, 1):
        print(f"\n  Group {i}/{len(prompts)}  [{g['section']}]  "
              f"key prefix: '{g['prefix']}'  ({g['total']} units)")

        if g['action'] == 'prompt':
            # Mixed: some player-buildable, some not
            print(f"    {g['human']} of these are buildable by players.")
            print(f"    {g['total'] - g['human']} are not buildable by any player.")
            print(f"    Player-buildable units (will always be kept):")
            for uid in g.get('human_units', [])[:4]:
                print(f"      {uid}")
            if len(g.get('human_units', [])) > 4:
                print(f"      ... and {len(g['human_units']) - 4} more")
            question = (f"    Include the {g['total'] - g['human']} "
                        f"non-player-buildable units in AI waves?")
        else:
            # Pure non-player group
            print(f"    None of these are buildable by any player.")
            print(f"    Sample units: {g['units'][:4]}")
            if len(g['units']) > 4:
                print(f"    ... and {len(g['units']) - 4} more")
            question = f"    Include these {g['total']} units in AI waves?"

        if yes_mode:
            print(f"{question}")
            if g['action'] == 'prompt_dummy':
                excluded.update(g['units'])
                print(f"    → Not included (--yes mode default)")
            else:
                non_human = [u for u in g['units']
                             if u not in g.get('human_units', [])]
                excluded.update(non_human)
                print(f"    → Not included ({len(non_human)} units, --yes mode default)")
        else:
            print(question)
            try:
                ans = input("    [y=include / n=skip / s=show all] > ").strip().lower()
            except EOFError:
                ans = 'n'

            if ans == 's':
                for uid in g['units']:
                    print(f"      {uid}")
                try:
                    ans = input("    [y=include / n=skip] > ").strip().lower()
                except EOFError:
                    ans = 'n'

            if ans != 'y':
                # Exclude (default: skip non-player-buildable)
                if g['action'] == 'prompt_dummy':
                    excluded.update(g['units'])
                else:
                    non_human = [u for u in g['units']
                                 if u not in g.get('human_units', [])]
                    excluded.update(non_human)

    return frozenset(excluded)


def scan_candidates(rules_path: str, parser,
                    bypass: tuple[bool,bool,bool] = (False,False,False),
                    group_exclude: frozenset = frozenset()
                    ) -> list[ScannedUnit]:
    """
    Scan all type lists for units not already in parser.buildable.
    Returns a list of ScannedUnit with bucket flags set.
    """
    from .rulesmd_parser import IniParser
    ini = IniParser(rules_path)

    buildable_ids    = {r.unit_id for r in parser.buildable}
    playable         = _playable_countries(ini)
    gp               = _generic_prereqs(ini)
    player_delivered = _player_deliverable_buildings(ini)
    prereq_alts      = _prereq_alternates(ini)
    attachment_ids   = _attachment_unit_ids(ini)  # silently excluded
    buildable_id_set = frozenset(r.unit_id for r in parser.buildable)

    # Build SW chain walker for resolving impossible-building prereqs
    from .sw_chain_walker import SWChainWalker, Reachability
    walker = SWChainWalker(ini, playable)

    # Pre-compute which impossible buildings are SW-deliverable
    # and which countries can receive them. Cache results.
    _sw_reachable_cache: dict[str, frozenset[str] | None] = {}
    # None = unreachable, frozenset = reachable by these countries (empty = all)

    def _sw_reachable(bid: str) -> frozenset[str] | None:
        """
        None if not reachable via SW, frozenset of countries if reachable.
        Empty frozenset = reachable by all countries.
        """
        if bid in _sw_reachable_cache:
            return _sw_reachable_cache[bid]
        result = walker.can_player_obtain(bid)
        if result.reachability == Reachability.POSSIBLE:
            val = result.countries  # frozenset, possibly empty (= all countries)
        else:
            val = None
        _sw_reachable_cache[bid] = val
        return val

    def _token_truly_impossible(token: str) -> bool:
        """
        Extended impossible check: after standard impossible check,
        also try SW chain walking for buildings that fail normal checks.
        Returns True only if BOTH normal check AND SW chain fail.
        """
        if not _is_token_impossible(token, ini, gp, playable, player_delivered,
                                     prereq_alts):
            return False
        # Standard check says impossible — try SW chain walker
        # Need to find the concrete building IDs this token resolves to
        upper = token.upper()
        from .rulesmd_parser import PREREQ_ALIAS_KEYS
        if upper in PREREQ_ALIAS_KEYS:
            gen_key = PREREQ_ALIAS_KEYS[upper].lower()
            gen_val = (ini.section('General') or {}).get(gen_key, '')
            blds = [b.strip() for b in gen_val.split(',') if b.strip()]
        elif upper in gp:
            blds = list(_fully_expand_token(token, gp))
        else:
            blds = [token]
        # If ANY building in the expansion is SW-reachable, token is possible
        for bid in blds:
            if _sw_reachable(bid) is not None:
                return False
        return True

    results: list[ScannedUnit] = []
    seen: set[str] = set()

    for sect, cat in [('InfantryTypes','Infantry'),
                      ('VehicleTypes','Vehicle'),
                      ('AircraftTypes','Aircraft')]:
        sec_data = ini.section(sect)
        if not sec_data:
            continue
        # Collect ALL values from the type list — not just numerically-keyed.
        # Mods use non-numeric keys like WP#, AIWP#, WPR# etc. which the
        # standard list_from_section() would skip.
        all_ids = set()
        for k, v in sec_data.items():
            uid = v.strip() if v else ''
            if uid:
                all_ids.add(uid)

        for uid in all_ids:
            if uid in seen or uid in buildable_ids:
                continue
            seen.add(uid)

            if uid in VANILLA_BLACKLIST:
                continue
            if uid in attachment_ids:
                continue  # attachment dummy — silently excluded
            if uid in group_exclude:
                continue  # excluded by type-list group analysis
            if _is_hard_blacklisted(uid, ini):
                continue  # Spawned=yes, Speed=0, or attachment locomotor

            s = ini.section(uid)
            if not s:
                continue  # stub entry, no definition

            name = s.get('name', uid)

            try:
                tl = int(s.get('techlevel', '-1') or '-1')
            except ValueError:
                tl = -1

            try:
                bl = int(s.get('buildlimit', '1') or '1')
            except ValueError:
                bl = 1

            prereq = s.get('prerequisite', '')

            unit = ScannedUnit(
                unit_id    = uid,
                name       = name,
                category   = cat,
                tech_level = tl,
                build_limit = bl,
                prereq     = prereq,
            )

            # Bucket A: TechLevel < 1
            if tl < 1:
                unit.bucket_a = True

            # CrateGoodie=yes — only obtainable via crates
            crate_goodie = s.get('crategoodie', 'no').strip().lower() == 'yes'
            if crate_goodie:
                unit.is_crate_goodie = True

            # Bucket B: BuildLimit=0
            # Only flag if at least one difficulty allows AI to bypass the limit.
            # If AllowBypassBuildLimit=no,no,no, AI cannot build these at all —
            # treat as a hard blacklist (silently excluded).
            if bl == 0 and any(bypass):
                unit.bucket_b = True
            elif bl == 0 and not any(bypass):
                continue  # no bypass possible — silently excluded

            # Bucket C: impossible prereq
            # Uses SW chain walker: buildings impossible to build normally
            # but SW-deliverable to players are treated as possible.
            if prereq:
                for tok in prereq.split(','):
                    tok = tok.strip()
                    if tok and _token_truly_impossible(tok):
                        unit.bucket_c = True
                        break

            # Suspicious: likely attachment dummy by objective properties
            # (not blacklisted, but flagged for user review)
            if not (unit.bucket_a or unit.bucket_b or unit.bucket_c):
                is_susp, reason = _is_attachment_dummy_by_properties(uid, ini)
                if is_susp:
                    unit.suspicious = True
                    unit.suspicious_reason = reason

            # Cost=0 clone detection: find original for cost balancing
            clone = _find_clone_original(uid, ini, buildable_id_set)
            if clone:
                orig_id, orig_cost = clone
                unit.suspicious = True
                unit.suspicious_reason = (
                    f'Cost=0 clone of [{orig_id}] (cost={orig_cost})')
                unit.cost_override = orig_cost

            # Only include if in at least one bucket or suspicious.
            # NOTE: CrateGoodie=yes does NOT gate buildability (it only controls
            # crate reward eligibility) and is never by itself a reason to flag
            # a unit as a candidate outside the normal buildable pool.
            if unit.bucket_a or unit.bucket_b or unit.bucket_c or unit.suspicious:
                results.append(unit)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Bucket grouping helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_uid_to_key(ini) -> dict[str, tuple[str, str]]:
    """Map unit_id → (type_list_section, key) from all type list sections."""
    mapping = {}
    for sect in ('VehicleTypes','InfantryTypes','AircraftTypes','BuildingTypes'):
        sec = ini.section(sect) or {}
        for k, v in sec.items():
            uid = v.strip() if v else ''
            if uid and uid not in mapping:
                mapping[uid] = (sect, k)
    return mapping


def _key_prefix(k: str) -> str:
    import re
    m = re.match(r'^([a-zA-Z]+)', k)
    return m.group(1).lower() if m else ''


def _key_num(k: str) -> int:
    import re
    m = re.match(r'^[a-zA-Z]*(\d+)', k)
    return int(m.group(1)) if m else -1


def _split_into_clusters(entries: list, max_gap: int = 20) -> list[list]:
    """
    Split a list of (key, unit) entries into numeric sub-clusters
    where consecutive numeric indices differ by more than max_gap.
    Entries without a numeric key stay as a single cluster at the end.
    """
    keyed   = [(k, u) for k, u in entries if _key_num(k) >= 0]
    no_num  = [(k, u) for k, u in entries if _key_num(k) < 0]
    keyed.sort(key=lambda x: _key_num(x[0]))

    clusters = []
    current  = []
    for item in keyed:
        if not current:
            current.append(item)
        elif _key_num(item[0]) - _key_num(current[-1][0]) > max_gap:
            clusters.append(current)
            current = [item]
        else:
            current.append(item)
    if current:
        clusters.append(current)
    if no_num:
        clusters.append(no_num)
    return clusters


def _group_by_prefix(units: list['ScannedUnit'],
                      uid_to_key: dict,
                      max_gap: int = 20
                      ) -> list[dict]:
    """
    Group units by type-list index prefix, splitting large numeric gaps
    into sub-clusters. Returns list of group dicts sorted by size desc.
    """
    import re
    from collections import defaultdict

    prefix_entries = defaultdict(list)
    for u in units:
        if u.unit_id in uid_to_key:
            sect, k = uid_to_key[u.unit_id]
            pfx = _key_prefix(k)
            prefix_entries[(sect, pfx)].append((k, u))
        else:
            prefix_entries[('?', '')].append(('?', u))

    groups = []
    for (sect, pfx), entries in prefix_entries.items():
        clusters = _split_into_clusters(entries, max_gap)
        for cluster in clusters:
            nums = sorted(_key_num(k) for k,_ in cluster if _key_num(k) >= 0)
            rng  = f"{nums[0]}-{nums[-1]}" if len(nums) > 1 else (str(nums[0]) if nums else '?')
            groups.append({
                'section':  sect,
                'prefix':   pfx,
                'range':    rng,
                'units':    [u for _, u in cluster],
                'label':    (f"[{sect}] key-prefix '{pfx}' ({rng})"
                              if pfx else f"[{sect}] numeric ({rng})"),
            })

    groups.sort(key=lambda g: -len(g['units']))
    return groups


def _find_impossible_token(uid: str, ini, gp: dict,
                            playable: set, player_del: frozenset) -> str:
    """
    Return the first prerequisite token that makes uid impossible for players.
    Used to group bucket C units by WHY they are impossible.
    """
    s = ini.section(uid)
    if not s:
        return 'UNKNOWN'
    prereq = s.get('prerequisite', '').strip()
    if not prereq:
        return 'NO_PREREQ'
    for tok in prereq.split(','):
        tok = tok.strip()
        if not tok:
            continue
        tok_upper = tok.upper()
        if tok_upper == 'IMPOSSIBLE':
            return 'IMPOSSIBLE'
        if tok_upper in {k.upper() for k in gp}:
            expanded = _fully_expand_token(tok, gp)
            if expanded and all(
                _is_building_impossible(b, ini, playable) and b not in player_del
                for b in expanded
            ):
                return tok
        else:
            if _is_building_impossible(tok, ini, playable) and tok not in player_del:
                return tok
    return 'MIXED'


def _group_by_token(units: list['ScannedUnit'],
                     ini, gp: dict,
                     playable: set, player_del: frozenset
                     ) -> list[dict]:
    """Group bucket C units by the impossible prereq token."""
    from collections import defaultdict
    token_groups = defaultdict(list)
    for u in units:
        tok = _find_impossible_token(u.unit_id, ini, gp, playable, player_del)
        token_groups[tok].append(u)
    groups = []
    for tok, tok_units in token_groups.items():
        groups.append({
            'token':  tok,
            'units':  tok_units,
            'label':  f"prerequisite token '{tok}' ({len(tok_units)} units)",
        })
    groups.sort(key=lambda g: -len(g['units']))
    return groups


def _group_by_both(units: list['ScannedUnit'],
                    uid_to_key: dict,
                    ini, gp: dict,
                    playable: set, player_del: frozenset,
                    max_gap: int = 20
                    ) -> list[dict]:
    """Cross-product: group by (impossible_token, prefix) simultaneously."""
    from collections import defaultdict
    combo = defaultdict(list)
    for u in units:
        tok  = _find_impossible_token(u.unit_id, ini, gp, playable, player_del)
        if u.unit_id in uid_to_key:
            sect, k = uid_to_key[u.unit_id]
            pfx = _key_prefix(k)
        else:
            sect, pfx = '?', ''
        combo[(tok, sect, pfx)].append(u)
    groups = []
    for (tok, sect, pfx), combo_units in combo.items():
        groups.append({
            'token':   tok,
            'section': sect,
            'prefix':  pfx,
            'units':   combo_units,
            'label':   (f"token '{tok}' + [{sect}] prefix '{pfx}' "
                         f"({len(combo_units)} units)"),
        })
    groups.sort(key=lambda g: -len(g['units']))
    return groups


def _ask_group_mode(bucket_label: str, has_token_option: bool,
                    yes_mode: bool) -> int:
    """
    Ask user how they want units grouped. Returns mode integer:
      0 = no grouping (all as one)
      1 = by type-list index prefix
      2 = by impossible prereq token (bucket C only)
      3 = by both (cross-product, bucket C only)
    """
    if yes_mode:
        return 1  # default: prefix grouping in --yes mode

    print(f"\n  How would you like {bucket_label} units grouped?")
    print(f"    0  No grouping — show all as one block")
    print(f"    1  By type-list index prefix (e.g. aiwp, wpcr, wp)")
    if has_token_option:
        print(f"    2  By impossible prerequisite token (e.g. AIBOOTY, DUM)")
        print(f"    3  By both — more groups, most precise")
    try:
        raw = input("  Select [1] > ").strip()
        mode = int(raw) if raw else 1
        if not has_token_option and mode > 1:
            mode = 1
        if mode < 0 or mode > 3:
            mode = 1
    except (ValueError, EOFError):
        mode = 1
    return mode


def _present_groups(groups: list[dict], prompt_verb: str,
                     yes_mode: bool, default_include: bool
                     ) -> set[str]:
    """
    Present each group to the user and collect inclusion decisions.
    prompt_verb: e.g. "Include" or "Add"
    default_include: True = default to include (enter = yes), False = default skip
    Returns set of included unit_ids.
    """
    included = set()
    default_char = 'y' if default_include else 'n'
    other_char   = 'n' if default_include else 'y'
    default_word = 'include' if default_include else 'skip'

    for i, g in enumerate(groups, 1):
        label    = g.get('label', f"Group {i}")
        units    = g['units']
        sample   = [u.unit_id for u in units[:4]]
        n_total  = len(units)

        print(f"\n  {i}/{len(groups)}  {label}")
        print(f"    Sample: {sample}" + (f" + {n_total-4} more" if n_total > 4 else ""))

        if yes_mode:
            if default_include:
                included.update(u.unit_id for u in units)
                print(f"    → Included ({n_total} units, --yes mode default)")
            else:
                print(f"    → Skipped ({n_total} units, --yes mode default)")
            continue

        print(f"    {prompt_verb} these {n_total} units in AI waves?")
        try:
            ans = input(f"    [{default_char}=default/{other_char}/s=show all] > ").strip().lower()
        except EOFError:
            ans = default_char

        if ans == 's':
            for u in units:
                print(f"      [{u.unit_id}] {u.name[:40]}")
            try:
                ans = input(f"    [{default_char}/{other_char}] > ").strip().lower()
            except EOFError:
                ans = default_char

        include = (ans != other_char) if ans in (default_char, other_char) else default_include
        if include:
            included.update(u.unit_id for u in units)
            print(f"    → Included {n_total} units.")
        else:
            print(f"    → Skipped {n_total} units.")

    return included


def prompt_user(candidates: list[ScannedUnit],
                bypass: tuple[bool,bool,bool],
                yes_mode: bool = False,
                ini=None) -> tuple[list[str], bool]:
    """
    Show bucket counts and let user choose which units to include in waves.
    Groups units by type-list index prefix and (for bucket C) by impossible
    prereq token, so the user can make informed decisions per group.
    Returns list of unit_ids to include.
    """
    bucket_a = [u for u in candidates if u.bucket_a
                and not u.bucket_b and not u.bucket_c
                and not u.suspicious]
    bucket_b = [u for u in candidates if u.bucket_b
                and not u.suspicious]
    bucket_c = [u for u in candidates if u.bucket_c
                and not u.bucket_a and not u.bucket_b
                and not u.suspicious]
    bucket_ac = [u for u in candidates if u.bucket_a and u.bucket_c
                 and not u.bucket_b and not u.suspicious]
    suspicious_only = [u for u in candidates
                       if u.suspicious
                       and not u.bucket_a and not u.bucket_b and not u.bucket_c]
    suspicious_in_buckets = [u for u in candidates
                              if u.suspicious
                              and (u.bucket_a or u.bucket_b or u.bucket_c)]

    hard_bypass, medium_bypass, easy_bypass = bypass

    # Build uid→key map once, used by all grouping functions
    uid_to_key = _build_uid_to_key(ini) if ini else {}

    # Lazy imports for C grouping
    gp = _generic_prereqs(ini) if ini else {}
    playable  = _playable_countries(ini) if ini else set()
    player_del = _player_deliverable_buildings(ini) if ini else frozenset()

    print("\n  ┌─ AI Unit Scanner ─────────────────────────────────────────┐")
    print(f"  │  Found {len(candidates)} units not in the regular buildable pool.  │")
    print("  └───────────────────────────────────────────────────────────┘")

    if bucket_a:
        print(f"\n  [A] TechLevel < 1: {len(bucket_a)} units")
        print(f"      Not buildable on the tech slider. Includes AI-only units,")
        print(f"      civilians, campaign-only units, and restricted variants.")
        for u in bucket_a[:3]:
            print(f"      e.g. [{u.unit_id}] {u.name} (TL={u.tech_level})")

    if bucket_b:
        print(f"\n  [B] BuildLimit=0: {len(bucket_b)} units")
        bypass_note = []
        if hard_bypass:   bypass_note.append("Hard AI can bypass")
        if medium_bypass: bypass_note.append("Medium AI can bypass")
        if easy_bypass:   bypass_note.append("Easy AI can bypass")
        if bypass_note:
            print(f"      AllowBypassBuildLimit: {', '.join(bypass_note)}")
        else:
            print(f"      AllowBypassBuildLimit not set — AI cannot bypass.")
        for u in bucket_b[:3]:
            print(f"      e.g. [{u.unit_id}] {u.name} (BuildLimit={u.build_limit})")

    if bucket_c:
        print(f"\n  [D] Impossible prerequisites: {len(bucket_c)} units")
        print(f"      Players can never satisfy their prerequisites.")
        print(f"      These are typically units the mod intends only for AI.")
        for u in bucket_c[:4]:
            print(f"      e.g. [{u.unit_id}] {u.name} (prereq: {u.prereq[:45]})")

    if bucket_ac:
        print(f"\n  [A+D] TechLevel<1 AND impossible prereqs: {len(bucket_ac)} units")
        print(f"        Strong indicator of AI-only units.")
        for u in bucket_ac[:3]:
            print(f"        e.g. [{u.unit_id}] {u.name}")

    # NOTE: CrateGoodie=yes is informational only — it determines whether a
    # unit CAN be granted as a crate reward, it does NOT gate normal
    # buildability. A unit with CrateGoodie=yes and a valid Prerequisite=
    # is fully buildable and never reaches this scanner. No bucket or
    # questionnaire needed for it.
    exclude_crate_goodies = False  # kept for call-site compatibility; unused
    all_suspicious = suspicious_only + suspicious_in_buckets
    if all_suspicious:
        print(f"\n  [Review needed] {len(all_suspicious)} units flagged for review.")

    included = set()

    if yes_mode:
        # Auto-include bucket D + A+D. Group for display only.
        auto = [u.unit_id for u in candidates
                if (u.bucket_c or (u.bucket_a and u.bucket_c))
                and not (u.suspicious and not u.bucket_c)]
        if hard_bypass or medium_bypass or easy_bypass:
            auto += [u.unit_id for u in candidates
                     if u.bucket_b and not u.suspicious]
        print(f"\n  Auto-including {len(auto)} units (Bucket D"
              f"{'+B (bypass)' if hard_bypass or medium_bypass or easy_bypass else ''}).")
        included.update(auto)
        print(f"  Including {len(included)} AI candidate units total.")
        # Return the same 2-tuple shape as the interactive path below, or the
        # caller's unpack (ai_unit_ids, exclude_crate_goodies = prompt_user(...))
        # raises ValueError. exclude_crate_goodies is False here (unused).
        return list(dict.fromkeys(included)), exclude_crate_goodies

    # ── Interactive ──────────────────────────────────────────────────────
    # First: which buckets to consider at all? Only offer buckets that
    # actually have units in them — an empty bucket isn't worth asking about.
    available_letters = []
    if bucket_a:  available_letters.append('A')
    if bucket_b:  available_letters.append('B')
    if bucket_c:  available_letters.append('D')   # impossible prereq = D
    if bucket_ac: available_letters.append('D')   # A+D folds into D selection
    available_letters = sorted(set(available_letters))

    if not available_letters:
        print("\n  No optional unit buckets available to choose from.")
        chosen_buckets = set()
    else:
        print("\n  Which buckets would you like to include?")
        print(f"  (comma-separated — e.g. {available_letters[-1]}  or  "
              f"{','.join(available_letters)}  or  none)")
        print(f"  Recommended: none")
        try:
            choice = input("  > ").strip().upper()
        except EOFError:
            choice = 'NONE'
        if not choice:
            choice = 'NONE'
        chosen_buckets = {c.strip() for c in choice.split(',')
                          if c.strip() and c.strip() != 'NONE'}

    # ── Bucket A ─────────────────────────────────────────────────────────
    if 'A' in chosen_buckets and bucket_a:
        mode_a = _ask_group_mode("Bucket A", has_token_option=False,
                                  yes_mode=yes_mode)
        if mode_a == 0:
            # No grouping — include all
            included.update(u.unit_id for u in bucket_a)
            print(f"  Including all {len(bucket_a)} Bucket A units.")
        else:
            groups_a = _group_by_prefix(bucket_a, uid_to_key)
            print(f"  Bucket A split into {len(groups_a)} group(s).")
            picked = _present_groups(groups_a, "Include", yes_mode,
                                      default_include=False)
            included.update(picked)

    # ── Bucket B ─────────────────────────────────────────────────────────
    if 'B' in chosen_buckets and bucket_b:
        mode_b = _ask_group_mode("Bucket B", has_token_option=False,
                                  yes_mode=yes_mode)
        if mode_b == 0:
            included.update(u.unit_id for u in bucket_b)
            print(f"  Including all {len(bucket_b)} Bucket B units.")
        else:
            groups_b = _group_by_prefix(bucket_b, uid_to_key)
            print(f"  Bucket B split into {len(groups_b)} group(s).")
            picked = _present_groups(groups_b, "Include", yes_mode,
                                      default_include=False)
            included.update(picked)

    # ── Bucket D (impossible prerequisites) ─────────────────────────────
    if 'D' in chosen_buckets and bucket_c:
        mode_c = _ask_group_mode("Bucket D", has_token_option=True,
                                  yes_mode=yes_mode)
        if mode_c == 0:
            included.update(u.unit_id for u in bucket_c)
            print(f"  Including all {len(bucket_c)} Bucket D units.")
        elif mode_c == 1:
            groups_c = _group_by_prefix(bucket_c, uid_to_key)
            print(f"  Bucket D split into {len(groups_c)} group(s) by index prefix.")
            picked = _present_groups(groups_c, "Include", yes_mode,
                                      default_include=True)
            included.update(picked)
        elif mode_c == 2:
            groups_c = _group_by_token(bucket_c, ini, gp, playable, player_del)
            print(f"  Bucket D split into {len(groups_c)} group(s) by prerequisite token.")
            picked = _present_groups(groups_c, "Include", yes_mode,
                                      default_include=True)
            included.update(picked)
        else:  # mode_c == 3
            groups_c = _group_by_both(bucket_c, uid_to_key, ini, gp,
                                       playable, player_del)
            print(f"  Bucket D split into {len(groups_c)} group(s) by token + prefix.")
            picked = _present_groups(groups_c, "Include", yes_mode,
                                      default_include=True)
            included.update(picked)

    # ── Bucket A+D ───────────────────────────────────────────────────────
    # A+D units: always treated like D (include by default)
    if 'D' in chosen_buckets and bucket_ac:
        included.update(u.unit_id for u in bucket_ac)

    # ── Suspicious review ─────────────────────────────────────────────────
    if all_suspicious:
        print(f"\n  Review {len(all_suspicious)} flagged units.")
        print("  For each: [k]eep (include), [s]kip, [a]ll keep, [n]one keep")
        kept  = []
        keep_all = skip_all = False
        for u in all_suspicious:
            if keep_all:
                kept.append(u.unit_id); continue
            if skip_all:
                continue
            in_bucket = u.bucket_a or u.bucket_b or u.bucket_c
            bstr = (f" [bucket {'A' if u.bucket_a else ''}"
                    f"{'B' if u.bucket_b else ''}{'D' if u.bucket_c else ''}]"
                    if in_bucket else "")
            print(f"  [{u.unit_id}] {u.name[:30]}{bstr}  {u.suspicious_reason}")
            if u.cost_override is not None:
                print(f"    Cost override: {u.cost_override}")
            try:
                ans = input("  k/s/a/n > ").strip().lower()
            except EOFError:
                ans = 's'
            if ans == 'a':   keep_all = True; kept.append(u.unit_id)
            elif ans == 'n': skip_all = True
            elif ans == 'k': kept.append(u.unit_id)
        included.update(kept)
        print(f"  Kept {len(kept)} flagged units.")

    print(f"\n  Including {len(included)} AI candidate units total.")
    return list(dict.fromkeys(included)), exclude_crate_goodies
