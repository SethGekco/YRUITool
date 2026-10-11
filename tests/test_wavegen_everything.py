#!/usr/bin/env python3
"""
Full pipeline ("generate everything"): regular waves + pool teams + procedural
extras + coverage pass + specialty waves, with SHARED id registries. Asserts it
produces a large chain, the coverage pass runs (it was crashing on a dead import),
insert grows the aimd, and the written chain adds no new unresolved refs. Needs a
rules path.

    python3 tests/test_wavegen_everything.py "/path/to/rulesmd.ini"
"""

import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.wavegen.generate import (
    everything_preview_text, insert_everything, everything_counts,
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
    # the coverage pass must run without the dead-import crash
    from yrbalance.wavegen.analysis import parse_rules
    from yrbalance.wavegen import coverage_tracker
    p = parse_rules(rules)
    cov_import_ok = "generate_script_for_taskforce" not in open(
        coverage_tracker.__file__, encoding="utf-8").read()
    print(f"coverage dead-import removed: {'OK' if cov_import_ok else 'FAIL'}")
    ok = ok and cov_import_ok

    wcfg = dict(general_waves_per_difficulty=6, country_waves_per_difficulty=2,
                difficulties=["Hard"])
    keys = ["dog", "suicide_rush", "mind_control"]
    with tempfile.TemporaryDirectory() as d:
        aimd = os.path.join(d, "aimd.ini")
        shutil.copy(van_aimd, aimd)

        wg, trg, text = everything_preview_text(rules, aimd, wcfg, keys, seed=1)
        c = everything_counts(wg, trg)
        prev_ok = (c["triggers"] > 0 and c["teams"] > 0
                   and "[AITriggerTypes]" in text)
        print(f"everything preview: {c}  {'OK' if prev_ok else 'FAIL'}")
        ok = ok and prev_ok

        from yrbalance.engine import (parse_ini_lines, build_section_map,
                                      parse_type_list, lint_ai)
        before = build_section_map(parse_ini_lines(aimd))[0]
        n_before = len(parse_type_list(before, "TaskForces"))
        be = [f for f in lint_ai(before, {}) if f.severity == "error"
              and ("=[" in f.message or "team [" in f.message)]
        cnt = insert_everything(rules, aimd, wcfg, keys, seed=1)
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
