#!/usr/bin/env python3
"""
Role-grading polish: role_unit_filter must differentiate the ground-attack roles
(ANTI_INF / ANTI_VEHICLE / ANTI_BUILDING) by the unit's actual anti-X capability,
so a sniper never lands in an anti-vehicle wave and a tank never in an anti-infantry
wave (before the fix both roles produced identical compositions). Needs a rules path.

    python3 tests/test_wavegen_roles.py "/path/to/rulesmd.ini"
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    rules = sys.argv[1] if len(sys.argv) > 1 else None
    if not rules or not os.path.isfile(rules):
        print("SKIP (no rules path)")
        return 0

    from yrbalance.wavegen.analysis import parse_rules
    from yrbalance.wavegen import taskforce_generator as T
    p = parse_rules(rules)

    def rf(role, uid):
        rec = p.units.get(uid)
        return bool(rec) and T.role_unit_filter(role, rec, p)

    ok = True
    # a sniper (anti-infantry only) must NOT pass ANTI_VEHICLE/ANTI_BUILDING
    if "SNIPE" in p.units:
        s = (rf("ANTI_INF", "SNIPE") and not rf("ANTI_VEHICLE", "SNIPE")
             and not rf("ANTI_BUILDING", "SNIPE"))
        print(f"sniper → anti-inf only: {'OK' if s else 'FAIL'}  "
              f"(inf={rf('ANTI_INF','SNIPE')} veh={rf('ANTI_VEHICLE','SNIPE')})")
        ok = ok and s
    # a Grizzly tank (no anti-infantry tag) must NOT pass ANTI_INF
    if "MTNK" in p.units:
        t = rf("ANTI_VEHICLE", "MTNK") and not rf("ANTI_INF", "MTNK")
        print(f"grizzly → anti-vehicle not anti-inf: {'OK' if t else 'FAIL'}  "
              f"(veh={rf('ANTI_VEHICLE','MTNK')} inf={rf('ANTI_INF','MTNK')})")
        ok = ok and t

    # the eligible pools for the two roles must actually differ for a faction
    facs = sorted(getattr(p.sides, "faction_countries", {}).keys())
    if facs:
        fac = facs[0]
        inf = {r.name for r in T._eligible_units(p, fac, "Late", "ANTI_INF",
                                                 None, 0, "Hard")}
        veh = {r.name for r in T._eligible_units(p, fac, "Late", "ANTI_VEHICLE",
                                                 None, 0, "Hard")}
        diff_ok = inf != veh and inf and veh
        print(f"anti-inf vs anti-vehicle pools differ ({len(inf)} vs {len(veh)}): "
              f"{'OK' if diff_ok else 'FAIL'}")
        ok = ok and diff_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
