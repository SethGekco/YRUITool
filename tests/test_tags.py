#!/usr/bin/env python3
"""
Unit tags: store (add/remove/bulk/all), auto + custom via effective_tags, and the
encyclopedia tag filter. Isolated HOME so the real tag store isn't touched.

    python3 tests/test_tags.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ok = True
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home
        from yrbalance.engine import (
            add_unit_tag, remove_unit_tag, bulk_add_tag, all_tags, load_tags,
            effective_tags, search_indexes, ModIndex, UnitEntry,
        )

        rp = os.path.join(home, "mod", "rulesmd.ini")
        os.makedirs(os.path.dirname(rp))
        open(rp, "w").write("[GI]\n")

        # store: add / normalize / remove / bulk / all
        add_unit_tag(rp, "GI", "  Reviewed  ")           # trims + lowercases
        add_unit_tag(rp, "GI", "reviewed")               # dedupe
        add_unit_tag(rp, "APOC", "OP")
        bulk_add_tag(rp, ["GI", "APOC", "DOG"], "from-oldmod")
        remove_unit_tag(rp, "APOC", "OP")
        st = load_tags(rp)
        store_ok = (st["gi"] == ["from-oldmod", "reviewed"]
                    and st["apoc"] == ["from-oldmod"]
                    and "op" not in all_tags(rp)
                    and set(all_tags(rp)) == {"from-oldmod", "reviewed"})
        print(f"tag store (add/normalize/dedupe/bulk/remove): "
              f"{'OK' if store_ok else 'FAIL'}  {dict(st)}")
        ok = ok and store_ok

        # effective tags = custom ∪ auto (source/kind/obtain)
        u = UnitEntry(name="GI", mod="turkey-mod", kind="infantry",
                      obtain="buildable", tags=["reviewed"])
        et = effective_tags(u)
        eff_ok = ("reviewed" in et and "source:turkey-mod" in et
                  and "kind:infantry" in et and "obtain:buildable" in et)
        print(f"effective_tags (custom + auto): {et}  {'OK' if eff_ok else 'FAIL'}")
        ok = ok and eff_ok

        # search by tag filter
        tank = UnitEntry(name="APOC", mod="turkey-mod", kind="vehicle",
                         obtain="crate", tags=["op"])
        idx = ModIndex(label="turkey-mod", folder="", rules_path=rp,
                       units={"gi": u, "apoc": tank})
        by_auto = {x.name for x in search_indexes([idx], tag="obtain:crate")}
        by_custom = {x.name for x in search_indexes([idx], tag="reviewed")}
        filt_ok = by_auto == {"APOC"} and by_custom == {"GI"}
        print(f"tag filter: obtain:crate→{by_auto} reviewed→{by_custom}  "
              f"{'OK' if filt_ok else 'FAIL'}")
        ok = ok and filt_ok

        # ── Eva-driven tagging: request → TagPlan → apply ───────────────────
        from yrbalance.engine import bulk_add_tag
        from yrbalance.ai.eva import plan_tags

        class FakeClient:
            def __init__(self, reply):
                self.reply = reply

            def chat(self, messages, model, num_ctx=None):
                return self.reply

        kinds = ["infantry", "vehicle", "aircraft", "building"]
        obts = ["buildable", "crate", "other", "dummy", "civilian", "unknown"]
        reply = ('Sure, here you go:\n{"tag":"needs-review","action":"add",'
                 '"kind":"vehicle","obtain":"crate","name_contains":"",'
                 '"has_tag":""}')
        plan = plan_tags(FakeClient(reply), "m", "tag crate-only vehicles "
                         "needs-review", kinds, obts, [])
        plan_ok = (plan.tag == "needs-review" and plan.action == "add"
                   and plan.kind == "vehicle" and plan.obtain == "crate"
                   and not plan.error)
        print(f"plan_tags parse: tag={plan.tag} kind={plan.kind} "
              f"obtain={plan.obtain}  {'OK' if plan_ok else 'FAIL'}")
        ok = ok and plan_ok

        bad = plan_tags(FakeClient("I can't do that."), "m", "x", kinds, obts, [])
        err_ok = bool(bad.error)
        print(f"plan_tags no-json → error: {'OK' if err_ok else 'FAIL'}")
        ok = ok and err_ok

        # apply the plan's filter to a small index, then tag
        idx2 = ModIndex(label="turkey-mod", folder="", rules_path=rp,
                        units={"gi": u, "apoc": tank})
        matched = [x for x in search_indexes([idx2], query=plan.name_contains,
                                             kind=plan.kind or None)
                   if x.obtain == plan.obtain]
        bulk_add_tag(rp, [m.name for m in matched], plan.tag)
        applied = load_tags(rp)
        apply_ok = ([m.name for m in matched] == ["APOC"]
                    and "needs-review" in applied.get("apoc", []))
        print(f"apply plan (only crate vehicle APOC tagged): "
              f"{'OK' if apply_ok else 'FAIL'}")
        ok = ok and apply_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
