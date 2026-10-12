#!/usr/bin/env python3
"""
AI Builder working file (engine): blank/vanilla/from-file seeds, load, pull a
library entry into the working file, and export with the destination untouched
until asked. Isolated HOME for the sidecar + knowledge dir.

    python3 tests/test_aiwork.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SRC_AI = """\
[TaskForces]
0=TF1
[ScriptTypes]
0=SC1
[TeamTypes]
0=TM1
[AITriggerTypes]
TR1=super trig,TM1,Turkey,5,0,SUPERTANK,0100,70,10,70,1,0,1,0,0,0,1,1

[TF1]
Name=super force
0=2,SUPERTANK
[SC1]
Name=the script
0=5,3
[TM1]
Name=super team
TaskForce=TF1
Script=SC1
House=Turkey
"""


def _w(d, name, text):
    p = os.path.join(d, name)
    open(p, "w").write(text)
    return p


def main():
    ok = True
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home
        from yrbalance.engine import (
            new_blank, new_from_vanilla, new_from_file, load_working,
            working_exists, working_path, export_working, resolve_vanilla_aimd,
            parse_ini_lines, build_section_map, has_ai_sections,
            extract_bundle, build_pull_plan, apply_pull,
        )

        # blank seed → four empty AI registries, no entries
        new_blank()
        lines, amap = load_working()
        blank_ok = (working_exists() and amap is not None
                    and has_ai_sections(amap)
                    and not amap.get("aitriggertypes", {}).get("keys"))
        print(f"new blank working file: {'OK' if blank_ok else 'FAIL'}")
        ok = ok and blank_ok

        # from-file seed (stand in for 'from loaded mod')
        src_ini = _w(home, "modaimd.ini", SRC_AI)
        new_from_file(src_ini)
        _lines, amap2 = load_working()
        fromfile_ok = (amap2.get("aitriggertypes") is not None
                       and "tr1" in amap2["aitriggertypes"]["keys"])
        print(f"seed from existing aimd: {'OK' if fromfile_ok else 'FAIL'}")
        ok = ok and fromfile_ok

        # vanilla fallback: no knowledge file, but extract from a fake mix dir —
        # here just assert it raises cleanly when neither source exists.
        os.remove(working_path())
        new_blank()                     # restore a working file for later steps
        van = resolve_vanilla_aimd(game_dir=None)
        van_ok = van is None            # no knowledge file in isolated HOME, no dir
        print(f"vanilla resolve returns None when unavailable: "
              f"{'OK' if van_ok else 'FAIL'}")
        ok = ok and van_ok

        # knowledge-dir vanilla is picked up when present
        kdir = os.path.join(home, ".yrbalancetool", "knowledge", "read")
        os.makedirs(kdir, exist_ok=True)
        _w(kdir, "vanilla_yr_aimd.ini", SRC_AI)
        van2 = resolve_vanilla_aimd()
        new_from_vanilla()
        _l, amap3 = load_working()
        van2_ok = (van2 is not None and amap3.get("aitriggertypes") is not None
                   and "tr1" in amap3["aitriggertypes"]["keys"])
        print(f"new from vanilla (knowledge dir): {'OK' if van2_ok else 'FAIL'}")
        ok = ok and van2_ok

        # pull a library bundle INTO the working file (build_pull_plan against it)
        new_blank()
        wl = parse_ini_lines(working_path())
        wmap = build_section_map(wl)[0]
        srcmap = build_section_map(parse_ini_lines(src_ini))[0]
        bundle = extract_bundle(srcmap, "TR1", source="turkey")
        plan = build_pull_plan(bundle, wmap, target_countries=["Americans"])
        apply_pull(wl, wmap, bundle, plan)
        with open(working_path(), "w", encoding="latin-1", newline="") as fh:
            fh.write("".join(wl))
        _l2, amap4 = load_working()
        pulled_ok = ("tr1" in amap4.get("aitriggertypes", {}).get("keys", {})
                     or any(k for k in amap4.get("aitriggertypes", {})
                            .get("keys", {})))
        print(f"pull library bundle into working file: "
              f"{'OK' if pulled_ok else 'FAIL'}")
        ok = ok and pulled_ok

        # export: destination is only written when asked; backup left to caller
        dest = os.path.join(home, "out_aimd.ini")
        exported_before = os.path.isfile(dest)
        export_working(dest)
        exp_ok = (not exported_before and os.path.isfile(dest)
                  and open(dest, encoding="latin-1").read()
                  == open(working_path(), encoding="latin-1").read())
        print(f"export working → dest (byte copy): {'OK' if exp_ok else 'FAIL'}")
        ok = ok and exp_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
