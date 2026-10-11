#!/usr/bin/env python3
"""
WaveGenDialog smoke (offscreen Qt): Generate fills the summary + preview, and Insert
writes the full chain into a copy of the aimd (backup + reload callback). Needs a
rules path (uses the knowledge vanilla aimd).

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_wavegen_wave_ui.py "<rulesmd>"
"""

import os
import sys
import shutil
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    rules = sys.argv[1] if len(sys.argv) > 1 else None
    van_aimd = os.path.expanduser(
        "~/.yrbalancetool/knowledge/read/vanilla_yr_aimd.ini")
    if not rules or not os.path.isfile(rules) or not os.path.isfile(van_aimd):
        print("SKIP (no rules / aimd)")
        return 0

    ok = True
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)

    d = tempfile.mkdtemp()
    aimd = os.path.join(d, "aimd.ini")
    shutil.copy(van_aimd, aimd)

    from yrbalance.ui.wavegen_wave_dialog import WaveGenDialog
    flag = {"inserted": False}
    dlg = WaveGenDialog(None, rules, aimd,
                        on_inserted=lambda: flag.update(inserted=True))
    # small config for speed
    dlg.general.setValue(3)
    dlg.country.setValue(1)
    for name, cb in dlg.diff_boxes.items():
        cb.setChecked(name == "Hard")

    dlg._generate()
    gen_ok = (dlg.insert_btn.isEnabled()
              and "[AITriggerTypes]" in dlg.preview.toPlainText()
              and "triggers" in dlg.summary.text())
    print(f"generate fills summary + preview: {'OK' if gen_ok else 'FAIL'}")
    ok = ok and gen_ok

    from yrbalance.engine import (parse_ini_lines, build_section_map,
                                  parse_type_list, list_backups,
                                  default_backup_dir)
    n_before = len(parse_type_list(build_section_map(parse_ini_lines(aimd))[0],
                                   "TaskForces"))
    dlg._insert()
    n_after = len(parse_type_list(build_section_map(parse_ini_lines(aimd))[0],
                                  "TaskForces"))
    ins_ok = (n_after > n_before
              and len(list_backups(default_backup_dir(aimd), aimd)) >= 1
              and flag["inserted"])
    print(f"insert writes chain (backup + reload cb): "
          f"{'OK' if ins_ok else 'FAIL'}  {n_before}→{n_after} taskforces")
    ok = ok and ins_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
