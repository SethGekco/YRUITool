#!/usr/bin/env python3
"""
StrategicTriggerDialog smoke (offscreen Qt): the faction list populates, Generate
fills summary + preview, Insert writes into a copy of the aimd (backup + reload cb).
aimd-only. Needs a rules path (uses the knowledge vanilla aimd).

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_wavegen_strategic_ui.py "<rulesmd>"
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

    from yrbalance.ui.wavegen_strategic_dialog import StrategicTriggerDialog
    flag = {"inserted": False}
    dlg = StrategicTriggerDialog(None, rules, aimd,
                                 on_inserted=lambda: flag.update(inserted=True))
    list_ok = dlg.fac_list.count() >= 2
    print(f"faction list populates ({dlg.fac_list.count()}): "
          f"{'OK' if list_ok else 'FAIL'}")
    ok = ok and list_ok

    for name, cb in dlg.diff_boxes.items():
        cb.setChecked(name == "Hard")

    dlg._generate()
    gen_ok = (dlg.insert_btn.isEnabled()
              and "[AITriggerTypes]" in dlg.preview.toPlainText())
    print(f"generate fills preview: {'OK' if gen_ok else 'FAIL'}")
    ok = ok and gen_ok

    from yrbalance.engine import (parse_ini_lines, build_section_map,
                                  parse_type_list, list_backups,
                                  default_backup_dir)
    n0 = len(parse_type_list(build_section_map(parse_ini_lines(aimd))[0],
                             "TaskForces"))
    dlg._insert()
    n1 = len(parse_type_list(build_section_map(parse_ini_lines(aimd))[0],
                             "TaskForces"))
    ins_ok = (n1 > n0 and flag["inserted"]
              and len(list_backups(default_backup_dir(aimd), aimd)) >= 1)
    print(f"insert writes + backup + reload cb: "
          f"{'OK' if ins_ok else 'FAIL'}  {n0}→{n1}")
    ok = ok and ins_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
