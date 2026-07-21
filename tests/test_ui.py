#!/usr/bin/env python3
"""
Headless UI test (offscreen Qt). Builds MainWindow, exercises the Naval
"same as Vehicles" per-field linkage in collect_spec, and runs the full
apply-and-save flow against a real rulesmd.ini to a temp file.

Run with the venv python:
    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_ui.py /path/to/rulesmd.ini
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication

from yrbalance.ui.main_window import MainWindow
from yrbalance.ui import controller
from yrbalance.engine import (
    parse_ini_lines, build_section_map, get_val, parse_type_list,
    get_current_field_value_from_line,
)


def main(path):
    ok = True
    app = QApplication.instance() or QApplication([])
    win = MainWindow()

    # ── Naval linkage in collect_spec ────────────────────────────────────────
    veh = win.category_tabs["veh"]
    nav = win.category_tabs["nav"]
    veh.spins["cost"].setValue(0.5)
    veh.spins["strength"].setValue(2.0)
    # Naval: keep cost linked (inherits 0.5), unlink strength and set 3.0.
    nav.links["strength"].setChecked(False)
    nav.spins["strength"].setValue(3.0)

    spec = win.collect_spec()
    nav_r = spec.categories["nav"]
    link_ok = nav_r.cost == 0.5 and nav_r.strength == 3.0
    print(f"naval linkage: cost={nav_r.cost} (linked->0.5), "
          f"strength={nav_r.strength} (own->3.0)  {'OK' if link_ok else 'FAIL'}")
    ok = ok and link_ok

    # ── Cost mode + TechLevel controls flow into the spec ────────────────────
    inf_tab = win.category_tabs["inf"]
    idx = inf_tab.cost_mode.findData("techlevel")
    inf_tab.cost_mode.setCurrentIndex(idx)
    bld_tab = win.category_tabs["bld"]
    bld_tab.tl_recompute.setChecked(True)
    bld_tab.tl_write.setChecked(True)
    depth_idx = bld_tab.cost_mode.findData("depth")
    bld_tab.cost_mode.setCurrentIndex(depth_idx)
    spec2 = win.collect_spec()
    adv_ok = (spec2.categories["inf"].cost_mode == "techlevel"
              and spec2.categories["bld"].tl_recompute
              and spec2.categories["bld"].tl_write
              and spec2.categories["bld"].cost_mode == "depth")
    print(f"cost/TL controls -> spec: inf.cost_mode={spec2.categories['inf'].cost_mode}, "
          f"bld.tl_recompute={spec2.categories['bld'].tl_recompute}, "
          f"bld.cost_mode={spec2.categories['bld'].cost_mode}  {'OK' if adv_ok else 'FAIL'}")
    ok = ok and adv_ok
    # Reset for the apply test below.
    inf_tab.cost_mode.setCurrentIndex(inf_tab.cost_mode.findData("flat"))

    # Infantry cost x0.5 for the apply test.
    win.category_tabs["inf"].spins["cost"].setValue(0.5)

    # ── Apply and save (via controller, no dialogs) ──────────────────────────
    win.source_path = path
    _lines, win.section_map = controller.load_rules(path)

    # Pre-capture a vanilla infantry cost.
    sm0 = win.section_map
    probe = next(t for t in parse_type_list(sm0, "InfantryTypes")
                 if get_val(sm0, t, "Cost", "").isdigit() and int(get_val(sm0, t, "Cost")) > 1)
    van_cost = int(get_val(sm0, probe, "Cost"))

    # A form tab compiles to a script and is gathered by collect_scripts.
    gen = win.form_tabs["general"]
    gen.section_edit.setText("General")
    gen.add_row("BuildSpeed", "0.9", used=True)
    scripts = win.collect_scripts()
    form_ok = any("[General]" in s and "BuildSpeed" in s for s in scripts)
    print(f"form tab -> script: {form_ok}  {'OK' if form_ok else 'FAIL'}")
    ok = ok and form_ok

    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "rulesmd_edited.ini")
        summary, script_results = controller.apply_and_save(
            path, out, win.collect_spec(), scripts=win.collect_scripts(),
            config=win.config)
        wrote = os.path.isfile(out)
        lines2 = parse_ini_lines(out)
        sm2, _ = build_section_map(lines2)
        new_cost = get_current_field_value_from_line(
            lines2[sm2[probe.lower()]["keys"]["cost"][0]])
        apply_ok = wrote and new_cost == van_cost // 2
        print(f"apply+save: wrote={wrote}, [{probe}] cost {van_cost}->{new_cost} "
              f"(x0.5)  {'OK' if apply_ok else 'FAIL'}")
        ok = ok and apply_ok

    # ── Script preview through the controller ────────────────────────────────
    win.script_panel.editor.setPlainText("[$INFANTRY]\nElite = yes\n")
    pok, msg = controller.preview_script(win.section_map, win.script_panel.text(), win.config)
    prev_ok = pok and "technos" in msg
    print(f"script preview: ok={pok}  {'OK' if prev_ok else 'FAIL'}")
    ok = ok and prev_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_ui.py /path/to/rulesmd.ini")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
