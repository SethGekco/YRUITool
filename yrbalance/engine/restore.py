"""
Restore-to-vanilla: undo the tool's stat edits by reading the inline annotations.

Every value the tool writes carries its original in a `; vanilla=` comment (or, for
soylent, the original after the first `;` alongside `vanilla_cost=`). restore_vanilla
rewrites each such line back to that original, stripping the annotation.

Limitation: this reverts scaled/edited *values*. Whole sections added by generator
scripts (clones, platforms) and brand-new tags are not removed — that is a separate,
heavier undo. This is enough to roll a rules file's balance numbers back to vanilla.
"""

import re

from .ini_model import line_ending, parse_vanilla_from_line, _parse_number


def restore_vanilla(lines):
    """Revert tool-edited lines to their vanilla values in place. Returns count."""
    count = 0
    for i, l in enumerate(lines):
        stripped = l.strip()
        if not stripped or stripped.startswith("[") or stripped.startswith(";"):
            continue

        van = parse_vanilla_from_line(l)
        if van is None and "vanilla_cost=" in l:
            # Soylent line: "new;<orig_soylent> Edit by ... ; vanilla_cost=X"
            m = re.match(r'^\s*[^=]+=\s*[^;]*;\s*([0-9.]+)', l)
            if m:
                van = _parse_number(m.group(1))
        if van is None:
            continue

        m = re.match(r'^(\s*)([^=;]+)=', l)
        if not m:
            continue
        ending = line_ending(l) or "\n"
        lines[i] = f"{m.group(1)}{m.group(2).strip()}={van}{ending}"
        count += 1
    return count
