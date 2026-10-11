#!/usr/bin/env python3
"""
Ares [#include] / Phobos [$include] resolution: the include graph expands into one
effective section map (nesting, numeric order, both directive spellings), the main
file wins on section conflicts, missing files are reported not fatal, cycles don't
hang, the opt-in `allow` filter drops a subtree, and provenance/source mapping is
correct. No rules path needed — builds its own fixture.

    python3 tests/test_includes.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    build_section_map, get_val,
    resolve_includes, load_with_includes, include_graph, flatten_graph,
    has_includes,
)


def _w(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        # main includes sw.ini (2) and countries.ini (1); countries includes
        # nested.ini; main also has a Phobos [$include] for phobos.ini and a
        # missing file; sw.ini re-includes main.ini (cycle).
        main = os.path.join(d, "rulesmd.ini")
        _w(main,
           "[#include]\r\n"
           "2=sw.ini\r\n"
           "1=countries.ini\r\n"
           "9=nope.ini\r\n"
           "[$include]\r\n"
           "1=phobos.ini\r\n"
           "\r\n"
           "[GATNK]\r\n"
           "Cost=800\r\n"            # main wins over countries' GATNK below
           "Name=Grizzly\r\n")
        _w(os.path.join(d, "countries.ini"),
           "[#include]\r\n1=nested.ini\r\n\r\n"
           "[Americans]\r\nSide=GDI\r\n\r\n"
           "[GATNK]\r\nCost=1\r\n")        # loses to main
        _w(os.path.join(d, "nested.ini"),
           "[DeepSection]\r\nValue=42\r\n")
        _w(os.path.join(d, "sw.ini"),
           "[#include]\r\n1=rulesmd.ini\r\n\r\n"  # cycle back to main
           "[NUKE]\r\nName=Nuclear Missile\r\n")
        _w(os.path.join(d, "phobos.ini"),
           "[PhobosSection]\r\nFlag=yes\r\n")

        # --- resolve_includes + section map ---
        missing = []
        lines, prov = resolve_includes(main, missing=missing)
        smap, _ = build_section_map(lines)

        merged_ok = all(s in smap for s in (
            "gatnk", "americans", "deepsection", "nuke", "phobossection"))
        print(f"all included sections merged: {'OK' if merged_ok else 'FAIL'}")
        ok = ok and merged_ok

        # main file wins the GATNK conflict (Cost=800, not 1)
        prec_ok = get_val(smap, "GATNK", "Cost") == "800"
        print(f"main file wins conflict (GATNK Cost=800): "
              f"{'OK' if prec_ok else 'FAIL'} ({get_val(smap, 'GATNK', 'Cost')})")
        ok = ok and prec_ok

        # nested include pulled in
        nest_ok = get_val(smap, "DeepSection", "Value") == "42"
        print(f"nested include resolved: {'OK' if nest_ok else 'FAIL'}")
        ok = ok and nest_ok

        miss_ok = any(fn == "nope.ini" for _f, fn in missing)
        print(f"missing include reported ({len(missing)}): "
              f"{'OK' if miss_ok else 'FAIL'}")
        ok = ok and miss_ok

        prov_ok = len(prov) == len(lines)
        print(f"provenance parallel to lines: {'OK' if prov_ok else 'FAIL'}")
        ok = ok and prov_ok

        # --- load_with_includes: section_source ---
        _l, smap2, _o, src = load_with_includes(main)
        src_ok = (os.path.basename(src.get("americans", "")) == "countries.ini"
                  and os.path.basename(src.get("nuke", "")) == "sw.ini"
                  and os.path.basename(src.get("gatnk", "")) == "rulesmd.ini")
        print(f"section_source maps sections to files: "
              f"{'OK' if src_ok else 'FAIL'}")
        ok = ok and src_ok

        # --- cycle didn't duplicate/hang (NUKE appears once) ---
        nuke_count = sum(1 for ln in lines if ln.strip().lower() == "[nuke]")
        cycle_ok = nuke_count == 1
        print(f"cycle-safe (NUKE once): {'OK' if cycle_ok else 'FAIL'} "
              f"({nuke_count})")
        ok = ok and cycle_ok

        # --- opt-in allow: drop sw.ini → NUKE gone ---
        allow = lambda p: os.path.basename(p).lower() != "sw.ini"
        l3, _p3 = resolve_includes(main, allow=allow)
        s3, _ = build_section_map(l3)
        allow_ok = "nuke" not in s3 and "americans" in s3
        print(f"opt-in filter drops a subtree: {'OK' if allow_ok else 'FAIL'}")
        ok = ok and allow_ok

        # --- include_graph + flatten ---
        g = include_graph(main)
        files = {os.path.basename(f) for f in flatten_graph(g)}
        graph_ok = ({"rulesmd.ini", "countries.ini", "nested.ini", "sw.ini",
                     "phobos.ini"} <= files)
        missing_node = any(not c["present"] for c in g["children"])
        print(f"include_graph lists tree + marks missing: "
              f"{'OK' if graph_ok and missing_node else 'FAIL'}")
        ok = ok and graph_ok and missing_node

        # --- has_includes ---
        hi_ok = has_includes(smap) and not has_includes(
            build_section_map(["[X]\n", "A=1\n"])[0])
        print(f"has_includes: {'OK' if hi_ok else 'FAIL'}")
        ok = ok and hi_ok

    # --- mix fallback: an include that isn't loose is pulled from a .mix ---
    from yrbalance.engine.mixlib import write_mix
    with tempfile.TemporaryDirectory() as d:
        _w(os.path.join(d, "rulesmd.ini"),
           "[#include]\r\n1=packed.ini\r\n2=loose.ini\r\n\r\n"
           "[MAIN]\r\nA=1\r\n")
        _w(os.path.join(d, "loose.ini"), "[LOOSE]\r\nB=2\r\n")
        # packed.ini only exists inside a .mix, and itself #includes deep.ini
        # (also packed) to prove recursion works through the mix.
        write_mix(os.path.join(d, "ecache99.mix"), {
            "packed.ini": b"[#include]\r\n1=deep.ini\r\n\r\n[PACKED]\r\nC=3\r\n",
            "deep.ini": b"[DEEPMIX]\r\nD=4\r\n",
        })

        missing = []
        _l, smap, _o, src = load_with_includes(os.path.join(d, "rulesmd.ini"),
                                               missing=missing)
        mix_ok = (get_val(smap, "PACKED", "C") == "3"
                  and get_val(smap, "DEEPMIX", "D") == "4"
                  and get_val(smap, "LOOSE", "B") == "2"
                  and not missing)
        print(f"include pulled from .mix (+ recursion): "
              f"{'OK' if mix_ok else 'FAIL'}")
        ok = ok and mix_ok

        src_ok = str(src.get("packed", "")).startswith("mix:")
        print(f"mix provenance label: {'OK' if src_ok else 'FAIL'} "
              f"({src.get('packed')})")
        ok = ok and src_ok

        # disabling the mix fallback (mix_folders=[]) → packed.ini now missing
        miss2 = []
        _l2, smap2, _o2, _s2 = load_with_includes(
            os.path.join(d, "rulesmd.ini"), missing=miss2, mix_folders=[])
        off_ok = ("packed" not in smap2
                  and any(fn == "packed.ini" for _f, fn in miss2)
                  and get_val(smap2, "LOOSE", "B") == "2")
        print(f"mix fallback is disablable: {'OK' if off_ok else 'FAIL'}")
        ok = ok and off_ok

    # --- encyclopedia integration: a unit defined in an included file is indexed ---
    from yrbalance.engine.encyclopedia import build_mod_index
    with tempfile.TemporaryDirectory() as d:
        _w(os.path.join(d, "rulesmd.ini"),
           "[#include]\n1=extra_units.ini\n\n"
           "[VehicleTypes]\n1=GATNK\n\n[GATNK]\nName=Grizzly\nCost=800\n")
        _w(os.path.join(d, "extra_units.ini"),
           "[VehicleTypes]\n2=HTNK\n\n[HTNK]\nName=Rhino\nCost=900\n")
        idx = build_mod_index(os.path.join(d, "rulesmd.ini"))
        off = build_mod_index(os.path.join(d, "rulesmd.ini"),
                              resolve_includes=False)
        enc_ok = ("HTNK" in idx.unit_names()
                  and idx.units["htnk"].stats.get("Cost") == "900"
                  and "HTNK" not in off.unit_names())
        print(f"encyclopedia indexes included units: "
              f"{'OK' if enc_ok else 'FAIL'}")
        ok = ok and enc_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
