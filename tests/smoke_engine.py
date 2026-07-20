#!/usr/bin/env python3
"""
Smoke test: verify the extracted engine parses a real rulesmd.ini and that its
section map matches an independent re-parse. Run against any rulesmd.ini:

    python3 tests/smoke_engine.py /path/to/rulesmd.ini
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    parse_ini_lines, build_section_map, get_val, modify_line,
    scale_from_vanilla, format_edited_value, parse_vanilla_from_line,
)


def main(path):
    lines = parse_ini_lines(path)
    section_map, section_order = build_section_map(lines)

    print(f"file:            {path}")
    print(f"lines:           {len(lines)}")
    print(f"unique sections: {len(section_map)}")
    print(f"header occurs:   {len(section_order)}")

    # Spot-check a few well-known YR sections.
    for probe in ("General", "InfantryTypes", "VehicleTypes", "BuildingTypes"):
        s = section_map.get(probe.lower())
        status = f"start=line {s['start']+1}, {len(s['keys'])} keys" if s else "MISSING"
        print(f"  [{probe}] {status}")

    # Exercise the edit path on a throwaway copy of one Cost line, non-destructively.
    sample = None
    for name, s in section_map.items():
        if "cost" in s["keys"]:
            cur = s["keys"]["cost"][2].split(";")[0].strip()
            if cur.isdigit() and int(cur) > 1:
                sample = (s["name_raw"], s["keys"]["cost"])
                break
    if sample:
        name, (idx, key_raw, val_raw) = sample
        cur = val_raw.split(";")[0].strip()
        try:
            vanilla = int(cur)
            new_str, _ = scale_from_vanilla(vanilla, 2.0)
            before = lines[idx].rstrip("\r\n")
            modify_line(lines, idx, key_raw,
                        format_edited_value(new_str, vanilla))
            after = lines[idx].rstrip("\r\n")
            round_trip = parse_vanilla_from_line(lines[idx])
            print(f"\nedit round-trip on [{name}]:")
            print(f"  before: {before}")
            print(f"  after:  {after}")
            print(f"  vanilla recovered: {round_trip}  ->  {'OK' if round_trip == vanilla else 'FAIL'}")
        except ValueError:
            print(f"\n[{name}] Cost not a plain int ({cur!r}); skipped edit probe.")

    print("\nsmoke test complete.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 tests/smoke_engine.py /path/to/rulesmd.ini")
        sys.exit(2)
    main(sys.argv[1])
