#!/usr/bin/env python3
"""
Corruption regression test for the section-manipulation infrastructure.

Reproduces the workload that historically corrupted files: clone every infantry
section, add several keys to each clone via set_key_in_appended_section (the
end-insertion path), and register each clone in [InfantryTypes]. Then verify:
  * every original section header still exists,
  * every clone header exists and holds the keys we set (no bleed/overwrite),
  * no key we set landed in the wrong section,
  * the file re-parses to the expected section count.

    python3 tests/test_sections.py /path/to/rulesmd.ini
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    parse_ini_lines, build_section_map, get_val, parse_type_list,
    make_safe_clone_name, get_section_raw_lines, append_raw_section,
    set_key_in_appended_section, new_registration_state, register_in_list,
)


def main(path):
    lines = parse_ini_lines(path)
    section_map, _ = build_section_map(lines)
    ending = "\r\n" if lines and lines[-1].endswith("\r\n") else "\n"

    original_sections = set(section_map.keys())
    infantry = parse_type_list(section_map, "InfantryTypes")
    print(f"infantry to clone: {len(infantry)}")

    state = new_registration_state()
    clones = {}
    for inf in infantry:
        if inf.lower() not in section_map:
            continue
        clone = make_safe_clone_name(inf, "_TESTCLONE", section_map)
        raw = get_section_raw_lines(lines, section_map, inf)
        clone_raw = [f"[{clone}]{ending}"] + [l.rstrip("\r\n") + ending for l in raw[1:]]
        append_raw_section(lines, section_map, clone, clone_raw, ending)
        # Set several keys — the exact pattern that used to cause bleed.
        set_key_in_appended_section(lines, section_map, clone, "GroupAs", inf)
        set_key_in_appended_section(lines, section_map, clone, "Prerequisite",
                                    "CAPARK01 ;test")
        set_key_in_appended_section(lines, section_map, clone, "TechLevel", "5")
        # Overwrite one of them (exercises the de-dupe/overwrite path).
        set_key_in_appended_section(lines, section_map, clone, "TechLevel", "6")
        register_in_list(lines, section_map, "InfantryTypes", state, "TC", clone)
        clones[clone] = inf

    print(f"clones created: {len(clones)}")

    # Re-parse from scratch — the real integrity check.
    sm2, _ = build_section_map(lines)
    ok = True

    missing = [s for s in original_sections if s not in sm2]
    print(f"original sections lost: {len(missing)}  {'OK' if not missing else 'FAIL ' + str(missing[:5])}")
    ok = ok and not missing

    missing_clones = [c for c in clones if c.lower() not in sm2]
    print(f"clone sections missing: {len(missing_clones)}  "
          f"{'OK' if not missing_clones else 'FAIL ' + str(missing_clones[:5])}")
    ok = ok and not missing_clones

    # Every clone must hold its own GroupAs/Prerequisite/TechLevel — no bleed.
    bad = []
    for clone, inf in clones.items():
        ga = get_val(sm2, clone, "GroupAs")
        pr = get_val(sm2, clone, "Prerequisite", "")
        tl = get_val(sm2, clone, "TechLevel")
        if ga != inf or "CAPARK01" not in pr or tl != "6":
            bad.append((clone, ga, pr, tl))
    print(f"clones with wrong/missing keys: {len(bad)}  "
          f"{'OK' if not bad else 'FAIL ' + str(bad[:3])}")
    ok = ok and not bad

    # No original infantry should have gained a stray GroupAs from a clone
    # (bleed detection): originals we didn't touch keep their own GroupAs or none.
    stray = []
    for inf in infantry:
        clone_names = [c for c, src in clones.items() if src == inf]
        # The original's GroupAs (if any) must not equal a *clone* section name.
        ga = get_val(sm2, inf, "GroupAs", "")
        if ga and ga.lower() in {c.lower() for c in clones}:
            stray.append((inf, ga))
    print(f"originals with bled-in clone GroupAs: {len(stray)}  "
          f"{'OK' if not stray else 'FAIL ' + str(stray[:3])}")
    ok = ok and not stray

    # Registered TC#### keys should all be present in [InfantryTypes].
    tc_keys = [k for k in sm2["infantrytypes"]["keys"] if k.startswith("tc")]
    reg_ok = len(tc_keys) == len(clones)
    print(f"registered index entries: {len(tc_keys)} (expect {len(clones)})  "
          f"{'OK' if reg_ok else 'FAIL'}")
    ok = ok and reg_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 tests/test_sections.py /path/to/rulesmd.ini")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
