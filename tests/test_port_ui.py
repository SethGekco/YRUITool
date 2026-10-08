#!/usr/bin/env python3
"""
PortDialog smoke test (offscreen Qt): analyze → populate factor table + tree →
select objects → override a ratio → apply into a temp target (sections written,
registered, owner cleaned, ratio override applied).

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_port_ui.py
"""

import os
import sys
import tempfile
import importlib.util

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from yrbalance.engine import (
    parse_ini_lines, build_section_map, build_port_plan, get_val,
)

# Borrow the engine test's fixtures.
_spec = importlib.util.spec_from_file_location(
    "tp", os.path.join(HERE, "test_portmod.py"))
tp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tp)


def main():
    ok = True
    app = QApplication.instance() or QApplication([])
    from yrbalance.ui.main_window import MainWindow
    from yrbalance.ui.port_dialog import PortDialog

    d = tempfile.mkdtemp()
    tgt = os.path.join(d, "rulesmd.ini")
    open(tgt, "w").write(tp.TARGET)
    # source in its OWN folder so its artmd/soundmd don't collide with target's
    sd = os.path.join(d, "src")
    os.makedirs(sd)
    src = os.path.join(sd, "rulesmd.ini")
    open(src, "w").write(tp.SOURCE)
    open(os.path.join(sd, "artmd.ini"), "w").write(tp.SRC_ART)
    open(os.path.join(sd, "soundmd.ini"), "w").write(tp.SRC_SOUND)
    van = os.path.join(d, "van.ini")
    open(van, "w").write(tp.VANILLA)
    # a target artmd to receive the ported art entry
    open(os.path.join(d, "artmd.ini"), "w").write("[Placeholder]\nx=1\n")

    mw = MainWindow()
    mw._load_rules_path(tgt)
    dlg = PortDialog(mw)
    dlg.src_edit.setText(src)
    dlg.van_edit.setText(van)

    # analyze inline (skip the worker thread)
    sm = build_section_map(parse_ini_lines(src))[0]
    am = build_section_map(parse_ini_lines(os.path.join(sd, "artmd.ini")))[0]
    snm = build_section_map(parse_ini_lines(os.path.join(sd, "soundmd.ini")))[0]
    plan = build_port_plan(sm, mw.section_map,
                           build_section_map(parse_ini_lines(van))[0],
                           config=mw.config, src_art_map=am, src_sound_map=snm)
    dlg._plan_ready((plan, True, True))

    ui_ok = dlg.factor_table.rowCount() >= 3 and dlg.tree.topLevelItemCount() >= 3
    print(f"populated: factors={dlg.factor_table.rowCount()} "
          f"kinds={dlg.tree.topLevelItemCount()}  {'OK' if ui_ok else 'FAIL'}")
    ok = ok and ui_ok

    want = {"SUPERTANK", "BigGun", "SuperWH", "Invisible"}
    for i in range(dlg.tree.topLevelItemCount()):
        top = dlg.tree.topLevelItem(i)
        for j in range(top.childCount()):
            ch = top.child(j)
            if ch.data(0, Qt.UserRole) in want:
                ch.setCheckState(0, Qt.Checked)
    sel = dlg._selected_names()
    sel_ok = want <= sel
    print(f"selection: {sorted(sel)}  {'OK' if sel_ok else 'FAIL'}")
    ok = ok and sel_ok

    dlg._factor_spins[("vehicle", "Cost")].setValue(2.0)   # override
    msg = dlg._apply(dlg._selected_names(), dlg._overrides())
    tm = build_section_map(parse_ini_lines(tgt))[0]
    reg = "SUPERTANK" in {v for _, (_, _, v) in tm["vehicletypes"]["keys"].items()}
    am2 = build_section_map(parse_ini_lines(os.path.join(d, "artmd.ini")))[0]
    apply_ok = ("supertank" in tm
                and get_val(tm, "SUPERTANK", "Cost") == "4000"   # 2000×2.0 override
                and "Turkey" not in (get_val(tm, "SUPERTANK", "Owner") or "")
                and reg and "supertank" in am2)
    print(f"apply: cost={get_val(tm,'SUPERTANK','Cost')} "
          f"owner={get_val(tm,'SUPERTANK','Owner')} reg={reg} "
          f"art={'supertank' in am2}  {'OK' if apply_ok else 'FAIL'}")
    print("   " + msg.split("\n")[0])
    ok = ok and apply_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
