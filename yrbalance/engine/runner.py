"""
Apply a parsed ModifierScript to a rules file (lines + section_map).

For each target block it resolves the scope to concrete technos, then for each
techno and each tag line:
  1. substitutes {param}/{NAME}/{CONFIG_KEYWORD} placeholders,
  2. auto-detects formula vs literal,
  3. evaluates formulas against that techno's own numeric tags (chained: a value
     set earlier in the block is visible to later formulas),
  4. writes the result, with the standard `; vanilla=` annotation unless the script
     is #raw.

Formula errors (e.g. a referenced tag the techno lacks) skip just that tag and are
collected in the result rather than aborting the run.
"""

import re
from dataclasses import dataclass, field

from .ini_model import (
    get_val, modify_line, parse_vanilla_from_line,
    get_current_field_value_from_line,
)
from .sections import set_key_in_appended_section
from .selectors import resolve_scope
from .config import ModConfig
from .formula import evaluate, looks_like_formula, strip_quotes, FormulaError
from .techlevel import get_effective_tl

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class RunError(Exception):
    """Raised for problems that abort a run (missing param, generator, ...)."""


@dataclass
class RunResult:
    technos_touched: int = 0
    tags_written: int = 0
    skipped: list = field(default_factory=list)   # (techno, tag, reason)


def _to_num(s):
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return None


def _fmt_num(n):
    if isinstance(n, float) and n == int(n):
        n = int(n)
    return str(n)


def config_keywords(config):
    """Uppercase config placeholders usable as {KEYWORD} in scripts."""
    return {
        "UNBUILDABLE": config.unbuildable_prereq,
        "PLATFORM_PREFIX": config.platform_prefix,
        "HOST_SUFFIX": config.host_suffix,
        "AI_HOST_SUFFIX": config.ai_host_suffix,
        "INFANTRY_CLONE_PREFIX": config.infantry_clone_prefix,
        "VEHICLE_CLONE_PREFIX": config.vehicle_clone_prefix,
    }


def _numeric_variables(section_map, name):
    """Build {tag: number} for a techno from its own numeric tags + primary weapon."""
    variables = {}
    s = section_map.get(name.lower())
    if not s:
        return variables
    for _klow, (_li, kr, vr) in s["keys"].items():
        num = _to_num(vr.split(";")[0].strip())
        if num is not None:
            variables[kr] = num
    primary = get_val(section_map, name, "Primary")
    if primary:
        ws = section_map.get(primary.lower())
        if ws:
            for field_key, var in (("damage", "Damage"), ("range", "Range"), ("rof", "ROF")):
                e = ws["keys"].get(field_key)
                if e:
                    num = _to_num(e[2].split(";")[0].strip())
                    if num is not None and var not in variables:
                        variables[var] = num
    etl = _to_num(str(get_effective_tl(section_map, name)))
    if etl is not None:
        variables["EffectiveTechLevel"] = etl
    return variables


def _substitute(rhs, subs, techno, line_no):
    """Replace {placeholders} in rhs from subs. Unknown placeholder -> RunError."""
    def repl(m):
        key = m.group(1)
        if key in subs:
            return str(subs[key])
        raise RunError(
            f"line {line_no}: unknown placeholder {{{key}}} (techno {techno})")
    return _PLACEHOLDER.sub(repl, rhs)


def _write_tag(lines, section_map, name, tag, value_str, annotate):
    """Set a tag to value_str, with vanilla annotation when annotate is True."""
    s = section_map.get(name.lower())
    entry = s["keys"].get(tag.lower()) if s else None
    if annotate and entry is not None:
        li = entry[0]
        old_val = get_current_field_value_from_line(lines[li])
        van = parse_vanilla_from_line(lines[li])
        if van is None:
            van = old_val if old_val is not None else value_str
        payload = f"{value_str};{old_val} Edit by script ; vanilla={van}"
    elif annotate:
        payload = f"{value_str};added by script"
    else:
        payload = value_str
    set_key_in_appended_section(lines, section_map, name, tag, payload)


def run_script(lines, section_map, script, config=None, param_values=None,
               progress=None):
    """
    Apply `script` to lines/section_map. `param_values` overrides script defaults.
    Returns a RunResult. Raises RunError on missing params or generator blocks.
    """
    if config is None:
        config = ModConfig.default()
    if script.has_generators:
        raise RunError("this script contains generator blocks ([+ ...]/@...) "
                       "which are not supported yet")

    # Merge params: script defaults <- caller overrides. Empty default = required.
    params = dict(script.params)
    if param_values:
        params.update({k: v for k, v in param_values.items() if v is not None})
    for pname, pval in params.items():
        if pval == "" or pval is None:
            raise RunError(f"parameter '{pname}' has no value")

    ck = config_keywords(config)
    annotate = not script.raw_mode
    result = RunResult()
    touched = set()

    for block in script.blocks:
        if block.is_generator:
            raise RunError("generator blocks are not supported yet")
        technos = resolve_scope(section_map, block.scope, config)
        for name in technos:
            variables = _numeric_variables(section_map, name)
            subs = {**ck, **params, "NAME": name}
            wrote_here = False
            for op in block.ops:
                rhs = _substitute(op.rhs, subs, name, op.line_no)
                known = set(variables) | set(params) | set(ck) | {"NAME"}
                if looks_like_formula(rhs, known):
                    try:
                        val = evaluate(rhs, variables)
                    except FormulaError as e:
                        result.skipped.append((name, op.tag, str(e)))
                        continue
                    value_str = _fmt_num(val)
                    variables[op.tag] = val  # chain: later formulas see it
                else:
                    value_str = strip_quotes(rhs)
                    num = _to_num(value_str)
                    if num is not None:
                        variables[op.tag] = num
                _write_tag(lines, section_map, name, op.tag, value_str, annotate)
                result.tags_written += 1
                wrote_here = True
            if wrote_here:
                touched.add(name)
        if progress:
            progress(f"  [{block.scope}] {len(technos)} technos")

    result.technos_touched = len(touched)
    return result
