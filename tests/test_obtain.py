#!/usr/bin/env python3
"""
Obtainability classifier: buildable / crate / other / dummy / civilian / unknown.

    python3 tests/test_obtain.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    parse_ini_lines, build_section_map, classify_obtainability,
)

RULES = """\
[InfantryTypes]
0=GI
1=SPY
[VehicleTypes]
0=TANK
1=HORNET
2=HARV
3=MCVPROXY
4=COW
5=TESTUNIT
[BuildingTypes]
0=REFINERY
1=CARRIER
2=TECHLAB
3=DUMMYPREREQ
4=SOMEPRE

[GI]
TechLevel=1
Cost=100
[SPY]
TechLevel=5
Cost=1000
[TANK]
TechLevel=3
Cost=900
CrateGoodie=yes
[HORNET]
TechLevel=-1
Cost=600
Image=HORNET
[HARV]
TechLevel=-1
CrateGoodie=yes
Image=HARV
[MCVPROXY]
TechLevel=-1
Image=MCVPROXY
[COW]
TechLevel=-1
Image=COW
Owner=Special
[TESTUNIT]
TechLevel=-1
[REFINERY]
TechLevel=2
FreeUnit=HARV
[CARRIER]
TechLevel=6
Spawns=HORNET
SpawnCount=4
[TECHLAB]
TechLevel=-1
Capturable=yes
Image=TECHLAB
Prerequisite=
[DUMMYPREREQ]
TechLevel=-1
Cost=0
[SOMEPRE]
TechLevel=-1
Cost=0
[SMCV]
DeploysInto=MCVPROXY
"""


def main():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "r.ini")
        open(p, "w").write(RULES)
        sm = build_section_map(parse_ini_lines(p))[0]
        # Proper artmd: only the objects that actually have art (NOT TESTUNIT).
        ap = os.path.join(d, "art.ini")
        open(ap, "w").write("[HORNET]\n[HARV]\n[MCVPROXY]\n[COW]\n[TECHLAB]\n")
        art = build_section_map(parse_ini_lines(ap))[0]
        ob = classify_obtainability(sm, art_map=art)

        def cls(n):
            o = ob.get(n.lower())
            return o.cls if o else "MISSING"

        cases = {
            "GI": "buildable",
            "TANK": "buildable",        # buildable even though CrateGoodie=yes
            "HARV": "crate",            # not buildable, CrateGoodie=yes
            "HORNET": "other",          # spawned by CARRIER
            "MCVPROXY": "other",        # DeploysInto target of SMCV
            "TECHLAB": "other",         # Capturable=yes
            "DUMMYPREREQ": "dummy",     # name DUMMY
            "SOMEPRE": "dummy",         # name ends PRE, cost 0, no owner ref
            "COW": "civilian",          # not buildable, has art, unreferenced
            "TESTUNIT": "unknown",      # not buildable, no art, unreferenced
        }
        for n, want in cases.items():
            got = cls(n)
            good = got == want
            ok = ok and good
            o = ob.get(n.lower())
            print(f"  {'OK ' if good else 'XX '}{n:12} -> {got:10} (want {want})"
                  f"  {o.reasons if o else ''}")

        # TANK carries a secondary 'crate-eligible' flag
        also_ok = "crate-eligible" in ob["tank"].also
        print(f"  TANK also crate-eligible: {also_ok}  {'OK' if also_ok else 'FAIL'}")
        ok = ok and also_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
