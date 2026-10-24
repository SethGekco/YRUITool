#!/usr/bin/env python3
"""
EofGuardBar smoke: a mod file ending on a real key shows the bar (naming the
line the game skips); "Add ';'" backs it up, appends the guard and hides.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_eof_bar_ui.py
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="yrbt_cfg_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from yrbalance.engine import mod_ini_files
    from yrbalance.ui.eof_guard_bar import EofGuardBar

    d = tempfile.mkdtemp(prefix="yrbt_eof_")
    rules = os.path.join(d, "rulesmd.ini")
    with open(rules, "wb") as fh:
        fh.write(b"[E1]\r\nCost=200\r\nStrength=125\r\n")
    bar = EofGuardBar()
    found = bar.check(mod_ini_files(rules), "the loaded mod", note="NOTE-X")
    shown = (not bar.isHidden() and "Strength=125" in bar.label.text()
             and "the loaded mod" in bar.label.text()
             and "NOTE-X" in bar.label.text() and len(found) == 1)
    n = bar.fix()
    with open(rules, "rb") as fh:
        data = fh.read()
    backups = os.listdir(os.path.join(d, "yrbt_backups"))
    fixed = (n == 1 and bar.isHidden()
             and data == b"[E1]\r\nCost=200\r\nStrength=125\r\n;\r\n"
             and len(backups) == 1)
    quiet = bar.check(mod_ini_files(rules), "the loaded mod") == [] \
        and bar.isHidden()
    # the background variant used on load
    with open(rules, "ab") as fh:
        fh.write(b"[E2]\r\nCost=1\r\n")
    scan = bar.check_later(lambda: mod_ini_files(rules), "the loaded mod")
    scan.wait(10000)
    for _ in range(20):
        app.processEvents()
    later = not bar.isHidden() and "Cost=1" in bar.label.text()
    ok = shown and fixed and quiet and later
    print(f"bar names the skipped line; fix backs up + appends ';'; a guarded "
          f"mod shows nothing: {'OK' if ok else 'FAIL'} "
          f"shown={shown} fixed={fixed} quiet={quiet} later={later}")
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
