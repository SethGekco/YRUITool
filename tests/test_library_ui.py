#!/usr/bin/env python3
"""
LibraryTab smoke test (offscreen Qt): pre-populate a source, build the tab, check
the kind sub-tabs fill + aggregate the AI trigger, live search shrinks a list,
selecting a mod folder shows its balance culture, and add-to-collection works.
Isolated HOME.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_library_ui.py
"""

import os
import sys
import tempfile
import importlib.util

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

# reuse the engine test's fixtures
_spec = importlib.util.spec_from_file_location(
    "tl", os.path.join(HERE, "test_library.py"))
tl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tl)


def main():
    ok = True
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home
        from PySide6.QtWidgets import (QApplication, QWidget, QMessageBox,
                                       QInputDialog)
        app = QApplication.instance() or QApplication([])
        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

        from yrbalance.engine import add_source, create_collection, load_catalog

        van = _w(home, "vanilla.ini", tl.VANILLA)
        mod_dir = os.path.join(home, "turkey-mod")
        os.makedirs(mod_dir)
        rules = _w(mod_dir, "rulesmd.ini", tl.MOD)
        _w(mod_dir, "aimd.ini", tl.MOD_AIMD)
        add_source(rules, label="turkey-mod", mode="reference",
                   added_at="2026-01-01", vanilla_path=van)
        create_collection("favs")

        # Add-source dialog resolves a pasted FOLDER (incl. hidden path) to its
        # rules file, and reports bad paths
        from yrbalance.ui.library_tab import _AddSourceDialog
        hidden = os.path.join(home, ".wine", "drive_c", "m2")
        os.makedirs(hidden)
        _w(hidden, "rulesmd.ini", "[GI]\n")
        asd = _AddSourceDialog(None, "")
        fp, e1 = asd._resolve(hidden)                 # paste folder
        fp2, _ = asd._resolve(os.path.join(hidden, "rulesmd.ini"))   # paste file
        _, ebad = asd._resolve("/no/such/dir")
        add_dlg_ok = (fp.endswith("rulesmd.ini") and not e1
                      and fp2.endswith("rulesmd.ini") and ebad)
        print(f"add-source resolves folder/file/hidden/bad: "
              f"{'OK' if add_dlg_ok else 'FAIL'}")
        ok = ok and add_dlg_ok

        from yrbalance.ui.library_tab import LibraryTab
        mw = QWidget()
        mw.source_path = rules
        tab = LibraryTab(mw)
        tab.show()                 # fire showEvent → lazy first-load
        app.processEvents()

        # collection list: All sources + auto mod folder + user collection
        col_texts = [tab.col_list.item(i).text()
                     for i in range(tab.col_list.count())]
        col_ok = (any("All sources" in t for t in col_texts)
                  and any("turkey-mod" in t for t in col_texts)
                  and any("favs" in t for t in col_texts))
        print(f"collections listed (All + folder + user): "
              f"{'OK' if col_ok else 'FAIL'}  {col_texts}")
        ok = ok and col_ok

        # kind tabs populated: a vehicle NEWTANK and the AI trigger TR1
        veh_tbl, veh_items = tab._tables["vehicle"]
        ai_tbl, ai_items = tab._tables["ai"]
        kind_ok = (any(i.name.lower() == "newtank" for i in veh_items)
                   and any(i.name.lower() == "tr1" for i in ai_items))
        print(f"kind tabs aggregate techno + AI: {'OK' if kind_ok else 'FAIL'}")
        ok = ok and kind_ok

        # live search shrinks the vehicle list
        tab.ktabs.setCurrentIndex(0)        # Vehicles
        tab.search.setText("newtank")
        vis = sum(1 for r in range(veh_tbl.rowCount())
                  if not veh_tbl.isRowHidden(r))
        search_ok = vis == 1 and "(1)" in tab.ktabs.tabText(0)
        print(f"live search shrinks list to NEWTANK: "
              f"{'OK' if search_ok else 'FAIL'}  visible={vis}")
        ok = ok and search_ok
        tab.search.clear()

        # select the mod folder → balance culture shown
        folder_row = next(i for i in range(tab.col_list.count())
                          if "turkey-mod" in tab.col_list.item(i).text())
        tab.col_list.setCurrentRow(folder_row)
        bal = tab.balance_text.toPlainText()
        bal_ok = "cheaper" in bal and "vehicle" in bal and "2.0" in bal
        print(f"balance culture shown for folder: {'OK' if bal_ok else 'FAIL'}")
        ok = ok and bal_ok

        # clicking a techno item fills the detail pane (rules preview + files)
        tab.ktabs.setCurrentIndex(0)
        nt0 = next(r for r, it in enumerate(veh_items)
                   if it.name.lower() == "newtank")
        veh_tbl.setCurrentCell(nt0, 0)
        det = tab.detail_text.toPlainText()
        det_ok = "NEWTANK" in det and "Strength" in det
        print(f"detail pane: techno rules preview: {'OK' if det_ok else 'FAIL'}")
        ok = ok and det_ok

        # clicking the AI trigger shows its closure
        ai_idx = [k for k, _ in __import__(
            "yrbalance.ui.library_tab", fromlist=["_KIND_ORDER"]
        )._KIND_ORDER].index("ai")
        tab.ktabs.setCurrentIndex(ai_idx)
        ai_tbl2, ai_items2 = tab._tables["ai"]
        tr_row = next(r for r, it in enumerate(ai_items2)
                      if it.name.lower() == "tr1")
        ai_tbl2.setCurrentCell(tr_row, 0)
        aidet = tab.detail_text.toPlainText()
        ai_det_ok = "Big Push" in aidet and "push team" in aidet
        print(f"detail pane: AI closure shown: {'OK' if ai_det_ok else 'FAIL'}")
        ok = ok and ai_det_ok

        # filter checkbox: NEWTANK is buildable? it has no Owner/prereq so it's
        # likely 'other'/unknown — hide non-buildable should drop non-buildables.
        # Use the dummy/civilian/no-art paths deterministically instead:
        tab.col_list.setCurrentRow(0)          # All sources
        tab.ktabs.setCurrentIndex(0)           # Vehicles
        veh_tbl, veh_items = tab._tables["vehicle"]
        base_vis = sum(1 for r in range(veh_tbl.rowCount())
                       if not veh_tbl.isRowHidden(r))
        tab.hide_noart.setChecked(True)        # fixture units have no art → all hidden
        noart_vis = sum(1 for r in range(veh_tbl.rowCount())
                        if not veh_tbl.isRowHidden(r))
        tab.hide_noart.setChecked(False)
        filt_ok = base_vis > 0 and noart_vis == 0
        print(f"filter 'no art files' hides art-less units: "
              f"{'OK' if filt_ok else 'FAIL'}  {base_vis}->{noart_vis}")
        ok = ok and filt_ok

        # AI items are exempt from techno filters (stay visible)
        ai_i = [k for k, _ in __import__(
            "yrbalance.ui.library_tab", fromlist=["_KIND_ORDER"]
        )._KIND_ORDER].index("ai")
        ai_tbl3, _ = tab._tables["ai"]
        tab.hide_nonbuild.setChecked(True)
        ai_vis = sum(1 for r in range(ai_tbl3.rowCount())
                     if not ai_tbl3.isRowHidden(r))
        tab.hide_nonbuild.setChecked(False)
        ai_exempt_ok = ai_vis >= 1
        print(f"AI exempt from 'hide non-buildable': "
              f"{'OK' if ai_exempt_ok else 'FAIL'}")
        ok = ok and ai_exempt_ok

        # add selected vehicle to 'favs' (mock the collection picker)
        QInputDialog.getItem = staticmethod(lambda *a, **k: ("favs", True))
        tab.ktabs.setCurrentIndex(0)
        nt_row = next(r for r, it in enumerate(veh_items)
                      if it.name.lower() == "newtank")
        veh_tbl.setCurrentCell(nt_row, 0)
        tab._add_selected_to_collection()
        _s, cols = load_catalog()
        favs = next(c for c in cols if c.name == "favs")
        addcol_ok = any(k[2].lower() == "newtank" for k in favs.items)
        print(f"add selected → collection: {'OK' if addcol_ok else 'FAIL'}")
        ok = ok and addcol_ok

        # selecting the 'favs' collection filters to just that item
        favs_row = next(i for i in range(tab.col_list.count())
                        if "favs" in tab.col_list.item(i).text())
        tab.col_list.setCurrentRow(favs_row)
        veh_tbl, veh_items = tab._tables["vehicle"]
        vis2 = sum(1 for r in range(veh_tbl.rowCount())
                   if not veh_tbl.isRowHidden(r))
        filt_ok = vis2 == 1
        print(f"collection filter narrows to its items: "
              f"{'OK' if filt_ok else 'FAIL'}  visible={vis2}")
        ok = ok and filt_ok

        # real SHP render path (uses a game SHP if present on this machine)
        import glob
        import types
        import shutil
        from yrbalance.engine import build_media_catalog, AssetRef
        cand = glob.glob("/home/rex/snap/cncra2yr/common/.wine/drive_c/"
                         "Westwood/RA2/button14.shp")
        if cand:
            shutil.copy(cand[0], os.path.join(mod_dir, "prevart.shp"))
            cat = build_media_catalog([mod_dir])
            fake_scan = types.SimpleNamespace(
                index=types.SimpleNamespace(catalog=cat), aimd_map=None)
            entry = types.SimpleNamespace(
                assets=[AssetRef("PREVART.SHP", "loose", "present")])
            tab._render_art(entry, "turkey-mod", fake_scan)
            pm = tab.art_img.pixmap()
            render_ok = pm is not None and not pm.isNull() and pm.width() > 0
            print(f"real SHP → pixmap render: {'OK' if render_ok else 'FAIL'}")
            ok = ok and render_ok
        else:
            print("real SHP render: SKIP (no sample on this machine)")

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def _w(d, name, text):
    p = os.path.join(d, name)
    open(p, "w").write(text)
    return p


if __name__ == "__main__":
    sys.exit(main())
