#!/usr/bin/env python3
"""
Specialty wave generation (dog / terror-drone / naval / mind-control / …): the type
list is non-empty, a selection previews with all four sections, insert grows the
aimd, and the generated chain adds no new unresolved refs. Needs a rules path.

    python3 tests/test_wavegen_specialty.py "/path/to/rulesmd.ini"
"""

import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.wavegen.generate import (
    specialty_wave_types, specialty_preview_text, insert_specialty_waves,
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
    types = specialty_wave_types()
    types_ok = len(types) >= 15 and all(len(t) == 2 for t in types)
    print(f"specialty types: {len(types)}  {'OK' if types_ok else 'FAIL'}")
    ok = ok and types_ok

    keys = ["dog", "suicide_rush", "parasite", "bfrt", "mind_control",
            "engineer_rush"]
    with tempfile.TemporaryDirectory() as d:
        aimd = os.path.join(d, "aimd.ini")
        shutil.copy(van_aimd, aimd)

        gen, text = specialty_preview_text(rules, aimd, keys, ["Hard"], seed=2)
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
        cnt = insert_specialty_waves(rules, aimd, keys, ["Hard"], seed=2)
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
