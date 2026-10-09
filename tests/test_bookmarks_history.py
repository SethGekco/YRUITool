#!/usr/bin/env python3
"""
Bookmarks store + mod change history (scan-on-load diff, live change log,
per-unit history). Runs against a temp HOME so the real store isn't touched.

    python3 tests/test_bookmarks_history.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ok = True
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home          # isolate ~/.yrbalancetool
        from yrbalance.engine import (
            parse_ini_lines, build_section_map,
            load_bookmarks, add_bookmark, remove_bookmark, is_bookmarked,
            record_load, record_changes, unit_history,
        )

        # ── bookmarks ───────────────────────────────────────────────────────
        a = os.path.join(home, "modA", "rulesmd.ini")
        b = os.path.join(home, "modB", "rulesmd.ini")
        for p in (a, b):
            os.makedirs(os.path.dirname(p))
            open(p, "w").write("[General]\n")
        add_bookmark(a)
        add_bookmark(b)                    # newest first
        bl = load_bookmarks()
        bm_ok = (len(bl) == 2 and bl[0]["label"] == "modB"
                 and bl[0]["exists"] and is_bookmarked(a))
        add_bookmark(a)                    # re-add moves to front, no dupe
        bl2 = load_bookmarks()
        bm_ok = bm_ok and len(bl2) == 2 and bl2[0]["label"] == "modA"
        remove_bookmark(b)
        bm_ok = bm_ok and len(load_bookmarks()) == 1
        print(f"bookmarks add/order/dedupe/remove: {'OK' if bm_ok else 'FAIL'}")
        ok = ok and bm_ok

        # ── history: scan-on-load diff ──────────────────────────────────────
        rp = os.path.join(home, "mod", "rulesmd.ini")
        os.makedirs(os.path.dirname(rp))
        open(rp, "w").write("[HTNK]\nCost=900\n\n[GI]\nCost=100\n")
        lines = parse_ini_lines(rp)
        sm = build_section_map(lines)[0]
        d1 = record_load(rp, lines, sm)          # first load → no diff
        first_ok = not (d1["added"] or d1["removed"] or d1["changed"])

        open(rp, "w").write("[HTNK]\nCost=1800\n\n[GI]\nCost=100\n\n[NEW]\nx=1\n")
        lines2 = parse_ini_lines(rp)
        sm2 = build_section_map(lines2)[0]
        d2 = record_load(rp, lines2, sm2)        # HTNK changed, NEW added
        diff_ok = ("htnk" in d2["changed"] and "new" in d2["added"]
                   and "gi" not in d2["changed"])
        print(f"load-diff (first clean, then HTNK changed + NEW added): "
              f"{'OK' if first_ok and diff_ok else 'FAIL'}  {d2}")
        ok = ok and first_ok and diff_ok

        # ── history: live change log + per-unit query ───────────────────────
        record_changes(rp, ["HTNK", "APOC"], note="Eva applied")
        uh = unit_history(rp, "HTNK")
        hist_ok = any("edited" in e for e in uh) and any(
            "changed since previous load" in e for e in uh)
        print(f"per-unit history (HTNK edit + load-change): {uh}  "
              f"{'OK' if hist_ok else 'FAIL'}")
        ok = ok and hist_ok

        # ── timeline formatting + clear ─────────────────────────────────────
        from yrbalance.engine import format_timeline, clear_history
        tl = format_timeline(rp)
        tl_ok = (len(tl) >= 2
                 and any("edited" in t and "Eva applied" in t for t in tl)
                 and any("loaded" in t for t in tl))
        print(f"timeline: {len(tl)} event(s)  {'OK' if tl_ok else 'FAIL'}")
        clear_history(rp)
        clr_ok = format_timeline(rp) == []
        print(f"clear history: {'OK' if clr_ok else 'FAIL'}")
        ok = ok and tl_ok and clr_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
