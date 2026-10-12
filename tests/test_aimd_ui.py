#!/usr/bin/env python3
"""
AI Builder smoke test (offscreen Qt): point it at a mod with an aimd.ini, confirm the
four registries fill, a trigger decodes in the detail pane, cross-references list,
jump-to-ref switches tabs, and live search filters.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_aimd_ui.py
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

AIMD = """\
[TaskForces]
0=TF1
[ScriptTypes]
0=SC1
[TeamTypes]
0=TM1
[AITriggerTypes]
TR1=Guard Base,TM1,Americans,7,0,GATECH,0100,70.0,10.0,70.0,1,0,1,0,TM1,0,1,1

[TF1]
Name=guard force
0=2,GGI
[SC1]
Name=the script
0=5,3
[TM1]
Name=guard team
TaskForce=TF1
Script=SC1
House=Americans
"""


def main():
    ok = True
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtCore import Qt
    app = QApplication.instance() or QApplication([])

    d = tempfile.mkdtemp()
    mod = os.path.join(d, "mod")
    os.makedirs(mod)
    rules = os.path.join(mod, "rulesmd.ini")
    open(rules, "w").write("[GI]\n")
    open(os.path.join(mod, "aimd.ini"), "w").write(AIMD)

    from yrbalance.ui.aimd_tab import AiBuilderTab
    from yrbalance.ui.ai_tab import AiTab
    mw = QWidget()
    mw.source_path = rules
    mw.section_map = None

    # container: three sub-tabs, builder reachable as .builder
    container = AiTab(mw)
    cont_ok = ([container.sub.tabText(i) for i in range(container.sub.count())]
               == ["AI Builder", "AI Library", "Unit Roles"]
               and isinstance(container.builder, AiBuilderTab))
    print(f"AiTab sub-tabs (Builder/Library/Roles): {'OK' if cont_ok else 'FAIL'}")
    ok = ok and cont_ok

    tab = AiBuilderTab(mw)
    tab.show()                 # fire showEvent → load
    app.processEvents()

    counts = {k: tab._lists[k].count() for k in tab._lists}
    fill_ok = (counts["TaskForces"] == 1 and counts["ScriptTypes"] == 1
               and counts["TeamTypes"] == 1 and counts["AITriggerTypes"] == 1)
    print(f"registries fill: {'OK' if fill_ok else 'FAIL'}  {counts}")
    ok = ok and fill_ok

    # select the trigger → decoded detail
    tidx = [k for k, _ in __import__("yrbalance.ui.aimd_tab",
            fromlist=["_REG_TABS"])._REG_TABS].index("AITriggerTypes")
    tab.reg_tabs.setCurrentIndex(tidx)
    tab._lists["AITriggerTypes"].setCurrentRow(0)
    det = tab.detail.toPlainText()
    dec_ok = "Guard Base" in det and "Primary team" in det and "TM1" in det
    print(f"trigger decoded in detail: {'OK' if dec_ok else 'FAIL'}")
    ok = ok and dec_ok

    # referenced-by: the trigger refs TM1 (primary + secondary) → refs list has TM1
    # select TM1 and check its refs include the trigger
    tmidx = [k for k, _ in __import__("yrbalance.ui.aimd_tab",
             fromlist=["_REG_TABS"])._REG_TABS].index("TeamTypes")
    tab.reg_tabs.setCurrentIndex(tmidx)
    tab._lists["TeamTypes"].setCurrentRow(0)
    refs = [tab.refs.item(r).text() for r in range(tab.refs.count())]
    refs_ok = any("TR1" in t for t in refs)
    print(f"cross-refs (team ← trigger): {'OK' if refs_ok else 'FAIL'}  {refs}")
    ok = ok and refs_ok

    # double-click the ref jumps to the trigger tab + selects it
    tab._jump_ref(tab.refs.item(0))
    jumped = (tab.reg_tabs.currentIndex() == tidx
              and tab._lists["AITriggerTypes"].currentItem() is not None)
    print(f"jump-to-ref switches tab + selects: {'OK' if jumped else 'FAIL'}")
    ok = ok and jumped

    # live search filters the TeamTypes list
    tab.search.setText("zzz-nomatch")
    hidden = tab._lists["TeamTypes"].isRowHidden(0)
    tab.search.clear()
    print(f"live search hides non-matches: {'OK' if hidden else 'FAIL'}")
    ok = ok and hidden

    # edit mode: change a trigger's owner + save → persisted to the file
    tab.reg_tabs.setCurrentIndex(tidx)
    tab._lists["AITriggerTypes"].setCurrentRow(0)
    tab.edit_chk.setChecked(True)                 # editable raw def shown
    tab.detail.setPlainText(
        "Guard Base,TM1,Russians,9,0,GATECH,0100,70.0,10.0,70.0,1,0,1,0,TM1,0,1,1")
    tab._save_entry()
    from yrbalance.engine import (parse_ini_lines as _pil,
                                  build_section_map as _bsm, trigger_fields as _tf)
    saved = _bsm(_pil(os.path.join(mod, "aimd.ini")))[0]
    trig_edit_ok = _tf(saved, "TR1")[2] == "Russians"
    print(f"edit trigger owner + save persists: "
          f"{'OK' if trig_edit_ok else 'FAIL'}")
    ok = ok and trig_edit_ok

    # edit mode: change a teamtype section + save
    tab.reg_tabs.setCurrentIndex(tmidx)
    tab._lists["TeamTypes"].setCurrentRow(0)
    tab.edit_chk.setChecked(True)
    tab.detail.setPlainText("[TM1]\nName=guard team\nTaskForce=TF1\nScript=SC1\n"
                            "House=Russians\nMax=9")
    tab._save_entry()
    from yrbalance.engine import get_val as _gv
    saved2 = _bsm(_pil(os.path.join(mod, "aimd.ini")))[0]
    sec_edit_ok = (_gv(saved2, "TM1", "House") == "Russians"
                   and _gv(saved2, "TM1", "Max") == "9")
    print(f"edit teamtype section + save persists: "
          f"{'OK' if sec_edit_ok else 'FAIL'}")
    ok = ok and sec_edit_ok

    # ── AI Library collection surface (P2) ────────────────────────────────────
    import tempfile as _tf
    home = _tf.mkdtemp()
    os.environ["HOME"] = home          # isolate the ailibrary.json sidecar
    from yrbalance.engine import (
        parse_ini_lines as _pil2, build_section_map as _bsm2, extract_block,
        add_entry, ai_create_collection, ai_add_to_collection, set_bookmark,
    )
    srcmap = _bsm2(_pil2(os.path.join(mod, "aimd.ini")))[0]
    add_entry(extract_block(srcmap, "taskforce", "TF1", source="turkey", name="alpha"))
    add_entry(extract_block(srcmap, "script", "SC1", source="turkey", name="beta"))
    set_bookmark("beta", "script", True)
    ai_create_collection("faves")
    ai_add_to_collection("faves", ("alpha", "taskforce"))

    from yrbalance.ui.ai_tab import AiLibraryPanel
    panel = AiLibraryPanel(mw)
    panel.reload()
    app.processEvents()

    # collections column: All / Bookmarked / faves
    coltexts = [panel.col_list.item(r).text() for r in range(panel.col_list.count())]
    lib_cols_ok = (any("All entries" in t for t in coltexts)
                   and any("Bookmarked" in t for t in coltexts)
                   and any("faves" in t for t in coltexts))
    print(f"AI library collections column: {'OK' if lib_cols_ok else 'FAIL'}  {coltexts}")
    ok = ok and lib_cols_ok

    # All view lists both entries; bookmark star on beta
    panel.col_list.setCurrentRow(0)
    app.processEvents()
    ent_texts = [panel.entry_list.item(r).text()
                 for r in range(panel.entry_list.count())]
    all_ok = (len(ent_texts) == 2 and any("alpha" in t for t in ent_texts)
              and any("★" in t and "beta" in t for t in ent_texts))
    print(f"AI library All view (2 entries, star on beta): "
          f"{'OK' if all_ok else 'FAIL'}  {ent_texts}")
    ok = ok and all_ok

    # Bookmarked view → only beta
    for r in range(panel.col_list.count()):
        if "Bookmarked" in panel.col_list.item(r).text():
            panel.col_list.setCurrentRow(r)
            break
    app.processEvents()
    bm_only = [panel.entry_list.item(r).text()
               for r in range(panel.entry_list.count())]
    bm_ok = len(bm_only) == 1 and "beta" in bm_only[0]
    print(f"AI library Bookmarked view filters: {'OK' if bm_ok else 'FAIL'}  {bm_only}")
    ok = ok and bm_ok

    # faves collection → only alpha
    for r in range(panel.col_list.count()):
        if "faves" in panel.col_list.item(r).text():
            panel.col_list.setCurrentRow(r)
            break
    app.processEvents()
    fav = [panel.entry_list.item(r).text()
           for r in range(panel.entry_list.count())]
    fav_ok = len(fav) == 1 and "alpha" in fav[0]
    print(f"AI library collection view filters: {'OK' if fav_ok else 'FAIL'}  {fav}")
    ok = ok and fav_ok

    # search narrows the All view
    panel.col_list.setCurrentRow(0)
    panel.search.setText("alpha")
    app.processEvents()
    searched = [panel.entry_list.item(r).text()
                for r in range(panel.entry_list.count())]
    search_ok = len(searched) == 1 and "alpha" in searched[0]
    panel.search.clear()
    print(f"AI library search narrows: {'OK' if search_ok else 'FAIL'}  {searched}")
    ok = ok and search_ok

    # ── AI Builder working file (P3) ───────────────────────────────────────────
    # (HOME is already isolated above for the AI library sidecar.)
    builder = AiBuilderTab(mw)
    builder.show()
    app.processEvents()

    # New blank → working mode on, empty registries, edit buttons enabled
    builder._work_new_blank()
    app.processEvents()
    blank_ok = (builder._working
                and builder._lists["AITriggerTypes"].count() == 0
                and builder.work_close_b.isEnabled())
    print(f"Builder new-blank working file: {'OK' if blank_ok else 'FAIL'}")
    ok = ok and blank_ok

    # Pull a library entry into the working file via the engine (the _PullDialog
    # apply is covered in test_aiwork; here we just verify the Builder reflects the
    # working file's new content after a pull). alpha = TF1 saved earlier.
    from yrbalance.engine import (
        load_library, working_path, parse_ini_lines, build_section_map,
        build_pull_plan, apply_pull,
    )
    entry = next(e for e in load_library() if e.name == "alpha")
    wl = parse_ini_lines(working_path())
    wmap = build_section_map(wl)[0]
    plan = build_pull_plan(entry, wmap, target_countries=["Americans"])
    apply_pull(wl, wmap, entry, plan)
    with open(working_path(), "w", encoding="latin-1", newline="") as fh:
        fh.write("".join(wl))
    builder._reload()
    app.processEvents()
    pulled_ok = builder._lists["TaskForces"].count() >= 1
    print(f"Builder reflects working-file pull: "
          f"{'OK' if pulled_ok else 'FAIL'}  "
          f"TF={builder._lists['TaskForces'].count()}")
    ok = ok and pulled_ok

    # Export writes a byte copy and does NOT touch the original mod aimd
    import tempfile as _tf2
    dest = os.path.join(_tf2.mkdtemp(), "exported_aimd.ini")
    from yrbalance.engine import export_working
    export_working(dest)
    exp_ok = (os.path.isfile(dest)
              and open(dest, encoding="latin-1").read()
              == open(working_path(), encoding="latin-1").read())
    print(f"Builder export working file: {'OK' if exp_ok else 'FAIL'}")
    ok = ok and exp_ok

    # Close working file → back to mod mode
    builder._work_close()
    close_ok = (not builder._working and not builder.work_close_b.isEnabled())
    print(f"Builder close working file: {'OK' if close_ok else 'FAIL'}")
    ok = ok and close_ok

    # ── Unit Roles panel (P4) ──────────────────────────────────────────────────
    from yrbalance.ui.ai_tab import UnitRolesPanel
    from yrbalance.engine import build_section_map as _bsm3, parse_ini_lines as _pil3
    # give mw a section_map with a couple of units so the picker fills
    mw.section_map = _bsm3(_pil3(os.path.join(mod, "rulesmd.ini")))[0] \
        if os.path.getsize(os.path.join(mod, "rulesmd.ini")) else None
    # rulesmd in this fixture only had [GI]; add vehicle types for the picker
    rpath = os.path.join(mod, "rulesmd.ini")
    open(rpath, "w").write("[GI]\n[VehicleTypes]\n0=TNK\n1=APOC\n")
    mw.section_map = _bsm3(_pil3(rpath))[0]

    roles = UnitRolesPanel(mw)
    roles.show()
    app.processEvents()
    # default role names present
    rnames = [roles.role_list.item(r).text().rsplit("  (", 1)[0]
              for r in range(roles.role_list.count())]
    roles_seed_ok = "Rush" in rnames and "Anti-Vehicle" in rnames
    print(f"Unit Roles seeded defaults: {'OK' if roles_seed_ok else 'FAIL'}")
    ok = ok and roles_seed_ok

    # picker filled from rules; check two units + save, reload keeps them
    for r in range(roles.role_list.count()):
        if roles.role_list.item(r).text().startswith("Rush"):
            roles.role_list.setCurrentRow(r)
            break
    app.processEvents()
    picker = [roles.unit_list.item(r).data(Qt.UserRole)
              for r in range(roles.unit_list.count())]
    for r in range(roles.unit_list.count()):
        if roles.unit_list.item(r).data(Qt.UserRole) in ("TNK", "APOC"):
            roles.unit_list.item(r).setCheckState(Qt.Checked)
    roles._save_role()
    app.processEvents()
    from yrbalance.engine import load_roles as _lr
    saved = _lr()["Rush"]
    picker_ok = ("TNK" in picker and set(saved) == {"TNK", "APOC"})
    print(f"Unit Roles picker + save: {'OK' if picker_ok else 'FAIL'}  saved={saved}")
    ok = ok and picker_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
