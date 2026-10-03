#!/usr/bin/env python3
"""
Test garrison-weapon scaling, Damage=1 protection, and the occupant ratio.

    python3 tests/test_garrison.py /path/to/rulesmd.ini
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    parse_ini_lines, build_section_map, get_val, parse_type_list,
    ModConfig, BalanceSpec, CategoryRatios, run_balance,
    get_current_field_value_from_line,
)


def _dmg(section_map, lines, weapon):
    e = section_map[weapon.lower()]["keys"].get("damage")
    return get_current_field_value_from_line(lines[e[0]]) if e else None


def main(path):
    ok = True
    config = ModConfig.default()
    lines = parse_ini_lines(path)
    section_map, _ = build_section_map(lines)

    # Find an infantry with an OccupyWeapon whose vanilla Damage != 1.
    occ = None
    for t in parse_type_list(section_map, "InfantryTypes"):
        w = get_val(section_map, t, "OccupyWeapon")
        if w and w.lower() in section_map:
            d = _dmg(section_map, lines, w)
            if d is not None and d != 1:
                occ = (t, w, d)
                break

    # Find a weapon referenced by infantry with Damage == 1.
    dmg1 = None
    for t in parse_type_list(section_map, "InfantryTypes"):
        for tag in ("Primary", "Secondary", "OccupyWeapon"):
            w = get_val(section_map, t, tag)
            if w and w.lower() in section_map and _dmg(section_map, lines, w) == 1:
                dmg1 = w
                break
        if dmg1:
            break

    # Apply triple damage to infantry (garrison defaults to same-as-above).
    run_balance(lines, section_map, BalanceSpec(categories={
        "inf": CategoryRatios(reg_dmg=3.0, eli_dmg=3.0)}), config)
    sm2, _ = build_section_map(lines)

    if occ:
        t, w, van = occ
        new = _dmg(sm2, lines, w)
        g_ok = new == van * 3
        print(f"garrison weapon: [{t}] OccupyWeapon [{w}] damage {van}->{new} "
              f"(x3)  {'OK' if g_ok else 'FAIL'}")
        ok = ok and g_ok
    else:
        print("garrison weapon: no sample found (skipped)")

    if dmg1:
        new1 = _dmg(sm2, lines, dmg1)
        d1_ok = new1 == 1
        print(f"Damage=1 protection: [{dmg1}] damage -> {new1} (stays 1)  "
              f"{'OK' if d1_ok else 'FAIL'}")
        ok = ok and d1_ok
    else:
        print("Damage=1 protection: no Damage=1 weapon found (skipped)")

    # Occupant ratio: MaxNumberOccupants x2 on a garrisonable building.
    lines2 = parse_ini_lines(path)
    sm3, _ = build_section_map(lines2)
    occ_bld = next((b for b in parse_type_list(sm3, "BuildingTypes")
                    if get_val(sm3, b, "MaxNumberOccupants", "").isdigit()
                    and int(get_val(sm3, b, "MaxNumberOccupants")) > 0), None)
    if occ_bld:
        van_occ = int(get_val(sm3, occ_bld, "MaxNumberOccupants"))
        run_balance(lines2, sm3, BalanceSpec(categories={}, occupant_ratio=2.0), config)
        sm4, _ = build_section_map(lines2)
        new_occ = get_current_field_value_from_line(
            lines2[sm4[occ_bld.lower()]["keys"]["maxnumberoccupants"][0]])
        o_ok = new_occ == van_occ * 2
        print(f"occupant ratio: [{occ_bld}] MaxNumberOccupants {van_occ}->{new_occ} "
              f"(x2)  {'OK' if o_ok else 'FAIL'}")
        ok = ok and o_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 tests/test_garrison.py /path/to/rulesmd.ini")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
