#!/usr/bin/env python3
"""
Test the backup system: timestamped copies, custom folder, and retention pruning.

    python3 tests/test_backup.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import make_backup, list_backups


def main():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "rulesmd.ini")
        with open(src, "w") as f:
            f.write("[General]\nName=v1\n")
        bdir = os.path.join(d, "mybackups")

        # Make 4 backups with distinct timestamps, keep=2.
        made = []
        for i in range(4):
            with open(src, "w") as f:
                f.write(f"[General]\nName=v{i}\n")
            made.append(make_backup(src, bdir, keep=2,
                                    _stamp=f"20260101-00000{i}"))

        exist = [p for p in made if os.path.isfile(p)]
        all_in_dir = list_backups(bdir, src)
        # Custom folder used, and pruned to the 2 newest.
        cust_ok = all(p.startswith(bdir) for p in made)
        prune_ok = len(all_in_dir) == 2 and os.path.isfile(made[-1]) and os.path.isfile(made[-2]) \
            and not os.path.isfile(made[0])
        print(f"custom backup folder: {cust_ok}  {'OK' if cust_ok else 'FAIL'}")
        print(f"retention keep=2: {len(all_in_dir)} kept (newest two)  "
              f"{'OK' if prune_ok else 'FAIL'}")
        ok = ok and cust_ok and prune_ok

        # The newest backup matches the file content before the last write.
        newest = list_backups(bdir, src)[0]
        content = open(newest).read()
        content_ok = "v3" in content  # last backup was taken of v3 before... actually of v3
        print(f"backup captured content: {content_ok}  {'OK' if content_ok else 'FAIL'}")
        ok = ok and content_ok

        # Default folder (next to file) when none given.
        b2 = make_backup(src, None, keep=5, _stamp="20260102-000000")
        def_ok = os.path.isfile(b2) and "yrbt_backups" in b2
        print(f"default folder: {def_ok}  {'OK' if def_ok else 'FAIL'}")
        ok = ok and def_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
