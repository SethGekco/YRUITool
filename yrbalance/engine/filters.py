"""
INCLUDE / EXCLUDE filter conditions for modifier-script blocks.

Inside a block, `{EXCLUDE} ... {/EXCLUDE}` and `{INCLUDE} ... {/INCLUDE}` regions
list conditions that narrow which technos in the scope the block's tags apply to:

  TechLevel = -1        exact match on the techno's tag
  TechLevel = <1        numeric comparison (<, >, <=, >=)
  BuildLimit = 0        exact
  BuildLimit = !        the tag is DEFINED on the techno
  Prerequisite = dummy      techno is effectively unbuildable (also: impossible)
  Prerequisite = NeutralTechBuildings   requires a [General]NeutralTechBuildings= bld
  Primary = M60         exact (case-insensitive) match
  [ADOG]                the specific section

EXCLUDE: a techno is dropped if it matches ANY exclude condition.
INCLUDE: if any include conditions exist, a techno is kept only if it matches at
least one (a whitelist). Multiple regions stack (their conditions are unioned).
"""

import re
from dataclasses import dataclass, field

from .ini_model import get_val
from .techlevel import valid_tl, parse_generic_prereqs

_UNBUILDABLE_WORDS = {"dummy", "impossible", "unbuildable"}
_NEUTRAL_WORD = "neutraltechbuildings"
_CMP = {"<=": "le", ">=": "ge", "<": "lt", ">": "gt"}


@dataclass
class Condition:
    kind: str            # 'tag' | 'section' | 'unbuildable' | 'neutral'
    tag: str = ""
    op: str = "eq"       # eq | lt | gt | le | ge | defined
    value: str = ""
    section: str = ""


@dataclass
class Filter:
    includes: list = field(default_factory=list)
    excludes: list = field(default_factory=list)

    def is_empty(self):
        return not self.includes and not self.excludes


def parse_condition(line):
    """Parse one condition line into a Condition (or None if blank)."""
    s = line.strip()
    if not s:
        return None
    if s.startswith("[") and s.endswith("]"):
        return Condition(kind="section", section=s[1:-1].strip())
    tag, sep, val = s.partition("=")
    if not sep:
        # A bare word is treated as a section name.
        return Condition(kind="section", section=s)
    tag, val = tag.strip(), val.strip()
    low_tag, low_val = tag.lower(), val.lower()

    if low_tag == "prerequisite" and low_val in _UNBUILDABLE_WORDS:
        return Condition(kind="unbuildable", tag=tag)
    if low_tag == "prerequisite" and low_val == _NEUTRAL_WORD:
        return Condition(kind="neutral", tag=tag)
    if val == "!":
        return Condition(kind="tag", tag=tag, op="defined")
    for sym, op in _CMP.items():
        if val.startswith(sym):
            return Condition(kind="tag", tag=tag, op=op, value=val[len(sym):].strip())
    return Condition(kind="tag", tag=tag, op="eq", value=val)


class FilterContext:
    """Precomputed data shared by condition evaluation for one run."""

    def __init__(self, section_map):
        self.section_map = section_map
        self.generics = parse_generic_prereqs(section_map)
        neutral = get_val(section_map, "General", "NeutralTechBuildings", "") or ""
        self.neutral = {b.strip().upper() for b in neutral.split(",") if b.strip()}

    def _prereq_tokens(self, name):
        tokens = set()
        for tag in ("Prerequisite", "PrerequisiteOverride"):
            csv = get_val(self.section_map, name, tag, "") or ""
            tokens.update(t.strip().upper() for t in csv.split(",") if t.strip())
        try:
            n = int(get_val(self.section_map, name, "Prerequisite.Lists", "0"))
        except (TypeError, ValueError):
            n = 0
        for i in range(1, n + 1):
            csv = get_val(self.section_map, name, f"Prerequisite.List{i}", "") or ""
            tokens.update(t.strip().upper() for t in csv.split(",") if t.strip())
        return tokens

    def is_unbuildable(self, name):
        """Heuristic: not player-buildable — invalid TechLevel, or a prerequisite
        token that is neither a generic nor an existing section (a dummy)."""
        if valid_tl(get_val(self.section_map, name, "TechLevel", "")) is None:
            return True
        for tok in self._prereq_tokens(name):
            if tok in self.generics:
                continue
            if self.section_map.get(tok.lower()) is None:
                return True
        return False

    def requires_neutral(self, name):
        if not self.neutral:
            return False
        return bool(self._prereq_tokens(name) & self.neutral)


def _as_num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def evaluate(cond, name, ctx):
    """Return True if `name` matches the condition."""
    sm = ctx.section_map
    if cond.kind == "section":
        return name.lower() == cond.section.lower()
    if cond.kind == "unbuildable":
        return ctx.is_unbuildable(name)
    if cond.kind == "neutral":
        return ctx.requires_neutral(name)
    # tag conditions
    cur = get_val(sm, name, cond.tag)
    if cond.op == "defined":
        return cur is not None
    if cur is None:
        return False
    if cond.op == "eq":
        return cur.strip().lower() == cond.value.strip().lower()
    a, b = _as_num(cur), _as_num(cond.value)
    if a is None or b is None:
        return False
    if cond.op == "lt":
        return a < b
    if cond.op == "gt":
        return a > b
    if cond.op == "le":
        return a <= b
    if cond.op == "ge":
        return a >= b
    return False


def filter_technos(technos, flt, ctx):
    """Apply an include/exclude Filter to a list of techno names."""
    if flt is None or flt.is_empty():
        return technos
    result = []
    for name in technos:
        if flt.includes and not any(evaluate(c, name, ctx) for c in flt.includes):
            continue
        if any(evaluate(c, name, ctx) for c in flt.excludes):
            continue
        result.append(name)
    return result
