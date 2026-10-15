#!/usr/bin/env python3
"""
Buildability predicates on the obtainability classifier.

A techno can carry TechLevel >= 0 and still be unbuildable, which `cls` alone
cannot express. These cover the four blockers — TechLevel floor, the tech-slider
ceiling, BuildLimit=0, and a Prerequisite that names nothing — plus the spawner
flag, and the deliberate refusal to judge Ares prerequisite lists.

    python3 tests/test_obtain_buildable.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    parse_ini_lines, build_section_map, classify_obtainability,
)

RULES = """\
[General]
PrerequisiteBarracks=GAPILE

[InfantryTypes]
0=OKUNIT
1=LIMITED
2=TOOHIGH
3=IMPOSSIBLE
4=GENERICOK
5=ARESLIST
6=NOPREREQ
[VehicleTypes]
0=CARRIER
1=SPAWNED
[BuildingTypes]
0=GAPILE

[GAPILE]
TechLevel=1
Cost=500

[OKUNIT]
TechLevel=5
Cost=200
Prerequisite=GAPILE

[LIMITED]
TechLevel=5
Cost=200
Prerequisite=GAPILE
BuildLimit=0

[TOOHIGH]
TechLevel=11
Cost=200
Prerequisite=GAPILE

[IMPOSSIBLE]
TechLevel=5
Cost=200
Prerequisite=NOSUCHBUILDING

[GENERICOK]
TechLevel=5
Cost=200
Prerequisite=BARRACKS

[ARESLIST]
TechLevel=5
Cost=200
Prerequisite=NOSUCHBUILDING
Prerequisite.List1=GAPILE

[NOPREREQ]
TechLevel=5
Cost=200

[CARRIER]
TechLevel=5
Cost=900
Spawns=SPAWNED

[SPAWNED]
TechLevel=-1
Cost=0
Image=SPAWNED
"""

ART = """\
[OKUNIT]
Image=OKUNIT
[SPAWNED]
Image=SPAWNED
"""


def main():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        rp = _w(d, "rules.ini", RULES)
        ap = _w(d, "art.ini", ART)
        sm = build_section_map(parse_ini_lines(rp))[0]
        am = build_section_map(parse_ini_lines(ap))[0]
        res = classify_obtainability(sm, art_map=am)

        def g(n):
            return res.get(n.lower())

        # (name, human_buildable, ai_buildable, substring expected in blockers)
        cases = [
            ("OKUNIT",     True,  True,  None),
            ("NOPREREQ",   True,  True,  None),
            ("GENERICOK",  True,  True,  None),   # generic [General] token
            ("LIMITED",    False, True,  "BuildLimit=0"),
            ("IMPOSSIBLE", False, True,  "names nothing that exists"),
            # Above the slider: attributed as the AI blocker per the project's
            # filter taxonomy, so human_buildable deliberately stays True.
            ("TOOHIGH",    True,  False, "above the tech slider"),
            # Conservative: an Ares Prerequisite.List* makes the plain key
            # unjudgeable, so no "impossible" verdict even though the plain
            # key names nothing.
            ("ARESLIST",   True,  True,  None),
        ]
        for name, want_h, want_a, want_blocker in cases:
            r = g(name)
            good = (r is not None and r.human_buildable is want_h
                    and r.ai_buildable is want_a)
            if want_blocker:
                good = good and any(want_blocker in b for b in r.blockers)
            elif r is not None:
                good = good and not r.blockers
            print(f"  {'OK ' if good else 'XX '}{name:11} "
                  f"human={r.human_buildable if r else '?'} "
                  f"ai={r.ai_buildable if r else '?'} {r.blockers if r else ''}")
            ok = ok and good

        # A blocked unit keeps cls=normal on purpose (three consumers key off
        # it) but must SAY it isn't buildable, in both reasons and also-flags.
        lim = g("LIMITED")
        honest = (lim.cls == "normal"
                  and "not player-buildable" in lim.also
                  and any("not actually buildable" in x for x in lim.reasons))
        print(f"blocked unit keeps cls but reports the truth: "
              f"{'OK' if honest else 'FAIL'}")
        ok = ok and honest

        high = g("TOOHIGH")
        ai_flag = "not AI-buildable" in high.also
        print(f"TechLevel 11 flagged not-AI-buildable: "
              f"{'OK' if ai_flag else 'FAIL'}")
        ok = ok and ai_flag

        # Spawner: named by another unit's Spawns= AND not buildable itself.
        sp, carrier = g("SPAWNED"), g("CARRIER")
        sp_ok = (sp.spawner and "spawner" in sp.also
                 and not carrier.spawner)      # the carrier is not a spawnee
        print(f"spawner flagged on the spawnee only: "
              f"{'OK' if sp_ok else 'FAIL'}")
        ok = ok and sp_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def _w(d, name, text):
    p = os.path.join(d, name)
    open(p, "w").write(text)
    return p


if __name__ == "__main__":
    sys.exit(main())
