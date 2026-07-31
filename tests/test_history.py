#!/usr/bin/env python3
"""
Test change-history scanning and per-change revert.

    python3 tests/test_history.py /path/to/rulesmd.ini
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    parse_ini_lines, build_section_map, get_val, parse_type_list,
    ModConfig, BalanceSpec, CategoryRatios, run_balance,
    scan_changes, revert_change, get_current_field_value_from_line,
)


def main(path):
    ok = True
    config = ModConfig.default()
    lines = parse_ini_lines(path)
    section_map, _ = build_section_map(lines)

    probe = next(t for t in parse_type_list(section_map, "InfantryTypes")
                 if get_val(section_map, t, "Strength", "").isdigit())
    van_str = int(get_val(section_map, probe, "Strength"))

    # Make some edits, then scan them.
    run_balance(lines, section_map, BalanceSpec(categories={
        "inf": CategoryRatios(strength=2.0, cost=0.5)}), config)
    sm2, _ = build_section_map(lines)
    changes = scan_changes(lines, sm2, config)

    inf_changes = [c for c in changes if c["category"] == "Infantry"]
    has_probe = [c for c in changes
                 if c["section"].lower() == probe.lower() and c["tag"].lower() == "strength"]
    scan_ok = len(inf_changes) > 0 and has_probe and has_probe[0]["vanilla"] == van_str
    print(f"scan: {len(changes)} changes, {len(inf_changes)} infantry; "
          f"[{probe}] Strength vanilla={has_probe[0]['vanilla'] if has_probe else None} "
          f"(exp {van_str})  {'OK' if scan_ok else 'FAIL'}")
    ok = ok and scan_ok

    # Revert the probe's Strength change; the line goes back to vanilla.
    revert_change(lines, has_probe[0])
    sm3, _ = build_section_map(lines)
    reverted = get_current_field_value_from_line(
        lines[sm3[probe.lower()]["keys"]["strength"][0]])
    # A fresh scan no longer lists that change.
    changes2 = scan_changes(lines, sm3, config)
    gone = not any(c["section"].lower() == probe.lower() and c["tag"].lower() == "strength"
                   for c in changes2)
    rev_ok = reverted == van_str and gone
    print(f"revert: [{probe}] Strength -> {reverted} (vanilla {van_str}), "
          f"gone from scan={gone}  {'OK' if rev_ok else 'FAIL'}")
    ok = ok and rev_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 tests/test_history.py /path/to/rulesmd.ini")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
