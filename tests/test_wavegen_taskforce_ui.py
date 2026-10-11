#!/usr/bin/env python3
"""
TaskforceGenDialog smoke (offscreen Qt): the config lists populate, Generate fills
the preview table, and Insert writes the ticked taskforces into a copy of the aimd.
Needs a rules path (uses the knowledge vanilla aimd).

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_wavegen_taskforce_ui.py "<rulesmd>"
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
    from PySide6.QtCore import Qt
    app = QApplication.instance() or QApplication([])
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)

    d = tempfile.mkdtemp()
    aimd = os.path.join(d, "aimd.ini")
    shutil.copy(van_aimd, aimd)

    from yrbalance.ui.wavegen_taskforce_dialog import TaskforceGenDialog
    inserted = {"called": False}
    dlg = TaskforceGenDialog(None, rules, aimd,
                             on_inserted=lambda: inserted.update(called=True))

    lists_ok = (dlg.fac_list.count() > 0 and dlg.phase_list.count() > 0
                and dlg.role_list.count() > 0)
    print(f"config lists populate: {'OK' if lists_ok else 'FAIL'}")
    ok = ok and lists_ok

    dlg._generate()
    gen_ok = dlg.table.rowCount() > 0 and dlg.insert_btn.isEnabled()
    print(f"generate fills preview table: {'OK' if gen_ok else 'FAIL'}  "
          f"rows={dlg.table.rowCount()}")
    ok = ok and gen_ok

    # keep only the first ticked, insert
    dlg._set_all(False)
    dlg.table.item(0, 0).setCheckState(Qt.Checked)
    first_id = dlg._entries[0].tf_id
    dlg._insert()
    from yrbalance.engine import (parse_ini_lines, build_section_map,
                                  parse_type_list, list_backups,
                                  default_backup_dir)
    m = build_section_map(parse_ini_lines(aimd))[0]
    ins_ok = (first_id in set(parse_type_list(m, "TaskForces"))
              and first_id.lower() in m
              and len(list_backups(default_backup_dir(aimd), aimd)) >= 1
              and inserted["called"])
    print(f"insert ticked → aimd (backup + reload cb): "
          f"{'OK' if ins_ok else 'FAIL'}")
    ok = ok and ins_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
