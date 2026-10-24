#!/usr/bin/env python3
"""
EOF guard for INIs the tool only READS or copies: the engine never reads an
INI's last line, so a loaded/ported mod file ending on a real key has that key
silently ignored in game.

    python3 tests/test_eofguard.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from yrbalance.engine import (last_line_unguarded, guard_ini_bytes,
                                  mod_ini_files, scan_eof_guards,
                                  add_eof_guard, write_mix, MixFile)
    from yrbalance.engine.mixlib import for_disk
    ok = True

    cases = [
        (b"[A]\r\nX=1\r\n", "X=1"),
        (b"[A]\nX=1", "X=1"),                  # no trailing newline
        (b"[A]\nX=1\n;\n", None),
        (b"[A]\nX=1\n;;;;;\r\n\r\n", None),    # author's own guard + blanks
        (b"[A]\nX=1\n; last note\n", None),    # any comment counts
        (b"", ""),
    ]
    det_ok = all(last_line_unguarded(d) == want for d, want in cases)
    print(f"last line the game skips is found; any comment counts as a "
          f"guard: {'OK' if det_ok else 'FAIL'}")
    ok = ok and det_ok

    g_ok = (guard_ini_bytes(b"[A]\r\nX=1\r\n") == b"[A]\r\nX=1\r\n;\r\n"
            and guard_ini_bytes(b"[A]\nX=1") == b"[A]\nX=1\n;\n"
            and guard_ini_bytes(b"[A]\n;;;;;\n") == b"[A]\n;;;;;\n"
            and guard_ini_bytes(b"") == b";\n")
    print(f"guard_ini_bytes keeps CRLF, adds the missing newline, leaves a "
          f"guarded file alone: {'OK' if g_ok else 'FAIL'}")
    ok = ok and g_ok

    with tempfile.TemporaryDirectory() as d:
        def w(name, data):
            p = os.path.join(d, name)
            with open(p, "wb") as fh:
                fh.write(data)
            return p
        # odd bytes on purpose: a fix must not re-encode anything
        body = b"[#include]\r\n1=extra.ini\r\n[E1]\r\nUIName=Caf\xe9\r\nCost=200\r\n"
        rules = w("rulesmd.ini", body)
        w("extra.ini", b"[X]\nA=1\n;\n")
        w("ARTMD.INI", b"[E1]\nImage=E1")
        w("readme.ini", b"[Not]\nRead=1\n")     # not a game file of this mod
        files = sorted(os.path.basename(p) for p in mod_ini_files(rules))
        files_ok = files == ["ARTMD.INI", "extra.ini", "rulesmd.ini"]
        miss = sorted(os.path.basename(p) for p, _l in
                      scan_eof_guards(mod_ini_files(rules)))
        changed = add_eof_guard(rules)
        again = add_eof_guard(rules)
        with open(rules, "rb") as fh:
            after = fh.read()
        fix_ok = (miss == ["ARTMD.INI", "rulesmd.ini"] and changed
                  and not again and after == body + b";\r\n")
        print(f"mod files = rules + includes + art/sound/AI siblings; fixing "
              f"appends ';' only, byte-exact, once: "
              f"{'OK' if files_ok and fix_ok else 'FAIL'} {files} {miss}")
        ok = ok and files_ok and fix_ok

        mix = os.path.join(d, "t.mix")
        write_mix(mix, {"rulesmd.ini": b"[A]\r\nX=1\r\n",
                        "pic.shp": b"\x00\x01X=1"})
        out = os.path.join(d, "out")
        MixFile(mix).extract_all(out)
        with open(os.path.join(out, "rulesmd.ini"), "rb") as fh:
            ini = fh.read()
        with open(os.path.join(out, "pic.shp"), "rb") as fh:
            shp = fh.read()
        mix_ok = (ini == b"[A]\r\nX=1\r\n;\r\n" and shp == b"\x00\x01X=1"
                  and for_disk("a.INI", b"X=1") == b"X=1\n;\n")
        print(f"an .ini extracted from a mix lands guarded; other files byte "
              f"for byte: {'OK' if mix_ok else 'FAIL'}")
        ok = ok and mix_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
