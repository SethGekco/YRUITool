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


def apply_and_save(source_path, out_path, spec, script_text=None, config=None,
                   param_values=None, progress=None):
    """
    Re-load the source rules, apply the balance spec (Pass 1 + sight), then any
    custom script, and write to out_path. Always re-loads from source so repeated
    applies don't compound. Returns (RunSummary, script_RunResult|None).
    """
    if config is None:
        config = ModConfig.default()

    lines, section_map = load_rules(source_path)
    summary = run_balance(lines, section_map, spec, config, progress=progress)

    script_result = None
    if script_text and script_text.strip():
        script = parse_script(script_text)
        script_result = run_script(lines, section_map, script, config,
                                   param_values=param_values, progress=progress)

    # newline="" so each line's own \r\n / \n (preserved by the parser) is written
    # verbatim rather than translated by the platform.
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)

    return summary, script_result


def summarize(summary, script_result):
    """Render a short human summary of an apply run."""
    out = []
    processed = {k: v for k, v in summary.per_category.items() if v}
    if processed:
        out.append("Balance pass:")
        for k, n in processed.items():
            out.append(f"  {k}: {n} technos")
    if summary.weapon_conflicts:
        out.append(f"Weapon conflicts resolved by cloning: {summary.weapon_conflicts}")
    if script_result is not None:
        out.append(f"Script: {script_result.tags_written} tags on "
                   f"{script_result.technos_touched} technos, "
                   f"{script_result.sections_created} sections created")
        if script_result.skipped:
            out.append(f"  {len(script_result.skipped)} tag(s) skipped")
    if not out:
        out.append("No changes (everything left at 1.0 and no script).")
    return "\n".join(out)
