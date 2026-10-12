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

        # convert dialog: the converted rules section reflects rescaled stats
        from yrbalance.ui.library_tab import _ConvertDialog
        veh_items2 = tab._tables["vehicle"][1]
        nt = next(i for i in veh_items2 if i.name.lower() == "newtank")
        rmap = tab._scan_by_source[nt.source_id].index.rules_map
        cd = _ConvertDialog(tab, nt, "turkey-mod", "target", {}, {},
                            [("NEWTANK", [("Cost", 2000, 1000, 0.5)])], rmap)
        sec = cd._converted_text()
        conv_dlg_ok = "[NEWTANK]" in sec and "Cost=1000" in sec
        print(f"convert dialog converted section: "
              f"{'OK' if conv_dlg_ok else 'FAIL'}")
        ok = ok and conv_dlg_ok

        # cross-mod tags: set tags on NEWTANK, persist + appear in the filter
        from yrbalance.engine import load_tags
        tab.ktabs.setCurrentIndex(0)
        veh_t, veh_i = tab._tables["vehicle"]
        nt_r = next(r for r, i in enumerate(veh_i) if i.name.lower() == "newtank")
        veh_t.setCurrentCell(nt_r, 0)
        tab.tags_edit.setText("favourite, op")
        tab._set_item_tags()
        stored = load_tags(rules)
        tag_ok = ("favourite" in stored.get("newtank", [])
                  and tab.tag_combo.findData("op") >= 0)
        print(f"tag set + persisted + in combo: {'OK' if tag_ok else 'FAIL'}")
        ok = ok and tag_ok

        # filter by the 'favourite' tag → only NEWTANK visible in Vehicles
        tab.tag_combo.setCurrentIndex(tab.tag_combo.findData("favourite"))
        tvis = sum(1 for r in range(veh_t.rowCount())
                   if not veh_t.isRowHidden(r))
        tfilt_ok = tvis == 1
        print(f"tag filter narrows to tagged item: "
              f"{'OK' if tfilt_ok else 'FAIL'}  visible={tvis}")
        ok = ok and tfilt_ok
        tab.tag_combo.setCurrentIndex(0)

        # Source display shows the last two path components (disambiguates …/RA2)
        sid0 = veh_i[0].source_id
        disp = tab._source_display(sid0)
        src_disp_ok = "/" in disp and "turkey-mod" in disp
        print(f"source display shows path components: "
              f"{'OK' if src_disp_ok else 'FAIL'}  '{disp}'")
        ok = ok and src_disp_ok

        # double-click rename of the mod folder → custom label shown
        QInputDialog.getText = staticmethod(lambda *a, **k: ("Turkey Mod", True))
        frow = next(i for i in range(tab.col_list.count())
                    if "turkey" in tab.col_list.item(i).text().lower())
        tab._rename_collection_row(tab.col_list.item(frow))
        renamed_shown = any("Turkey Mod" in tab.col_list.item(i).text()
                            for i in range(tab.col_list.count()))
        ren_ok = renamed_shown and tab._source_display(sid0) == "Turkey Mod"
        print(f"rename mod folder → custom label: {'OK' if ren_ok else 'FAIL'}")
        ok = ok and ren_ok

        # open-in-porter guards cleanly when no target rules are loaded
        tab.ktabs.setCurrentIndex(0)
        veh_t2, _vi = tab._tables["vehicle"]
        veh_t2.setCurrentCell(0, 0)
        mw.section_map = None                 # no target loaded in main window
        try:
            tab._open_in_porter()             # should hit the guard, not crash
            porter_guard_ok = True
        except Exception:
            porter_guard_ok = False
        print(f"open-in-porter guard (no target): "
              f"{'OK' if porter_guard_ok else 'FAIL'}")
        ok = ok and porter_guard_ok

        # open-in-encyclopedia: loads the item's mod; AI items are guarded
        import yrbalance.ui.encyclopedia_dialog as _ed
        from PySide6.QtWidgets import QLineEdit, QTableWidget
        ency_cap = {}

        class _StubEncy:
            def __init__(self, mw):
                self.search_edit = QLineEdit()
                self.table = QTableWidget()
                self._worker = None
                self._results = []
                self.indexes = []

            def _load_index(self, p):
                ency_cap["loaded"] = p

            def _show_detail(self):
                pass

            def show(self):
                pass
        _ed.EncyclopediaDialog = _StubEncy

        tab.ktabs.setCurrentIndex(0)
        veh_te, _vie = tab._tables["vehicle"]
        veh_te.setCurrentCell(0, 0)
        tab._open_in_encyclopedia()
        ency_ok = ency_cap.get("loaded", "").endswith("rulesmd.ini")
        print(f"open-in-encyclopedia loads source: {'OK' if ency_ok else 'FAIL'}")
        ok = ok and ency_ok

        ai_i2 = [k for k, _ in __import__(
            "yrbalance.ui.library_tab", fromlist=["_KIND_ORDER"]
        )._KIND_ORDER].index("ai")
        tab.ktabs.setCurrentIndex(ai_i2)
        ai_te, _aie = tab._tables["ai"]
        ai_te.setCurrentCell(0, 0)
        ency_cap.clear()
        tab.status.setText("")
        tab._open_in_encyclopedia()
        ai_guard_ok = "loaded" not in ency_cap and "AI triggers" in tab.status.text()
        print(f"encyclopedia AI-item guard: {'OK' if ai_guard_ok else 'FAIL'}")
        ok = ok and ai_guard_ok

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

        # real VXL render path
        vcand = glob.glob("/home/rex/snap/cncra2yr/common/.wine/drive_c/"
                          "Westwood/RA2/usaguntur.vxl")
        if vcand:
            shutil.copy(vcand[0], os.path.join(mod_dir, "prevvxl.vxl"))
            vcat = build_media_catalog([mod_dir])
            vscan = types.SimpleNamespace(
                index=types.SimpleNamespace(catalog=vcat), aimd_map=None)
            ventry = types.SimpleNamespace(
                assets=[AssetRef("PREVVXL.VXL", "loose", "present")])
            tab._render_art(ventry, "turkey-mod", vscan)
            vpm = tab.art_img.pixmap()
            vxl_ok = vpm is not None and not vpm.isNull() and vpm.width() > 0
            print(f"real VXL → pixmap render: {'OK' if vxl_ok else 'FAIL'}")
            ok = ok and vxl_ok
        else:
            print("real VXL render: SKIP (no sample on this machine)")

        # audio: populate the combo + export WAV using a real audio.bag if present
        import types as _t
        from yrbalance.engine import AssetRef

        # Synthetic (always runs): a bag-located sound gets status 'present' in
        # real data (build_mod_index), which the old populate wrongly skipped.
        syn_cat = {"TESTSND": ("bag", "/x/audio.bag")}
        syn_scan = _t.SimpleNamespace(
            index=_t.SimpleNamespace(catalog=syn_cat), aimd_map=None)
        syn_entry = _t.SimpleNamespace(
            assets=[AssetRef("TESTSND", "bag: audio.bag", "present")])
        tab._populate_audio(syn_entry, syn_scan)
        syn_ok = tab.audio_combo.count() == 1 and tab.export_btn.isEnabled()
        print(f"audio combo from 'present' bag sound: "
              f"{'OK' if syn_ok else 'FAIL'}")
        ok = ok and syn_ok

        # cameo palette: SHP cameos must use cameo.pal, not the unit palette.
        import tempfile as _tf
        cam_dir = _tf.mkdtemp()
        # two distinct 6-bit palettes (colour 1 differs)
        unit_pal = bytearray(768); unit_pal[3:6] = bytes((10, 0, 0))
        cam_pal = bytearray(768);  cam_pal[3:6] = bytes((0, 0, 40))
        open(os.path.join(cam_dir, "unittem.pal"), "wb").write(unit_pal)
        open(os.path.join(cam_dir, "cameo.pal"), "wb").write(cam_pal)
        fake_src = _t.SimpleNamespace(
            id="camsid", rules_path=os.path.join(cam_dir, "rulesmd.ini"))
        tab.sources = [fake_src]
        tab._pal_cache = {}
        cam = tab._cameo_palette("camsid", None)
        unit = tab._preview_palette("camsid", None)
        cam_ok = cam[1] == (0, 0, 40 << 2 | 40 >> 4) and cam[1] != unit[1]
        print(f"cameo palette uses cameo.pal (not unit): "
              f"{'OK' if cam_ok else 'FAIL'}")
        ok = ok and cam_ok

        real_bag = ("/mnt/wwn-0x50014ee6b383afb8-part1/(3) Games Data/RA2 & "
                    "Yuri's Revenge/0 World Powers/000-RA2YR Data - cncnet "
                    "edition/audio.idx")
        if os.path.isfile(real_bag):
            from yrbalance.engine import build_media_catalog, parse_audio_idx
            bagfolder = os.path.dirname(real_bag)
            cat = build_media_catalog([bagfolder])
            some = next(iter(parse_audio_idx(real_bag)))     # a real sample id
            fake_scan = _t.SimpleNamespace(
                index=_t.SimpleNamespace(catalog=cat), aimd_map=None)
            entry = _t.SimpleNamespace(
                assets=[AssetRef(some + ".WAV", "bag: audio.bag", "present")])
            tab._populate_audio(entry, fake_scan)
            combo_ok = tab.audio_combo.count() == 1 and tab.export_btn.isEnabled()
            print(f"audio combo populated from bag: "
                  f"{'OK' if combo_ok else 'FAIL'}")
            ok = ok and combo_ok
            # decode + WAV bytes are produced
            _name, wav = tab._current_wav()
            wav_ok = wav and wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
            print(f"bag sample → WAV bytes: {'OK' if wav_ok else 'FAIL'}")
            ok = ok and wav_ok
        else:
            print("audio bag check: SKIP (no game audio.bag on this machine)")

    # detail pane is 4 tabs: Preview / INI / Assets / Balance Culture
    import types as _t2
    dtabs = [tab.detail_tabs.tabText(i)
             for i in range(tab.detail_tabs.count())]
    tabs_ok = dtabs == ["Preview", "INI", "Assets", "Balance Culture"]
    print(f"detail tabs {dtabs}: {'OK' if tabs_ok else 'FAIL'}")
    ok = ok and tabs_ok

    # INI tab builder emits the raw rules section (+ art/sound) for an entry
    from yrbalance.engine import build_section_map, parse_ini_lines
    with tempfile.TemporaryDirectory() as d:
        rp = _w(d, "r.ini", "[GI]\nCost=100\nStrength=120\nPrimary=M60\n"
                            "[M60]\nDamage=25\n")
        ap = _w(d, "a.ini", "[GI]\nCameo=GICAMEO\nSequence=E1Seq\n")
        rmap = build_section_map(parse_ini_lines(rp))[0]
        amap = build_section_map(parse_ini_lines(ap))[0]
        idx = _t2.SimpleNamespace(rules_map=rmap, art_map=amap, sound_map={})
        scan = _t2.SimpleNamespace(index=idx)
        entry = _t2.SimpleNamespace(name="GI", art_name="GI", sound_names=[],
                                    weapons=[("Primary", "M60", "", "")])
        txt = tab._ini_text(entry, scan)
        ini_ok = ("[GI]" in txt and "Cost=100" in txt and "[M60]" in txt
                  and "Damage=25" in txt and "artmd.ini" in txt
                  and "Cameo=GICAMEO" in txt)
        print(f"INI tab raw sections: {'OK' if ini_ok else 'FAIL'}")
        ok = ok and ini_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def _w(d, name, text):
    p = os.path.join(d, name)
    open(p, "w").write(text)
    return p


if __name__ == "__main__":
    sys.exit(main())
