"""
Apply a parsed ModifierScript to a rules file (lines + section_map).

Two kinds of block:

* Modify block  `[scope]` — for each techno in scope, apply each `Tag = value`:
  substitute {param}/{NAME}/{CONFIG} placeholders, auto-detect formula vs literal,
  evaluate formulas against that techno's own numeric tags (chained), and write the
  result with the standard `; vanilla=` annotation (unless the script is #raw).

* Generator block  `[+ PATTERN from SCOPE]` (or `[+ PATTERN]` once) — for each
  techno in scope, create/emit new sections and register/patch them:
    @clone SRC          fill the current generated section from SRC's body
    Tag = value         set a tag on the current generated section (plain, no anno)
    @register IDX [as P] add the current section to index list IDX (keys P####)
    @emit NAME          start another fresh generated section as current target
    @patch NAME         switch to modifying an existing section (annotated); the
                        alias `source` means the current foreach techno
  Placeholders: {NAME}=source techno, {THIS}=current generated name, plus params
  and {CONFIG} keywords. A generator iteration is skipped if its primary section
  already exists (idempotent re-runs).

Formula errors on a single tag skip just that tag and are collected in the result.
"""

import re
from dataclasses import dataclass, field

from .ini_model import (
    get_val, parse_vanilla_from_line, get_current_field_value_from_line,
)
from .sections import (
    set_key_in_appended_section, append_raw_section, get_section_raw_lines,
    new_registration_state, register_in_list,
)
from .selectors import resolve_scope
from .filters import FilterContext, filter_technos
from .config import ModConfig
from .formula import evaluate, looks_like_formula, strip_quotes, FormulaError
from .techlevel import get_effective_tl

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class RunError(Exception):
    """Raised for problems that abort a run (missing param, bad directive, ...)."""


@dataclass
class RunResult:
    technos_touched: int = 0
    tags_written: int = 0
    sections_created: int = 0
    registered: int = 0
    generated: list = field(default_factory=list)   # names of created sections
    skipped: list = field(default_factory=list)     # (techno_or_section, tag, reason)


def _to_num(s):
    try:
        return int(s)
    except (ValueError, TypeError):
        try:
            return float(s)
        except (ValueError, TypeError):
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
    """Build {tag: number} for a section from its numeric tags + primary weapon."""
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


def _substitute(text, subs, where, line_no):
    """Replace {placeholders} in text from subs. Unknown placeholder -> RunError."""
    def repl(m):
        key = m.group(1)
        if key in subs:
            return str(subs[key])
        raise RunError(f"line {line_no}: unknown placeholder {{{key}}} ({where})")
    return _PLACEHOLDER.sub(repl, text)


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


def _apply_tag_op(lines, section_map, target, op, subs, variables, annotate, result):
    """Substitute, auto-detect, evaluate/literal, and write one tag op to target."""
    rhs = _substitute(op.rhs, subs, target, op.line_no)
    known = set(variables) | set(subs)
    if looks_like_formula(rhs, known):
        try:
            val = evaluate(rhs, variables)
        except FormulaError as e:
            result.skipped.append((target, op.tag, str(e)))
            return
        value_str = _fmt_num(val)
        variables[op.tag] = val
    else:
        value_str = strip_quotes(rhs)
        num = _to_num(value_str)
        if num is not None:
            variables[op.tag] = num
    _write_tag(lines, section_map, target, op.tag, value_str, annotate)
    result.tags_written += 1


# ── Modify blocks ────────────────────────────────────────────────────────────

def _run_modify_block(lines, section_map, block, subs_base, config, annotate,
                      result, progress, filter_ctx):
    technos = resolve_scope(section_map, block.scope, config)
    technos = filter_technos(technos, block.filter, filter_ctx)
    touched = 0
    for name in technos:
        variables = _numeric_variables(section_map, name)
        subs = {**subs_base, "NAME": name}
        wrote = False
        for op in block.ops:
            _apply_tag_op(lines, section_map, name, op, subs, variables,
                          annotate, result)
            wrote = True
        if wrote:
            touched += 1
    if progress:
        progress(f"  [{block.scope}] {len(technos)} technos")
    return touched


# ── Generator blocks ─────────────────────────────────────────────────────────

_DEFAULT_REG_PREFIX = {
    "infantrytypes": "infantry_clone_prefix",
    "vehicletypes": "vehicle_clone_prefix",
    "attachmenttypes": "attachment_id_prefix",
}


def _parse_gen_header(scope):
    """Split '[+ PATTERN from SCOPE]' into (pattern, foreach_scope|None)."""
    m = re.split(r"\s+from\s+", scope, maxsplit=1, flags=re.IGNORECASE)
    if len(m) == 2:
        return m[0].strip(), m[1].strip()
    return scope.strip(), None


def _ending_for(lines):
    return "\r\n" if lines and lines[-1].endswith("\r\n") else "\n"


def _ensure_section(lines, section_map, name):
    """Create an empty [name] section if absent. Returns True if it already existed."""
    if name.lower() in section_map:
        return True
    ending = _ending_for(lines)
    append_raw_section(lines, section_map, name, [f"[{name}]{ending}"], ending)
    return False


def _clone_into(lines, section_map, target, source):
    """Create `target` as a copy of `source`'s body. No-op if target exists."""
    if target.lower() in section_map:
        return
    if source.lower() not in section_map:
        raise RunError(f"@clone source not found: {source}")
    ending = _ending_for(lines)
    raw = get_section_raw_lines(lines, section_map, source)
    clone_raw = [f"[{target}]{ending}"] + [l.rstrip("\r\n") + ending for l in raw[1:]]
    append_raw_section(lines, section_map, target, clone_raw, ending)


def _reg_prefix_for(index_section, config):
    attr = _DEFAULT_REG_PREFIX.get(index_section.lower())
    return getattr(config, attr) if attr else "GEN"


_NAME_SENTINEL = "\x00"


def _pattern_affixes(pattern, subs_base):
    """
    Return the literal (prefix, suffix) the name pattern wraps {NAME} in, e.g.
    '{NAME}{HOST_SUFFIX}' -> ('', '_HOST'). Used to skip already-generated sources
    on re-runs so a generator doesn't build clones-of-clones.
    """
    try:
        filled = _substitute(pattern, {**subs_base, "NAME": _NAME_SENTINEL},
                             "generator", 0)
    except RunError:
        return "", ""
    if _NAME_SENTINEL not in filled:
        return "", ""
    pre, _sep, post = filled.partition(_NAME_SENTINEL)
    return pre, post


def _is_generated_product(src, pre, suf):
    """True if src looks like something this pattern already produced."""
    if not pre and not suf:
        return False
    if len(src) <= len(pre) + len(suf):
        return False
    return src.startswith(pre) and src.endswith(suf)


def _run_generator_block(lines, section_map, block, subs_base, config,
                         reg_state, result, progress, filter_ctx):
    pattern, foreach = _parse_gen_header(block.scope)
    guard_pre, guard_suf = _pattern_affixes(pattern, subs_base)

    if foreach is not None:
        sources = filter_technos(resolve_scope(section_map, foreach, config),
                                 block.filter, filter_ctx)
    else:
        sources = [None]

    made = 0
    for src in sources:
        subs = dict(subs_base)
        if src is not None:
            subs["NAME"] = src
            # Skip sources that are already products of this generator (re-run).
            if _is_generated_product(src, guard_pre, guard_suf):
                continue

        primary = _substitute(pattern, subs, "generator", 0).strip()[:config.max_name_len]
        subs["THIS"] = primary
        if primary.lower() in section_map:
            continue  # idempotent: primary already generated

        target = primary          # current write target
        annotate = False          # generated sections written plain
        variables = {}
        variables_target = None   # rebuild variables lazily when target changes

        def ensure_empty(name):
            """Create an empty section (counting it) if missing. Returns True if created."""
            if name.lower() in section_map:
                return False
            _ensure_section(lines, section_map, name)
            result.generated.append(name)
            result.sections_created += 1
            return True

        for op in block.ops:
            if op.tag != "@directive":
                if target is None:
                    continue  # a prior @patch pointed at a missing section
                if ensure_empty(target) and target == primary:
                    made += 1
                if variables_target != target:
                    variables = _numeric_variables(section_map, target)
                    variables_target = target
                _apply_tag_op(lines, section_map, target, op, subs, variables,
                              annotate, result)
                continue

            parts = op.rhs[1:].split()
            if not parts:
                raise RunError(f"line {op.line_no}: empty directive")
            name, args = parts[0].lower(), parts[1:]

            if name == "clone":
                if not args:
                    raise RunError(f"line {op.line_no}: @clone needs a source")
                source = _substitute(args[0], subs, "generator", op.line_no)
                if source.lower() == "source":
                    source = subs.get("NAME", "")
                if target.lower() not in section_map:
                    _clone_into(lines, section_map, target, source)
                    result.generated.append(target)
                    result.sections_created += 1
                    if target == primary:
                        made += 1
                variables_target = None

            elif name == "emit":
                if not args:
                    raise RunError(f"line {op.line_no}: @emit needs a name")
                target = _substitute(args[0], subs, "generator", op.line_no).strip()[:config.max_name_len]
                annotate = False
                variables_target = None

            elif name == "patch":
                if not args:
                    raise RunError(f"line {op.line_no}: @patch needs a target")
                tgt = _substitute(args[0], subs, "generator", op.line_no)
                if tgt.lower() == "source":
                    tgt = subs.get("NAME", "")
                if tgt.lower() not in section_map:
                    result.skipped.append((tgt, "@patch", "section not found"))
                    target = None
                    continue
                target = tgt
                annotate = True
                variables_target = None

            elif name == "register":
                if not args:
                    raise RunError(f"line {op.line_no}: @register needs an index section")
                if target is None:
                    continue
                ensure_empty(target)
                index_section = args[0]
                prefix = _reg_prefix_for(index_section, config)
                if len(args) >= 3 and args[1].lower() == "as":
                    prefix = args[2]
                register_in_list(lines, section_map, index_section, reg_state,
                                 prefix, target)
                result.registered += 1

            else:
                raise RunError(f"line {op.line_no}: unknown directive @{name}")

    if progress:
        label = f"{pattern} from {foreach}" if foreach else pattern
        progress(f"  [+ {label}] created {made} section-set(s)")
    return made


# ── Entry point ──────────────────────────────────────────────────────────────

def run_script(lines, section_map, script, config=None, param_values=None,
               progress=None):
    """
    Apply `script` to lines/section_map. `param_values` overrides script defaults.
    Returns a RunResult. Raises RunError on missing params or bad directives.
    """
    if config is None:
        config = ModConfig.default()

    params = dict(script.params)
    if param_values:
        params.update({k: v for k, v in param_values.items() if v is not None})
    for pname, pval in params.items():
        if pval == "" or pval is None:
            raise RunError(f"parameter '{pname}' has no value")

    subs_base = {**config_keywords(config), **params}
    annotate_default = not script.raw_mode
    reg_state = new_registration_state()
    filter_ctx = FilterContext(section_map)
    result = RunResult()
    touched = 0

    for block in script.blocks:
        if block.is_generator:
            _run_generator_block(lines, section_map, block, subs_base, config,
                                 reg_state, result, progress, filter_ctx)
        else:
            touched += _run_modify_block(lines, section_map, block, subs_base,
                                         config, annotate_default, result, progress,
                                         filter_ctx)

    result.technos_touched = touched
    return result
