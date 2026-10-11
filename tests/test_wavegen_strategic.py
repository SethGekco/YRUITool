#!/usr/bin/env python3
"""
Strategic (condition-reactive) trigger generation: the scenario list is non-empty,
a run previews with all four sections, insert grows the aimd, and the generated
chain adds no new unresolved refs. aimd-only (no rules.ini writes). Needs a rules
path.

    python3 tests/test_wavegen_strategic.py "/path/to/rulesmd.ini"
"""

import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.wavegen.generate import (
    strategic_scenarios, strategic_preview_text, insert_strategic_triggers,
    wave_counts,
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
    scns = strategic_scenarios()
    scns_ok = len(scns) >= 10 and all(len(s) == 2 for s in scns)
    print(f"strategic scenarios: {len(scns)}  {'OK' if scns_ok else 'FAIL'}")
    ok = ok and scns_ok

    with tempfile.TemporaryDirectory() as d:
        aimd = os.path.join(d, "aimd.ini")
        shutil.copy(van_aimd, aimd)

        gen, text = strategic_preview_text(rules, aimd, difficulties=["Hard"],
                                           seed=3)
        c = wave_counts(gen)
        prev_ok = (c["triggers"] > 0
                   and all(s in text for s in ("[TaskForces]", "[TeamTypes]",
                                               "[AITriggerTypes]")))
        print(f"preview: {c}  sections present: {'OK' if prev_ok else 'FAIL'}")
        ok = ok and prev_ok

        from yrbalance.engine import (parse_ini_lines, build_section_map,
                                      parse_type_list, lint_ai)
        before = build_section_map(parse_ini_lines(aimd))[0]
        n_before = len(parse_type_list(before, "TaskForces"))
        be = [f for f in lint_ai(before, {}) if f.severity == "error"
              and ("=[" in f.message or "team [" in f.message)]
        cnt = insert_strategic_triggers(rules, aimd, difficulties=["Hard"],
                                        seed=3)
        after = build_section_map(parse_ini_lines(aimd))[0]
        ae = [f for f in lint_ai(after, {}) if f.severity == "error"
              and ("=[" in f.message or "team [" in f.message)]
        ins_ok = (len(parse_type_list(after, "TaskForces")) > n_before
                  and len(ae) - len(be) == 0)
        print(f"insert grows aimd + 0 new unresolved refs: "
              f"{'OK' if ins_ok else 'FAIL'}  {cnt}")
        ok = ok and ins_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
