"""
aimd.ini browsing helpers: the four AI registries (TaskForces / ScriptTypes /
TeamTypes / AITriggerTypes), a human-readable decode of the cryptic inline
AITrigger line, each entry's raw definition, and cross-references (what uses what).

I/O-free apart from load_aimd, which finds and parses the aimd sibling of a rules
file. Editing stays in ailib / portai; this module is read/browse only.
"""

import os

from .ini_model import parse_ini_lines, build_section_map, get_val
from .typelists import parse_type_list

_INDEX_REGISTRIES = ("TaskForces", "ScriptTypes", "TeamTypes")

# AITriggerTypes inline layout: ID=field0,field1,… (18 fields on RA2/YR).
_TRIG_LABELS = [
    "Name", "Primary team", "Owner", "TechLevel", "ConditionType",
    "Condition object", "Comparator", "Starting weight", "Min weight",
    "Max weight", "Skirmish", "(unused)", "Side", "Base defense",
    "Secondary team", "Easy", "Medium", "Hard",
]
_TRIG_NAME, _TRIG_TEAM1, _TRIG_OWNER, _TRIG_TEAM2 = 0, 1, 2, 14


def sibling_aimd(rules_path):
    """The aimd.ini / ai.ini next to a rules file, or None."""
    folder = os.path.dirname(rules_path or "")
    if not folder or not os.path.isdir(folder):
        return None
    low = {f.lower(): f for f in os.listdir(folder)}
    for n in ("aimd.ini", "ai.ini"):
        if n in low:
            return os.path.join(folder, low[n])
    return None


def load_aimd(rules_path):
    """(lines, section_map) for the aimd sibling of `rules_path`, or (None, None)."""
    path = sibling_aimd(rules_path)
    if not path:
        return None, None
    lines = parse_ini_lines(path)
    return lines, build_section_map(lines)[0]


def ai_registry(aimd_map):
    """{'TaskForces': [ids], 'ScriptTypes': [ids], 'TeamTypes': [ids],
    'AITriggerTypes': [(id, name)]}."""
    out = {reg: list(parse_type_list(aimd_map, reg)) for reg in _INDEX_REGISTRIES}
    trig = aimd_map.get("aitriggertypes")
    tlist = []
    if trig:
        for _klow, (_li, kr, vr) in trig["keys"].items():
            name = vr.split(",", 1)[0].strip()
            tlist.append((kr, name))
    out["AITriggerTypes"] = tlist
    return out


def decode_trigger(fields):
    """[(label, value)] for an AITrigger's comma fields (labels for the first 18,
    'fieldN' beyond). The Comparator blob is truncated for display."""
    out = []
    for i, lbl in enumerate(_TRIG_LABELS):
        if i >= len(fields):
            break
        v = fields[i].strip()
        if lbl == "Comparator" and len(v) > 14:
            v = v[:14] + "…"
        out.append((lbl, v))
    for j in range(len(_TRIG_LABELS), len(fields)):
        out.append((f"field{j}", fields[j].strip()))
    return out


def trigger_fields(aimd_map, trigger_id):
    """Raw comma fields of a trigger id, or []."""
    trig = aimd_map.get("aitriggertypes")
    if not trig:
        return []
    row = trig["keys"].get(trigger_id.lower())
    if not row:
        return []
    return [f.strip() for f in row[2].split(",")]


def entry_text(aimd_map, entry_id):
    """The raw text of a registry section ([id] + key=value lines), or '' ."""
    sec = aimd_map.get(entry_id.lower())
    if not sec:
        return ""
    lines = [f"[{sec['name_raw']}]"]
    for _klow, (_li, kr, vr) in sorted(sec["keys"].items(),
                                       key=lambda kv: kv[1][0]):
        lines.append(f"{kr}={vr}")
    return "\n".join(lines)


def _ending(lines):
    return "\r\n" if lines and lines[-1].endswith("\r\n") else "\n"


def replace_trigger(lines, aimd_map, trigger_id, new_def):
    """Replace an AITrigger's inline definition (the comma string AFTER `ID=`) in
    place. Mutates `lines`. Returns True if the trigger existed."""
    trig = aimd_map.get("aitriggertypes")
    row = trig["keys"].get(trigger_id.lower()) if trig else None
    if not row:
        return False
    li, kr, _vr = row
    ending = "\r\n" if lines[li].endswith("\r\n") else "\n"
    lines[li] = f"{kr}={new_def.strip()}{ending}"
    return True


def replace_section(lines, aimd_map, name, new_text):
    """Replace a registry section's lines ([id] + key=value) with `new_text`.
    Spans from the section header to just before the next section (trailing blanks
    included), then appends one blank separator. Mutates `lines`; the caller should
    re-parse afterwards (line indices shift). Returns True if the section existed."""
    sec = aimd_map.get(name.lower())
    if not sec:
        return False
    start = sec["start"]
    starts = sorted(s["start"] for s in aimd_map.values())
    after = [s for s in starts if s > start]
    end = min(after) if after else len(lines)
    ending = _ending(lines)
    body = [ln.rstrip("\r\n") for ln in new_text.replace("\r\n", "\n").split("\n")]
    while body and body[-1].strip() == "":
        body.pop()
    new = [ln + ending for ln in body] + [ending]   # + blank separator
    lines[start:end] = new
    return True


def references_to(aimd_map, entry_id):
    """What references `entry_id`: [(kind, id)] — teamtypes using a TaskForce or
    Script; triggers whose primary/secondary team is a TeamType."""
    eid = entry_id.lower()
    refs = []
    for tm in parse_type_list(aimd_map, "TeamTypes"):
        tf = (get_val(aimd_map, tm, "TaskForce") or "").strip().lower()
        sc = (get_val(aimd_map, tm, "Script") or "").strip().lower()
        if eid in (tf, sc):
            refs.append(("TeamType", tm))
    trig = aimd_map.get("aitriggertypes")
    if trig:
        for _klow, (_li, kr, vr) in trig["keys"].items():
            f = [x.strip().lower() for x in vr.split(",")]
            t1 = f[_TRIG_TEAM1] if len(f) > _TRIG_TEAM1 else ""
            t2 = f[_TRIG_TEAM2] if len(f) > _TRIG_TEAM2 else ""
            if eid in (t1, t2):
                refs.append(("AITrigger", kr))
    return refs
