"""
entry_manager.py

Manages classification and selective deletion of AI entries in aimd.ini,
with dependency tracking and comment-out of orphaned entries.

Entry categories:
  VANILLA   - matches a vanilla YR 1.001 entry (same ID AND same content)
  TOOL      - tagged with '; YR-AI-Tool'
  MOD       - everything else (custom modder content)

Sections handled: [TaskForces], [ScriptTypes], [TeamTypes], [AITriggerTypes]
Each has a registry list section that also needs updating.
"""

from __future__ import annotations
import re, hashlib
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum, auto


class Category(Enum):
    VANILLA = auto()
    TOOL    = auto()
    MOD     = auto()


class SectionType(Enum):
    TASKFORCE   = 'TaskForces'
    SCRIPT      = 'ScriptTypes'
    TEAM        = 'TeamTypes'
    TRIGGER     = 'AITriggerTypes'
    OTHER       = 'Other'


@dataclass
class AiEntry:
    section_id:   str           # e.g. 'ABCD1234-G'
    section_type: SectionType
    category:     Category
    raw_lines:    list[str]     # all lines of the section including header
    list_key:     str | None    # the '5=ABCD1234-G' line in the registry
    list_section: str | None    # which list section it belongs to
    # Dependency fields (filled by dependency analysis)
    taskforce_id: str | None = None   # for TeamTypes: TF it references
    script_id:    str | None = None   # for TeamTypes: Script it references
    team_ids:     list[str] = field(default_factory=list)  # for Triggers: teams
    # Derived
    side:         int  = 0     # Side= value (0=all, 1=GDI, 2=NOD, 3=Yuri)
    difficulty:   str  = ''    # EnabledInE/M/H → 'Easy','Medium','Hard','All'
    name:         str  = ''


# ─────────────────────────────────────────────────────────────────────────────
# Vanilla fingerprinting
# ─────────────────────────────────────────────────────────────────────────────

def _fingerprint(filepath: str | Path) -> dict[str, str]:
    """Return {section_id: content_hash} for [HEXID-G] sections AND inline triggers.

    Sections: hash of normalized body lines.
    Inline AITriggerTypes (HEXID-G=Name,...): hash of the CSV line itself.
    This lets vanilla trigger IDs be recognized even without a section body.
    """
    sections = {}
    current_id = None
    current_lines: list[str] = []
    in_triggers = False

    with open(filepath, encoding='latin-1', errors='replace') as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith(';'):
                continue

            if s.startswith('['):
                # Flush open section
                if current_id:
                    blob = '\n'.join(current_lines)
                    sections[current_id] = hashlib.md5(blob.encode()).hexdigest()
                    current_id = None
                    current_lines = []
                raw = s.split(';')[0].strip()  # strip inline comments
                low = raw[1:-1].lower() if raw.endswith(']') else ''
                in_triggers = (low == 'aitriggertypes')
                m = re.match(r'^\[([0-9A-Fa-f]{8}-G)\]', raw, re.IGNORECASE)
                if m:
                    current_id = m.group(1).upper()
                    in_triggers = False
                continue

            if in_triggers:
                m = re.match(r'^([0-9A-Fa-f]{8}-G)=(.+)', s, re.IGNORECASE)
                if m:
                    tid = m.group(1).upper()
                    sections[tid] = hashlib.md5(s.encode()).hexdigest()
                continue

            if current_id:
                current_lines.append(s)

    if current_id:
        blob = '\n'.join(current_lines)
        sections[current_id] = hashlib.md5(blob.encode()).hexdigest()

    return sections


# ─────────────────────────────────────────────────────────────────────────────
# Parser
# ─────────────────────────────────────────────────────────────────────────────

_SECTION_TYPES = {
    'taskforces':    SectionType.TASKFORCE,
    'scripttypes':   SectionType.SCRIPT,
    'teamtypes':     SectionType.TEAM,
    'aitriggertypes': SectionType.TRIGGER,
}

_DIFFICULTY_MAP = {
    (1,0,0): 'Easy', (0,1,0): 'Medium', (0,0,1): 'Hard',
    (1,1,0): 'Easy+Medium', (1,0,1): 'Easy+Hard',
    (0,1,1): 'Medium+Hard', (1,1,1): 'All',
}


def parse_entries(aimd_path: str | Path,
                  vanilla_fp: dict[str, str]) -> list[AiEntry]:
    """
    Parse aimd.ini and return all AI entries with classification.
    vanilla_fp: {section_id: content_hash} from _fingerprint(vanilla_aimd.ini)
    """
    with open(aimd_path, encoding='latin-1', errors='replace') as f:
        lines = f.readlines()

    # Build list-registry maps: section_type_lower → {id: key_line}
    registry: dict[str, dict[str, str]] = {}
    cur_list = None
    for line in lines:
        s = line.strip()
        if not s or s.startswith(';'):
            continue
        low = s.lower()
        if low in _SECTION_TYPES or low in (
                'taskforces','scripttypes','teamtypes','aitriggertypes'):
            cur_list = low.strip('[]') if s.startswith('[') else low
            cur_list = s[1:-1].lower() if s.startswith('[') else None
            continue
        if s.startswith('['):
            cur_list = None
            low2 = s[1:-1].lower()
            if low2 in _SECTION_TYPES:
                cur_list = low2
            continue
        if cur_list and '=' in s and not s.startswith(';'):
            k, v = s.split('=', 1)
            v = v.split(';')[0].strip()
            if re.match(r'^[0-9A-Fa-f]{8}-G$', v, re.IGNORECASE):
                registry.setdefault(cur_list, {})[v] = s

    # Parse sections
    entries = []
    cur_id    = None
    cur_type  = SectionType.OTHER
    cur_lines: list[str] = []
    is_tool   = False

    def _flush():
        nonlocal cur_id, cur_type, cur_lines, is_tool
        if not cur_id or cur_type == SectionType.OTHER:
            cur_id = None; cur_lines = []; is_tool = False
            return

        # Hash body lines only — exclude the [ID] header line so the hash
        # matches _fingerprint(), which also excludes headers. Including the
        # header made every vanilla section hash-mismatch → classified MOD.
        content_lines = [l.strip() for l in cur_lines
                         if l.strip()
                         and not l.strip().startswith(';')
                         and not l.strip().startswith('[')]
        blob = '\n'.join(content_lines)
        content_hash = hashlib.md5(blob.encode()).hexdigest()

        # Locate the registry list line FIRST — the tool tag usually lives
        # on the list entry (e.g. "30=ABCD1234-G  ; YR-AI-Tool"), not on the
        # section body. Teams/taskforces/scripts are tagged this way.
        list_key = None
        list_sec = None
        for lname, lmap in registry.items():
            if cur_id in lmap:
                list_key = lmap[cur_id]
                list_sec = lname
                break

        if is_tool or (list_key and '; YR-AI-Tool' in list_key):
            cat = Category.TOOL
        elif (cur_id in vanilla_fp and
              vanilla_fp[cur_id] == content_hash):
            cat = Category.VANILLA
        else:
            cat = Category.MOD

        entry = AiEntry(
            section_id   = cur_id,
            section_type = cur_type,
            category     = cat,
            raw_lines    = list(cur_lines),
            list_key     = list_key,
            list_section = list_sec,
        )

        # Extract Name=
        for l in cur_lines:
            m = re.match(r'^Name\s*=\s*(.+)', l.strip(), re.IGNORECASE)
            if m:
                entry.name = m.group(1).strip().split(';')[0].strip()
                break

        # TeamTypes: extract TaskForce= and Script=
        if cur_type == SectionType.TEAM:
            for l in cur_lines:
                s2 = l.strip()
                m = re.match(r'^TaskForce\s*=\s*([0-9A-Fa-f]{8}-G)',
                             s2, re.IGNORECASE)
                if m: entry.taskforce_id = m.group(1)
                m = re.match(r'^Script\s*=\s*([0-9A-Fa-f]{8}-G)',
                             s2, re.IGNORECASE)
                if m: entry.script_id = m.group(1)
                m = re.match(r'^Side\s*=\s*(\d+)', s2, re.IGNORECASE)
                if m: entry.side = int(m.group(1))

        # AITriggerTypes: extract team IDs and side from the CSV line
        if cur_type == SectionType.TRIGGER:
            for l in cur_lines:
                s2 = l.strip()
                m = re.match(r'^[0-9A-Fa-f]{8}-G\s*=\s*(.+)', s2,
                             re.IGNORECASE)
                if m:
                    parts = m.group(1).split(',')
                    if len(parts) > 1:
                        t1 = parts[1].strip()
                        if re.match(r'^[0-9A-Fa-f]{8}-G$', t1, re.IGNORECASE):
                            entry.team_ids.append(t1)
                    if len(parts) > 13:
                        try: entry.side = int(parts[12].strip())
                        except ValueError: pass
                    if len(parts) > 15:
                        try:
                            e = int(parts[15].strip())
                            nm = int(parts[16].strip()) if len(parts)>16 else 0
                            h  = int(parts[17].strip()) if len(parts)>17 else 0
                            entry.difficulty = _DIFFICULTY_MAP.get(
                                (e,nm,h), 'All')
                        except ValueError: pass
                    if len(parts) > 14:
                        t2 = parts[14].strip()
                        if re.match(r'^[0-9A-Fa-f]{8}-G$', t2, re.IGNORECASE):
                            entry.team_ids.append(t2)
                    break

        entries.append(entry)
        cur_id = None; cur_lines = []; is_tool = False

    for line in lines:
        s = line.strip()
        # Allow optional trailing comment after ']' on both header forms
        m = re.match(r'^\[([0-9A-Fa-f]{8}-G)\]\s*(?:;.*)?$', s)
        m_aiext = re.match(r'^\[([0-9A-Fa-f]{8}-G)\.AIExt\]', s, re.IGNORECASE)

        if m_aiext:
            # .AIExt sidecar: flush any open section, then start tracking
            # the sidecar under its base trigger ID so apply_deletions can
            # find and remove it alongside the parent trigger.
            _flush()
            # We don't create a separate AiEntry for .AIExt — they're handled
            # implicitly by apply_deletions when the parent trigger is deleted.
            # Just track it so the is_tool flag gets picked up.
            cur_id = m_aiext.group(1)
            cur_type = SectionType.TRIGGER   # treat as trigger for classification
            cur_lines = [line]
            # Tag may be on the header itself: "[ID.AIExt]  ; YR-AI-Tool"
            is_tool = '; YR-AI-Tool' in line
        elif m:
            _flush()
            cur_id = m.group(1)
            # Determine type from preceding non-blank non-comment context
            # Actually we can check any list registry
            found_type = SectionType.OTHER
            for lname, lmap in registry.items():
                if cur_id in lmap:
                    found_type = _SECTION_TYPES.get(lname, SectionType.OTHER)
                    break
            cur_type = found_type
            cur_lines = [line]
            is_tool = '; YR-AI-Tool' in line
        elif s.startswith('['):
            # Any other section header ([ScriptTypes], [TeamTypes], etc.)
            # closes the current section. Without this, the last hex section
            # before a list header absorbed the list's entries into its
            # body, corrupting its content hash (→ vanilla misread as MOD).
            _flush()
        elif cur_id:
            cur_lines.append(line)
            if '; YR-AI-Tool' in line:
                is_tool = True

    _flush()

    # ── Inline AITriggerTypes pass ────────────────────────────────────────
    # AITriggerTypes entries are flat inline lines: HEXID-G=Name,Team1,...
    # They have no [HEXID-G] section header so the section scanner above
    # misses any that don't have a corresponding .AIExt sidecar.
    # This pass reads them directly from the [AITriggerTypes] section.
    already_parsed = {e.section_id.upper() for e in entries
                      if e.section_type == SectionType.TRIGGER}
    in_triggers = False
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        if s.startswith('['):
            in_triggers = s[1:-1].lower().split(';')[0].strip() == 'aitriggertypes'
            continue
        if not in_triggers:
            continue
        if s.startswith(';'):
            continue
        # Match inline trigger: HEXID-G=Name,...
        m = re.match(r'^([0-9A-Fa-f]{8}-G)=(.+)', s, re.IGNORECASE)
        if not m:
            continue
        tid = m.group(1).upper()
        if tid in already_parsed:
            continue   # already have this one from .AIExt sidecar

        csv = m.group(2)
        parts = csv.split(',')

        # Classify: tool tag appears as trailing comment on the inline line
        is_tool_entry = '; YR-AI-Tool' in line

        # Vanilla classification for triggers: match by ID only
        # (inline triggers have no body to hash — we just check ID presence)
        if is_tool_entry:
            cat = Category.TOOL
        elif tid in vanilla_fp:
            cat = Category.VANILLA
        else:
            cat = Category.MOD

        entry = AiEntry(
            section_id   = tid,
            section_type = SectionType.TRIGGER,
            category     = cat,
            raw_lines    = [line],
            list_key     = None,
            list_section = 'aitriggertypes',
        )

        # Extract team IDs and difficulty flags from CSV
        if len(parts) > 1:
            t1 = parts[1].strip()
            if re.match(r'^[0-9A-Fa-f]{8}-G$', t1, re.IGNORECASE):
                entry.team_ids.append(t1)
        if len(parts) > 12:
            try:   entry.side = int(parts[12].strip())
            except ValueError: pass
        if len(parts) > 14:
            t2 = parts[14].strip()
            if re.match(r'^[0-9A-Fa-f]{8}-G$', t2, re.IGNORECASE):
                entry.team_ids.append(t2)
        if len(parts) >= 18:
            try:
                e_f = int(parts[15].strip())
                m_f = int(parts[16].strip())
                h_f = int(parts[17].split(';')[0].strip())
                entry.difficulty = _DIFFICULTY_MAP.get((e_f, m_f, h_f), 'All')
            except ValueError:
                pass

        entries.append(entry)
        already_parsed.add(tid)

    return entries


# ─────────────────────────────────────────────────────────────────────────────
# Selection prompt
# ─────────────────────────────────────────────────────────────────────────────

def prompt_deletion(entries: list[AiEntry],
                    yes_mode: bool = False
                    ) -> list[str]:
    """
    Interactive prompt to select entries for deletion.
    Returns list of section_ids to delete.
    """
    vanilla = [e for e in entries if e.category == Category.VANILLA]
    tool    = [e for e in entries if e.category == Category.TOOL]
    mod     = [e for e in entries if e.category == Category.MOD]

    print(f"\n  ┌─ Entry Manager ─────────────────────────────────────────┐")
    print(f"  │  Vanilla YR entries : {len(vanilla):>4}                           │")
    print(f"  │  Tool-generated     : {len(tool):>4}  (; YR-AI-Tool)            │")
    print(f"  │  Mod-added          : {len(mod):>4}  (custom modder content)   │")
    print(f"  └─────────────────────────────────────────────────────────┘")

    if not entries:
        return []

    # Step 1: Which categories?
    print(f"\n  Step 1: Which categories to delete?")
    print(f"    V  Vanilla entries ({len(vanilla)})")
    print(f"    T  Tool-generated entries ({len(tool)})")
    print(f"    M  Mod-added entries ({len(mod)})")
    print(f"    (comma-separated, e.g. V,T — 'all' or Enter to delete all)")
    try:
        raw = input("  > ").strip().upper()
    except EOFError:
        raw = ''
    if not raw:
        print("  No entries deleted.")
        return []

    chosen_cats: set[Category] = set()
    if raw in ('ALL', 'V,T,M', 'VTM', 'T,V,M', 'M,V,T', 'M,T,V', 'T,M,V'):
        chosen_cats = {Category.VANILLA, Category.TOOL, Category.MOD}
    else:
        if 'V' in raw: chosen_cats.add(Category.VANILLA)
        if 'T' in raw: chosen_cats.add(Category.TOOL)
        if 'M' in raw: chosen_cats.add(Category.MOD)

    pool = [e for e in entries if e.category in chosen_cats]
    if not pool:
        print("  Nothing to delete in selected categories.")
        return []

    # Step 2: Which section types?
    type_counts = {}
    for e in pool:
        type_counts[e.section_type] = type_counts.get(e.section_type, 0) + 1

    print(f"\n  Step 2: Which section types to delete?")
    type_labels = {
        SectionType.TRIGGER:   f"A  AITriggerTypes ({type_counts.get(SectionType.TRIGGER, 0)})",
        SectionType.TEAM:      f"E  TeamTypes       ({type_counts.get(SectionType.TEAM, 0)})",
        SectionType.TASKFORCE: f"F  TaskForces      ({type_counts.get(SectionType.TASKFORCE, 0)})",
        SectionType.SCRIPT:    f"S  ScriptTypes     ({type_counts.get(SectionType.SCRIPT, 0)})",
    }
    for t, label in type_labels.items():
        if type_counts.get(t, 0):
            print(f"    {label}")
    print(f"    (comma-separated, e.g. A,F — or Enter/all for all listed)")
    try:
        raw2 = input("  > ").strip().upper()
    except EOFError:
        raw2 = ''

    if not raw2 or raw2 in ('ALL', 'A,E,F,S', 'AEFS'):
        chosen_types = set(type_counts.keys())
    else:
        chosen_types = set()
        if 'A' in raw2: chosen_types.add(SectionType.TRIGGER)
        if 'E' in raw2: chosen_types.add(SectionType.TEAM)
        if 'F' in raw2: chosen_types.add(SectionType.TASKFORCE)
        if 'S' in raw2: chosen_types.add(SectionType.SCRIPT)
        # If nothing matched, treat as "all"
        if not chosen_types:
            chosen_types = set(type_counts.keys())

    pool = [e for e in pool if e.section_type in chosen_types]
    if not pool:
        print("  Nothing to delete in selected section types.")
        return []

    # Step 3: Filter by side? (optional)
    sides_present = sorted({e.side for e in pool if e.side > 0})
    if sides_present:
        side_names = {1:'GDI/Allied', 2:'NOD/Soviet', 3:'Yuri', 4:'Faction 4', 5:'Faction 5'}
        print(f"\n  Step 3: Filter by side? (optional — press Enter for all)")
        for s in sides_present:
            n = [e for e in pool if e.side == s]
            print(f"    {s}  {side_names.get(s, f'Side {s}')} ({len(n)} entries)")
        print(f"    0  Side=0 / unassigned ({sum(1 for e in pool if e.side==0)})")
        try:
            raw3 = input("  Sides (e.g. 1,2) or Enter to skip: ").strip()
        except EOFError:
            raw3 = ''
        if raw3:
            try:
                sides_filter = {int(s.strip()) for s in raw3.split(',')}
                pool = [e for e in pool if e.side in sides_filter]
            except ValueError:
                print("  Invalid input — side filter skipped.")

    # Step 4: Filter by difficulty? (optional)
    diffs_present = sorted({e.difficulty for e in pool
                            if e.difficulty and e.difficulty != 'All'})
    if diffs_present:
        print(f"\n  Step 4: Filter by difficulty? (optional — press Enter for all)")
        for d in diffs_present:
            n = [e for e in pool if e.difficulty == d]
            print(f"    {d} ({len(n)} entries)")
        try:
            raw4 = input("  Difficulties (e.g. Easy,Hard) or Enter to skip: ").strip()
        except EOFError:
            raw4 = ''
        if raw4:
            diffs_filter = {d.strip().capitalize() for d in raw4.split(',')}
            pool = [e for e in pool if e.difficulty in diffs_filter or
                    not e.difficulty]

    print(f"\n  Selected {len(pool)} entries for deletion.")
    if not yes_mode:
        try:
            ans = input("  Confirm deletion? [Y/n]: ").strip().lower()
            if ans == 'n':
                print("  Deletion cancelled.")
                return []
        except EOFError:
            pass

    return [e.section_id for e in pool]


# ─────────────────────────────────────────────────────────────────────────────
# Dependency resolution
# ─────────────────────────────────────────────────────────────────────────────

def resolve_orphans(to_delete: set[str],
                    entries: list[AiEntry]) -> set[str]:
    """
    Given a set of section_ids to delete, find all entries that would
    become orphaned and add them to the delete set (or comment-out set).
    Returns the full set of IDs that should be commented out (not deleted,
    but rendered inert — their dependents may still want them visible).

    Cascade rules:
      Deleted TaskForce → teams referencing it become orphaned
      Deleted Script    → teams referencing it become orphaned
      Deleted Team      → triggers referencing it become orphaned
    Orphaned entries are commented out, not deleted.
    """
    by_id = {e.section_id: e for e in entries}
    to_comment: set[str] = set()

    # Teams orphaned by missing TF or Script
    for e in entries:
        if e.section_type != SectionType.TEAM:
            continue
        if e.section_id in to_delete:
            continue
        if ((e.taskforce_id and e.taskforce_id in to_delete) or
                (e.script_id and e.script_id in to_delete)):
            to_comment.add(e.section_id)

    # Triggers orphaned by missing or commented-out teams
    dead_teams = (to_delete | to_comment) & {
        e.section_id for e in entries if e.section_type == SectionType.TEAM}
    for e in entries:
        if e.section_type != SectionType.TRIGGER:
            continue
        if e.section_id in to_delete:
            continue
        if any(t in dead_teams for t in e.team_ids):
            to_comment.add(e.section_id)

    return to_comment


# ─────────────────────────────────────────────────────────────────────────────
# File writer
# ─────────────────────────────────────────────────────────────────────────────

def apply_deletions(aimd_path: str | Path,
                    to_delete: set[str],
                    to_comment: set[str]) -> tuple[int, int]:
    """
    Apply deletions and comment-outs to the file in-place.
    Returns (n_deleted, n_commented).
    """
    with open(aimd_path, encoding='latin-1', errors='replace') as f:
        lines = f.readlines()

    # Build line ranges for each section — including .AIExt sidecars.
    # Normal sections: [HEXID-G]
    # AIExt sidecars:  [HEXID-G.AIExt]
    # We track both, keying .AIExt sections by their base ID so deletion
    # of a trigger also deletes its sidecar.
    section_starts: dict[str, int] = {}   # base_id → start line
    section_ends:   dict[str, int] = {}   # base_id → end line
    aiext_starts:   dict[str, int] = {}   # base_id → .AIExt start line
    aiext_ends:     dict[str, int] = {}   # base_id → .AIExt end line
    cur_id     = None
    cur_aiext  = None

    _NORMAL_RE = re.compile(r'^\[([0-9A-Fa-f]{8}-G)\]\s*(?:;.*)?$', re.IGNORECASE)
    _AIEXT_RE  = re.compile(r'^\[([0-9A-Fa-f]{8}-G)\.AIExt\]', re.IGNORECASE)

    for i, line in enumerate(lines):
        s = line.strip()
        m_normal = _NORMAL_RE.match(s)
        m_aiext  = _AIEXT_RE.match(s)

        if m_normal:
            # Close any open sections
            if cur_id:
                section_ends[cur_id] = i
            if cur_aiext:
                aiext_ends[cur_aiext] = i
                cur_aiext = None
            cur_id = m_normal.group(1).upper()
            section_starts[cur_id] = i

        elif m_aiext:
            if cur_id:
                section_ends[cur_id] = i
                cur_id = None
            if cur_aiext:
                aiext_ends[cur_aiext] = i
            cur_aiext = m_aiext.group(1).upper()
            aiext_starts[cur_aiext] = i

        elif s.startswith('[') and s.endswith(']'):
            # Any other section header closes open sections
            if cur_id:
                section_ends[cur_id] = i
                cur_id = None
            if cur_aiext:
                aiext_ends[cur_aiext] = i
                cur_aiext = None

    if cur_id:
        section_ends[cur_id] = len(lines)
    if cur_aiext:
        aiext_ends[cur_aiext] = len(lines)

    # Build registry line indices.
    # For TaskForces/Scripts/Teams: lines of form  N=HEXID-G
    # For AITriggerTypes: lines of form  HEXID-G=Name,Team1,...  (inline)
    # We need to blank both forms when deleting.
    registry_line_ids:   dict[str, int] = {}   # base_id → line index (N=ID format)
    trigger_inline_ids:  dict[str, int] = {}   # base_id → line index (ID=... format)
    in_list      = False
    in_triggers  = False
    list_sections = {'taskforces', 'scripttypes', 'teamtypes'}

    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith(';'):
            continue
        if s.startswith('['):
            low = s[1:-1].lower()
            in_list     = low in list_sections
            in_triggers = (low == 'aitriggertypes')
            continue

        if in_list and '=' in s:
            # N=HEXID-G format
            k, v = s.split('=', 1)
            v = v.split(';')[0].strip()
            if re.match(r'^[0-9A-Fa-f]{8}-G$', v, re.IGNORECASE):
                registry_line_ids[v.upper()] = i

        elif in_triggers and '=' in s:
            # HEXID-G=Name,... inline format (ID on left)
            k, _ = s.split('=', 1)
            k = k.split(';')[0].strip()
            if re.match(r'^[0-9A-Fa-f]{8}-G$', k, re.IGNORECASE):
                trigger_inline_ids[k.upper()] = i

    # Normalize to_delete and to_comment to uppercase
    to_delete = {sid.upper() for sid in to_delete}
    to_comment = {sid.upper() for sid in to_comment}

    # Apply: delete sections + .AIExt sidecars + registry/inline lines
    out_lines = list(lines)
    n_deleted = 0
    n_commented = 0

    for sid in to_delete:
        deleted_something = False
        # Delete normal section (plus any preceding `; [LABEL]` comment
        # lines that belong to it — they'd otherwise orphan in the file).
        if sid in section_starts:
            start = section_starts[sid]
            end   = section_ends.get(sid, len(lines))
            # Walk backward from `start` and swallow adjacent comment lines
            # like `; [GDI][RUSH_PHASE][...]` that were emitted as labels.
            # Stop at first non-comment (blank line, other section, real content).
            back = start - 1
            while back >= 0:
                stripped = out_lines[back].strip()
                if stripped.startswith('; [') and stripped.endswith(']'):
                    start = back  # extend range to include this label
                    back -= 1
                else:
                    break
            for i in range(start, end):
                out_lines[i] = ''
            deleted_something = True
        # Delete .AIExt sidecar
        if sid in aiext_starts:
            start = aiext_starts[sid]
            end   = aiext_ends.get(sid, len(lines))
            for i in range(start, end):
                out_lines[i] = ''
        # Remove N=ID registry line (TaskForces/Scripts/Teams)
        if sid in registry_line_ids:
            out_lines[registry_line_ids[sid]] = ''
        # Remove inline trigger line
        if sid in trigger_inline_ids:
            out_lines[trigger_inline_ids[sid]] = ''
            deleted_something = True
        if deleted_something:
            n_deleted += 1

    for sid in to_comment:
        commented_something = False
        if sid in section_starts:
            start = section_starts[sid]
            end   = section_ends.get(sid, len(lines))
            for i in range(start, end):
                if out_lines[i].strip():
                    out_lines[i] = '; ' + out_lines[i].lstrip()
            commented_something = True
        if sid in aiext_starts:
            start = aiext_starts[sid]
            end   = aiext_ends.get(sid, len(lines))
            for i in range(start, end):
                if out_lines[i].strip():
                    out_lines[i] = '; ' + out_lines[i].lstrip()
        if sid in registry_line_ids:
            idx = registry_line_ids[sid]
            if out_lines[idx].strip():
                out_lines[idx] = '; ' + out_lines[idx].lstrip()
        if sid in trigger_inline_ids:
            idx = trigger_inline_ids[sid]
            if out_lines[idx].strip():
                out_lines[idx] = '; ' + out_lines[idx].lstrip()
            commented_something = True
        if commented_something:
            n_commented += 1

    with open(aimd_path, 'w', encoding='latin-1') as f:
        f.writelines(out_lines)

    return n_deleted, n_commented
    cur_id = None

    for i, line in enumerate(lines):
        s = line.strip()
        m = re.match(r'^\[([0-9A-Fa-f]{8}-G)\]$', s)
        if m:
            if cur_id:
                section_ends[cur_id] = i
            cur_id = m.group(1)
            section_starts[cur_id] = i
    if cur_id:
        section_ends[cur_id] = len(lines)

    # Build list-registry line indices
    # We need to comment/remove the '5=ABCD1234-G' lines too
    registry_line_ids: dict[str, int] = {}  # section_id → line index
    in_list = False
    cur_list_sec = None
    list_sections = {'taskforces','scripttypes','teamtypes','aitriggertypes'}
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith(';'):
            continue
        if s.startswith('['):
            low = s[1:-1].lower()
            in_list = low in list_sections
            cur_list_sec = low if in_list else None
            continue
        if in_list and '=' in s:
            k, v = s.split('=', 1)
            v = v.split(';')[0].strip()
            if re.match(r'^[0-9A-Fa-f]{8}-G$', v, re.IGNORECASE):
                registry_line_ids[v] = i

    # Apply: delete sections + their registry lines
    out_lines = list(lines)
    n_deleted = 0
    n_commented = 0

    for sid in to_delete:
        if sid in section_starts:
            start = section_starts[sid]
            end   = section_ends.get(sid, len(lines))
            for i in range(start, end):
                out_lines[i] = ''
            n_deleted += 1
        if sid in registry_line_ids:
            out_lines[registry_line_ids[sid]] = ''

    for sid in to_comment:
        if sid in section_starts:
            start = section_starts[sid]
            end   = section_ends.get(sid, len(lines))
            for i in range(start, end):
                if out_lines[i].strip():
                    out_lines[i] = '; ' + out_lines[i].lstrip()
            n_commented += 1
        if sid in registry_line_ids:
            idx = registry_line_ids[sid]
            if out_lines[idx].strip():
                out_lines[idx] = '; ' + out_lines[idx].lstrip()

    with open(aimd_path, 'w', encoding='latin-1') as f:
        f.writelines(out_lines)

    return n_deleted, n_commented


def cleanup_orphan_sidecars(aimd_path: str | Path) -> int:
    """
    Remove [XYZ.AIExt] sidecar sections whose base trigger XYZ doesn't
    exist anywhere in the file. These arise when a previous tool run's
    trigger was replaced/deleted but its sidecar somehow persisted.

    Returns the number of orphaned sidecars removed.
    """
    import re
    with open(aimd_path, 'r', encoding='latin-1') as f:
        lines = f.readlines()

    # Collect all trigger IDs that ARE defined (either as inline
    # `HEXID=Name,...` under [AITriggerTypes] or elsewhere in the file).
    _AIEXT_HEADER = re.compile(r'^\[([0-9A-Fa-f]{8}-G)\.AIExt\]', re.IGNORECASE)
    _TRIG_INLINE  = re.compile(r'^([0-9A-Fa-f]{8}-G)=[^\s]', re.IGNORECASE)
    _SECTION_HEAD = re.compile(r'^\[([^\]]+)\]')

    defined_ids: set[str] = set()
    for ln in lines:
        m = _TRIG_INLINE.match(ln.strip())
        if m:
            defined_ids.add(m.group(1).upper())

    # Walk the file and find sidecar sections whose base ID isn't defined.
    # Track (start, end) of each orphan sidecar for deletion.
    orphan_ranges: list[tuple[int, int]] = []
    cur_start = None
    cur_id    = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        m_aiext = _AIEXT_HEADER.match(s)
        if m_aiext:
            # Close any open orphan-in-progress
            if cur_start is not None and cur_id and cur_id.upper() not in defined_ids:
                orphan_ranges.append((cur_start, i))
            cur_start = i
            cur_id    = m_aiext.group(1)
        elif _SECTION_HEAD.match(s):
            # Any other section header closes the current sidecar tracking
            if cur_start is not None and cur_id and cur_id.upper() not in defined_ids:
                orphan_ranges.append((cur_start, i))
            cur_start = None
            cur_id    = None

    # Close a trailing open orphan
    if cur_start is not None and cur_id and cur_id.upper() not in defined_ids:
        orphan_ranges.append((cur_start, len(lines)))

    if not orphan_ranges:
        return 0

    # Blank the orphan ranges (also swallow preceding label comment lines
    # just like apply_deletions does, so no stray `; [POOL]` comments
    # remain).
    for start, end in orphan_ranges:
        back = start - 1
        while back >= 0:
            stripped = lines[back].strip()
            if stripped.startswith('; [') and stripped.endswith(']'):
                start = back
                back -= 1
            else:
                break
        for i in range(start, end):
            lines[i] = ''

    with open(aimd_path, 'w', encoding='latin-1') as f:
        f.writelines(lines)

    return len(orphan_ranges)
