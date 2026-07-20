#!/usr/bin/env python3
"""
End-to-end test of generator-block execution: run the Host Squad example against a
real rulesmd.ini and verify clones are created, filled, registered, and that the
originals get patched. Also checks idempotent re-runs, once-mode + @emit, and
integrity (no section loss/bleed after a full re-parse).

    python3 tests/test_generator.py /path/to/rulesmd.ini
"""

import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from yrbalance.engine import (
    parse_ini_lines, build_section_map, get_val, parse_type_list,
    load_script_file, parse_script, run_script, ModConfig,
    parse_vanilla_from_line,
)


def main(path):
    ok = True
    config = ModConfig.default()

    lines = parse_ini_lines(path)
    section_map, _ = build_section_map(lines)
    original_sections = set(section_map.keys())
    infantry = parse_type_list(section_map, "InfantryTypes")

    script = load_script_file(os.path.join(ROOT, "presets", "examples", "host_squad.yrs"))
    res = run_script(lines, section_map, script, config,
                     param_values={"hosttech": "5"}, progress=print)
    print(f"\nrun: created {res.sections_created} sections, "
          f"registered {res.registered}, skipped {len(res.skipped)}")

    sm2, _ = build_section_map(lines)

    # Integrity: no original section lost.
    lost = [s for s in original_sections if s not in sm2]
    print(f"original sections lost: {len(lost)}  {'OK' if not lost else 'FAIL'}")
    ok = ok and not lost

    # Probe a few infantry.
    probes = [t for t in infantry if t.lower() in sm2][:5]
    print("\nverification:")
    for t in probes:
        host = f"{t}{config.host_suffix}"[:config.max_name_len]
        host_exists = host.lower() in sm2
        # Clone copied the original's Cost, and got TechLevel=5 + GroupAs.
        orig_cost = None
        clone_cost = get_val(sm2, host, "Cost") if host_exists else None
        tl = get_val(sm2, host, "TechLevel") if host_exists else None
        ga = get_val(sm2, host, "GroupAs") if host_exists else None
        # Original patched: Prerequisite=CAPARK01 (annotated), GroupAs=t.
        prereq = get_val(sm2, t, "Prerequisite", "")
        orig_ga = get_val(sm2, t, "GroupAs", "")
        # Clone must carry over the original's Cost (proves @clone filled the body).
        good = (host_exists and tl == "5" and ga == t and clone_cost is not None
                and config.unbuildable_prereq in prereq and orig_ga == t)
        ok = ok and good
        print(f"  [{t}] -> [{host}] exists={host_exists} TL={tl} GroupAs={ga} "
              f"cloneCost={clone_cost} | orig Prereq={prereq!r}  "
              f"{'OK' if good else 'FAIL'}")

    # Registered index entries present.
    geni = [k for k in sm2["infantrytypes"]["keys"]
            if k.startswith(config.infantry_clone_prefix.lower())]
    reg_ok = len(geni) == res.sections_created
    print(f"\nindex entries registered: {len(geni)} (expect {res.sections_created})  "
          f"{'OK' if reg_ok else 'FAIL'}")
    ok = ok and reg_ok

    # Idempotency: run again on the SAME (already-processed) file -> 0 new.
    res2 = run_script(lines, section_map, script, config, param_values={"hosttech": "5"})
    idem_ok = res2.sections_created == 0
    print(f"idempotent re-run: created {res2.sections_created} (expect 0)  "
          f"{'OK' if idem_ok else 'FAIL'}")
    ok = ok and idem_ok

    # Once-mode + @emit: a fresh single section plus a second emitted one.
    lines3 = parse_ini_lines(path)
    sm3, _ = build_section_map(lines3)
    once = parse_script(
        "[+ SHARED_ATTACH]\n"
        "RespawnAtCreation = true\n"
        "@register AttachmentTypes\n"
        "@emit SHARED_HELPER\n"
        "OccupiesCell = false\n")
    r3 = run_script(lines3, sm3, once, config)
    sm4, _ = build_section_map(lines3)
    once_ok = ("shared_attach" in sm4 and "shared_helper" in sm4
               and get_val(sm4, "SHARED_ATTACH", "RespawnAtCreation") == "true"
               and get_val(sm4, "SHARED_HELPER", "OccupiesCell") == "false"
               and r3.sections_created == 2)
    print(f"once-mode + @emit: created {r3.sections_created} (expect 2)  "
          f"{'OK' if once_ok else 'FAIL'}")
    ok = ok and once_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 tests/test_generator.py /path/to/rulesmd.ini")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
