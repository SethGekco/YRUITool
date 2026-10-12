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
    compute_factors, plan_to_blueprint, apply_port_plan, get_val,
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
2=DUMMYGUNNER
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
[DUMMYGUNNER]
Cost=0
Strength=50
TechLevel=-1
Primary=DummyGun
[DummyGun]
Damage=25
Warhead=SuperWH
[SUPERTANK]
Cost=2000
Strength=500
Speed=7
Primary=BigGun
OpenTopped=yes
InitialPayload.Types=DUMMYGUNNER
InitialPayload.Nums=2
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


SRC_ART = """\
[SUPERTANK]
Voxel=yes
Remapable=yes
Cameo=SUPERICON
AltCameo=SUPERICNU
"""

SRC_SOUND = """\
[SuperReady]
Control=2
Type=12
Sounds=isupready1,isupready2
"""


def _w(d, name, text):
    p = os.path.join(d, name)
    open(p, "w").write(text)
    return p


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

        # ── InitialPayload: built-in dummy passenger is a dependency ────────────
        dep_ok = tank.deps == ["DUMMYGUNNER"] and any(
            "InitialPayload" in w for w in tank.warnings)
        print(f"InitialPayload dep captured {tank.deps}: "
              f"{'OK' if dep_ok else 'FAIL'}")
        ok = ok and dep_ok

        # ── blueprint ───────────────────────────────────────────────────────
        bp = plan_to_blueprint(plan)
        bp_ok = ("[SUPERTANK]" in bp and "[BigGun]" in bp
                 and f"Cost={cc.new:g}" in bp and "Owner=Americans" in bp
                 and "Americans,Turkey" not in bp)
        print(f"blueprint renders balanced sections: {'OK' if bp_ok else 'FAIL'}")
        ok = ok and bp_ok

        # ── multi-file: art/sound resolution + apply ────────────────────────
        src_art = build_section_map(parse_ini_lines(_w(d, "art.ini", SRC_ART)))[0]
        src_snd = build_section_map(parse_ini_lines(_w(d, "snd.ini", SRC_SOUND)))[0]
        plan2 = build_port_plan(sm, tm, vm, src_art_map=src_art, src_sound_map=src_snd)
        tank2 = {o.name: o for o in plan2.objects}["SUPERTANK"]
        art_ok = (tank2.art_entry is not None and tank2.art_entry[0] == "SUPERTANK"
                  and any(f.endswith(".VXL") for f in tank2.media_files)
                  and any("SUPERICON" in f for f in tank2.media_files))
        snd_ok = any(n == "SuperReady" for n, _ in tank2.sound_entries)
        print(f"art resolved ({tank2.art_entry[0] if tank2.art_entry else None}, "
              f"media={tank2.media_files}): {'OK' if art_ok else 'FAIL'}")
        print(f"sound resolved ({[n for n,_ in tank2.sound_entries]}): "
              f"{'OK' if snd_ok else 'FAIL'}")
        ok = ok and art_ok and snd_ok

        # Apply only SUPERTANK (+ its weapon chain), with a ratio override, into
        # fresh copies of the target rules/art/sound.
        tlines = parse_ini_lines(_w(d, "t.ini", TARGET))
        tmap = build_section_map(tlines)[0]
        alines = parse_ini_lines(_w(d, "tart.ini", "[Dummy]\nx=1\n"))
        amap = build_section_map(alines)[0]
        # Select only SUPERTANK's own chain; the closure must auto-add its built-in
        # DUMMYGUNNER passenger AND that gunner's weapon (Vulcan).
        sel = {"SUPERTANK", "BigGun", "SuperWH", "Invisible"}
        summ = apply_port_plan(tlines, tmap, plan2, selected=sel,
                               overrides={("vehicle", "Cost"): 2.0},
                               art=(alines, amap))
        wrote_tank = "supertank" in tmap
        registered = "SUPERTANK" in {v for _, (_, _, v) in
                                     tmap["vehicletypes"]["keys"].items()}
        override_ok = get_val(tmap, "SUPERTANK", "Cost") == "4000"  # 2000×2.0
        no_turkey = "Turkey" not in (get_val(tmap, "SUPERTANK", "Owner") or "")
        art_written = "supertank" in amap
        # dep closure: the dummy gunner (infantry) and its weapon came along,
        # even though neither was in `sel`.
        gunner_written = "dummygunner" in tmap
        gunner_registered = "DUMMYGUNNER" in {v for _, (_, _, v) in
                                              tmap["infantrytypes"]["keys"].items()}
        dgun_written = "dummygun" in tmap      # the gunner's source-only weapon
        apply_ok = (wrote_tank and registered and override_ok and no_turkey
                    and art_written and gunner_written and gunner_registered
                    and dgun_written and summ["sections"] == 6
                    and summ["registered"] == 2 and summ["art"] == 1)
        print(f"apply: {summ} wrote={wrote_tank} reg={registered} "
              f"gunner={gunner_written}/{gunner_registered} dummygun={dgun_written} "
              f"{'OK' if apply_ok else 'FAIL'}")
        ok = ok and apply_ok

        # ── raw per-file view (what each associated file receives) ──────────────
        from yrbalance.engine import plan_to_files
        files = plan_to_files(plan2, {"SUPERTANK", "BigGun", "SuperWH", "Invisible"},
                              {("vehicle", "Cost"): 2.0})
        rules = files.get("rulesmd.ini", "")
        raw_ok = (
            "[SUPERTANK]" in rules
            and "[DUMMYGUNNER]" in rules          # InitialPayload dep expanded
            and "[DummyGun]" in rules             # dep's weapon pulled too
            and "Cost=4000" in rules              # override reflected
            and "[SUPERTANK]" in files.get("artmd.ini", "")
            and "[SuperReady]" in files.get("soundmd.ini", ""))
        print(f"raw files {sorted(files)}: {'OK' if raw_ok else 'FAIL'}")
        ok = ok and raw_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
