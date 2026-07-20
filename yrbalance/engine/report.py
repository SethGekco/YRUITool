"""
Re-run detection and the observation report.

When the tool is run a second time on an already-edited file, it compares what it
finds against what its own comments say it last wrote, and flags anything that
doesn't line up (manual edits, foreign comments, no history). Ported from the
monolith; generalized off the Dying-Earth branding and given a `format()` method
so a GUI can display the report without writing a file.
"""

import datetime as _dt

from .ini_model import (
    scale_int, scale_from_vanilla,
    parse_vanilla_from_line, parse_last_ratio_from_line,
    get_current_field_value_from_line, modify_line,
)

# Sentinels bracketing a processed file's summary block. The new tool writes its
# own sentinels; detection below also tolerates the legacy DE one so files made by
# the original Dying-Earth tool are still recognized as re-runs.
SUMMARY_SENTINEL = "; [YRBT_SUMMARY_START]"
SUMMARY_END      = "; [YRBT_SUMMARY_END]"
_LEGACY_SENTINELS = ("; [DE_TOOL_SUMMARY_START]",)
_LEGACY_ENDS = ("; [DE_TOOL_SUMMARY_END]",)

DEFAULT_TOOL_NAME = "YRBalanceTool"


def detect_rerun(lines):
    """
    Return True if the file appears to have been previously processed by this tool
    (or the legacy Dying-Earth tool). Detection: a summary sentinel on the first
    line, OR any line in the first 500 containing '; vanilla='.
    """
    if lines:
        head = lines[0]
        if SUMMARY_SENTINEL.strip() in head:
            return True
        for legacy in _LEGACY_SENTINELS:
            if legacy.strip() in head:
                return True
    for l in lines[:500]:
        if "; vanilla=" in l:
            return True
    return False


def write_summary_block(lines, ratios, gb_config=None, want_ai_clone=False,
                        tool_name=DEFAULT_TOOL_NAME):
    """
    Insert (or replace) a human-readable summary comment block at the top of
    `lines`, bracketed by sentinels so it can be detected and replaced on re-runs.

    ratios may be nested {cat: {field: val}} or flat {cat_field: val}. Any existing
    block (new or legacy Dying-Earth sentinels) is removed first.
    """
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    ending = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"

    block = []
    block.append(f"{SUMMARY_SENTINEL}{ending}")
    block.append(f"; {tool_name} — Applied Settings{ending}")
    block.append(f"; Last run: {now}{ending}")
    block.append(f";{ending}")
    block.append(f"; STAT RATIOS (1.0 = no change){ending}")

    cats = [("Infantry", "inf"), ("Vehicle", "veh"), ("Naval", "nav"),
            ("Aircraft", "air"), ("Building", "bld"), ("Defensive", "def")]
    for cat_label, ck in cats:
        cr = ratios.get(ck, {}) if isinstance(ratios.get(ck), dict) else {}

        def _rv(field):
            v = cr.get(field)
            if v is None:
                v = ratios.get(f"{ck}_{field}", 1.0)
            try:
                return float(v)
            except (TypeError, ValueError):
                return 1.0

        parts = []
        for field, fkey in [("Str", "str"), ("Cost", "cost"), ("Spd", "spd"),
                            ("DmgR", "fp_r"), ("DmgE", "fp_e"),
                            ("RngR", "rng_r"), ("RngE", "rng_e"),
                            ("ROFR", "rof_r"), ("ROFE", "rof_e")]:
            fv = _rv(fkey)
            if fv != 1.0:
                parts.append(f"{field}={fv}")
        summary = "  ".join(parts) if parts else "no changes"
        block.append(f";   {cat_label:<12}: {summary}{ending}")

    if gb_config:
        block.append(f";{ending}")
        block.append(f"; HOST: mode={gb_config['mode']}  value={gb_config['value']}"
                     f"  AI_clones={'yes' if want_ai_clone else 'no'}{ending}")
    block.append(f"{SUMMARY_END}{ending}")
    block.append(f";{ending}")

    # Remove an existing block (new or legacy sentinels) if present at the top.
    starts = (SUMMARY_SENTINEL.strip(),) + tuple(s.strip() for s in _LEGACY_SENTINELS)
    ends = (SUMMARY_END.strip(),) + tuple(e.strip() for e in _LEGACY_ENDS)
    if lines and lines[0].strip() in starts:
        end_idx = 0
        for i, l in enumerate(lines):
            if l.strip() in ends:
                end_idx = i + 1
                break
        lines[0:end_idx] = []

    for bline in reversed(block):
        lines.insert(0, bline)


class ObservationReport:
    """Collects unusual observations during a re-run and renders a text report."""

    def __init__(self):
        self.entries = []

    def flag(self, category, unit, field, vanilla, last_ratio, expected,
             actual, mode_result, reason):
        self.entries.append({
            "category": category, "unit": unit, "field": field,
            "vanilla": vanilla, "last_ratio": last_ratio,
            "expected": expected, "actual": actual,
            "mode_result": mode_result, "reason": reason,
        })

    def flag_no_history(self, category, unit, field, actual, treatment):
        self.entries.append({
            "category": category, "unit": unit, "field": field,
            "vanilla": None, "last_ratio": None, "expected": None,
            "actual": actual, "mode_result": None,
            "reason": f"No tool history — treated as {treatment}",
        })

    def flag_foreign_comment(self, category, unit, field, actual):
        self.entries.append({
            "category": category, "unit": unit, "field": field,
            "vanilla": None, "last_ratio": None, "expected": None,
            "actual": actual, "mode_result": None,
            "reason": "vanilla= comment present but unit not in ticket — foreign edit",
        })

    def format(self, rerun_mode, uncommented_mode, tool_name="YRBalanceTool"):
        """Render the report to a string (no file I/O)."""
        now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        out = []
        out.append("=" * 72 + "\n")
        out.append(f"  {tool_name} — Observation Report\n")
        out.append(f"  Generated: {now}\n")
        out.append(f"  Rerun mode: {rerun_mode}  |  Uncommented lines: {uncommented_mode}\n")
        out.append("=" * 72 + "\n\n")

        if not self.entries:
            out.append("  No unusual observations. Everything matched expected values.\n")
            return "".join(out)

        out.append(f"  {len(self.entries)} observation(s) flagged.\n\n")
        out.append("  Each entry shows what was found and what the tool did with it.\n")
        out.append("  Review any entries marked [FOREIGN] or [MANUAL EDIT].\n\n")
        out.append("-" * 72 + "\n\n")

        by_unit = {}
        for e in self.entries:
            by_unit.setdefault((e["category"], e["unit"]), []).append(e)

        for (cat, unit), entries in sorted(by_unit.items()):
            out.append(f"  [{unit}]  ({cat})\n")
            for e in entries:
                field, reason, actual = e["field"], e["reason"], e["actual"]
                vanilla, last_r = e["vanilla"], e["last_ratio"]
                expected, result = e["expected"], e["mode_result"]
                if vanilla is not None:
                    out.append(f"    {field}:\n")
                    out.append(f"      Vanilla value      : {vanilla}\n")
                    out.append(f"      Last tool ratio    : {last_r}\n")
                    out.append(f"      Expected (tool)    : {expected}\n")
                    out.append(f"      Actual in file     : {actual}")
                    diff = ""
                    if expected is not None and actual is not None:
                        try:
                            pct = (float(actual) - float(expected)) / float(expected) * 100
                            diff = f"  ({pct:+.1f}% from expected)"
                        except (ZeroDivisionError, TypeError):
                            pass
                    out.append(f"{diff}\n")
                    if result is not None:
                        out.append(f"      This run result    : {result}\n")
                    out.append(f"      Note               : {reason}\n")
                else:
                    out.append(f"    {field}:\n")
                    out.append(f"      Actual in file     : {actual}\n")
                    out.append(f"      Note               : {reason}\n")
            out.append("\n")
        return "".join(out)

    def write(self, report_path, rerun_mode, uncommented_mode,
              tool_name="YRBalanceTool"):
        """Write the report to a file and return the number of entries."""
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(self.format(rerun_mode, uncommented_mode, tool_name))
        return len(self.entries)


def smart_apply_tag(lines, section_map, section_name, tag, new_ratio,
                    category, ticket, rerun_mode, uncommented_mode,
                    report, suffix="Edit by AI script"):
    """
    Apply a ratio to a tag, handling re-run logic:

    - Line has vanilla= and last_ratio= comments AND a ticket exists: use those as
      ground truth; flag if the actual value differs from what we last wrote.
    - Line has vanilla= but no ticket: foreign comment. Flag it, treat per
      uncommented_mode.
    - Line has no vanilla= comment: vanilla or a stripped edit. Treat per
      uncommented_mode.

    rerun_mode / uncommented_mode: 'vanilla' | 'current'
    """
    s = section_map.get(section_name.lower())
    if s is None:
        return False
    entry = s["keys"].get(tag.lower())
    if entry is None:
        return False
    li, kr, vr = entry

    full_line = lines[li]
    vanilla_in_comment = parse_vanilla_from_line(full_line)
    last_ratio_in_comment = parse_last_ratio_from_line(full_line)
    current_val = get_current_field_value_from_line(full_line)

    was_tool_edited = vanilla_in_comment is not None
    ticket_exists = ticket is not None

    if was_tool_edited and ticket_exists:
        # Known tool edit — rebase from vanilla; flag manual drift.
        vanilla = vanilla_in_comment
        last_r = last_ratio_in_comment if last_ratio_in_comment else 1.0
        try:
            expected = scale_int(int(vanilla), last_r) if isinstance(vanilla, int) \
                       else int(vanilla * last_r)
        except (TypeError, ValueError):
            expected = None

        if expected is not None and current_val is not None and current_val != expected:
            base_val = vanilla if rerun_mode == "vanilla" else current_val
            _, new_num = scale_from_vanilla(base_val, new_ratio)
            report.flag(
                category, section_name, tag,
                vanilla, last_r, expected, current_val, new_num,
                "MANUAL EDIT detected — value differs from last tool output. "
                + ("Used vanilla as base." if rerun_mode == "vanilla"
                   else "Used current value as base (current mode)."))
        else:
            base_val = vanilla if vanilla is not None else current_val

        new_val_str, _ = scale_from_vanilla(base_val, new_ratio)
        modify_line(lines, li, kr,
                    f"{new_val_str};{vanilla_in_comment} {suffix}"
                    f" ; vanilla={vanilla_in_comment} ; last_ratio={new_ratio}")

    elif was_tool_edited and not ticket_exists:
        # Foreign comment (no ticket, but has vanilla=).
        report.flag_foreign_comment(category, section_name, tag, current_val)
        base_val = current_val if uncommented_mode == "current" else \
                   (vanilla_in_comment if vanilla_in_comment else current_val)
        new_val_str, _ = scale_from_vanilla(base_val, new_ratio)
        modify_line(lines, li, kr,
                    f"{new_val_str};{base_val} {suffix}"
                    f" ; vanilla={base_val} ; last_ratio={new_ratio}")

    else:
        # No tool history — the raw value IS the baseline in both modes.
        raw = vr.split(";")[0].strip()
        try:
            orig = int(raw)
        except ValueError:
            try:
                orig = float(raw)
            except ValueError:
                return True
        report.flag_no_history(category, section_name, tag, orig, uncommented_mode)
        new_val_str, _ = scale_from_vanilla(orig, new_ratio)
        modify_line(lines, li, kr,
                    f"{new_val_str};{orig} {suffix}"
                    f" ; vanilla={orig} ; last_ratio={new_ratio}")

    return True
