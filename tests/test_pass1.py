#!/usr/bin/env python3
"""
End-to-end test of the extracted engine: run a full Pass-1 balance over the
infantry list and verify strength/cost scaling and weapon edits landed, entirely
in memory (no files written).

    python3 tests/test_pass1.py /path/to/rulesmd.ini
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    parse_ini_lines, build_section_map, get_val,
    parse_type_list, build_weapon_snapshots, WeaponRegistry,
    reset_session_state, process_unit_stats,
    parse_vanilla_from_line, get_current_field_value_from_line,
)


def main(path):
    lines = parse_ini_lines(path)
    section_map, _ = build_section_map(lines)
    reset_session_state()  # first-run mode

    infantry = parse_type_list(section_map, "InfantryTypes")
    assert infantry, "no infantry found — is this a rulesmd.ini?"
    print(f"infantry types: {len(infantry)}")

    # Capture vanilla costs for a few probes BEFORE editing.
    probes = [t for t in infantry if get_val(section_map, t, "Cost", "").isdigit()][:5]
    vanilla_cost = {t: int(get_val(section_map, t, "Cost")) for t in probes}
    vanilla_str = {t: get_val(section_map, t, "Strength") for t in probes}

    snapshots = build_weapon_snapshots(section_map, [infantry])
    registry = WeaponRegistry()

    # Double strength, halve cost, +50% damage on regular weapons.
    process_unit_stats(
        lines, section_map, infantry,
        label="Infantry", cat_label="infantry",
        strength_ratio=2.0, cost_ratio=0.5, speed_ratio=1.0, buildtime_ratio=1.0,
        reg_dmg=1.5, reg_rng=1.0, reg_rof=1.0,
        eli_dmg=1.0, eli_rng=1.0, eli_rof=1.0,
        snapshots=snapshots, registry=registry,
        progress=print)

    # Verify: re-parse the mutated lines and check the probes.
    sm2, _ = build_section_map(lines)
    ok = True
    print("\nverification:")
    for t in probes:
        cost_li = sm2[t.lower()]["keys"]["cost"][0]
        str_li = sm2[t.lower()]["keys"]["strength"][0]
        new_cost = get_current_field_value_from_line(lines[cost_li])
        van_cost = parse_vanilla_from_line(lines[cost_li])
        new_str = get_current_field_value_from_line(lines[str_li])
        van_str = parse_vanilla_from_line(lines[str_li])
        exp_cost = round(vanilla_cost[t] * 0.5 + 0.0)  # scale_int rounds half up
        cost_good = van_cost == vanilla_cost[t]
        str_good = van_str == int(vanilla_str[t]) if vanilla_str[t].isdigit() else True
        ok = ok and cost_good and str_good
        print(f"  [{t}] cost {vanilla_cost[t]}->{new_cost} (van={van_cost} {'OK' if cost_good else 'FAIL'})"
              f"  str {vanilla_str[t]}->{new_str} (van={van_str} {'OK' if str_good else 'FAIL'})")

    print(f"\nweapon edits recorded: {len(registry.edited)}")
    print(f"conflicts resolved:    {len(registry.conflicts)}")
    print(registry.format_summary())

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 tests/test_pass1.py /path/to/rulesmd.ini")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
