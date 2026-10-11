#!/usr/bin/env python3
"""
Strategic triggers WITH supporting base-state dummies (the full cli tactical
system). Asserts: the dummy system is bounded + request-driven (handful of
dummies, not the combinatorial explosion), the strategic chain is much larger
than the aimd-only path, insert writes the ai_prereq_*/ai_defense include files,
grows rules.ini only modestly (list entries + [#include]), grows aimd.ini, and
adds 0 new unresolved aimd refs. Writes to COPIES. Needs a rules path.

    python3 tests/test_wavegen_strategic_full.py "/path/to/rulesmd.ini"
"""

import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.wavegen.generate import (
    strategic_full_preview, insert_strategic_full, strategic_full_counts,
    insert_strategic_triggers,
)


def main():
    rules = sys.argv[1] if len(sys.argv) > 1 else None
    if not rules or not os.path.isfile(rules):
        print("SKIP (no rules path)")
        return 0
    van_aimd = os.path.join(os.path.dirname(rules), "aimd.ini")
    if not os.path.isfile(van_aimd):
        van_aimd = os.path.expanduser(
            "~/.yrbalancetool/knowledge/read/vanilla_yr_aimd.ini")
    if not os.path.isfile(van_aimd):
        print("SKIP (no aimd)")
        return 0

    ok = True
    with tempfile.TemporaryDirectory() as d:
        aimd = os.path.join(d, "aimd.ini")
        shutil.copy(van_aimd, aimd)
        rules_out = os.path.join(d, "rulesmd.ini")
        shutil.copy(rules, rules_out)

        # force phobos=True to exercise the include-file path (the primary
        # path for modern mods; non-Phobos mods fall back to inline writing).
        bundle, aimd_text, note = strategic_full_preview(
            rules, aimd, difficulties=["Hard"], seed=5, phobos=True)
        c = strategic_full_counts(bundle)
        # dummies present but bounded (max_combo=1 → not thousands)
        bounded = (0 < c["presence_dummies"] < 400
                   and 0 < c["absence_dummies"] < 400)
        prev_ok = (c["triggers"] > 0 and bounded
                   and "[AITriggerTypes]" in aimd_text
                   and c["defense_dummies"] > 0)
        print(f"preview: {c}  {'OK' if prev_ok else 'FAIL'}")
        print(f"  note: {note.splitlines()[0]}")
        ok = ok and prev_ok

        # the dummy-backed path must yield strictly more triggers than aimd-only
        aimd2 = os.path.join(d, "aimd2.ini")
        shutil.copy(van_aimd, aimd2)
        bare = insert_strategic_triggers(rules, aimd2, difficulties=["Hard"],
                                         seed=5)
        richer_ok = c["triggers"] > bare["triggers"]
        print(f"dummy-backed richer than bare: {c['triggers']} > "
              f"{bare['triggers']}  {'OK' if richer_ok else 'FAIL'}")
        ok = ok and richer_ok

        from yrbalance.engine import (parse_ini_lines, build_section_map,
                                      parse_type_list, lint_ai)
        rlines0 = len(open(rules_out, encoding="latin-1").read().splitlines())
        before = build_section_map(parse_ini_lines(aimd))[0]
        n_before = len(parse_type_list(before, "TaskForces"))
        be = [f for f in lint_ai(before, {}) if f.severity == "error"
              and ("=[" in f.message or "team [" in f.message)]

        cnt = insert_strategic_full(rules, aimd, difficulties=["Hard"], seed=5,
                                    phobos=True, rules_out=rules_out, out_dir=d)

        rlines1 = len(open(rules_out, encoding="latin-1").read().splitlines())
        includes = [f for f in ("ai_prereq_present.ini", "ai_prereq_absent.ini",
                                "ai_defense_dummies.ini")
                    if os.path.isfile(os.path.join(d, f))]
        after = build_section_map(parse_ini_lines(aimd))[0]
        ae = [f for f in lint_ai(after, {}) if f.severity == "error"
              and ("=[" in f.message or "team [" in f.message)]
        rules_delta = rlines1 - rlines0
        ins_ok = (len(parse_type_list(after, "TaskForces")) > n_before
                  and len(includes) >= 3
                  and 0 < rules_delta < 2000       # modest in-place growth
                  and len(ae) - len(be) == 0)
        print(f"insert: aimd grows, {len(includes)} include files, "
              f"rules +{rules_delta} lines, 0 new aimd refs: "
              f"{'OK' if ins_ok else 'FAIL'}  {cnt}")
        ok = ok and ins_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
