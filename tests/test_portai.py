#!/usr/bin/env python3
"""
AI import: closure (unit→taskforce→teamtype+script→trigger), unique-id re-minting
+ reference rewriting, Owner/House cleanup, dangling-unit + index-ref warnings,
and apply writing resolvable AI into the target.

    python3 tests/test_portai.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    parse_ini_lines, build_section_map, get_val,
    build_ai_import_plan, apply_ai_import, lint_ai,
)

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
1=1,E1
2=1,GHOSTUNIT
Group=-1
[SC1]
Name=the script
0=0,0
1=18,3
[TM1]
Name=super team
TaskForce=TF1
Script=SC1
House=Turkey
Max=2
"""

TGT_AI = """\
[TaskForces]
0=EXIST_TF
[ScriptTypes]
0=EXIST_SC
[TeamTypes]
0=EXIST_TM
[AITriggerTypes]
EXIST_TR=existing,EXIST_TM,<all>,5,0,GATECH,0100,70,10,70,1,0,1,0,0,0,1,1

[EXIST_TF]
Name=x
0=1,E1
[EXIST_SC]
Name=x
[EXIST_TM]
Name=x
TaskForce=EXIST_TF
Script=EXIST_SC
"""


def main():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        src = build_section_map(parse_ini_lines(_w(d, "src.ini", SRC_AI)))[0]
        tl = parse_ini_lines(_w(d, "tgt.ini", TGT_AI))
        tgt = build_section_map(tl)[0]

        plan = build_ai_import_plan(
            src, tgt, ported_units={"SUPERTANK"},
            available_units={"SUPERTANK", "E1"},   # GHOSTUNIT absent
            target_countries=["Americans", "Russians"])
        bk = plan.by_kind()
        closure_ok = (len(bk.get("taskforce", [])) == 1
                      and len(bk.get("script", [])) == 1
                      and len(bk.get("teamtype", [])) == 1
                      and len(bk.get("trigger", [])) == 1)
        print(f"closure (1 each tf/script/team/trigger): "
              f"{'OK' if closure_ok else 'FAIL'}")
        ok = ok and closure_ok

        tm = bk["teamtype"][0]
        tf = bk["taskforce"][0]
        sc = bk["script"][0]
        tr = bk["trigger"][0]
        kd = dict(tm.keys)
        # TeamType's TaskForce/Script rewritten to the minted ids; House cleaned
        remap_ok = (kd["TaskForce"] == tf.new_id and kd["Script"] == sc.new_id
                    and kd["House"] == "<none>")
        # trigger: Team1 → team's new id, Owner Turkey → <all>
        tfields = tr.trigger_def.split(",")
        trig_ok = tfields[1] == tm.new_id and tfields[2] == "<all>"
        # unique minted ids, not colliding with target
        ids = {tf.new_id, sc.new_id, tm.new_id, tr.new_id}
        uniq_ok = len(ids) == 4 and not (ids & {"EXIST_TF", "EXIST_SC", "EXIST_TM"})
        print(f"id re-mint + ref rewrite + House/Owner clean: "
              f"{'OK' if remap_ok and trig_ok and uniq_ok else 'FAIL'}")
        ok = ok and remap_ok and trig_ok and uniq_ok

        # warnings: dangling GHOSTUNIT, index-ref action 18, owner Turkey
        warn_ok = (any("GHOSTUNIT" in w for w in tf.warnings)
                   and any("index" in w for w in sc.warnings)
                   and any("owner" in w.lower() for w in tr.warnings))
        print(f"warnings (dangling unit / index action / owner): "
              f"{'OK' if warn_ok else 'FAIL'}")
        ok = ok and warn_ok

        # apply → the written AI is internally resolvable
        summ = apply_ai_import(tl, tgt, plan)
        tgt2 = build_section_map(parse_ini_lines(_w(d, "out.ini", "".join(tl))))[0]
        new_team_tf = get_val(tgt2, tm.new_id, "TaskForce")
        applied_ok = (summ["teamtype"] == 1 and summ["trigger"] == 1
                      and tm.new_id.lower() in tgt2
                      and new_team_tf.lower() in tgt2)       # ref resolves
        print(f"apply: {summ}; new team's TaskForce resolves: "
              f"{'OK' if applied_ok else 'FAIL'}")
        ok = ok and applied_ok

        # lint the result: the only error should be the honest dangling GHOSTUNIT
        findings = lint_ai(tgt2, {"supertank": {}, "e1": {}})  # rules has SUPERTANK,E1
        errs = [f.message for f in findings if f.severity == "error"]
        lint_ok = (any("GHOSTUNIT" in m for m in errs)
                   and not any("TaskForce" in m or "Script=" in m for m in errs))
        print(f"lint after import (only GHOSTUNIT dangles, refs resolve): "
              f"{errs}  {'OK' if lint_ok else 'FAIL'}")
        ok = ok and lint_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def _w(d, name, text):
    p = os.path.join(d, name)
    open(p, "w").write(text)
    return p


if __name__ == "__main__":
    sys.exit(main())
