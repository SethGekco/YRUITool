#!/usr/bin/env python3
"""
aimd browsing helpers: registry listing, AITrigger decode, entry text, and
cross-references (what uses what). Self-contained synthetic aimd.

    python3 tests/test_aimd.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    parse_ini_lines, build_section_map, ai_registry, decode_trigger,
    trigger_fields, entry_text, references_to, sibling_aimd, load_aimd,
)

AIMD = """\
[TaskForces]
0=TF1
[ScriptTypes]
0=SC1
[TeamTypes]
0=TM1
[AITriggerTypes]
TR1=Guard Base,TM1,Americans,7,0,GATECH,0100,70.0,10.0,70.0,1,0,1,0,TM1,0,1,1

[TF1]
Name=guard force
0=2,GGI
[SC1]
Name=the script
0=5,3
[TM1]
Name=guard team
TaskForce=TF1
Script=SC1
House=Americans
"""


def main():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        mod = os.path.join(d, "mod")
        os.makedirs(mod)
        open(os.path.join(mod, "rulesmd.ini"), "w").write("[GI]\n")
        open(os.path.join(mod, "aimd.ini"), "w").write(AIMD)
        rules = os.path.join(mod, "rulesmd.ini")

        # sibling + load
        sib = sibling_aimd(rules)
        lines, m = load_aimd(rules)
        load_ok = sib and sib.endswith("aimd.ini") and m is not None
        print(f"sibling_aimd + load_aimd: {'OK' if load_ok else 'FAIL'}")
        ok = ok and load_ok

        reg = ai_registry(m)
        reg_ok = (reg["TaskForces"] == ["TF1"] and reg["ScriptTypes"] == ["SC1"]
                  and reg["TeamTypes"] == ["TM1"]
                  and reg["AITriggerTypes"] == [("TR1", "Guard Base")])
        print(f"ai_registry: {'OK' if reg_ok else 'FAIL'}  "
              f"{ {k: len(v) for k, v in reg.items()} }")
        ok = ok and reg_ok

        dec = dict(decode_trigger(trigger_fields(m, "TR1")))
        dec_ok = (dec["Name"] == "Guard Base" and dec["Primary team"] == "TM1"
                  and dec["Owner"] == "Americans" and dec["TechLevel"] == "7"
                  and dec["Secondary team"] == "TM1" and dec["Hard"] == "1")
        print(f"decode_trigger: {'OK' if dec_ok else 'FAIL'}")
        ok = ok and dec_ok

        txt = entry_text(m, "TM1")
        txt_ok = "[TM1]" in txt and "TaskForce=TF1" in txt and "Script=SC1" in txt
        print(f"entry_text (team section): {'OK' if txt_ok else 'FAIL'}")
        ok = ok and txt_ok

        # cross-refs: TF1/SC1 ← TM1 ; TM1 ← TR1 (primary + secondary)
        ref_tf = references_to(m, "TF1")
        ref_sc = references_to(m, "SC1")
        ref_tm = references_to(m, "TM1")
        refs_ok = (("TeamType", "TM1") in ref_tf
                   and ("TeamType", "TM1") in ref_sc
                   and ("AITrigger", "TR1") in ref_tm)
        print(f"references_to (TF/SC←team, team←trigger): "
              f"{'OK' if refs_ok else 'FAIL'}  tm={ref_tm}")
        ok = ok and refs_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
