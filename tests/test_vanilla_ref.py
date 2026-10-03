#!/usr/bin/env python3
"""
Test KnowledgeStore.add_reference_ini: tagged vanilla baseline ingestion.

    python3 tests/test_vanilla_ref.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.ai.knowledge import KnowledgeStore


def main():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        ks = KnowledgeStore(base_dir=os.path.join(d, "knowledge"))
        src = os.path.join(d, "rulesmd.ini")
        with open(src, "w") as f:
            f.write("[HTNK]\nCost=900\nStrength=400\n")

        dest = ks.add_reference_ini(src, "yr")
        name_ok = os.path.basename(dest) == "vanilla_yr_rulesmd.ini"
        body = open(dest, encoding="utf-8").read()
        header_ok = "VANILLA BASELINE REFERENCE" in body and "YR (Yuri" in body
        content_ok = "Cost=900" in body  # original content preserved
        print(f"tagged name: {name_ok}  {'OK' if name_ok else 'FAIL'}")
        print(f"header marks baseline: {header_ok}  {'OK' if header_ok else 'FAIL'}")
        print(f"content preserved: {content_ok}  {'OK' if content_ok else 'FAIL'}")
        ok = ok and name_ok and header_ok and content_ok

        # Re-import overwrites (no _2 duplicate), and the doc is listed.
        dest2 = ks.add_reference_ini(src, "yr")
        overwrite_ok = dest2 == dest
        names = [n for n, _ in ks.list_docs()]
        listed_ok = names.count("vanilla_yr_rulesmd.ini") == 1
        print(f"re-import overwrites: {overwrite_ok}  {'OK' if overwrite_ok else 'FAIL'}")
        print(f"listed once: {listed_ok}  {'OK' if listed_ok else 'FAIL'}")
        ok = ok and overwrite_ok and listed_ok

        # RA2 naming, and bad game rejected.
        ra2_src = os.path.join(d, "rules.ini")
        with open(ra2_src, "w") as f:
            f.write("[GI]\nCost=200\n")
        ra2_dest = ks.add_reference_ini(ra2_src, "ra2")
        ra2_ok = os.path.basename(ra2_dest) == "vanilla_ra2_rules.ini"
        try:
            ks.add_reference_ini(src, "banana")
            reject_ok = False
        except ValueError:
            reject_ok = True
        print(f"ra2 naming: {ra2_ok}  {'OK' if ra2_ok else 'FAIL'}")
        print(f"bad game rejected: {reject_ok}  {'OK' if reject_ok else 'FAIL'}")
        ok = ok and ra2_ok and reject_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
