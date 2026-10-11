#!/usr/bin/env python3
"""
Full wave-chain generation (TaskForces + Scripts + Teams + Triggers): preview text
has all four sections, insert grows the aimd, and the written chain is internally
consistent (every team's TaskForce/Script and every trigger's team resolve). Needs
a rules(md).ini path (uses its sibling aimd or the knowledge vanilla aimd).

    python3 tests/test_wavegen_wave.py "/path/to/rulesmd.ini"
"""

import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.wavegen.generate import (
    wave_vocab, waves_preview_text, insert_waves, wave_counts,
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
    v = wave_vocab()
    vocab_ok = bool(v["role_pool"]) and bool(v["phase_pool"])
    print(f"wave_vocab: {'OK' if vocab_ok else 'FAIL'}")
    ok = ok and vocab_ok

    cfg = dict(general_waves_per_difficulty=4,
               country_waves_per_difficulty=2, difficulties=["Hard"])
    with tempfile.TemporaryDirectory() as d:
        aimd = os.path.join(d, "aimd.ini")
        shutil.copy(van_aimd, aimd)

        gen, text = waves_preview_text(rules, aimd, cfg, seed=42)
        c = wave_counts(gen)
        prev_ok = (c["triggers"] > 0 and c["teams"] > 0 and c["taskforces"] > 0
                   and all(s in text for s in ("[TaskForces]", "[ScriptTypes]",
                                               "[TeamTypes]", "[AITriggerTypes]")))
        print(f"preview: {c}  sections present: {'OK' if prev_ok else 'FAIL'}")
        ok = ok and prev_ok

        from yrbalance.engine import (parse_ini_lines, build_section_map,
                                      parse_type_list)
        before = build_section_map(parse_ini_lines(aimd))[0]
        n_before = len(parse_type_list(before, "TaskForces"))
        cnt = insert_waves(rules, aimd, cfg, seed=42)
        after = build_section_map(parse_ini_lines(aimd))[0]
        grew = len(parse_type_list(after, "TaskForces")) > n_before
        print(f"insert grows aimd: {'OK' if grew else 'FAIL'}  {cnt}")
        ok = ok and grew

        # internal consistency: team TaskForce/Script + trigger team resolve
        from yrbalance.engine import lint_ai
        errs = [f.message for f in lint_ai(after, {})
                if f.severity == "error"
                and ("=[" in f.message or "team [" in f.message)]
        # the vanilla base itself has a few pre-existing dangling triggers; the
        # generated chain must not ADD unresolved refs beyond those
        base_errs = [f.message for f in lint_ai(before, {})
                     if f.severity == "error"
                     and ("=[" in f.message or "team [" in f.message)]
        cons_ok = len(errs) <= len(base_errs)
        print(f"no new unresolved refs (base {len(base_errs)} → after {len(errs)}): "
              f"{'OK' if cons_ok else 'FAIL'}")
        ok = ok and cons_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
