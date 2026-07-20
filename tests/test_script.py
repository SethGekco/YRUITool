#!/usr/bin/env python3
"""
End-to-end modifier-script test: parse the bundled example scripts and run one
against a real rulesmd.ini, verifying formulas, chaining, literals, and params.

    python3 tests/test_script.py /path/to/rulesmd.ini
"""

import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from yrbalance.engine import (
    parse_ini_lines, build_section_map, get_val, parse_type_list,
    load_script_file, parse_script, run_script, ModConfig,
    parse_vanilla_from_line, get_current_field_value_from_line,
)


def main(path):
    ok = True
    config = ModConfig.default()

    # Parse bundled examples (proves the parser handles real files).
    ex_dir = os.path.join(ROOT, "presets", "examples")
    cost_script = load_script_file(os.path.join(ex_dir, "cost_by_tier.yrs"))
    glass = load_script_file(os.path.join(ex_dir, "glass_cannons.yrs"))
    print(f"parsed '{cost_script.name}': {len(cost_script.blocks)} block(s), "
          f"params={cost_script.params}")
    print(f"parsed '{glass.name}': params={glass.params}")

    # ── Run cost_by_tier with factor=100, refund=0.5 on infantry ──
    lines = parse_ini_lines(path)
    section_map, _ = build_section_map(lines)
    infantry = parse_type_list(section_map, "InfantryTypes")

    # Capture vanilla TechLevel for probes with a numeric TL >= 1.
    probes = []
    for t in infantry:
        tl = get_val(section_map, t, "TechLevel", "")
        if tl.lstrip("-").isdigit() and int(tl) >= 1:
            probes.append((t, int(tl)))
        if len(probes) >= 5:
            break

    res = run_script(lines, section_map, cost_script, config,
                     param_values={"factor": "100", "refund": "0.5"}, progress=print)
    print(f"\nrun: {res.technos_touched} technos, {res.tags_written} tags, "
          f"{len(res.skipped)} skipped")

    sm2, _ = build_section_map(lines)
    print("\nverification (Cost = TechLevel*100, Soylent = Cost*0.5):")
    for t, tl in probes:
        cost_li = sm2[t.lower()]["keys"]["cost"][0]
        soy_li = sm2[t.lower()]["keys"]["soylent"][0]
        cost = get_current_field_value_from_line(lines[cost_li])
        soy = get_current_field_value_from_line(lines[soy_li])
        van = parse_vanilla_from_line(lines[cost_li])
        exp_cost = tl * 100
        exp_soy = exp_cost * 0.5
        good = cost == exp_cost and soy == exp_soy and van is not None
        ok = ok and good
        print(f"  [{t}] TL={tl}: Cost={cost} (exp {exp_cost}), "
              f"Soylent={soy} (exp {exp_soy:g}), vanilla-annotated={van is not None}  "
              f"{'OK' if good else 'FAIL'}")

    # ── Literal path: inline script setting Elite = yes ──
    lines2 = parse_ini_lines(path)
    sm3, _ = build_section_map(lines2)
    inline = parse_script("[$INFANTRY]\nElite = yes\nOwner = Russians,Africans\n")
    run_script(lines2, sm3, inline, config)
    sm4, _ = build_section_map(lines2)
    sample = infantry[0]
    elite = get_val(sm4, sample, "Elite")
    owner = get_val(sm4, sample, "Owner")
    lit_ok = elite == "yes" and owner == "Russians,Africans"
    ok = ok and lit_ok
    print(f"\nliteral path [{sample}]: Elite={elite!r}, Owner={owner!r}  "
          f"{'OK' if lit_ok else 'FAIL'}")

    # ── Missing param is an error ──
    from yrbalance.engine import RunError
    try:
        lines3 = parse_ini_lines(path)
        sm5, _ = build_section_map(lines3)
        run_script(lines3, sm5, parse_script("#param: k =\n[$INFANTRY]\nCost = k\n"), config)
        print("  FAIL  missing param should raise")
        ok = False
    except RunError:
        print("  OK    missing param raises RunError")

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 tests/test_script.py /path/to/rulesmd.ini")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
