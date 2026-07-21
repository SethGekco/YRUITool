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

    # ── Restore to vanilla (undo) ───────────────────────────────────────────
    from yrbalance.engine import restore_vanilla
    reverted = restore_vanilla(lines)  # `lines` still has the inf/bld edits above
    smr, _ = build_section_map(lines)
    restore_ok = reverted > 0 and all(
        get_current_field_value_from_line(lines[smr[t.lower()]["keys"]["cost"][0]]) == van[t][1]
        and get_current_field_value_from_line(lines[smr[t.lower()]["keys"]["strength"][0]]) == van[t][0]
        for t in inf)
    print(f"restore vanilla: reverted {reverted} values, probes back to vanilla="
          f"{restore_ok}  {'OK' if restore_ok else 'FAIL'}")
    ok = ok and restore_ok

    # ── Cost mode: x TechLevel ──────────────────────────────────────────────
    lines2 = parse_ini_lines(path)
    sm2, _ = build_section_map(lines2)
    tl_probe = next((t for t in parse_type_list(sm2, "InfantryTypes")
                     if get_val(sm2, t, "Cost", "").isdigit()
                     and get_val(sm2, t, "TechLevel", "").lstrip("-").isdigit()
                     and int(get_val(sm2, t, "TechLevel")) >= 1), None)
    if tl_probe:
        vc = int(get_val(sm2, tl_probe, "Cost"))
        vtl = int(get_val(sm2, tl_probe, "TechLevel"))
        spec2 = BalanceSpec(categories={
            "inf": CategoryRatios(cost=1.0, cost_mode="techlevel", cost_base="vanilla")})
        s2 = run_balance(lines2, sm2, spec2, config)
        sm2b, _ = build_section_map(lines2)
        nc = get_current_field_value_from_line(
            lines2[sm2b[tl_probe.lower()]["keys"]["cost"][0]])
        good = nc == vc * vtl and s2.costs_scaled > 0
        ok = ok and good
        print(f"cost x TechLevel: [{tl_probe}] {vc} x TL{vtl} -> {nc} "
              f"(exp {vc*vtl})  {'OK' if good else 'FAIL'}")

    # ── Cost mode: x depth (buildings) ──────────────────────────────────────
    lines3 = parse_ini_lines(path)
    sm3, _ = build_section_map(lines3)
    spec3 = BalanceSpec(categories={
        "bld": CategoryRatios(cost=1.0, cost_mode="depth", cost_base="vanilla")})
    s3 = run_balance(lines3, sm3, spec3, config)
    depth_ok = s3.costs_scaled > 0
    print(f"cost x depth (buildings): scaled {s3.costs_scaled}  "
          f"{'OK' if depth_ok else 'FAIL'}")
    ok = ok and depth_ok

    # ── TechLevel recompute + write ─────────────────────────────────────────
    lines4 = parse_ini_lines(path)
    sm4, _ = build_section_map(lines4)
    spec4 = BalanceSpec(categories={
        "bld": CategoryRatios(tl_recompute=True, tl_write=True)})
    s4 = run_balance(lines4, sm4, spec4, config)
    sm4b, _ = build_section_map(lines4)
    gapowr_tl = get_val(sm4b, "GAPOWR", "TechLevel") if "gapowr" in sm4b else None
    # GAPOWR is depth-1 -> TechLevel 1 after recompute.
    tlrec_ok = s4.techlevels_written > 0 and (gapowr_tl == "1" or gapowr_tl is None)
    print(f"TechLevel recompute+write: wrote {s4.techlevels_written}, "
          f"GAPOWR TL={gapowr_tl}  {'OK' if tlrec_ok else 'FAIL'}")
    ok = ok and tlrec_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 tests/test_pipeline.py /path/to/rulesmd.ini")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
