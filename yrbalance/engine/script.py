"""
Modifier-script parsing.

A script is the INI-flavored text described in docs/SCRIPTS.md: optional `#`
metadata, then one or more `[scope]` target blocks each followed by `Tag = value`
lines. This module only parses; `runner.py` applies the result.

Generator blocks (`[+ ... from ...]` / `@` directives) are recognized and parsed
into a Generator marker, but executing them is a later slice — the runner raises a
clear error if asked to run one, so scripts stay forward-compatible.
"""

import re
from dataclasses import dataclass, field

from .filters import Filter, parse_condition


class ScriptError(Exception):
    """Raised for malformed modifier scripts."""


@dataclass
class TagOp:
    tag: str
    rhs: str          # raw right-hand side (params/NAME not yet substituted)
    line_no: int = 0


@dataclass
class TargetBlock:
    scope: str
    ops: list = field(default_factory=list)
    is_generator: bool = False
    header: str = ""   # original header text (for generator parsing later)
    filter: Filter = field(default_factory=Filter)  # INCLUDE/EXCLUDE conditions


@dataclass
class ModifierScript:
    name: str = "Untitled"
    desc: str = ""
    params: dict = field(default_factory=dict)   # {name: default_string}
    blocks: list = field(default_factory=list)
    raw_mode: bool = False                        # #raw: true -> no annotation
    has_generators: bool = False


def _strip_inline_comment(s):
    """Remove an inline ';' comment, honoring single/double quotes."""
    out = []
    quote = None
    for ch in s:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        else:
            if ch in "\"'":
                quote = ch
                out.append(ch)
            elif ch == ";":
                break
            else:
                out.append(ch)
    return "".join(out).rstrip()


def _parse_bool(v):
    return str(v).strip().lower() in ("true", "yes", "1", "on")


def parse_script(text):
    """Parse modifier-script text into a ModifierScript. Raises ScriptError."""
    script = ModifierScript()
    current = None
    filter_mode = None  # None | "include" | "exclude"

    for i, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        # INCLUDE / EXCLUDE region markers.
        mark = re.match(r"^\{\s*(/?)(exclude|include)\s*\}$", line, re.IGNORECASE)
        if mark:
            if current is None:
                raise ScriptError(f"line {i}: {{{mark.group(2)}}} before any [scope]")
            filter_mode = None if mark.group(1) == "/" else mark.group(2).lower()
            continue

        # Condition lines inside an INCLUDE/EXCLUDE region.
        if filter_mode is not None and current is not None and not line.startswith("#"):
            content = _strip_inline_comment(line)
            if content:
                cond = parse_condition(content)
                if cond is not None:
                    target = (current.filter.excludes if filter_mode == "exclude"
                              else current.filter.includes)
                    target.append(cond)
            continue

        # Metadata.
        if line.startswith("#"):
            body = line[1:].strip()
            key, sep, val = body.partition(":")
            if not sep:
                continue  # bare '#' comment
            key = key.strip().lower()
            val = val.strip()
            if key == "name":
                script.name = val or script.name
            elif key in ("desc", "description"):
                script.desc = val
            elif key == "param":
                pname, psep, pdef = val.partition("=")
                pname = pname.strip()
                if not pname:
                    raise ScriptError(f"line {i}: #param needs a name")
                script.params[pname] = pdef.strip() if psep else ""
            elif key == "raw":
                script.raw_mode = _parse_bool(val)
            continue

        # Target header.
        if line.startswith("["):
            m = re.match(r"^\[(.*)\]\s*$", _strip_inline_comment(line))
            if not m:
                raise ScriptError(f"line {i}: malformed header: {raw_line!r}")
            inner = m.group(1).strip()
            is_gen = inner.startswith("+")
            if is_gen:
                script.has_generators = True
            current = TargetBlock(
                scope=inner.lstrip("+").strip() if is_gen else inner,
                is_generator=is_gen, header=inner)
            script.blocks.append(current)
            filter_mode = None
            continue

        # Generator directive (only valid inside a generator block).
        if line.startswith("@"):
            if current is None or not current.is_generator:
                raise ScriptError(f"line {i}: '@' directive outside a generator block")
            script.has_generators = True
            current.ops.append(TagOp(tag="@directive", rhs=line, line_no=i))
            continue

        # Tag line.
        content = _strip_inline_comment(line)
        if not content:
            continue
        if current is None:
            raise ScriptError(
                f"line {i}: tag before any [scope] header: {raw_line!r}")
        tag, sep, rhs = content.partition("=")
        if not sep:
            raise ScriptError(f"line {i}: expected 'Tag = value': {raw_line!r}")
        tag = tag.strip()
        if not tag:
            raise ScriptError(f"line {i}: empty tag name: {raw_line!r}")
        current.ops.append(TagOp(tag=tag, rhs=rhs.strip(), line_no=i))

    if not script.blocks:
        raise ScriptError("script has no [scope] blocks")
    return script


def load_script_file(path):
    """Read and parse a script file."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return parse_script(f.read())
