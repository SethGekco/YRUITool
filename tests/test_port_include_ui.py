#!/usr/bin/env python3
"""
Porter per-file include opt-in (offscreen Qt): the source rules [#include] graph is
listed with a checkbox per file, the selection dialog yields the right file-id set
(None when nothing deselected), deselecting an include drops its sections from the
resolved map the porter analyses, and PortDialog resets/clears the selection on
source change / toggle-off.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_port_include_ui.py
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from yrbalance.engine import (
    include_graph, load_with_includes, build_port_plan, get_val,
)


def _w(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    ok = True
    app = QApplication.instance() or QApplication([])
    from yrbalance.ui.main_window import MainWindow
    from yrbalance.ui.port_dialog import PortDialog, _IncludeSelectDialog

    d = tempfile.mkdtemp()
    # target (what we're porting INTO)
    tgt = os.path.join(d, "rulesmd.ini")
    _w(tgt, "[VehicleTypes]\n1=GATNK\n\n[GATNK]\nCost=800\n")
    # source mod: main rules includes two files, each defining a vehicle
    sd = os.path.join(d, "src")
    os.makedirs(sd)
    src = os.path.join(sd, "rulesmd.ini")
    _w(src, "[#include]\n1=units_a.ini\n2=units_b.ini\n\n"
            "[VehicleTypes]\n1=MAINV\n\n[MAINV]\nCost=500\nName=Main\n")
    _w(os.path.join(sd, "units_a.ini"),
       "[VehicleTypes]\n2=AUNIT\n\n[AUNIT]\nCost=1000\nName=AlphaUnit\n")
    _w(os.path.join(sd, "units_b.ini"),
       "[VehicleTypes]\n3=BUNIT\n\n[BUNIT]\nCost=2000\nName=BravoUnit\n")

    mw = MainWindow()
    mw._load_rules_path(tgt)
    dlg = PortDialog(mw)
    dlg.src_edit.setText(src)

    # --- the selection dialog lists both includes, all checked by default ---
    graph = include_graph(src)
    sel_dlg = _IncludeSelectDialog(dlg, graph)
    names = []
    it = sel_dlg.tree.invisibleRootItem()
    for i in range(it.childCount()):
        names.append(it.child(i).text(0))
    list_ok = "units_a.ini" in names and "units_b.ini" in names
    print(f"include tree lists files {names}: {'OK' if list_ok else 'FAIL'}")
    ok = ok and list_ok

    # all checked → selection() is None ("all")
    all_ok = sel_dlg.selection() is None
    print(f"all-checked selection is None: {'OK' if all_ok else 'FAIL'}")
    ok = ok and all_ok

    # deselect units_b.ini → selection excludes it, keeps units_a.ini
    for node in sel_dlg._iter():
        if node.text(0) == "units_b.ini":
            node.setCheckState(0, Qt.Unchecked)
    sel = sel_dlg.selection()
    sel_names = {os.path.basename(f) for f in (sel or set())}
    desel_ok = (sel is not None and "units_a.ini" in sel_names
                and "units_b.ini" not in sel_names)
    print(f"deselect yields {sorted(sel_names)}: {'OK' if desel_ok else 'FAIL'}")
    ok = ok and desel_ok

    # --- the selection drives the resolved map the porter ports from ---
    allow = (lambda fid: fid in sel)
    _l, smap, _o, _s = load_with_includes(src, allow=allow)
    plan = build_port_plan(smap, mw.section_map, None, config=mw.config)
    pnames = {o.name.upper() for o in plan.objects}
    # AUNIT (units_a, selected) imports; BUNIT (units_b, deselected) does not;
    # MAINV (main file) always imports.
    port_ok = ("AUNIT" in pnames and "MAINV" in pnames
               and "BUNIT" not in pnames)
    print(f"plan honours selection {sorted(pnames)}: "
          f"{'OK' if port_ok else 'FAIL'}")
    ok = ok and port_ok

    # and with everything (allow=None) BUNIT comes back
    _l2, smap2, _o2, _s2 = load_with_includes(src)
    plan2 = build_port_plan(smap2, mw.section_map, None, config=mw.config)
    all_in = {"AUNIT", "BUNIT", "MAINV"} <= {o.name.upper() for o in plan2.objects}
    print(f"all-includes plan has every unit: {'OK' if all_in else 'FAIL'}")
    ok = ok and all_in

    # --- PortDialog state: store selection; reset on src change; clear on toggle ---
    dlg._include_selection = sel
    dlg._src_changed()
    reset_ok = dlg._include_selection is None
    dlg._include_selection = sel
    dlg._inc_toggled(False)
    toggle_ok = dlg._include_selection is None and not dlg.inc_btn.isEnabled()
    print(f"selection resets on src change / clears on toggle-off: "
          f"{'OK' if reset_ok and toggle_ok else 'FAIL'}")
    ok = ok and reset_ok and toggle_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
