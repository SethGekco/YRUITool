#!/usr/bin/env python3
"""
Asset/AI library catalog: add a source (+ auto per-mod collection), scan items
grouped by kind (incl. AITriggerTypes), estimate balance culture vs vanilla +
describe it, conversion factors between two cultures, collection CRUD, and
snapshot-mode file copying. Isolated HOME for the sidecar.

    python3 tests/test_library.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VANILLA = """\
[InfantryTypes]
0=E1
1=E2
[VehicleTypes]
0=MTNK
1=HTNK
[E1]
Cost=100
Strength=100
Speed=4
[E2]
Cost=200
Strength=120
Speed=4
[MTNK]
Cost=700
Strength=300
Speed=6
[HTNK]
Cost=900
Strength=400
Speed=5
"""

# cheaper infantry (~0.5x cost), pricier + tougher vehicles (~2x)
MOD = """\
[InfantryTypes]
0=E1
1=E2
2=NEWGUY
[VehicleTypes]
0=MTNK
1=HTNK
2=NEWTANK
[E1]
Cost=50
Strength=100
Speed=4
[E2]
Cost=100
Strength=120
Speed=4
[MTNK]
Cost=1400
Strength=600
Speed=6
[HTNK]
Cost=1800
Strength=800
Speed=5
[NEWGUY]
Cost=60
Strength=110
Speed=4
[NEWTANK]
Cost=2000
Strength=900
Speed=6
"""

MOD_AIMD = """\
[AITriggerTypes]
TR1=Big Push,TM1,Americans,5,0,NEWTANK,0100,70,10,70,1,0,1,0,0,0,1,1
[TeamTypes]
0=TM1
[TM1]
Name=push team
"""


def main():
    ok = True
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home
        from yrbalance.engine import (
            add_source, remove_source, load_catalog, scan_items,
            create_collection, add_to_collection, remove_from_collection,
            delete_collection, describe_culture, conversion_factors,
            LibraryItem,
        )

        van = _w(home, "vanilla.ini", VANILLA)
        mod_dir = os.path.join(home, "turkey-mod")
        os.makedirs(mod_dir)
        rules = _w(mod_dir, "rulesmd.ini", MOD)
        _w(mod_dir, "aimd.ini", MOD_AIMD)

        # add reference source → auto collection created, balance computed
        src = add_source(rules, label="turkey-mod", mode="reference",
                         added_at="2026-01-01", vanilla_path=van)
        sources, cols = load_catalog()
        add_ok = (len(sources) == 1 and src.balance
                  and any(c.auto_source == src.id for c in cols))
        print(f"add source + auto collection + balance: "
              f"{'OK' if add_ok else 'FAIL'}")
        ok = ok and add_ok

        # balance culture: infantry cheaper (<1), vehicle pricier+tougher (>1)
        inf = src.balance.get("infantry", {})
        veh = src.balance.get("vehicle", {})
        cult_ok = (inf.get("Cost", {}).get("factor", 9) < 0.7
                   and veh.get("Cost", {}).get("factor", 0) > 1.5
                   and veh.get("Strength", {}).get("factor", 0) > 1.5)
        print(f"balance culture (inf cheap / veh pricey+tough): "
              f"{'OK' if cult_ok else 'FAIL'}  "
              f"inf.Cost={inf.get('Cost', {}).get('factor')} "
              f"veh.Cost={veh.get('Cost', {}).get('factor')}")
        ok = ok and cult_ok

        desc = describe_culture(src.balance)
        desc_ok = "infantry" in desc and "cheaper" in desc and "vehicle" in desc
        print(f"describe culture: {'OK' if desc_ok else 'FAIL'}  “{desc}”")
        ok = ok and desc_ok

        # scan items grouped by kind — infantry/vehicle TechnoTypes + the AI trigger
        items = scan_items(src)
        by_kind = {}
        for it in items:
            by_kind.setdefault(it.kind, []).append(it.name.lower())
        scan_ok = ("newtank" in by_kind.get("vehicle", [])
                   and "newguy" in by_kind.get("infantry", [])
                   and any("tr1" == n for n in by_kind.get("ai", [])))
        print(f"scan items by kind: veh={len(by_kind.get('vehicle', []))} "
              f"inf={len(by_kind.get('infantry', []))} ai={len(by_kind.get('ai', []))} "
              f"{'OK' if scan_ok else 'FAIL'}")
        ok = ok and scan_ok

        # conversion between this culture and a mirrored 'vanilla-like' culture
        vanilla_like = {"vehicle": {"Cost": {"factor": 1.0}}}
        conv = conversion_factors(src.balance, vanilla_like)
        # to move a turkey-mod vehicle cost into vanilla scale: 1.0 / ~2.0 ≈ 0.5
        conv_ok = "vehicle" in conv and conv["vehicle"]["Cost"] < 0.7
        print(f"conversion factors (veh cost ~0.5): {'OK' if conv_ok else 'FAIL'}  "
              f"{conv}")
        ok = ok and conv_ok

        # collections: create, add item, remove item, delete
        create_collection("my-favs")
        veh_item = next(i for i in items if i.name.lower() == "newtank")
        add_to_collection("my-favs", veh_item)
        _s, cols2 = load_catalog()
        favs = next(c for c in cols2 if c.name == "my-favs")
        col_add_ok = [veh_item.source_id, "vehicle", "NEWTANK"] in favs.items
        remove_from_collection("my-favs", veh_item)
        _s, cols3 = load_catalog()
        col_rm_ok = not next(c for c in cols3 if c.name == "my-favs").items
        delete_collection("my-favs")
        _s, cols4 = load_catalog()
        col_del_ok = not any(c.name == "my-favs" for c in cols4)
        print(f"collections add/remove/delete: "
              f"{'OK' if col_add_ok and col_rm_ok and col_del_ok else 'FAIL'}")
        ok = ok and col_add_ok and col_rm_ok and col_del_ok

        # snapshot mode copies rules + aimd into a private dir
        snap = add_source(rules, label="turkey-snap", mode="snapshot",
                          added_at="2026-01-01", vanilla_path=van)
        snap_ok = (snap.rules_path != rules and os.path.isfile(snap.rules_path)
                   and os.path.isfile(os.path.join(os.path.dirname(snap.rules_path),
                                                    "aimd.ini")))
        print(f"snapshot copies rules + aimd privately: "
              f"{'OK' if snap_ok else 'FAIL'}")
        ok = ok and snap_ok

        # remove source prunes its auto collection + snapshot dir
        snap_dir = os.path.dirname(snap.rules_path)
        remove_source(snap.id)
        _s, cols5 = load_catalog()
        rm_ok = (not any(c.auto_source == snap.id for c in cols5)
                 and not os.path.isdir(snap_dir))
        print(f"remove source prunes collection + snapshot: "
              f"{'OK' if rm_ok else 'FAIL'}")
        ok = ok and rm_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def _w(d, name, text):
    p = os.path.join(d, name)
    open(p, "w").write(text)
    return p


if __name__ == "__main__":
    sys.exit(main())
