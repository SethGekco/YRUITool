"""
AI lint: conservative structural checks for the AI registry sections
(TaskForces / ScriptTypes / TeamTypes / AITriggerTypes), distilled from AICHECK
but deliberately avoiding its over-strict, extension-hostile checks.

I/O-free: operates on parsed section_maps. It only reports problems that are real
regardless of which engine extensions (Ares/Phobos/KratosPP) are loaded:
  * a registry index list points at a section that doesn't exist
  * a TaskForce unit, a TeamType's TaskForce/Script, or an AITrigger team that
    resolves to no declared section anywhere in the loaded data
  * non-numeric / non-contiguous registry indices, bad unit counts, bad TechLevel
  * (info) declared taskforces/scripts/teamtypes that nothing references

It never validates script-action or trigger-condition *numbers* against a fixed
list, and never calls an unknown tag/object "invalid" — that is exactly where
AICHECK false-positives on custom/extension content. Unknown unit names are only
flagged when they are absent from BOTH the rules and the AI data (a true dangling
reference), never merely because they are unfamiliar.
"""

from dataclasses import dataclass

from .ini_model import (get_val, clean_value as _clean,
                        numbered_entries as _numbered)

# Index-list registries: [Reg] has 0=<id>,1=<id>,… pointing at [<id>] sections.
_INDEX_REGISTRIES = ("TaskForces", "ScriptTypes", "TeamTypes")
# AITriggerTypes is inline: each line is ID=Name,Team1,Owner,TechLevel,… ,Team2,…


@dataclass
class LintFinding:
    severity: str   # "error" | "warning" | "info"
    where: str      # section id / registry context
    message: str


def _is_int(s):
    try:
        int(s)
        return True
    except (TypeError, ValueError):
        return False


def has_ai_sections(section_map):
    """True if any AI registry section is present."""
    return any(r.lower() in section_map
               for r in _INDEX_REGISTRIES + ("AITriggerTypes",))


def lint_ai(ai_map, rules_map=None):
    """Lint AI registry sections. `ai_map` holds the AI sections; `rules_map` holds
    the TechnoType definitions (for TaskForce unit resolution) — pass the loaded
    rules section_map. Returns a list of LintFinding, most-severe first."""
    rules_map = rules_map or {}
    findings = []

    def exists(name):
        n = name.lower()
        return n in ai_map or n in rules_map

    if not has_ai_sections(ai_map):
        return [LintFinding("info", "-",
                "No AI sections (TaskForces / ScriptTypes / TeamTypes / "
                "AITriggerTypes) found to lint.")]

    referenced = set()   # ids pointed at by something (for unused detection)

    # ── index-list registries → [id] sections ──────────────────────────────
    reg_ids = {}
    for reg in _INDEX_REGISTRIES:
        reg_ids[reg] = []
        if reg.lower() not in ai_map:
            continue
        seen = set()
        for idx, val in _numbered(ai_map, reg):
            if idx in seen:
                findings.append(LintFinding("warning", reg,
                                            f"duplicate index {idx}"))
            seen.add(idx)
            if not val:
                continue
            reg_ids[reg].append(val)
            if val.lower() not in ai_map:
                findings.append(LintFinding("error", f"{reg}[{idx}]",
                    f"references undefined [{val}] (no such section)"))
        idxs = sorted(seen)
        if idxs and idxs != list(range(len(idxs))):
            findings.append(LintFinding("warning", reg,
                f"indices are not contiguous from 0 (got {len(idxs)} entries, "
                f"max index {idxs[-1]})"))

    # ── TaskForce sections: count,UNIT entries ─────────────────────────────
    for tf in reg_ids["TaskForces"]:
        if tf.lower() not in ai_map:
            continue
        units = _numbered(ai_map, tf)
        if not units:
            findings.append(LintFinding("warning", tf,
                                        "TaskForce has no unit entries"))
        for idx, val in units:
            parts = [p.strip() for p in val.split(",")]
            if len(parts) < 2 or not parts[0] or not parts[1]:
                findings.append(LintFinding("error", f"{tf}[{idx}]",
                    f"malformed unit entry {val!r} (expected count,UNIT)"))
                continue
            cnt, unit = parts[0], parts[1]
            if not _is_int(cnt):
                findings.append(LintFinding("error", f"{tf}[{idx}]",
                    f"non-numeric unit count {cnt!r}"))
            elif int(cnt) <= 0:
                findings.append(LintFinding("warning", f"{tf}[{idx}]",
                    f"non-positive unit count {cnt}"))
            if unit.lower() not in ("none", "<none>") and not exists(unit):
                findings.append(LintFinding("error", f"{tf}[{idx}]",
                    f"undefined unit [{unit}] (not declared in rules or AI)"))

    # ── TeamType sections: TaskForce / Script must resolve ─────────────────
    for tm in reg_ids["TeamTypes"]:
        if tm.lower() not in ai_map:
            continue
        for key in ("TaskForce", "Script"):
            ref = _clean(get_val(ai_map, tm, key))
            if not ref:
                findings.append(LintFinding("warning", tm,
                                            f"TeamType missing {key}="))
                continue
            referenced.add(ref.lower())
            if ref.lower() not in ai_map:
                findings.append(LintFinding("error", tm,
                                            f"{key}=[{ref}] is undefined"))
        tl = _clean(get_val(ai_map, tm, "TechLevel"))
        if tl and not _is_int(tl):
            findings.append(LintFinding("warning", tm,
                                        f"non-numeric TechLevel {tl!r}"))

    # ── AITriggerTypes: inline ID=Name,Team1,Owner,TechLevel,…,Team2,… ─────
    trig = ai_map.get("aitriggertypes")
    if trig:
        for klow, (_li, kr, vr) in trig["keys"].items():
            fields = [f.strip() for f in _clean(vr).split(",")]
            tid = kr

            def _team(field_i, label):
                if len(fields) <= field_i:
                    return
                t = fields[field_i]
                if not t or t in ("0", "<none>", "none"):
                    return
                if t.lower() in ai_map:
                    referenced.add(t.lower())
                else:
                    findings.append(LintFinding("error", f"AITrigger {tid}",
                        f"{label} team [{t}] is undefined"))

            _team(1, "primary")
            _team(14, "backup")
            if len(fields) > 3 and fields[3] and not _is_int(fields[3]):
                findings.append(LintFinding("warning", f"AITrigger {tid}",
                    f"non-numeric TechLevel field {fields[3]!r}"))

    # ── unused (info, summarized to avoid noise) ───────────────────────────
    for reg, kind in (("TaskForces", "TaskForce"), ("ScriptTypes", "Script"),
                      ("TeamTypes", "TeamType")):
        unused = [r for r in reg_ids[reg] if r.lower() not in referenced]
        if unused:
            sample = ", ".join(unused[:5]) + ("…" if len(unused) > 5 else "")
            findings.append(LintFinding("info", reg,
                f"{len(unused)} unused {kind}(s) nothing references: {sample}"))

    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order.get(f.severity, 3))
    return findings


def format_findings(findings):
    """Render findings as a plain-text report."""
    errs = sum(1 for f in findings if f.severity == "error")
    warns = sum(1 for f in findings if f.severity == "warning")
    infos = sum(1 for f in findings if f.severity == "info")
    lines = [f"AI lint: {errs} error(s), {warns} warning(s), {infos} note(s).", ""]
    icon = {"error": "✗", "warning": "!", "info": "·"}
    for f in findings:
        lines.append(f"{icon.get(f.severity, '-')} [{f.where}] {f.message}")
    return "\n".join(lines)
