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

    # ── Defensive links to Building (like Naval to Vehicle) ──────────────────
    win.category_tabs["bld"].spins["strength"].setValue(1.5)
    def_r = win.collect_spec().categories["def"]
    def_link_ok = def_r.strength == 1.5   # linked by default
    print(f"defensive links to building: strength={def_r.strength} (->1.5)  "
          f"{'OK' if def_link_ok else 'FAIL'}")
    ok = ok and def_link_ok

    # ── Sight no-weapon + soylent-fraction controls flow into the spec ───────
    it = win.category_tabs["inf"]
    it.sight_nw_mode.setCurrentIndex(it.sight_nw_mode.findData("minimum"))
    it.sight_nw_value.setValue(9.0)
    it.soylent_cost_frac.setChecked(True)
    ir = win.collect_spec().categories["inf"]
    nw_ok = (ir.sight_no_weapon_mode == "minimum" and ir.sight_no_weapon_value == 9.0
             and ir.soylent_mode == "cost_fraction")
    print(f"sight-nw + soylent-fraction -> spec: {nw_ok}  {'OK' if nw_ok else 'FAIL'}")
    ok = ok and nw_ok
    it.soylent_cost_frac.setChecked(False)
    it.sight_nw_mode.setCurrentIndex(it.sight_nw_mode.findData("leave"))

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

    # ── Change preview (diff) ────────────────────────────────────────────────
    summary_p, results_p, diff = controller.preview_changes(
        path, win.collect_spec(), win.collect_scripts(), win.config)
    diff_ok = "-Cost=" in diff and "+Cost=" in diff and "Elite" in diff
    print(f"change preview diff: has cost+elite changes={diff_ok}  "
          f"{'OK' if diff_ok else 'FAIL'}")
    ok = ok and diff_ok

    # ── Global tab feeds range_threshold into the spec ───────────────────────
    win.global_tab.range_protect.setChecked(True)
    win.global_tab.range_threshold.setValue(3.5)
    gt_ok = win.collect_spec().range_threshold == 3.5
    win.global_tab.range_protect.setChecked(False)
    gt_off = win.collect_spec().range_threshold is None
    print(f"global range threshold: on=3.5 ({gt_ok}), off=None ({gt_off})  "
          f"{'OK' if gt_ok and gt_off else 'FAIL'}")
    ok = ok and gt_ok and gt_off
    win.global_tab.range_protect.setChecked(True)
    win.global_tab.range_threshold.setValue(3.0)

    # ── Blueprint round-trip ─────────────────────────────────────────────────
    # Current state: inf.cost=0.5, nav.strength unlinked=3.0, bld tl_recompute,
    # general has BuildSpeed row, script box has "Elite = yes".
    win.global_tab.range_threshold.setValue(4.25)
    text = win.to_blueprint_text(name="Test")
    win2 = MainWindow()
    win2.apply_blueprint_text(text)
    s2 = win2.collect_spec()
    n2 = s2.categories["nav"]
    bp_ok = (s2.categories["inf"].cost == 0.5
             and n2.strength == 3.0                       # unlinked override survived
             and s2.categories["bld"].tl_recompute
             and any("BuildSpeed" in s for s in win2.collect_scripts())
             and "Elite" in win2.script_panel.text()
             and win2.global_tab.range_threshold.value() == 4.25)
    print(f"blueprint round-trip: {bp_ok}  {'OK' if bp_ok else 'FAIL'}")
    ok = ok and bp_ok

    # ── Hybrid working-copy session ──────────────────────────────────────────
    from yrbalance.engine import parse_type_list as _ptl
    win3 = MainWindow()
    win3.source_path = path
    _lines, win3.section_map = controller.load_rules(path)
    # Capture a vanilla infantry cost.
    smw = win3.section_map
    probe2 = next(t for t in _ptl(smw, "InfantryTypes")
                  if get_val(smw, t, "Cost", "").isdigit() and int(get_val(smw, t, "Cost")) > 1)
    van2 = int(get_val(smw, probe2, "Cost"))

    # Apply infantry-only (cost x0.5) to the working copy; twice (idempotent).
    win3.category_tabs["inf"].spins["cost"].setValue(0.5)
    win3.tabs.setCurrentIndex(0)  # Infantry
    win3.apply_working_current()
    c_after_1 = _cur(win3.session.lines, probe2, "cost")
    win3.apply_working_current()   # re-apply must NOT compound
    c_after_2 = _cur(win3.session.lines, probe2, "cost")
    idem_ok = c_after_1 == van2 // 2 and c_after_2 == van2 // 2
    print(f"working copy idempotent: {van2}->{c_after_1}->{c_after_2} "
          f"(expect {van2//2} both)  {'OK' if idem_ok else 'FAIL'}")
    ok = ok and idem_ok

    # Save the working copy, then discard.
    with tempfile.TemporaryDirectory() as d:
        out2 = os.path.join(d, "working.ini")
        win3.session.save(out2)
        saved_ok = os.path.isfile(out2) and not win3.session.dirty
    win3.discard_working()
    disc_ok = win3.session is None
    print(f"working copy save+discard: saved={saved_ok}, discarded={disc_ok}  "
          f"{'OK' if saved_ok and disc_ok else 'FAIL'}")
    ok = ok and saved_ok and disc_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def _cur(lines, section, tag):
    from yrbalance.engine import build_section_map, get_current_field_value_from_line
    sm, _ = build_section_map(lines)
    return get_current_field_value_from_line(lines[sm[section.lower()]["keys"][tag.lower()][0]])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_ui.py /path/to/rulesmd.ini")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
