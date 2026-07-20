#!/usr/bin/env python3
"""
Ticket + summary-block round-trip test. Writes two sessions to a temp ticket,
reads them back, and verifies ratios / unit changes / host block survive. Also
checks write_summary_block inserts and then replaces (not duplicates) its block.

    python3 tests/test_ticket.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    write_ticket, read_ticket, next_session_id,
    write_summary_block, detect_rerun, SUMMARY_SENTINEL, SUMMARY_END,
)


def main():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        fake_rules = os.path.join(d, "rulesmd.ini")
        open(fake_rules, "w").write("[General]\nName=fake\n")
        ticket = os.path.join(d, "rulesmd_ticket.ini")

        # Session 1: infantry cost 0.5, strength 2.0, one unit change.
        write_ticket(
            ticket,
            ratios={"inf_cost": 0.5, "inf_str": 2.0, "veh_cost": 1.0},
            gb_config={"mode": "flat", "value": 3}, want_ai_clone=True,
            rerun_mode="vanilla", uncommented_mode="vanilla",
            buildable_inf=["E1", "E2"], ini_path=fake_rules,
            unit_changes={"Infantry": {"E1": {"Cost": {"old": 200, "new": 100, "ratio": 0.5}}}})

        # Session 2: vehicle cost 1.5.
        write_ticket(
            ticket,
            ratios={"veh_cost": 1.5}, gb_config=None, want_ai_clone=False,
            rerun_mode="current", uncommented_mode="current",
            buildable_inf=[], ini_path=fake_rules)

        t = read_ticket(ticket)
        print(f"meta: {t['meta'].get('toolversion')}  hash={t['meta'].get('vanillahash')}")
        print(f"sessions: {len(t['sessions'])}")
        assert len(t["sessions"]) == 2, "expected 2 sessions"
        assert next_session_id(ticket) == 3, "next id should be 3"

        s1 = t["sessions"][0]
        checks = {
            "s1 inf_cost=0.5": s1["ratios"].get("inf_cost") == "0.5",
            "s1 inf_str=2.0": s1["ratios"].get("inf_str") == "2.0",
            "s1 host processed E1/E2": s1["giftbox"]["processed"] == {"E1", "E2"},
            # read_ticket lowercases the category label (faithful to original).
            "s1 unit change E1.Cost": (s1["unit_changes"].get("infantry", {})
                                       .get("E1", {}).get("Cost", {}).get("new") == "100"),
            "s2 veh_cost=1.5": t["sessions"][1]["ratios"].get("veh_cost") == "1.5",
            "latest ratios = s2": t["ratios"].get("veh_cost") == "1.5",
        }
        for label, good in checks.items():
            print(f"  {'OK' if good else 'FAIL'}  {label}")
            ok = ok and good

        # Summary block: insert, then re-insert (must replace, not stack).
        lines = ["[General]\n", "Name=fake\n"]
        write_summary_block(lines, {"inf": {"cost": 0.5, "str": 2.0}},
                            gb_config={"mode": "flat", "value": 3}, want_ai_clone=True)
        first_len = len(lines)
        assert lines[0].strip() == SUMMARY_SENTINEL.strip(), "sentinel not at top"
        assert detect_rerun(lines), "detect_rerun should see the summary block"
        write_summary_block(lines, {"veh": {"cost": 1.5}})
        n_sentinels = sum(1 for l in lines if l.strip() == SUMMARY_SENTINEL.strip())
        n_ends = sum(1 for l in lines if l.strip() == SUMMARY_END.strip())
        replace_ok = n_sentinels == 1 and n_ends == 1
        print(f"  {'OK' if replace_ok else 'FAIL'}  summary block replaced not duplicated "
              f"(sentinels={n_sentinels}, ends={n_ends})")
        ok = ok and replace_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
