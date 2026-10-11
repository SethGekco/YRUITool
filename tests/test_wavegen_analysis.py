#!/usr/bin/env python3
"""
Vendored wave-generator analysis layer (headless): parse a mod's rules and build
the MOD OVERVIEW report without any input(). Needs a rules(md).ini path.

    python3 tests/test_wavegen_analysis.py "/path/to/rulesmd.ini"
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.wavegen.analysis import overview_report_text, mod_summary


def main():
    rules = sys.argv[1] if len(sys.argv) > 1 else None
    if not rules or not os.path.isfile(rules):
        print("SKIP (no rules path)")
        return 0

    summ = mod_summary(rules)
    sum_ok = summ["units"] > 100 and bool(summ["factions"])
    print(f"mod_summary: {summ['units']} units, factions={list(summ['factions'])}  "
          f"{'OK' if sum_ok else 'FAIL'}")

    text = overview_report_text(rules)
    rep_ok = ("MOD OVERVIEW" in text and "FACTIONS" in text
              and len(text.splitlines()) > 50)
    print(f"overview report: {len(text.splitlines())} lines  "
          f"{'OK' if rep_ok else 'FAIL'}")

    ok = sum_ok and rep_ok
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
