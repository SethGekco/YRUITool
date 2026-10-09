#!/usr/bin/env python3
"""
EncyclopediaDialog smoke test (offscreen Qt): load an index, search/filter,
select a unit, show its report, and export it.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_encyclopedia_ui.py
"""

import os
import sys
import tempfile
import importlib.util
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PySide6.QtWidgets import QApplication

from yrbalance.engine import build_mod_index, mix_id, write_mix

# reuse the engine test's fixtures
_spec = importlib.util.spec_from_file_location(
    "te", os.path.join(HERE, "test_encyclopedia.py"))
te = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(te)


def main():
    ok = True
    app = QApplication.instance() or QApplication([])
    from yrbalance.ui.main_window import MainWindow
    from yrbalance.ui.encyclopedia_dialog import EncyclopediaDialog

    d = tempfile.mkdtemp()
    mod = os.path.join(d, "mymod")
    os.makedirs(mod)
    open(os.path.join(mod, "rulesmd.ini"), "w").write(te.RULES)
    open(os.path.join(mod, "artmd.ini"), "w").write(te.ART)
    open(os.path.join(mod, "STNKART.VXL"), "wb").write(b"Voxel Animation\x1a")
    write_mix(os.path.join(mod, "ecacheX.mix"), {"STNKART.HVA": b"HVA"})

    mw = MainWindow()
    dlg = EncyclopediaDialog(mw)
    idx = build_mod_index(os.path.join(mod, "rulesmd.ini"),
                          vanilla_ids={mix_id("STNKICON.SHP")})
    dlg._index_ready(idx)                      # bypass the worker thread

    dlg.search_edit.setText("super")
    dlg._refresh()
    search_ok = len(dlg._results) == 1 and dlg._results[0].name == "SUPERTANK"
    print(f"search populates table: {[r.name for r in dlg._results]}  "
          f"{'OK' if search_ok else 'FAIL'}")
    ok = ok and search_ok

    dlg.table.selectRow(0)
    report = dlg.detail.toPlainText()
    detail_ok = "[SUPERTANK]" in report and "weapons:" in report and "loose" in report
    print(f"detail report shown: {'OK' if detail_ok else 'FAIL'}")
    ok = ok and detail_ok

    dest = os.path.join(d, "library")
    with mock.patch("yrbalance.ui.encyclopedia_dialog.QFileDialog."
                    "getExistingDirectory", return_value=dest), \
         mock.patch("yrbalance.ui.encyclopedia_dialog.QMessageBox.information",
                    return_value=None):
        dlg._export()
    exp_ok = (os.path.isfile(os.path.join(dest, "SUPERTANK.ini"))
              and os.path.isfile(os.path.join(dest, "STNKART.VXL"))
              and os.path.isfile(os.path.join(dest, "STNKART.HVA")))
    print(f"export writes ini + media to library: {'OK' if exp_ok else 'FAIL'}")
    ok = ok and exp_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
