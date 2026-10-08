#!/usr/bin/env python3
"""
Mod-content porter engine: extraction + dummy split + weapon closure + robust
balance factors (with vanilla cross-check + confidence) + Owner/Prereq cleanup.

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

# Vanilla: a couple of shared units + a weapon, at baseline values.
VANILLA = """\
[Countries]
0=Americans
1=Russians

[E1]
Cost=100
Strength=100
[HTNK]
Cost=900
Strength=400
Primary=120mm
[120mm]
Damage=100
ROF=80
"""

# Target (the loaded/new mod): shared units scaled ~2x cost, ~1.5x strength.
TARGET = """\
[Countries]
0=Americans
1=Russians

[InfantryTypes]
0=E1
[VehicleTypes]
0=HTNK
[E1]
Cost=200
Strength=150
Owner=Americans,Russians
[HTNK]
Cost=1800
Strength=600
Primary=120mm
[120mm]
Damage=150
ROF=80
"""

# Source (old mod): same shared units at its own curve, PLUS new content —
# a new tank (owned partly by a country the target lacks), a new weapon+warhead,
# and a dummy prerequisite building.
SOURCE = """\
[Countries]
0=Americans
1=Russians
2=Turkey

[InfantryTypes]
0=E1
[VehicleTypes]
0=HTNK
1=SUPERTANK
[BuildingTypes]
0=GATECH
1=DUMMYPREREQ

[E1]
Cost=120
Strength=110
[HTNK]
Cost=1000
Strength=420
Primary=120mm
[120mm]
Damage=110
ROF=80

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

        # ── classification: new objects + dummy split + weapon closure ──────
        kinds = classify_new_objects(sm, tm)
        def has(kind, name):
            return name in kinds.get(kind, [])
        cls_ok = (has("vehicle", "SUPERTANK")
                  and has("building", "GATECH")
                  and has("building_dummy", "DUMMYPREREQ")
                  and has("weapon", "BigGun")
                  and has("warhead", "SuperWH")
                  and has("projectile", "Invisible")
                  and "E1" not in kinds.get("infantry", [])   # shared, not new
                  and "HTNK" not in kinds.get("vehicle", []))
        print(f"classify (new + dummy split + closure): {cls_ok}  "
              f"{'OK' if cls_ok else 'FAIL'}")
        print(f"   kinds: { {k: v for k, v in kinds.items()} }")
        ok = ok and cls_ok

        # ── balance factors from shared units, cross-checked vs vanilla ─────
        f = compute_factors(sm, tm, vm)
        # Cost: target/source on E1 (200/120=1.67) & HTNK (1800/1000=1.8) ≈ 1.7-1.8
        cost_ok = 1.6 <= f["Cost"].factor <= 1.9 and f["Cost"].samples >= 2
        # Strength: 150/110=1.36, 600/420=1.43 ≈ 1.4
        str_ok = 1.3 <= f["Strength"].factor <= 1.5
        # Damage: shared weapon 120mm 150/110=1.36
        dmg_ok = 1.2 <= f["Damage"].factor <= 1.5
        print(f"Cost factor {f['Cost'].factor} [{f['Cost'].confidence}] "
              f"samples={f['Cost'].samples}: {cost_ok}  {'OK' if cost_ok else 'FAIL'}")
        print(f"Strength factor {f['Strength'].factor}: "
              f"{str_ok}  {'OK' if str_ok else 'FAIL'}")
        print(f"Damage factor {f['Damage'].factor}: "
              f"{dmg_ok}  {'OK' if dmg_ok else 'FAIL'}")
        ok = ok and cost_ok and str_ok and dmg_ok

        # ── full plan: owner/prereq cleanup + applied rescale ───────────────
        plan = build_port_plan(sm, tm, vm)
        st = {o.name: o for o in plan.objects}
        tank = st["SUPERTANK"]
        # SUPERTANK Cost 2000 * ~1.7 ≈ 3400 (rescaled, not raw)
        cost_change = next((c for c in tank.stat_changes if c.stat == "Cost"), None)
        rescaled_ok = cost_change is not None and cost_change.new > 2500
        # Owner: Turkey dropped, Americans kept
        owner_ok = (tank.owner_after == "Americans"
                    and "Turkey" in tank.owner_dropped)
        # Prereq: GATECH + DUMMYPREREQ kept (being moved), TurkBarracks dropped
        prereq_ok = ("GATECH" in tank.prereq_after
                     and "DUMMYPREREQ" in tank.prereq_after
                     and "TurkBarracks" in tank.prereq_dropped)
        # Asset refs flagged (Image -> art, Report -> sound)
        asset_ok = (any(t == "Image" for t, _, _ in tank.asset_refs)
                    and any(t == "Report" for t, _, _ in tank.asset_refs))
        print(f"rescaled Cost {cost_change.old}->{cost_change.new}: "
              f"{rescaled_ok}  {'OK' if rescaled_ok else 'FAIL'}")
        print(f"Owner cleanup (Turkey dropped): {tank.owner_after!r} "
              f"dropped={tank.owner_dropped}  {'OK' if owner_ok else 'FAIL'}")
        print(f"Prereq cleanup: kept={tank.prereq_after!r} "
              f"dropped={tank.prereq_dropped}  {'OK' if prereq_ok else 'FAIL'}")
        print(f"asset refs flagged: {tank.asset_refs}  "
              f"{'OK' if asset_ok else 'FAIL'}")
        ok = ok and rescaled_ok and owner_ok and prereq_ok and asset_ok

        # ── blueprint renders the balanced sections ─────────────────────────
        bp = plan_to_blueprint(plan)
        bp_ok = ("[SUPERTANK]" in bp and "[BigGun]" in bp
                 and "Cost=3466.6" in bp           # rescaled, not the raw 2000
                 and "Owner=Americans" in bp
                 and "Americans,Turkey" not in bp)  # cleaned owner value
        print(f"blueprint renders balanced sections: {bp_ok}  "
              f"{'OK' if bp_ok else 'FAIL'}")
        ok = ok and bp_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
