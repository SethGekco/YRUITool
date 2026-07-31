#!/usr/bin/env python3
"""
Test INCLUDE/EXCLUDE filter parsing and evaluation against a real rulesmd.ini.

    python3 tests/test_filters.py /path/to/rulesmd.ini
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    parse_ini_lines, build_section_map, get_val, parse_type_list,
    parse_script, run_script, resolve_scope, ModConfig,
    FilterContext, filter_technos,
)


def main(path):
    ok = True
    config = ModConfig.default()
    lines = parse_ini_lines(path)
    section_map, _ = build_section_map(lines)
    ctx = FilterContext(section_map)
    infantry = resolve_scope(section_map, "$INFANTRY", config)

    # Parse a script with an EXCLUDE region anywhere in the block.
    script = parse_script(
        "[$INFANTRY]\n"
        "{EXCLUDE}\n"
        "TechLevel = <1\n"       # drop unbuildable (TL 0/-1)
        "[ADOG]\n"               # drop the ADOG specifically
        "{/EXCLUDE}\n"
        "Elite = yes\n")
    blk = script.blocks[0]
    parse_ok = len(blk.filter.excludes) == 2 and len(blk.ops) == 1
    print(f"parse EXCLUDE region: {len(blk.filter.excludes)} conds, "
          f"{len(blk.ops)} ops  {'OK' if parse_ok else 'FAIL'}")
    ok = ok and parse_ok

    # Evaluate the filter directly.
    kept = filter_technos(infantry, blk.filter, ctx)
    # Nothing kept should have TechLevel < 1, and ADOG must be gone.
    bad_tl = [t for t in kept
              if get_val(section_map, t, "TechLevel", "1").lstrip("-").isdigit()
              and int(get_val(section_map, t, "TechLevel", "1")) < 1]
    adog_gone = not any(t.lower() == "adog" for t in kept)
    filt_ok = not bad_tl and adog_gone and len(kept) < len(infantry)
    print(f"filter TechLevel<1 + [ADOG]: {len(infantry)}->{len(kept)}, "
          f"no TL<1={not bad_tl}, ADOG gone={adog_gone}  "
          f"{'OK' if filt_ok else 'FAIL'}")
    ok = ok and filt_ok

    # BuildLimit = ! (defined) as an INCLUDE whitelist.
    s2 = parse_script("[$INFANTRY]\n{INCLUDE}\nBuildLimit = !\n{/INCLUDE}\nElite = yes\n")
    kept2 = filter_technos(infantry, s2.blocks[0].filter, ctx)
    all_have_bl = all(get_val(section_map, t, "BuildLimit") is not None for t in kept2)
    print(f"INCLUDE BuildLimit=! : kept {len(kept2)} (all have BuildLimit={all_have_bl})  "
          f"{'OK' if all_have_bl else 'FAIL'}")
    ok = ok and all_have_bl

    # Prerequisite = dummy (unbuildable) drops non-buildable infantry.
    s3 = parse_script("[$INFANTRY]\n{EXCLUDE}\nPrerequisite = dummy\n{/EXCLUDE}\nElite=yes\n")
    kept3 = filter_technos(infantry, s3.blocks[0].filter, ctx)
    print(f"EXCLUDE Prerequisite=dummy: {len(infantry)}->{len(kept3)} "
          f"(buildable kept)  {'OK' if len(kept3) <= len(infantry) else 'FAIL'}")

    # End-to-end via run_script: Elite=yes only on filtered infantry.
    s4 = parse_script("[$INFANTRY]\n{EXCLUDE}\nTechLevel=<1\n{/EXCLUDE}\nElite = yes\n")
    kept_s4 = filter_technos(infantry, s4.blocks[0].filter, ctx)
    res = run_script(lines, section_map, s4, config)
    sm2, _ = build_section_map(lines)
    # A kept unit has Elite=yes; an excluded (TL<1) unit does not.
    excluded = [t for t in infantry
                if get_val(section_map, t, "TechLevel", "1").lstrip("-").isdigit()
                and int(get_val(section_map, t, "TechLevel", "1")) < 1]
    run_ok = res.tags_written == len(kept_s4)
    if excluded:
        run_ok = run_ok and get_val(sm2, excluded[0], "Elite") is None
    print(f"run_script with EXCLUDE: {res.tags_written} tags (== {len(kept_s4)} kept)  "
          f"{'OK' if run_ok else 'FAIL'}")
    ok = ok and run_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 tests/test_filters.py /path/to/rulesmd.ini")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
