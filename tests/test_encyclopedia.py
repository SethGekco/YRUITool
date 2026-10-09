#!/usr/bin/env python3
"""
Mod encyclopedia: per-unit index across mods — stats, weapon chain, art/sound,
asset locations (loose/mix), missing-vs-vanilla, obtain class; search; export.

    python3 tests/test_encyclopedia.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    build_mod_index, search_indexes, export_unit, mix_id, write_mix,
)

RULES = """\
[Countries]
0=Americans
[InfantryTypes]
0=GI
[VehicleTypes]
0=SUPERTANK
[GI]
Cost=100
Strength=100
Owner=Americans
Primary=M60
[SUPERTANK]
Cost=2000
Strength=800
Speed=6
TechLevel=5
Owner=Americans
Image=STNKART
Primary=BigGun
[M60]
Damage=50
Warhead=SA
[BigGun]
Damage=200
Warhead=AP
Projectile=Invisible
[SA]
[AP]
[Invisible]
"""

ART = """\
[STNKART]
Voxel=yes
Cameo=STNKICON
[GI]
Cameo=GIICON
"""


def main():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        mod = os.path.join(d, "mymod")
        os.makedirs(mod)
        open(os.path.join(mod, "rulesmd.ini"), "w").write(RULES)
        open(os.path.join(mod, "artmd.ini"), "w").write(ART)
        # SUPERTANK's voxel is a loose file in the mod; its cameo is only vanilla
        open(os.path.join(mod, "STNKART.VXL"), "wb").write(b"Voxel Animation\x1a")
        # a mod mix holding the HVA
        write_mix(os.path.join(mod, "ecacheX.mix"), {"STNKART.HVA": b"HVA"})
        # pretend STNKICON.SHP is a vanilla cameo (not in the mod)
        vanilla_ids = {mix_id("STNKICON.SHP")}

        idx = build_mod_index(os.path.join(mod, "rulesmd.ini"),
                              vanilla_ids=vanilla_ids)
        u = idx.units["supertank"]

        # stats + weapon chain
        stats_ok = u.stats.get("Cost") == "2000" and u.stats.get("TechLevel") == "5"
        wchain_ok = any(w == "BigGun" and wh == "AP" and pr == "Invisible"
                        for _s, w, wh, pr in u.weapons)
        print(f"stats + weapon chain: cost={u.stats.get('Cost')} "
              f"weapons={u.weapons}  {'OK' if stats_ok and wchain_ok else 'FAIL'}")
        ok = ok and stats_ok and wchain_ok

        # obtain class (TechLevel 5 → buildable)
        ob_ok = u.obtain == "buildable"
        print(f"obtain: {u.obtain}  {'OK' if ob_ok else 'FAIL'}")
        ok = ok and ob_ok

        # asset locations: VXL loose, HVA in a mix, cameo vanilla
        byname = {a.name: a for a in u.assets}
        loc_ok = (byname["STNKART.VXL"].status == "present"
                  and byname["STNKART.VXL"].location == "loose"
                  and byname["STNKART.HVA"].location.startswith("mix:")
                  and byname["STNKICON.SHP"].status == "vanilla")
        print(f"asset locations: { {a.name: (a.status, a.location) for a in u.assets} }"
              f"  {'OK' if loc_ok else 'FAIL'}")
        ok = ok and loc_ok

        # search across (one) index
        res = search_indexes([idx], "super")
        search_ok = len(res) == 1 and res[0].name == "SUPERTANK"
        kind_ok = {u2.name for u2 in search_indexes([idx], "", kind="infantry")} == {"GI"}
        print(f"search: 'super'→{[r.name for r in res]} kind=infantry→GI  "
              f"{'OK' if search_ok and kind_ok else 'FAIL'}")
        ok = ok and search_ok and kind_ok

        # export unit → ini with weapon chain + extracted media
        dest = os.path.join(d, "lib")
        summ = export_unit(idx, "SUPERTANK", dest)
        ini = open(os.path.join(dest, "SUPERTANK.ini"), encoding="latin-1").read()
        exp_ok = ("[SUPERTANK]" in ini and "[BigGun]" in ini and "[AP]" in ini
                  and "[Invisible]" in ini
                  and "STNKART.VXL" in summ["media"]      # loose extracted
                  and "STNKART.HVA" in summ["media"]      # from mix extracted
                  and "STNKICON.SHP" in summ["vanilla_assets"]
                  and os.path.isfile(os.path.join(dest, "STNKART.VXL")))
        print(f"export: ini+chain written, media={summ['media']}, "
              f"vanilla={summ['vanilla_assets']}  {'OK' if exp_ok else 'FAIL'}")
        ok = ok and exp_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
