#!/usr/bin/env python3
"""
Obtainability classifier taxonomy:
  normal / special / crate / ai-only / civilian / dummy / unknown.

Covers the indirect-obtain split (special with art vs dummy without), AI-only
detection via aimd TaskForces, superweapon weapon-holder → dummy, and
no-art-unobtainable → dummy.

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
6=AIUNIT
7=SWHOLDER
[BuildingTypes]
0=REFINERY
1=CARRIER
2=TECHLAB
3=DUMMYPREREQ
4=SOMEPRE
[SuperWeaponTypes]
0=TESTSW

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
[AIUNIT]
TechLevel=-1
Image=AIUNIT
[SWHOLDER]
TechLevel=-1
Image=SWHOLDER
Primary=SWBEAM
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
[TESTSW]
Weapon=SWBEAM
[SMCV]
DeploysInto=MCVPROXY
"""

AIMD = """\
[TaskForces]
0=TF1
[TF1]
0=1,AIUNIT
Name=AI force
"""


def main():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "r.ini")
        open(p, "w").write(RULES)
        sm = build_section_map(parse_ini_lines(p))[0]
        # Proper artmd: only the objects that actually have art (NOT TESTUNIT).
        ap = os.path.join(d, "art.ini")
        open(ap, "w").write("[HORNET]\n[HARV]\n[MCVPROXY]\n[COW]\n[TECHLAB]\n"
                            "[AIUNIT]\n[SWHOLDER]\n")
        art = build_section_map(parse_ini_lines(ap))[0]
        aip = os.path.join(d, "aimd.ini")
        open(aip, "w").write(AIMD)
        aimd = build_section_map(parse_ini_lines(aip))[0]

        ob = classify_obtainability(sm, art_map=art, aimd_map=aimd)

        def cls(o, n):
            r = o.get(n.lower())
            return r.cls if r else "MISSING"

        cases = {
            "GI": "normal",
            "TANK": "normal",           # buildable even though CrateGoodie=yes
            "HARV": "crate",            # not buildable, CrateGoodie=yes
            "HORNET": "special",        # spawned by CARRIER, has art
            "MCVPROXY": "special",      # DeploysInto target of SMCV, has art
            "TECHLAB": "special",       # Capturable=yes, has art
            "DUMMYPREREQ": "dummy",     # name DUMMY
            "SOMEPRE": "dummy",         # name ends PRE, cost 0
            "COW": "civilian",          # not buildable, has art, unreferenced
            "TESTUNIT": "dummy",        # not buildable, NO art → logic object
            "AIUNIT": "ai-only",        # unobtainable + art + in aimd TaskForce
            "SWHOLDER": "dummy",        # holds a weapon used by a superweapon
        }
        for n, want in cases.items():
            got = cls(ob, n)
            good = got == want
            ok = ok and good
            r = ob.get(n.lower())
            print(f"  {'OK ' if good else 'XX '}{n:12} -> {got:10} (want {want})"
                  f"  {r.reasons if r else ''}")

        # Without aimd, AIUNIT falls back to civilian (art, unreferenced).
        ob2 = classify_obtainability(sm, art_map=art)
        civ_ok = cls(ob2, "AIUNIT") == "civilian"
        print(f"  AIUNIT without aimd -> {cls(ob2,'AIUNIT')} (want civilian)  "
              f"{'OK' if civ_ok else 'FAIL'}")
        ok = ok and civ_ok

        # TANK carries a secondary 'crate-eligible' flag; AIUNIT 'in aimd'.
        also_ok = ("crate-eligible" in ob["tank"].also
                   and "in aimd TaskForces" in ob["aiunit"].also)
        print(f"  also-flags: {'OK' if also_ok else 'FAIL'}")
        ok = ok and also_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
