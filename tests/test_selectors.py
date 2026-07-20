#!/usr/bin/env python3
"""
Test the config category model + scope resolution against a real rulesmd.ini.

    python3 tests/test_selectors.py /path/to/rulesmd.ini
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    parse_ini_lines, build_section_map, ModConfig,
    build_category_lists, resolve_scope, available_keywords,
)


def main(path):
    lines = parse_ini_lines(path)
    section_map, _ = build_section_map(lines)
    config = ModConfig.default()

    cats = build_category_lists(section_map, config)
    print("category counts:")
    total = 0
    for key in ("inf", "veh", "nav", "air", "bld", "def"):
        n = len(cats.get(key, []))
        total += n
        print(f"  {key:4s}: {n}")

    print(f"\navailable keywords: {', '.join(available_keywords(config))}")

    ok = True

    # $INFANTRY resolves to the infantry list.
    inf = resolve_scope(section_map, "$INFANTRY", config)
    check = len(inf) == len(cats["inf"]) and len(inf) > 0
    print(f"\n$INFANTRY -> {len(inf)} technos  {'OK' if check else 'FAIL'}")
    ok = ok and check

    # $STRUCTURE = bld + def, de-duplicated by section name (the index may list a
    # building twice; resolve_scope collapses that).
    struct = resolve_scope(section_map, "$STRUCTURE", config)
    expected = len({c.lower() for c in (cats["bld"] + cats["def"])})
    check = len(struct) == expected
    print(f"$STRUCTURE -> {len(struct)} (unique bld+def={expected})  "
          f"{'OK' if check else 'FAIL'}")
    ok = ok and check

    # Combined scope with dedup: "$VEHICLE, $NAVAL, E1" — E1 added once, no dupes.
    combo = resolve_scope(section_map, "$VEHICLE, $NAVAL, E1", config)
    lows = [c.lower() for c in combo]
    check = len(lows) == len(set(lows))  # no duplicates
    e1_present = any(c.lower() == "e1" for c in combo) if section_map.get("e1") else True
    print(f"'$VEHICLE, $NAVAL, E1' -> {len(combo)} technos, no dupes={check}, "
          f"E1 present={e1_present}")
    ok = ok and check and e1_present

    # Unknown literal is dropped.
    junk = resolve_scope(section_map, "NOTAREALSECTION_XYZ", config)
    check = len(junk) == 0
    print(f"unknown literal -> {len(junk)} (expect 0)  {'OK' if check else 'FAIL'}")
    ok = ok and check

    # dying_earth profile reproduces the original tokens.
    de = ModConfig.dying_earth()
    check = (de.infantry_clone_prefix == "DEGB" and de.vehicle_clone_prefix == "DEAT"
             and de.unbuildable_prereq == "CAPARK01" and de.platform_prefix == "ATPLAT_")
    print(f"\ndying_earth profile tokens: {'OK' if check else 'FAIL'} "
          f"(DEGB/DEAT/CAPARK01/ATPLAT_)")
    ok = ok and check

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 tests/test_selectors.py /path/to/rulesmd.ini")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
