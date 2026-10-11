#!/usr/bin/env python3
"""
Headless TaskForce generation over the vendored composer: vocab, factions,
generate a batch from specs, and that each entry renders to a valid aimd section +
inserts (write_to_ini) with collision-free ids. Needs a rules(md).ini path (its
sibling aimd is used if present; else the knowledge vanilla aimd).

    python3 tests/test_wavegen_taskforce.py "/path/to/rulesmd.ini"
"""

import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.wavegen.generate import (
    taskforce_vocab, mod_factions, generate_taskforces, entry_composition,
    entry_section_text,
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
    v = taskforce_vocab()
    vocab_ok = ("GENERAL_ATTACK" in v["roles"] and "Mid" in v["phases"]
                and v["difficulties"] == ["Easy", "Medium", "Hard"])
    facs = mod_factions(rules)
    print(f"vocab + factions: roles={len(v['roles'])} factions={facs}  "
          f"{'OK' if vocab_ok and facs else 'FAIL'}")
    ok = ok and vocab_ok and bool(facs)

    specs = [{"faction": facs[0], "phase": "Mid", "role": "ANTI_INF",
              "count": 3}]
    gen, parser, entries = generate_taskforces(rules, van_aimd, specs, seed=7)
    gen_ok = len(entries) >= 1 and all(e.units for e in entries)
    print(f"generate {len(entries)} taskforces (non-empty): "
          f"{'OK' if gen_ok else 'FAIL'}")
    ok = ok and gen_ok
    if entries:
        print(f"  e.g. {entries[0].name}: {entry_composition(entries[0], parser)}")

    sec = entry_section_text(entries[0])
    sec_ok = (f"[{entries[0].tf_id}]" in sec and "Name=" in sec
              and "Group=-1" in sec and "0=" in sec)
    print(f"section text well-formed: {'OK' if sec_ok else 'FAIL'}")
    ok = ok and sec_ok

    # insert into a COPY of the aimd and confirm the ids land in [TaskForces]
    with tempfile.TemporaryDirectory() as d:
        tmp = os.path.join(d, "aimd.ini")
        shutil.copy(van_aimd, tmp)
        gen.write_to_ini(entries, tmp)
        from yrbalance.engine import (parse_ini_lines, build_section_map,
                                      parse_type_list)
        m = build_section_map(parse_ini_lines(tmp))[0]
        tf_ids = set(parse_type_list(m, "TaskForces"))
        inserted = all(e.tf_id in tf_ids and e.tf_id.lower() in m
                       for e in entries)
        print(f"write_to_ini: ids registered + sections present: "
              f"{'OK' if inserted else 'FAIL'}")
        ok = ok and inserted

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
