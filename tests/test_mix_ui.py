#!/usr/bin/env python3
"""
MixBrowserDialog smoke test (offscreen Qt): open a mix, list/filter, extract,
stage an add + a remove, save (repack), and verify the result.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_mix_ui.py
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt

from yrbalance.engine import write_mix, MixFile


def main():
    ok = True
    app = QApplication.instance() or QApplication([])
    from yrbalance.ui.mix_dialog import MixBrowserDialog

    d = tempfile.mkdtemp()
    src = os.path.join(d, "mod_ecache.mix")
    write_mix(src, {"keep.vxl": b"KEEP-DATA", "drop.hva": b"DROP-DATA",
                    "icon.shp": b"ICON-DATA"})

    dlg = MixBrowserDialog(None, path=src)
    open_ok = dlg.mix is not None and len(dlg.mix.entries) >= 3
    print(f"open + list: {len(dlg.mix.entries)} entries  "
          f"{'OK' if open_ok else 'FAIL'}")
    ok = ok and open_ok

    # filter narrows the view
    dlg.filter_edit.setText("keep")
    filt_ok = any("keep" in (e.name or "").lower() for e in dlg._rows) \
        and all(dlg.filter_edit.text() in (e.name or "") for e in dlg._rows)
    print(f"filter: {len(dlg._rows)} shown  {'OK' if filt_ok else 'FAIL'}")
    ok = ok and filt_ok
    dlg.filter_edit.setText("")

    # stage: remove drop.hva, add a new file (name collision replaces icon)
    newf = os.path.join(d, "icon.shp")
    open(newf, "wb").write(b"NEW-ICON")
    add2 = os.path.join(d, "extra.pal")
    open(add2, "wb").write(b"PAL-DATA")
    dlg.adds[os.path.basename(newf)] = newf          # replace icon.shp
    dlg.adds[os.path.basename(add2)] = add2          # add extra.pal
    drop_id = next(e.id for e in dlg.mix.entries if e.name == "drop.hva")
    dlg.removed.add(drop_id)

    # save (repack) to a new path, bypassing the (modal) dialogs
    dest = os.path.join(d, "out.mix")
    from unittest import mock
    with mock.patch("yrbalance.ui.mix_dialog.QFileDialog.getSaveFileName",
                    return_value=(dest, "")), \
         mock.patch("yrbalance.ui.mix_dialog.QMessageBox.information",
                    return_value=None), \
         mock.patch("yrbalance.ui.mix_dialog.QMessageBox.warning",
                    return_value=QMessageBox.Yes):
        dlg._save()

    m = MixFile(dest)
    names = {e.name for e in m.entries if e.name}
    save_ok = ("keep.vxl" in names and "drop.hva" not in names
               and "extra.pal" in names
               and m.read("icon.shp") == b"NEW-ICON"      # replaced
               and m.read("keep.vxl") == b"KEEP-DATA")
    print(f"save (repack): names={sorted(names)}  {'OK' if save_ok else 'FAIL'}")
    ok = ok and save_ok

    # extract_all round-trips bytes to disk
    exdir = os.path.join(d, "ex")
    written = m.extract_all(exdir)
    ex_ok = (os.path.isfile(os.path.join(exdir, "keep.vxl"))
             and open(os.path.join(exdir, "icon.shp"), "rb").read() == b"NEW-ICON")
    print(f"extract_all: {len(written)} files  {'OK' if ex_ok else 'FAIL'}")
    ok = ok and ex_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
