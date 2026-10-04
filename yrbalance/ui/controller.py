"""
Qt-free controller: the glue between the UI's collected state and the engine.

Keeping this Qt-free means the whole apply/preview flow is unit-testable without
constructing widgets or a QApplication.
"""

import os

from ..engine import (
    parse_ini_lines, build_section_map,
    run_balance, parse_script, run_script, resolve_scope, ScriptError,
    restore_vanilla, ModConfig,
    lint_ai, has_ai_sections,
)


# AI files to look for alongside a loaded rules file (YR first, then RA2).
_AI_FILENAMES = ("aimd.ini", "ai.ini")


def lint_ai_for(source_path, section_map):
    """Run the AI linter for a loaded rules file.

    The AI registry (TaskForces/ScriptTypes/TeamTypes/AITriggerTypes) usually
    lives in aimd.ini (YR) or ai.ini (RA2) beside the rules file, but some mods
    keep it in the rules file itself. Resolve the AI source accordingly, using the
    loaded rules map to resolve unit references. Returns (findings, ai_source_desc)."""
    rules_map = section_map or {}
    if has_ai_sections(rules_map):
        return lint_ai(rules_map, rules_map), "loaded rules file"

    if source_path:
        folder = os.path.dirname(source_path)
        for fn in _AI_FILENAMES:
            cand = os.path.join(folder, fn)
            if os.path.isfile(cand):
                try:
                    ai_lines = parse_ini_lines(cand)
                    ai_map, _ = build_section_map(ai_lines)
                except OSError:
                    continue
                return lint_ai(ai_map, rules_map), os.path.basename(cand)

    # Nothing found — lint the (empty-of-AI) rules map to get the friendly note.
    return lint_ai(rules_map, rules_map), None


def load_rules(source_path):
    """Parse a rules file into (lines, section_map)."""
    lines = parse_ini_lines(source_path)
    section_map, _ = build_section_map(lines)
    return lines, section_map


def preview_script(section_map, script_text, config=None):
    """
    Validate a script and describe what it would touch, without editing anything.
    Returns (ok: bool, message: str).
    """
    if config is None:
        config = ModConfig.default()
    if not script_text or not script_text.strip():
        return True, "Script box is empty."
    try:
        script = parse_script(script_text)
    except ScriptError as e:
        return False, f"Parse error: {e}"

    lines = []
    for block in script.blocks:
        if block.is_generator:
            lines.append(f"[+ {block.scope}]  (generator)")
        else:
            n = len(resolve_scope(section_map, block.scope, config)) if section_map else 0
            lines.append(f"[{block.scope}]  ->  {n} technos, {len(block.ops)} tag(s)")
    params = (", ".join(f"{k}={v or '?'}" for k, v in script.params.items())
              if script.params else "none")
    header = f"'{script.name}'  |  params: {params}"
    return True, header + "\n" + "\n".join(lines)


def apply_to_lines(lines, section_map, spec, scripts=None, config=None,
                   param_values=None, progress=None, restore_first=False):
    """
    Apply a spec + scripts to an already-loaded (lines, section_map) in place.
    Returns (RunSummary, [RunResult]). Used by both apply_and_save and preview.

    restore_first: revert existing tool annotations to vanilla before applying, so
    an in-place re-apply rebuilds from vanilla instead of compounding. The caller
    must rebuild section_map afterwards if it reverted (we re-read indices here).
    """
    if config is None:
        config = ModConfig.default()
    if restore_first:
        restore_vanilla(lines)
        section_map.clear()
        section_map.update(build_section_map(lines)[0])
    summary = run_balance(lines, section_map, spec, config, progress=progress)
    results = []
    for text in (scripts or []):
        if text and text.strip():
            script = parse_script(text)
            results.append(run_script(lines, section_map, script, config,
                                      param_values=param_values, progress=progress))
    return summary, results


def preview_changes(source_path, spec, scripts=None, config=None,
                    param_values=None, max_diff_lines=600):
    """
    Apply spec + scripts to an in-memory copy of the source and return
    (RunSummary, [RunResult], diff_text) without writing anything. diff_text is a
    unified diff (capped) so the UI can show exactly what would change.
    """
    import difflib
    src = parse_ini_lines(source_path)
    working = list(src)
    section_map, _ = build_section_map(working)
    summary, results = apply_to_lines(working, section_map, spec, scripts, config,
                                      param_values)
    diff = list(difflib.unified_diff(
        [l.rstrip("\r\n") for l in src],
        [l.rstrip("\r\n") for l in working],
        fromfile="vanilla", tofile="edited", lineterm="", n=1))
    truncated = len(diff) > max_diff_lines
    if truncated:
        diff = diff[:max_diff_lines]
        diff.append(f"... (diff truncated at {max_diff_lines} lines)")
    return summary, results, "\n".join(diff) if diff else "(no changes)"


class WorkingSession:
    """
    A persistent in-memory edit built up across several applies. Each apply
    regenerates the working copy from the pristine source, so re-applying never
    compounds and feature ordering is always correct. Save writes the working copy.
    """

    def __init__(self, source_path, config=None):
        self.source_path = source_path
        self.config = config or ModConfig.default()
        self.lines = None
        self.summary = None
        self.results = None
        self.dirty = False

    def apply(self, spec, scripts=None, param_values=None, progress=None):
        lines, section_map = load_rules(self.source_path)
        self.summary, self.results = apply_to_lines(
            lines, section_map, spec, scripts, self.config, param_values, progress)
        self.lines = lines
        self.dirty = True
        return self.summary, self.results

    def diff_text(self, max_lines=600):
        if self.lines is None:
            return "(nothing applied yet)"
        import difflib
        src = parse_ini_lines(self.source_path)
        diff = list(difflib.unified_diff(
            [l.rstrip("\r\n") for l in src],
            [l.rstrip("\r\n") for l in self.lines],
            fromfile="vanilla", tofile="working", lineterm="", n=1))
        if len(diff) > max_lines:
            diff = diff[:max_lines] + [f"... (diff truncated at {max_lines} lines)"]
        return "\n".join(diff) if diff else "(no changes)"

    def change_count(self):
        """Number of changed/added lines in the working copy (rough magnitude)."""
        if self.lines is None:
            return 0
        import difflib
        src = parse_ini_lines(self.source_path)
        sm = difflib.SequenceMatcher(
            None, [l.rstrip("\r\n") for l in src],
            [l.rstrip("\r\n") for l in self.lines])
        return sum(max(i2 - i1, j2 - j1)
                   for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")

    def save(self, out_path):
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            f.writelines(self.lines)
        self.dirty = False


def apply_and_save(source_path, out_path, spec, scripts=None, config=None,
                   param_values=None, progress=None, restore_first=False):
    """
    Re-load the source rules, apply the balance spec (Pass 1 + sight) and each
    script, and write to out_path. Returns (RunSummary, [RunResult]).

    restore_first (used for in-place writes) reverts existing annotations to vanilla
    before applying so repeated in-place applies don't compound.
    """
    lines, section_map = load_rules(source_path)
    summary, results = apply_to_lines(lines, section_map, spec, scripts, config,
                                      param_values, progress,
                                      restore_first=restore_first)
    # newline="" so each line's own \r\n / \n (preserved by the parser) is written
    # verbatim rather than translated by the platform.
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)
    return summary, results


def summarize(summary, script_results):
    """Render a short human summary of an apply run."""
    out = []
    processed = {k: v for k, v in summary.per_category.items() if v}
    if processed:
        out.append("Balance pass:")
        for k, n in processed.items():
            out.append(f"  {k}: {n} technos")
    if summary.techlevels_written:
        out.append(f"TechLevel written: {summary.techlevels_written}")
    if summary.costs_scaled:
        out.append(f"Costs scaled (TechLevel/depth): {summary.costs_scaled}")
    if summary.weapon_conflicts:
        out.append(f"Weapon conflicts resolved by cloning: {summary.weapon_conflicts}")
    total_tags = sum(r.tags_written for r in script_results)
    total_secs = sum(r.sections_created for r in script_results)
    total_skip = sum(len(r.skipped) for r in script_results)
    if total_tags or total_secs:
        out.append(f"Scripts: {total_tags} tags, {total_secs} sections created")
        if total_skip:
            out.append(f"  {total_skip} tag(s) skipped")
    if not out:
        out.append("No changes (everything left at 1.0 and no scripts).")
    return "\n".join(out)
