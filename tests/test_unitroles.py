#!/usr/bin/env python3
"""
Unit role lists (engine) + role→AI-bundle generation (wavegen.role_bundles):
sidecar CRUD, then build a role bundle and pull it into a blank aimd — asserting
the TaskForce/Script/TeamType are minted, references resolve, and the result lints
clean. Isolated HOME.

    python3 tests/test_unitroles.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TGT_AI = """\
[TaskForces]
[ScriptTypes]
[TeamTypes]
[AITriggerTypes]
[VehicleTypes]
0=TNK
1=APOC
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
            load_roles, save_roles, set_role_units, create_role, delete_role,
            rename_role, DEFAULT_ROLES, parse_ini_lines, build_section_map,
            get_val, build_pull_plan, apply_pull, lint_ai,
        )
        from yrbalance.wavegen.role_bundles import (
            build_role_entry, suggest_units_for_role, available_roles, ROLE_MAP,
        )

        # defaults seeded on first load
        roles = load_roles()
        seed_ok = all(r in roles for r in DEFAULT_ROLES) and roles["Rush"] == []
        print(f"default roles seeded: {'OK' if seed_ok else 'FAIL'}")
        ok = ok and seed_ok

        # CRUD
        set_role_units("Rush", ["TNK", "APOC", "TNK"])     # dup dropped
        create_role("My Harass")
        rename_role("My Harass", "Harass")
        delete_role("Anti-Air")
        roles = load_roles()
        crud_ok = (roles["Rush"] == ["TNK", "APOC"] and "Harass" in roles
                   and "My Harass" not in roles and "Anti-Air" not in roles)
        print(f"role CRUD (set/create/rename/delete): {'OK' if crud_ok else 'FAIL'}")
        ok = ok and crud_ok

        # every offered role builds a structurally-valid entry
        shape_ok = True
        for disp in available_roles():
            e = build_role_entry(disp, ["TNK"])
            kinds = sorted(b.kind for b in e.blocks)
            if not (e.kind == "bundle" and kinds == ["script", "taskforce",
                    "teamtype"] and len(e.trigger_def.split(",")) == 18):
                shape_ok = False
                print(f"   bad shape for {disp}: {kinds} "
                      f"fields={len(e.trigger_def.split(','))}")
        print(f"all roles build 3-block + 18-field entry: "
              f"{'OK' if shape_ok else 'FAIL'}")
        ok = ok and shape_ok

        # pull a Rush bundle into a blank aimd → minted ids, refs resolve, lint clean
        entry = build_role_entry("Rush", ["TNK", "APOC"], label="My Rush")
        tl = parse_ini_lines(_w(home, "tgt.ini", TGT_AI))
        tgt = build_section_map(tl)[0]
        plan = build_pull_plan(entry, tgt, target_countries=["Americans"])
        summ = apply_pull(tl, tgt, entry, plan)
        out = build_section_map(parse_ini_lines(_w(home, "out.ini", "".join(tl))))[0]

        team_item = next(i for i in plan.items if i.kind == "teamtype")
        tf_item = next(i for i in plan.items if i.kind == "taskforce")
        sc_item = next(i for i in plan.items if i.kind == "script")
        team_tf = (get_val(out, team_item.new_id, "TaskForce") or "").lower()
        team_sc = (get_val(out, team_item.new_id, "Script") or "").lower()
        refs_ok = (team_tf == tf_item.new_id.lower()
                   and team_sc == sc_item.new_id.lower()
                   and tf_item.new_id.lower() != "rtf")     # actually minted
        print(f"pull mints ids + wires team→TF/Script: "
              f"{'OK' if refs_ok else 'FAIL'}  tf={team_tf} sc={team_sc}")
        ok = ok and refs_ok

        # trigger appended, its primary team = the minted TeamType, no missing units
        trig = out.get("aitriggertypes")
        tline = list(trig["keys"].values())[0][2] if trig and trig["keys"] else ""
        tfields = [f.strip() for f in tline.split(",")]
        trig_ok = (summ["trigger"] == 1
                   and tfields[1].lower() == team_item.new_id.lower()
                   and not plan.missing_units)
        print(f"trigger wired to team, no missing units: "
              f"{'OK' if trig_ok else 'FAIL'}  team1={tfields[1] if len(tfields)>1 else '?'}")
        ok = ok and trig_ok

        # lint the result: no dangling AI references
        errs = [f.message for f in lint_ai(out, {"tnk": {}, "apoc": {}})
                if f.severity == "error"
                and ("=[" in f.message or "team [" in f.message)]
        lint_ok = not errs
        print(f"generated bundle lints clean: {errs}  "
              f"{'OK' if lint_ok else 'FAIL'}")
        ok = ok and lint_ok

        # suggest is best-effort: a bogus rules path yields [] rather than raising
        sug_ok = suggest_units_for_role("Rush", "/no/such/rules.ini") == []
        print(f"suggest degrades to [] on bad rules path: "
              f"{'OK' if sug_ok else 'FAIL'}")
        ok = ok and sug_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
