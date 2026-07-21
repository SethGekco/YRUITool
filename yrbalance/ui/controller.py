"""
Qt-free controller: the glue between the UI's collected state and the engine.

Keeping this Qt-free means the whole apply/preview flow is unit-testable without
constructing widgets or a QApplication.
"""

from ..engine import (
    parse_ini_lines, build_section_map,
    run_balance, parse_script, run_script, resolve_scope, ScriptError,
    ModConfig,
)


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
                   param_values=None, progress=None):
    """
    Apply a spec + scripts to an already-loaded (lines, section_map) in place.
    Returns (RunSummary, [RunResult]). Used by both apply_and_save and preview.
    """
    if config is None:
        config = ModConfig.default()
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


def apply_and_save(source_path, out_path, spec, scripts=None, config=None,
                   param_values=None, progress=None):
    """
    Re-load the source rules, apply the balance spec (Pass 1 + sight) and each
    script, and write to out_path. Always re-loads from source so repeated applies
    don't compound. Returns (RunSummary, [RunResult]).
    """
    lines, section_map = load_rules(source_path)
    summary, results = apply_to_lines(lines, section_map, spec, scripts, config,
                                      param_values, progress)
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
