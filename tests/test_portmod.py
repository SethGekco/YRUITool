#!/usr/bin/env python3
"""
Mod-content porter engine: extraction + dummy split + weapon closure + PER-KIND
balance factors (mods rebalance categories differently) with vanilla cross-check,
+ Owner/Prereq cleanup.

    python3 tests/test_portmod.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    parse_ini_lines, build_section_map, build_port_plan, classify_new_objects,
    compute_factors, plan_to_blueprint,
)

# Vanilla baseline. Source ≈ vanilla; target scales categories DIFFERENTLY:
# infantry ×1.5, vehicles ×1.8, weapons ×1.3 — so a global factor would be wrong.
VANILLA = """\
[Countries]
0=Americans
1=Russians
[E1]
Cost=100
Strength=100
[GGI]
Cost=400
Strength=100
[HTNK]
Cost=900
Strength=400
[MTNK]
Cost=700
Strength=300
[APOC]
Cost=1750
Strength=850
[LTNK]
Cost=600
Strength=200
[120mm]
Damage=100
[Vulcan]
Damage=50
"""

TARGET = """\
[Countries]
0=Americans
1=Russians
[InfantryTypes]
0=E1
1=GGI
[VehicleTypes]
0=HTNK
1=MTNK
2=APOC
3=LTNK
[E1]
Cost=150
Strength=150
[GGI]
Cost=600
Strength=150
[HTNK]
Cost=1620
Strength=720
[MTNK]
Cost=1260
Strength=540
[APOC]
Cost=3150
Strength=1530
[LTNK]
Cost=1080
Strength=360
[120mm]
Damage=130
[Vulcan]
Damage=65
"""

# Source (old mod) ≈ vanilla for shared units, PLUS new content to port.
SOURCE = """\
[Countries]
0=Americans
1=Russians
2=Turkey
[InfantryTypes]
0=E1
1=GGI
[VehicleTypes]
0=HTNK
1=MTNK
2=APOC
3=LTNK
4=SUPERTANK
[BuildingTypes]
0=GATECH
1=DUMMYPREREQ
[E1]
Cost=100
Strength=100
[GGI]
Cost=400
Strength=100
[HTNK]
Cost=900
Strength=400
[MTNK]
Cost=700
Strength=300
[APOC]
Cost=1750
Strength=850
[LTNK]
Cost=600
Strength=200
[120mm]
Damage=100
[Vulcan]
Damage=50
[SUPERTANK]
Cost=2000
Strength=500
Speed=7
Primary=BigGun
Owner=Americans,Turkey
Prerequisite=GATECH,DUMMYPREREQ,TurkBarracks
Image=SUPERTANK
Report=SuperReady
[BigGun]
Damage=300
ROF=60
Range=8
Warhead=SuperWH
Projectile=Invisible
[SuperWH]
Verses=100%,90%,80%
[Invisible]
Arcing=no
[GATECH]
Cost=2000
Owner=Americans,Turkey
[DUMMYPREREQ]
TechLevel=-1
Cost=0
"""


def main():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        maps = {}
        for name, text in (("v", VANILLA), ("t", TARGET), ("s", SOURCE)):
            p = os.path.join(d, name + ".ini")
            open(p, "w").write(text)
            maps[name] = build_section_map(parse_ini_lines(p))[0]
        vm, tm, sm = maps["v"], maps["t"], maps["s"]

        # ── classification ──────────────────────────────────────────────────
        kinds = classify_new_objects(sm, tm)
        def has(k, n):
            return n in kinds.get(k, [])
        cls_ok = (has("vehicle", "SUPERTANK") and has("building", "GATECH")
                  and has("building_dummy", "DUMMYPREREQ") and has("weapon", "BigGun")
                  and has("warhead", "SuperWH") and has("projectile", "Invisible")
                  and "HTNK" not in kinds.get("vehicle", []))
        print(f"classify (new + dummy split + closure): "
              f"{'OK' if cls_ok else 'FAIL'}  {dict(kinds)}")
        ok = ok and cls_ok

        # ── PER-KIND factors (the key fix) ──────────────────────────────────
        f = compute_factors(sm, tm, vm)
        veh_cost = f["vehicle"]["Cost"].factor
        inf_cost = f["infantry"]["Cost"].factor
        wpn_dmg = f["weapon"]["Damage"].factor
        perkind_ok = (1.75 <= veh_cost <= 1.85       # vehicles ×1.8
                      and 1.45 <= inf_cost <= 1.55     # infantry ×1.5 (different!)
                      and 1.25 <= wpn_dmg <= 1.35)     # weapons ×1.3
        print(f"per-kind factors: vehicle.Cost={veh_cost} infantry.Cost={inf_cost} "
              f"weapon.Damage={wpn_dmg}  {'OK' if perkind_ok else 'FAIL'}")
        differ_ok = abs(veh_cost - inf_cost) > 0.2     # proves they're NOT global
        print(f"  categories scale differently (not one global factor): "
              f"{'OK' if differ_ok else 'FAIL'}")
        ok = ok and perkind_ok and differ_ok

        # ── full plan: rescale with the vehicle factor + owner/prereq ───────
        plan = build_port_plan(sm, tm, vm)
        tank = {o.name: o for o in plan.objects}["SUPERTANK"]
        cc = next((c for c in tank.stat_changes if c.stat == "Cost"), None)
        # 2000 × ~1.8 = ~3600 (vehicle factor, NOT infantry's 1.5)
        rescale_ok = cc is not None and 3500 <= cc.new <= 3700
        owner_ok = tank.owner_after == "Americans" and "Turkey" in tank.owner_dropped
        prereq_ok = ("GATECH" in tank.prereq_after
                     and "DUMMYPREREQ" in tank.prereq_after
                     and "TurkBarracks" in tank.prereq_dropped)
        asset_ok = (any(t == "Image" for t, _, _ in tank.asset_refs)
                    and any(t == "Report" for t, _, _ in tank.asset_refs))
        print(f"rescaled Cost {cc.old:g}->{cc.new:g} (vehicle factor): "
              f"{'OK' if rescale_ok else 'FAIL'}")
        print(f"owner cleanup {tank.owner_after!r} dropped={tank.owner_dropped}: "
              f"{'OK' if owner_ok else 'FAIL'}")
        print(f"prereq cleanup kept={tank.prereq_after!r} "
              f"dropped={tank.prereq_dropped}: {'OK' if prereq_ok else 'FAIL'}")
        print(f"asset refs flagged {tank.asset_refs}: {'OK' if asset_ok else 'FAIL'}")
        ok = ok and rescale_ok and owner_ok and prereq_ok and asset_ok

        # ── blueprint ───────────────────────────────────────────────────────
        bp = plan_to_blueprint(plan)
        bp_ok = ("[SUPERTANK]" in bp and "[BigGun]" in bp
                 and f"Cost={cc.new:g}" in bp and "Owner=Americans" in bp
                 and "Americans,Turkey" not in bp)
        print(f"blueprint renders balanced sections: {'OK' if bp_ok else 'FAIL'}")
        ok = ok and bp_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
