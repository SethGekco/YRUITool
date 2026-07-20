#!/usr/bin/env python3
"""
End-to-end test of the TechLevel depth tree and cost scaling. Computes building
depths from the real tech tree, checks the classic anchors, then applies depth-
based cost scaling — all in memory.

    python3 tests/test_techlevel.py /path/to/rulesmd.ini
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    parse_ini_lines, build_section_map, get_val,
    parse_type_list, parse_generic_prereqs, compute_building_depths,
    apply_depth_cost_scaling, get_three_costs, valid_tl,
)


def main(path):
    lines = parse_ini_lines(path)
    section_map, _ = build_section_map(lines)

    generics = parse_generic_prereqs(section_map)
    print("generic prerequisite tokens:")
    for tok, members in sorted(generics.items()):
        print(f"  {tok:10s} -> {len(members)} members ({', '.join(members[:4])}...)")

    buildings = parse_type_list(section_map, "BuildingTypes")
    print(f"\nbuildings: {len(buildings)}")

    depths = compute_building_depths(section_map, buildings, generics,
                                     generic_agg="lowest", progress=print)
    depths.pop("_generic_depths", None)

    # Classic Allied tech tree anchors from the project notes.
    expected = {"GAPOWR": 1, "GAPILE": 2, "GAWEAP": 3, "GATECH": 4, "GACSPH": 5}
    print("\ndepth anchors (expected build-order depth):")
    for b, exp in expected.items():
        if b.lower() in depths:
            got = depths[b.lower()]
            flag = "OK" if got == exp else f"got {got}, expected {exp}"
            print(f"  [{b}] depth={got}  ({flag})")
        else:
            print(f"  [{b}] not present in this rules file")

    # Apply depth-based cost scaling from vanilla, ratio 1.0 (so cost = (depth+1)x).
    probe = next((b for b in buildings
                  if b.lower() in depths and depths[b.lower()] >= 2
                  and valid_tl(get_val(section_map, b, "TechLevel"))), None)
    if probe:
        van_before, _, cur_before = get_three_costs(lines, section_map, probe)
        n = apply_depth_cost_scaling(lines, section_map, buildings, depths,
                                     ratio=1.0, base_choice="vanilla",
                                     min_depth=1, tiered_rates=None)
        sm2, _ = build_section_map(lines)
        _, _, cur_after = get_three_costs(lines, sm2, probe)
        d = depths[probe.lower()]
        exp_cost = round(van_before * (d + 1))
        print(f"\ndepth cost scaling: scaled {n} buildings")
        print(f"  probe [{probe}] depth={d}: cost {van_before} -> {cur_after} "
              f"(expected ~{exp_cost}) "
              f"{'OK' if abs(cur_after - exp_cost) <= 1 else 'CHECK'}")

    print("\ndone.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 tests/test_techlevel.py /path/to/rulesmd.ini")
        sys.exit(2)
    main(sys.argv[1])
