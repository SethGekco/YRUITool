#!/usr/bin/env python3
"""
AI library: extract a trigger bundle / single block out of an aimd, store+reload,
then pull into a target — exercising identical-block REUSE, >=95%-similar WARN,
unit remap + stub bundling, and lint-clean apply. Isolated HOME for the sidecar.

    python3 tests/test_ailib.py
"""

import sys
import os
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
1=1,E1
Group=-1
[SC1]
Name=the script
0=5,3
1=0,0
2=4,4
[TM1]
Name=super team
TaskForce=TF1
Script=SC1
House=Turkey
Max=2
"""

SRC_RULES = """\
[VehicleTypes]
0=SUPERTANK
[SUPERTANK]
Name=Super Tank
Strength=400
"""

# Target already HAS a taskforce byte-identical to TF1 (different name only) and a
# script 2/3 similar to SC1 — exercises reuse + near-dup warn.
TGT_AI = """\
[TaskForces]
0=EXIST_TF
[ScriptTypes]
0=EXIST_SC
[TeamTypes]
0=EXIST_TM
[VehicleTypes]
0=E2
1=SUPERTANK
2=E1
[AITriggerTypes]
EXIST_TR=existing,EXIST_TM,<all>,5,0,GATECH,0100,70,10,70,1,0,1,0,0,0,1,1

[EXIST_TF]
Name=some other name
0=2,SUPERTANK
1=1,E1
Group=-1
[EXIST_SC]
Name=near script
0=5,3
1=0,0
2=9,9
[EXIST_TM]
Name=x
TaskForce=EXIST_TF
Script=EXIST_SC
"""


def main():
    ok = True
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home
        from yrbalance.engine import (
            parse_ini_lines, build_section_map, get_val,
            extract_bundle, extract_block, add_entry, load_library,
            build_pull_plan, apply_pull, lint_ai,
        )

        src = build_section_map(parse_ini_lines(_w(home, "src.ini", SRC_AI)))[0]
        srules = build_section_map(parse_ini_lines(_w(home, "sr.ini", SRC_RULES)))[0]

        # extract a bundle (trigger + team + tf + script + unit stub)
        ent = extract_bundle(src, "TR1", source="turkey-mod", src_rules=srules)
        kinds = sorted(b.kind for b in ent.blocks)
        ex_ok = (ent.kind == "bundle" and kinds == ["script", "taskforce", "teamtype"]
                 and ent.trigger_def.split(",")[0] == "super trig"
                 and ent.units == ["SUPERTANK", "E1"]
                 and "SUPERTANK" in ent.unit_stubs)
        print(f"extract bundle (3 blocks + trigger + units + stub): "
              f"{'OK' if ex_ok else 'FAIL'}  {kinds} units={ent.units}")
        ok = ok and ex_ok

        # store + reload roundtrip
        add_entry(ent)
        add_entry(extract_block(src, "taskforce", "TF1", source="turkey-mod",
                                name="just-a-tf"))
        lib = load_library()
        rt_ok = (len(lib) == 2
                 and any(e.kind == "bundle" and e.trigger_def for e in lib)
                 and any(e.kind == "taskforce" and e.name == "just-a-tf"
                         for e in lib))
        print(f"store + reload roundtrip (2 entries): "
              f"{'OK' if rt_ok else 'FAIL'}")
        ok = ok and rt_ok

        # private user-notes + separate Credits box persist
        from yrbalance.engine import set_entry_meta
        set_entry_meta(ent.name, "bundle",
                       user_notes="my private note: use vs naval rushes",
                       credits="AI by SomeModder, 2019")
        re = next(e for e in load_library()
                  if e.kind == "bundle" and e.name == ent.name)
        meta_ok = ("private note" in re.user_notes
                   and "SomeModder" in re.credits)
        print(f"user-notes + Credits box persist: "
              f"{'OK' if meta_ok else 'FAIL'}")
        ok = ok and meta_ok

        # pull the bundle into the target: TF is byte-identical to EXIST_TF → REUSED;
        # SC is 50% similar to EXIST_SC → added as new, warned (threshold lowered so
        # the fixture needn't carry 20 identical keys to clear 0.95)
        tl = parse_ini_lines(_w(home, "tgt.ini", TGT_AI))
        tgt = build_section_map(tl)[0]
        bundle = next(e for e in lib if e.kind == "bundle")
        plan = build_pull_plan(bundle, tgt, target_countries=["Americans"],
                               similarity_threshold=0.5)
        reused = [i for i in plan.items if i.reused]
        new = [i for i in plan.items if not i.reused]
        reuse_ok = (any(i.kind == "taskforce" and i.reused for i in plan.items)
                    and any(i.kind == "script" and not i.reused for i in plan.items))
        warn_ok = any("similar to existing" in w for w in plan.warnings)
        # both SUPERTANK and E1 are in the target's lists → nothing missing
        miss_ok = not plan.missing_units
        print(f"pull plan: reused={[i.kind for i in reused]} new={[i.kind for i in new]} "
              f"| near-dup warn={'Y' if warn_ok else 'N'} | missing={plan.missing_units}")
        print(f"  reuse/warn/no-missing: "
              f"{'OK' if reuse_ok and warn_ok and miss_ok else 'FAIL'}")
        ok = ok and reuse_ok and warn_ok and miss_ok

        # missing-unit + remap reporting on a target lacking E1
        tgt_nomiss = build_section_map(parse_ini_lines(
            _w(home, "tnm.ini", TGT_AI.replace("2=E1\n", ""))))[0]
        pm = build_pull_plan(bundle, tgt_nomiss, target_countries=["Americans"],
                             unit_remap={"SUPERTANK": "E2"})
        # SUPERTANK→E2 (present, not missing); E1 absent and not remapped → missing
        rm_ok = ("E1" in pm.missing_units and "SUPERTANK" not in pm.missing_units
                 and any(i.kind == "taskforce" and not i.reused for i in pm.items))
        print(f"remap + missing: missing={pm.missing_units}  "
              f"{'OK' if rm_ok else 'FAIL'}")
        ok = ok and rm_ok

        # apply → team's TaskForce points at the REUSED existing tf; refs resolve
        summ = apply_pull(tl, tgt, bundle, plan)
        out = build_section_map(parse_ini_lines(_w(home, "out.ini", "".join(tl))))[0]
        team_item = next(i for i in plan.items if i.kind == "teamtype")
        team_tf = get_val(out, team_item.new_id, "TaskForce")
        apply_ok = (summ["reused"] >= 1 and summ["script"] == 1
                    and summ["trigger"] == 1
                    and team_tf.lower() == "exist_tf"       # reused, not minted
                    and team_item.new_id.lower() in out)
        print(f"apply: {summ}; reused tf wired into team: "
              f"{'OK' if apply_ok else 'FAIL'}")
        ok = ok and apply_ok

        # pull again WITH the stub for E1-less bundle but bring SUPERTANK stub
        tl2 = parse_ini_lines(_w(home, "t2.ini", TGT_AI))
        tgt2 = build_section_map(tl2)[0]
        plan2 = build_pull_plan(bundle, tgt2, target_countries=["Americans"],
                                bring_stubs=["SUPERTANK"])
        summ2 = apply_pull(tl2, tgt2, bundle, plan2)
        out2 = build_section_map(parse_ini_lines(_w(home, "o2.ini", "".join(tl2))))[0]
        stub_ok = (summ2["unit_stub"] == 1 and "supertank" in out2)
        print(f"stub bundling (SUPERTANK section appended): "
              f"{'OK' if stub_ok else 'FAIL'}")
        ok = ok and stub_ok

        # lint the first result: no new ref errors introduced by the pull
        errs = [f.message for f in lint_ai(out, {"supertank": {}, "e1": {}})
                if f.severity == "error"
                and ("=[" in f.message or "team [" in f.message)]
        lint_ok = not errs
        print(f"lint after pull (no ref errors): {errs}  "
              f"{'OK' if lint_ok else 'FAIL'}")
        ok = ok and lint_ok

        # ── P2: collections / bookmarks / rename / reorder ──────────────────
        from yrbalance.engine import (
            ai_create_collection as create_collection,
            ai_add_to_collection as add_to_collection,
            ai_remove_from_collection as remove_from_collection,
            ai_load_collections as load_collections,
            ai_rename_collection as rename_collection,
            ai_delete_collection as delete_collection,
            set_bookmark, rename_entry, reorder_entries, remove_entry,
        )
        # collections persist, reference entries by (name, kind), survive reload
        create_collection("naval answers")
        add_to_collection("naval answers", (ent.name, "bundle"))
        add_to_collection("naval answers", ("just-a-tf", "taskforce"))
        cols = load_collections()
        col = next((c for c in cols if c.name == "naval answers"), None)
        col_ok = (col is not None and [ent.name, "bundle"] in col.items
                  and len(col.items) == 2)
        print(f"collection create + add (2 items): {'OK' if col_ok else 'FAIL'}")
        ok = ok and col_ok

        # saving the library does NOT clobber collections (shared doc)
        set_bookmark(ent.name, "bundle", True)
        cols2 = load_collections()
        bm = next(e for e in load_library()
                  if e.kind == "bundle" and e.name == ent.name)
        preserve_ok = (bm.bookmarked and any(c.name == "naval answers"
                                             for c in cols2))
        print(f"bookmark set + collections preserved across save: "
              f"{'OK' if preserve_ok else 'FAIL'}")
        ok = ok and preserve_ok

        # rename an entry → collection reference follows, no clash check
        rename_entry("just-a-tf", "taskforce", "renamed-tf")
        colR = next(c for c in load_collections() if c.name == "naval answers")
        libnames = {e.name for e in load_library()}
        rename_ok = ("renamed-tf" in libnames and "just-a-tf" not in libnames
                     and ["renamed-tf", "taskforce"] in colR.items)
        print(f"rename entry (collection ref follows): "
              f"{'OK' if rename_ok else 'FAIL'}")
        ok = ok and rename_ok

        # reorder the whole library by keys
        before = [(e.name, e.kind) for e in load_library()]
        reorder_entries(list(reversed(before)))
        after = [(e.name, e.kind) for e in load_library()]
        reorder_ok = after == list(reversed(before))
        print(f"reorder entries: {'OK' if reorder_ok else 'FAIL'}")
        ok = ok and reorder_ok

        # rename + delete collection; remove_entry drops it from collections
        rename_collection("naval answers", "naval")
        remove_entry(ent.name, "bundle")
        colD = next((c for c in load_collections() if c.name == "naval"), None)
        clean_ok = (colD is not None
                    and [ent.name, "bundle"] not in colD.items
                    and ["renamed-tf", "taskforce"] in colD.items)
        delete_collection("naval")
        gone_ok = not any(c.name == "naval" for c in load_collections())
        print(f"rename coll + remove_entry prunes coll + delete coll: "
              f"{'OK' if clean_ok and gone_ok else 'FAIL'}")
        ok = ok and clean_ok and gone_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def _w(d, name, text):
    p = os.path.join(d, name)
    open(p, "w").write(text)
    return p


if __name__ == "__main__":
    sys.exit(main())
