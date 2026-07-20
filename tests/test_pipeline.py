#!/usr/bin/env python3
"""
Test the high-level balance pipeline: build a BalanceSpec (double infantry HP,
half infantry cost, +50% building strength) and verify run_balance applies it.

    python3 tests/test_pipeline.py /path/to/rulesmd.ini
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    parse_ini_lines, build_section_map, get_val,
    ModConfig, CategoryRatios, BalanceSpec, run_balance,
    parse_vanilla_from_line, get_current_field_value_from_line,
)


def main(path):
    ok = True
    config = ModConfig.default()
    lines = parse_ini_lines(path)
    section_map, _ = build_section_map(lines)

    # Capture a couple of vanilla infantry values first.
    from yrbalance.engine import parse_type_list
    inf = [t for t in parse_type_list(section_map, "InfantryTypes")
           if get_val(section_map, t, "Cost", "").isdigit()][:3]
    van = {t: (int(get_val(section_map, t, "Strength", "0")),
               int(get_val(section_map, t, "Cost"))) for t in inf}

    spec = BalanceSpec(categories={
        "inf": CategoryRatios(strength=2.0, cost=0.5),
        "bld": CategoryRatios(strength=1.5),
    })
    summary = run_balance(lines, section_map, spec, config, progress=print)

    print(f"\nper-category processed: {summary.per_category}")
    print(f"weapon conflicts: {summary.weapon_conflicts}")

    sm2, _ = build_section_map(lines)
    print("\nverification:")
    for t in inf:
        s_li = sm2[t.lower()]["keys"]["strength"][0]
        c_li = sm2[t.lower()]["keys"]["cost"][0]
        new_s = get_current_field_value_from_line(lines[s_li])
        new_c = get_current_field_value_from_line(lines[c_li])
        vs, vc = van[t]
        good = new_s == vs * 2 and new_c == vc // 2 and \
            parse_vanilla_from_line(lines[c_li]) == vc
        ok = ok and good
        print(f"  [{t}] Str {vs}->{new_s} (x2), Cost {vc}->{new_c} (x0.5)  "
              f"{'OK' if good else 'FAIL'}")

    # Building strength x1.5 on a sample defensive/building.
    bld = [b for b in parse_type_list(section_map, "BuildingTypes")
           if get_val(sm2, b, "Strength", "").isdigit() and int(get_val(sm2, b, "Strength")) > 0][:1]
    if bld:
        b = bld[0]
        # Its vanilla strength is recoverable from the annotation if it was edited.
        s_li = sm2[b.lower()]["keys"]["strength"][0]
        van_b = parse_vanilla_from_line(lines[s_li])
        new_b = get_current_field_value_from_line(lines[s_li])
        if van_b is not None:
            exp = round(van_b * 1.5)
            good = abs(new_b - exp) <= 1
            ok = ok and good
            print(f"  [{b}] Str {van_b}->{new_b} (x1.5, exp {exp})  {'OK' if good else 'FAIL'}")

    # no-op category is skipped.
    noop = CategoryRatios()
    print(f"\nno-op detection (unit): {noop.is_noop(False)}  "
          f"{'OK' if noop.is_noop(False) else 'FAIL'}")
    ok = ok and noop.is_noop(False)

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 tests/test_pipeline.py /path/to/rulesmd.ini")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
