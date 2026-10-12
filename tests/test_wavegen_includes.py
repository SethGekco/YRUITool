#!/usr/bin/env python3
"""
Include resolution in the wavegen parser: the vendored IniParser now resolves the
full Ares [#include] / Phobos [$include] graph via the engine resolver — both
directive spellings, recursive nesting, and the .mix fallback (the old vendored
loop did one-level loose #include only). The full RulesMdParser then sees units
defined in included files. Builds its own fixtures; no rules path needed.

    python3 tests/test_wavegen_includes.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.wavegen.rulesmd_parser import IniParser, RulesMdParser
from yrbalance.engine.mixlib import write_mix


def _w(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    ok = True

    # --- IniParser: $include + recursion + main-wins precedence ---
    with tempfile.TemporaryDirectory() as d:
        _w(os.path.join(d, "rulesmd.ini"),
           "[$include]\n1=units.ini\n\n[GATNK]\nCost=800\n")   # main wins
        _w(os.path.join(d, "units.ini"),
           "[#include]\n1=deep.ini\n\n"
           "[VehicleTypes]\n1=HTNK\n\n[HTNK]\nCost=900\n"
           "[GATNK]\nCost=1\n")                                 # loses to main
        _w(os.path.join(d, "deep.ini"), "[DEEP]\nVal=42\n")

        ini = IniParser(os.path.join(d, "rulesmd.ini"))
        p_ok = (ini.get("HTNK", "Cost") == "900"               # $include resolved
                and ini.get("DEEP", "Val") == "42"             # nested #include
                and ini.get("GATNK", "Cost") == "800")         # main precedence
        print(f"IniParser $include + recursion + precedence: "
              f"{'OK' if p_ok else 'FAIL'}")
        ok = ok and p_ok

    # --- IniParser: .mix fallback ---
    with tempfile.TemporaryDirectory() as d:
        _w(os.path.join(d, "rulesmd.ini"),
           "[#include]\n1=packed.ini\n\n[MAIN]\nA=1\n")
        write_mix(os.path.join(d, "ecache99.mix"),
                  {"packed.ini": b"[PACKED]\r\nC=3\r\n"})
        ini = IniParser(os.path.join(d, "rulesmd.ini"))
        mix_ok = ini.get("PACKED", "C") == "3"
        print(f"IniParser .mix fallback: {'OK' if mix_ok else 'FAIL'}")
        ok = ok and mix_ok

    # --- full RulesMdParser sees an included unit ---
    with tempfile.TemporaryDirectory() as d:
        _w(os.path.join(d, "rulesmd.ini"),
           "[General]\n\n"
           "[VehicleTypes]\n1=MTNK\n\n"
           "[MTNK]\nName=Main\nCost=700\nStrength=200\nArmor=heavy\n"
           "TechLevel=1\nOwner=GDI\n\n"
           "[#include]\n1=extra.ini\n")
        _w(os.path.join(d, "extra.ini"),
           "[VehicleTypes]\n2=XTNK\n\n"
           "[XTNK]\nName=Extra\nCost=1234\nStrength=300\nArmor=heavy\n"
           "TechLevel=1\nOwner=GDI\n")
        parser = RulesMdParser(os.path.join(d, "rulesmd.ini"))
        parser.parse()
        seen = {u.lower() for u in parser.units}
        unit_ok = ("xtnk" in seen and "mtnk" in seen
                   and parser.ini.get("XTNK", "Cost") == "1234")
        print(f"RulesMdParser indexes included unit XTNK: "
              f"{'OK' if unit_ok else 'FAIL'} ({sorted(seen)})")
        ok = ok and unit_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
