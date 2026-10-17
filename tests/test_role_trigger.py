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
    camps_ok = all(r in roles for r in ("Anti-Defenses (Standoff)",
                                        "Anti-Defenses (Breacher)",
                                        "Anti-Defenses (Air)")) \
        and "Anti-Defenses" not in roles \
        and "Anti-Defenses (Ground)" not in roles
    print(f"three camps offered, old names retired from the list: "
          f"{'OK' if camps_ok else 'FAIL'}")
    ok = ok and camps_ok

    # The old display name is what sits in users' unit_roles.json, so it has to
    # keep resolving or their saved picks silently detach from the role.
    # Both historical display names must keep resolving: they are what sits in
    # users' unit_roles.json, and a miss silently detaches their saved picks.
    legacy_ok = (_resolve("Anti-Defenses")[0] == "ANTI_DEFENSES"
                 and _resolve("Anti-Defenses (Ground)")[0] == "ANTI_DEFENSES"
                 and _resolve("Anti-Defenses (Standoff)")[0] == "ANTI_DEFENSES"
                 and _resolve("Anti-Defenses (Breacher)")[0]
                 == "ANTI_DEFENSES_BREACH"
                 and _resolve("Anti-Defenses (Air)")[0] == "ANTI_DEFENSES_AIR"
                 and set(LEGACY_ROLE_NAMES) == {"Anti-Defenses",
                                                "Anti-Defenses (Ground)"})
    print(f"both legacy names resolve to the standoff camp: "
          f"{'OK' if legacy_ok else 'FAIL'}")
    ok = ok and legacy_ok

    # A new key is useless if the three tables it indexes lack an entry — it
    # would silently fall back to GENERAL_ATTACK weights/flags/script.
    tables_ok = all(k in SCRIPT_BY_ROLE and k in ROLE_WEIGHTS
                    and k in ROLE_FLAGS
                    for k in ("ANTI_DEFENSES_AIR", "ANTI_DEFENSES_BREACH"))
    print(f"new camps present in script/weights/flags tables: "
          f"{'OK' if tables_ok else 'FAIL'}")
    ok = ok and tables_ok

    for nm in ("Anti-Defenses (Breacher)", "Anti-Defenses (Standoff)"):
        f2 = fields(build_role_entry(nm, ["X"]).trigger_def)
        if f2["TechLevel"] != "1" or f2["Team2"] != "<none>":
            ok = False
            print(f"{nm} trigger malformed: FAIL")

    air = build_role_entry("Anti-Defenses (Air)", ["V3"])
    af = fields(air.trigger_def)
    airtrig_ok = (af["TechLevel"] == "1" and af["Team2"] == "<none>"
                  and af["Side"] in ("1", "2", "3")
                  and len(af["Comparator"]) == 64)
    print(f"air camp emits a vanilla-shaped trigger: "
          f"{'OK' if airtrig_ok else 'FAIL'}")
    ok = ok and airtrig_ok

    # ── Rush is gated on PREREQUISITE tier, never TechLevel ────────────────
    from yrbalance.wavegen.role_bundles import (
        role_tiers, role_buildable_only, UNCOMPUTED_TIER, ROLE_MAP,
    )
    tier_ok = (role_buildable_only("Rush")
               and role_tiers("General Attack") == ()
               and not role_buildable_only("General Attack")
               and role_tiers("Anti-Defenses (Standoff)") == ())
    print(f"Rush gated on build-count + buildable, others ungated: "
          f"{'OK' if tier_ok else 'FAIL'}")
    ok = ok and tier_ok

    # UnitRecord.tier DEFAULTS to the literal string "Rush", which is not a
    # tier — it means "never computed", and it sits on 418 of vanilla's 559
    # units, nearly all unbuildable props. A gate that excluded tiers instead
    # of naming them would therefore admit all of that junk.
    sentinel_ok = (UNCOMPUTED_TIER == "Rush"
                   and UNCOMPUTED_TIER not in role_tiers("Rush")
                   and ROLE_MAP["Rush"]["max_build_count"] == 4)
    print(f"build-count ceiling is 4 (war factory), sentinel unused: "
          f"{'OK' if sentinel_ok else 'FAIL'}")
    ok = ok and sentinel_ok

    # No role may use TechLevel as a gate — it correlates with tech tier but is
    # not the proper instrument (Rex); prerequisites are.
    no_tl = all("techlevel" not in str(k).lower()
                for spec in ROLE_MAP.values() for k in spec)
    print(f"no role spec gates on TechLevel: {'OK' if no_tl else 'FAIL'}")
    ok = ok and no_tl

    # ── Rush prerequisite allowlist + building DPS ─────────────────────────
    from yrbalance.wavegen.rulesmd_parser import (
        prereq_chain, production_allowlist, FACTORY_TAG_VALUES,
    )
    from yrbalance.wavegen.role_bundles import role_any_tags

    class _Ini:
        """Minimal INI stand-in: a barracks behind power, plus a lab."""
        SEC = {
            "GACNST": {}, "GAPOWR": {"prerequisite": "GACNST"},
            "GAPILE": {"prerequisite": "GAPOWR", "factory": "InfantryType"},
            "GAWEAP": {"prerequisite": "GAPILE", "factory": "UnitType"},
            "GATECH": {"prerequisite": "GAWEAP"},
            "E1":     {"prerequisite": "GAPILE"},
            "CLEG":   {"prerequisite": "GAPILE,GATECH"},
            "DOG":    {"prerequisite": "Barracks"},
            "FREE":   {},
        }

        def get(self, sec, key, default=""):
            return self.SEC.get(sec, {}).get(key.lower(), default)

        def section(self, sec):
            return self.SEC.get(sec, {})

        def list_from_section(self, _name):
            return list(self.SEC)

    class _Al:
        def expand(self, tok):
            return ["GAPILE"] if tok == "Barracks" else [tok]

    ini, al = _Ini(), _Al()
    # An alias must expand or the chain is one unknown token and no membership
    # test can work — vanilla's Attack Dog lists Prerequisite=Barracks.
    dog = prereq_chain("DOG", ini, al)
    alias_ok = ("GAPILE" in dog and "Barracks" not in dog
                and "GAPOWR" in dog            # and it recurses past the alias
                and prereq_chain("FREE", ini, al) == set()
                and prereq_chain("", ini, al) == set())
    print(f"prereq_chain expands aliases and recurses: "
          f"{'OK' if alias_ok else 'FAIL'} DOG={sorted(dog)}")
    ok = ok and alias_ok

    # An alias is an OR, so a build COUNT must take the cheapest option per
    # alias. Taking the union is what broke the earlier membership test: in
    # vanilla, POWER expands to NAPOWR or NANRCT, and the nuclear reactor
    # requires NATECH, so a battle lab leaked into the Rhino Tank's chain
    # through a power plant it would never choose.
    from yrbalance.wavegen.rulesmd_parser import (
        build_prereq_count, build_prereq_set,
    )

    class _Ini2(_Ini):
        SEC = dict(_Ini.SEC)
        SEC["CHEAP"] = {"prerequisite": "GACNST"}
        SEC["DEAR"] = {"prerequisite": "GATECH"}
        SEC["EITHER"] = {"prerequisite": "AnyOf"}

    class _Al2:
        def expand(self, tok):
            if tok == "AnyOf":
                return ["DEAR", "CHEAP"]      # deliberately dearest-first
            if tok == "Barracks":
                return ["GAPILE"]
            return [tok]

    ini2, al2 = _Ini2(), _Al2()
    # Transitive: GAPILE never lists GACNST, but reaching it requires one.
    e1_set = build_prereq_set("E1", ini2, al2)
    cheapest = build_prereq_set("EITHER", ini2, al2)
    count_ok = (e1_set == {"GAPILE", "GAPOWR", "GACNST"}
                and build_prereq_count("E1", ini2, al2) == 3
                and "CHEAP" in cheapest and "DEAR" not in cheapest
                and build_prereq_count("FREE", ini2, al2) == 0)
    print(f"build count is transitive and takes the cheapest alias option: "
          f"{'OK' if count_ok else 'FAIL'} E1={sorted(e1_set)} "
          f"either={sorted(cheapest)}")
    ok = ok and count_ok

    # Requiring BOTH production-killer tags was rejected: on vanilla it drops
    # the GI and the Initiate and leaves a Rhino and a Destroyer.
    tags_ok = set(role_any_tags("Rush")) == {"CY_Killer", "Factory_Killer"}
    print(f"Rush needs ANY production-killer tag, not both: "
          f"{'OK' if tags_ok else 'FAIL'}")
    ok = ok and tags_ok

    # ── one bundle per factory queue ───────────────────────────────────────
    from yrbalance.wavegen.role_bundles import (
        build_role_entries, split_by_queue, production_queue, role_one_queue,
        QUEUE_INFANTRY, QUEUE_VEHICLE, QUEUE_NAVAL, QUEUE_AIRCRAFT,
        QUEUE_OTHER,
    )
    from yrbalance.engine import parse_ini_lines, build_section_map
    import tempfile as _tf

    QRULES = """\
[InfantryTypes]
0=GI
[VehicleTypes]
0=TANK
1=BOAT
[AircraftTypes]
0=JET
[GI]
Cost=200
[TANK]
Cost=700
[BOAT]
Cost=1000
Naval=yes
[JET]
Cost=1200
"""
    with _tf.TemporaryDirectory() as d:
        rp = os.path.join(d, "r.ini")
        open(rp, "w").write(QRULES)
        sm = build_section_map(parse_ini_lines(rp))[0]

        # A naval vehicle comes out of a naval YARD, not the war factory, so it
        # must not share a rush taskforce with land armour.
        q_ok = (production_queue("GI", sm) == QUEUE_INFANTRY
                and production_queue("TANK", sm) == QUEUE_VEHICLE
                and production_queue("BOAT", sm) == QUEUE_NAVAL
                and production_queue("JET", sm) == QUEUE_AIRCRAFT
                and production_queue("NOPE", sm) == QUEUE_OTHER
                and production_queue("GI", None) == QUEUE_OTHER)
        print(f"production_queue splits naval off from vehicles: "
              f"{'OK' if q_ok else 'FAIL'}")
        ok = ok and q_ok

        units = ["GI", "TANK", "BOAT", "JET"]
        groups = split_by_queue(units, sm)
        split_ok = ([q for q, _ in groups] == [QUEUE_INFANTRY, QUEUE_VEHICLE,
                                               QUEUE_NAVAL, QUEUE_AIRCRAFT]
                    and all(len(m) == 1 for _q, m in groups))
        print(f"split_by_queue is ordered and complete: "
              f"{'OK' if split_ok else 'FAIL'} {[q for q,_ in groups]}")
        ok = ok and split_ok

        # Rush must not mix queues; a role without the flag is untouched.
        rush = build_role_entries("Rush", units, rules_map=sm)
        gen = build_role_entries("General Attack", units, rules_map=sm)
        names = [e.name for e in rush]
        tfs = [sorted(v for k, v in e.blocks[0].keys if k.isdigit())
               for e in rush]
        bundle_ok = (role_one_queue("Rush")
                     and not role_one_queue("General Attack")
                     and len(rush) == 4 and len(gen) == 1
                     and all(len(x) == 1 for x in tfs)      # no mixing
                     and any("Infantry" in n for n in names)
                     and any("Aircraft" in n for n in names))
        print(f"Rush yields one bundle per queue, labelled: "
              f"{'OK' if bundle_ok else 'FAIL'} {names}")
        ok = ok and bundle_ok

        # Each bundle must be independently valid, not just the first.
        each_ok = all(len(fields(e.trigger_def)["Name"]) <= 47
                      and fields(e.trigger_def)["Team2"] == "<none>"
                      and fields(e.trigger_def)["TechLevel"] == "1"
                      for e in rush)
        print(f"every queue bundle emits a valid trigger: "
              f"{'OK' if each_ok else 'FAIL'}")
        ok = ok and each_ok

        # Without rules the queue is unknowable — one honest combined bundle
        # beats silently dropping units.
        fb = build_role_entries("Rush", units, rules_map=None)
        single = build_role_entries("Rush", ["GI"], rules_map=sm)
        fb_ok = (len(fb) == 1 and len(fb[0].units) == 4
                 and len(single) == 1 and "Infantry" not in single[0].name)
        print(f"no rules -> one combined bundle; single queue -> no suffix: "
              f"{'OK' if fb_ok else 'FAIL'}")
        ok = ok and fb_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
