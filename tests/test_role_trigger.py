#!/usr/bin/env python3
"""
Role -> AITrigger formation: field validity against vanilla, and the condition
variants.

Three fields used to be emitted as values that appear in NONE of the 165
vanilla AITriggerTypes: TechLevel=0 (vanilla floor is 1), Side=0 (vanilla is
always 1/2/3 — there is no "any side") and Team2="0" (vanilla is "<none>" or a
real TeamType id). A trigger carrying those may simply not behave.

    python3 tests/test_role_trigger.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

F = ["Name", "Team1", "Owner", "TechLevel", "CondType", "CondObj",
     "Comparator", "StartW", "MinW", "MaxW", "Skirmish", "Unused", "Side",
     "IsBaseDef", "Team2", "E", "M", "H"]


def fields(trig):
    parts = trig.split(",")
    assert len(parts) == 18, f"expected 18 fields, got {len(parts)}"
    return dict(zip(F, parts))


def main():
    ok = True
    from yrbalance.wavegen.role_bundles import (
        build_role_entry, role_trigger_variants, COND_ALWAYS_LABEL,
    )

    e = build_role_entry("Rush", ["E1", "E2"])
    f = fields(e.trigger_def)
    shape_ok = (f["TechLevel"] == "1"          # never 0
                and f["Side"] == "1"            # never 0
                and f["Team2"] == "<none>"      # never "0"
                and f["Owner"] == "<all>"       # 155/165 vanilla
                and f["Skirmish"] == "1" and f["IsBaseDef"] == "0"
                and (f["E"], f["M"], f["H"]) == ("1", "1", "1")
                and len(f["Comparator"]) == 64)
    print(f"default trigger is vanilla-shaped: {'OK' if shape_ok else 'FAIL'}")
    if not shape_ok:
        print(f"   {f}")
    ok = ok and shape_ok

    # Name must stay comma-free and <=47 chars or the field count breaks.
    long_e = build_role_entry("Rush", ["E1"], label="x" * 80 + ",oops")
    lf = fields(long_e.trigger_def)
    name_ok = len(lf["Name"]) <= 47 and "," not in lf["Name"]
    print(f"label truncated + comma-stripped: {'OK' if name_ok else 'FAIL'} "
          f"len={len(lf['Name'])}")
    ok = ok and name_ok

    # Side is clamped into 1..3 — there is no valid 0.
    sides = [fields(build_role_entry("Rush", ["E1"], side=s).trigger_def)["Side"]
             for s in (0, 2, 3, 9, None, "2")]
    side_ok = sides == ["1", "2", "3", "1", "1", "2"]
    print(f"side clamped to 1..3: {'OK' if side_ok else 'FAIL'} {sides}")
    ok = ok and side_ok

    # ── condition variants ─────────────────────────────────────────────────
    v = role_trigger_variants("Miner Killer", units=["E1", "E2"],
                              enemy_objects=["HARV", "GAPILL"])
    labels = [x["label"] for x in v]
    var_ok = (labels[0] == COND_ALWAYS_LABEL
              and v[0]["cond_type"] == 4 and v[0]["cond_object"] == "<none>"
              and any(x["cond_type"] == 1 and x["cond_object"] == "E1" for x in v)
              and any(x["cond_type"] == 0 and x["cond_object"] == "HARV"
                      for x in v))
    print(f"variants: always first, owner-side + enemy-side offered: "
          f"{'OK' if var_ok else 'FAIL'} {len(v)} options")
    ok = ok and var_ok

    enemy = next(x for x in v if x["cond_object"] == "HARV")
    ef = fields(build_role_entry("Miner Killer", ["E1"], side=2,
                                 condition=enemy).trigger_def)
    applied_ok = (ef["CondType"] == "0" and ef["CondObj"] == "HARV"
                  and ef["Side"] == "2"
                  and ef["Comparator"].startswith("0100000003000000"))
    print(f"chosen condition reaches the trigger line: "
          f"{'OK' if applied_ok else 'FAIL'}")
    ok = ok and applied_ok

    # No units / no enemy objects must still yield a usable single option.
    bare = role_trigger_variants("Rush")
    bare_ok = len(bare) == 1 and bare[0]["cond_type"] == 4
    print(f"no rules loaded -> just the unconditional option: "
          f"{'OK' if bare_ok else 'FAIL'}")
    ok = ok and bare_ok

    # ── the two Anti-Defenses camps ─────────────────────────────────────────
    from yrbalance.wavegen.role_bundles import (
        available_roles, _resolve, LEGACY_ROLE_NAMES, SCRIPT_BY_ROLE,
    )
    from yrbalance.wavegen.trigger_generator import ROLE_WEIGHTS
    from yrbalance.wavegen.team_generator import ROLE_FLAGS

    roles = available_roles()
    camps_ok = ("Anti-Defenses (Ground)" in roles
                and "Anti-Defenses (Air)" in roles
                and "Anti-Defenses" not in roles)
    print(f"both camps offered, bare name retired from the list: "
          f"{'OK' if camps_ok else 'FAIL'}")
    ok = ok and camps_ok

    # The old display name is what sits in users' unit_roles.json, so it has to
    # keep resolving or their saved picks silently detach from the role.
    legacy_ok = (_resolve("Anti-Defenses")[0] == "ANTI_DEFENSES"
                 and _resolve("Anti-Defenses (Ground)")[0] == "ANTI_DEFENSES"
                 and _resolve("Anti-Defenses (Air)")[0] == "ANTI_DEFENSES_AIR"
                 and "Anti-Defenses" in LEGACY_ROLE_NAMES)
    print(f"legacy 'Anti-Defenses' still resolves to the ground camp: "
          f"{'OK' if legacy_ok else 'FAIL'}")
    ok = ok and legacy_ok

    # A new key is useless if the three tables it indexes lack an entry — it
    # would silently fall back to GENERAL_ATTACK weights/flags/script.
    k = "ANTI_DEFENSES_AIR"
    tables_ok = (k in SCRIPT_BY_ROLE and k in ROLE_WEIGHTS and k in ROLE_FLAGS)
    print(f"air camp present in script/weights/flags tables: "
          f"{'OK' if tables_ok else 'FAIL'}")
    ok = ok and tables_ok

    air = build_role_entry("Anti-Defenses (Air)", ["V3"])
    af = fields(air.trigger_def)
    airtrig_ok = (af["TechLevel"] == "1" and af["Team2"] == "<none>"
                  and af["Side"] in ("1", "2", "3")
                  and len(af["Comparator"]) == 64)
    print(f"air camp emits a vanilla-shaped trigger: "
          f"{'OK' if airtrig_ok else 'FAIL'}")
    ok = ok and airtrig_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
