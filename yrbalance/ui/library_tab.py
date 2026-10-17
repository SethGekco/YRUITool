"""
Library tab — a cross-mod shelf of TechnoTypes and AITriggerTypes.

Sub-tabs are fixed KIND views (Vehicles / Buildings / Infantry / Aircraft /
AI content / Other), each aggregating that kind across every scanned or
snapshotted source. A live search box shrinks the lists as you type. The left
panel is collections: "All sources", one auto folder per mod, plus any
collections you create — selecting one filters the kind views. Each mod folder
shows its estimated balance culture (per-kind factors vs vanilla), optionally
summarized by Eva.
"""

import os
import sys
import shutil
import subprocess
import tempfile
import datetime

from PySide6.QtCore import Qt, QSettings, QDir, QTimer
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QTabWidget, QTableWidget, QTableWidgetItem,
    QSplitter, QFileDialog, QMessageBox, QInputDialog, QGroupBox, QPlainTextEdit,
    QHeaderView, QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox,
    QRadioButton, QComboBox, QApplication, QMenu,
)

from ..engine import (
    load_catalog, save_catalog, add_source, remove_source, set_source_summary,
    create_collection, delete_collection, rename_collection, rename_source,
    add_to_collection, scan_source,
    describe_culture, balance_culture, conversion_factors, convert_unit_stats,
    convert_weapon_stats, culture_statfactors, load_tags, set_unit_tags, all_tags,
    parse_ini_lines, build_section_map, parse_type_list, build_port_plan, apply_port_plan,
    make_backup, unit_report, extract_bundle, resolve_media, get_val,
    parse_shp, load_palette, grayscale_palette, frame_to_rgba, best_frame_index,
    house_remap_palette, HOUSE_COLORS, parse_pcx,
    bag_sample_to_wav, parse_vxl, render_vxl, render_parts, voxel_count,
    parse_vpl, parse_hva, find_in_mixes, mix_id,
    load_addons, add_addon, remove_addon, scan_addon,
    spec_summary,
)
# Dialog split out in cleanup C3; re-exported so existing references resolve.
from .add_source_dialog import _AddSourceDialog
# Reusable media helpers extracted in cleanup C1 (aliased to the old local names).
from .media_preview import (
    unit_palette as _unit_palette, cameo_palette as _cameo_pal,
    playable_bag_sounds as _playable_bag_sounds,
    find_audio_player as _find_audio_player, play_wav_file as _play_wav_file,
)
from ..ai.ollama_client import OllamaClient
from ..ai.eva import summarize_balance
from .encyclopedia_dialog import _Worker

_KIND_ORDER = [("vehicle", "Vehicles"), ("building", "Buildings"),
               ("infantry", "Infantry"), ("aircraft", "Aircraft"),
               ("ai", "AI content"), ("other", "Other")]

_DEFAULT_VANILLA = os.path.expanduser(
    "~/.yrbalancetool/knowledge/read/vanilla_yr_rulesmd.ini")


class LibraryTab(QWidget):
    def __init__(self, mw=None):
        super().__init__()
        self.mw = mw
        self.sources = []
        self.collections = []
        self._items_by_source = {}         # source_id -> [LibraryItem]
        self._scan_by_source = {}          # source_id -> SourceScan (index + aimd)
        self._tables = {}                  # kind -> (QTableWidget, [LibraryItem])
        self._col_desc = []                # per col_list row: ("all"/"auto"/"col", key)
        self._has_art = {}                 # (source_id, name_lower) -> bool
        self._pal_cache = {}               # source_id -> palette (list of rgb)
        self._tags_by_source = {}          # source_id -> {unit_lower: [tags]}
        self._shared_vpl = None            # imported voxels.vpl (game lighting)
        self._vpl_import_started = False

        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        add_btn = QPushButton("Add source…")
        add_btn.clicked.connect(self._add_source)
        newcol = QPushButton("New collection…")
        newcol.clicked.connect(self._new_collection)
        bar.addWidget(add_btn)
        bar.addWidget(newcol)
        bar.addStretch(1)
        bar.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter lists live…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.search.setMaximumWidth(280)
        bar.addWidget(self.search)
        bar.addWidget(QLabel("Tag:"))
        self.tag_combo = QComboBox()
        self.tag_combo.addItem("All tags", None)
        self.tag_combo.currentIndexChanged.connect(lambda _i: self._apply_filter())
        bar.addWidget(self.tag_combo)
        root.addLayout(bar)

        # ── filter checkboxes (techno kinds; AI items ignore these) ───────────
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Hide:"))
        self.hide_nonbuild = QCheckBox("non-buildable")
        self.hide_dummy = QCheckBox("dummy")
        self.hide_civilian = QCheckBox("civilian")
        self.hide_noart = QCheckBox("no art files")
        self.hide_vanilla = QCheckBox("vanilla")
        self.hide_vanilla.setToolTip(
            "Hide TechnoTypes that exist in vanilla YR (by name), so only the "
            "mod's own/added objects show.")
        # Persist the hide toggles across launches (sticky until unticked).
        st = QSettings("YRBalanceTool", "YRBalanceTool")
        self._hide_boxes = {
            "lib_hide_nonbuild": self.hide_nonbuild,
            "lib_hide_dummy": self.hide_dummy,
            "lib_hide_civilian": self.hide_civilian,
            "lib_hide_noart": self.hide_noart,
            "lib_hide_vanilla": self.hide_vanilla,
        }
        for key, cb in self._hide_boxes.items():
            cb.setChecked(st.value(key, False, type=bool))   # before connect
            cb.toggled.connect(lambda on, k=key: self._on_hide_toggled(k, on))
            frow.addWidget(cb)
        frow.addStretch(1)
        root.addLayout(frow)

        split = QSplitter(Qt.Horizontal)

        # ── left: collections ────────────────────────────────────────────────
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>Collections</b>"))
        self.col_list = QListWidget()
        self.col_list.currentRowChanged.connect(self._on_collection)
        self.col_list.itemDoubleClicked.connect(self._rename_collection_row)
        self.col_list.setToolTip(
            "Double-click to rename · right-click for Rename/Remove · "
            "drag mod folders to reorder")
        # Right-click menu (rename/remove) + drag-to-reorder mod folders.
        self.col_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.col_list.customContextMenuRequested.connect(self._col_context_menu)
        self.col_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.col_list.setDefaultDropAction(Qt.MoveAction)
        self.col_list.model().rowsMoved.connect(self._on_sources_reordered)
        ll.addWidget(self.col_list, 1)
        self.add_to_btn = QPushButton("Add selected → collection…")
        self.add_to_btn.clicked.connect(self._add_selected_to_collection)
        ll.addWidget(self.add_to_btn)
        self.remove_btn = QPushButton("Remove selected folder/collection")
        self.remove_btn.clicked.connect(self._remove_selected)
        ll.addWidget(self.remove_btn)
        self.convert_btn = QPushButton("Convert selected → current mod culture…")
        self.convert_btn.setToolTip(
            "Rescale the selected unit's stats from its mod's balance culture into "
            "the mod currently loaded in the main window.")
        self.convert_btn.clicked.connect(self._convert_selected)
        ll.addWidget(self.convert_btn)
        self.porter_btn = QPushButton("Open selected in Porter…")
        self.porter_btn.setToolTip("Open the mod-content porter with this item's "
                                   "mod as source, filtered to this unit.")
        self.porter_btn.clicked.connect(self._open_in_porter)
        ll.addWidget(self.porter_btn)
        self.ency_btn = QPushButton("Open selected in Encyclopedia…")
        self.ency_btn.setToolTip("Open the unit encyclopedia focused on this unit.")
        self.ency_btn.clicked.connect(self._open_in_encyclopedia)
        ll.addWidget(self.ency_btn)

        # ── center: kind tabs (top) + detail pane (bottom) ───────────────────
        center = QSplitter(Qt.Vertical)
        self.ktabs = QTabWidget()
        for kind, title in _KIND_ORDER:
            tbl = QTableWidget(0, 3)
            tbl.setHorizontalHeaderLabels(["Name", "Source", "Obtain / detail"])
            tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
            tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
            tbl.setSelectionMode(QAbstractItemView.SingleSelection)
            tbl.verticalHeader().setVisible(False)
            kh = tbl.horizontalHeader()
            kh.setSectionResizeMode(0, QHeaderView.Stretch)       # Name
            kh.setSectionResizeMode(1, QHeaderView.Interactive)   # Source
            kh.setSectionResizeMode(2, QHeaderView.Interactive)   # Obtain / detail
            kh.setStretchLastSection(False)
            tbl.itemSelectionChanged.connect(self._show_detail)
            self.ktabs.addTab(tbl, title)
            self._tables[kind] = (tbl, [])
        self.ktabs.currentChanged.connect(self._on_tab_changed)
        center.addWidget(self.ktabs)

        # ── addon tabs: user-declared views Eva authored ─────────────────────
        # Its own strip rather than extra entries in ktabs: scan_source coerces
        # any unknown kind to "other", so an addon masquerading as a
        # TechnoType kind would have to change the predicates every existing
        # tab depends on. The header stays visible with no addons so the entry
        # point is discoverable.
        addon_box = QWidget()
        abl = QVBoxLayout(addon_box)
        abl.setContentsMargins(0, 4, 0, 0)
        ahdr = QHBoxLayout()
        ahdr.addWidget(QLabel("<b>Addon tabs</b>"))
        self.addon_new_btn = QPushButton("New tab from Eva…")
        self.addon_new_btn.setToolTip(
            "Describe a tab in plain English — e.g. a tab for detecting "
            "AttachmentTypes. Eva proposes it; you approve before it appears.")
        self.addon_new_btn.clicked.connect(self._new_addon)
        ahdr.addWidget(self.addon_new_btn)
        self.addon_del_btn = QPushButton("Remove tab")
        self.addon_del_btn.clicked.connect(self._remove_addon)
        ahdr.addWidget(self.addon_del_btn)
        self.addon_hint = QLabel("")
        self.addon_hint.setWordWrap(True)
        self.addon_hint.setStyleSheet("color:#888;")
        ahdr.addWidget(self.addon_hint, 1)
        abl.addLayout(ahdr)
        self.atabs = QTabWidget()
        self._addon_tables = {}            # addon name -> QTableWidget
        abl.addWidget(self.atabs, 1)
        center.addWidget(addon_box)

        center.addWidget(self._build_detail())
        center.setSizes([330, 190, 250])

        # ── balance culture (stacked under collections in the left column) ────
        bal_box = QGroupBox("Balance culture")
        rl = QVBoxLayout(bal_box)
        self.balance_text = QPlainTextEdit()
        self.balance_text.setReadOnly(True)
        self.balance_text.setFont(QFont("monospace"))
        rl.addWidget(self.balance_text, 1)
        self.eva_btn = QPushButton("Ask Eva to summarize")
        self.eva_btn.clicked.connect(self._eva_summarize)
        self.eva_btn.setEnabled(False)
        rl.addWidget(self.eva_btn)

        left_col = QSplitter(Qt.Vertical)
        left_col.addWidget(left)
        left_col.addWidget(bal_box)
        left_col.setSizes([420, 240])

        split.addWidget(left_col)
        split.addWidget(center)
        split.setSizes([250, 820])        # give the lists/detail the bulk of the width
        root.addWidget(split, 1)

        self.status = QLabel("")
        root.addWidget(self.status)

        # defer the (potentially slow) source scan until the tab is first shown,
        # so adding large mods never slows app startup
        self._loaded = False
        self._rebuild_addon_tabs()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self._reload()

    # ── detail pane ───────────────────────────────────────────────────────────
    def _build_detail(self):
        tabs = QTabWidget()
        self.detail_tabs = tabs

        # 1) Preview — the rules-preview summary + cross-mod tags.
        prev = QWidget()
        pl = QVBoxLayout(prev)
        self.detail_text = QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setFont(QFont("monospace"))
        pl.addWidget(self.detail_text, 1)
        trow = QHBoxLayout()
        trow.addWidget(QLabel("Tags:"))
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("comma-separated — cross-mod labels")
        self.tags_edit.returnPressed.connect(self._set_item_tags)
        trow.addWidget(self.tags_edit, 1)
        set_tags = QPushButton("Set")
        set_tags.clicked.connect(self._set_item_tags)
        trow.addWidget(set_tags)
        pl.addLayout(trow)
        tabs.addTab(prev, "Preview")

        # 2) INI — the raw rulesmd / artmd / soundmd sections for this item.
        ini = QWidget()
        il = QVBoxLayout(ini)
        self.ini_text = QPlainTextEdit()
        self.ini_text.setReadOnly(True)
        self.ini_text.setFont(QFont("monospace"))
        self.ini_text.setLineWrapMode(QPlainTextEdit.NoWrap)
        il.addWidget(self.ini_text, 1)
        tabs.addTab(ini, "INI")

        # 3) Assets — files, cameo, voxel/SHP preview, audio.
        assets = QWidget()
        al = QVBoxLayout(assets)
        al.addWidget(QLabel("<b>Files</b>"))
        self.file_table = QTableWidget(0, 3)
        self.file_table.setHorizontalHeaderLabels(["File", "Location", ""])
        self.file_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.file_table.verticalHeader().setVisible(False)
        fh = self.file_table.horizontalHeader()
        fh.setSectionResizeMode(0, QHeaderView.Interactive)   # File — draggable
        fh.setSectionResizeMode(1, QHeaderView.Stretch)       # Location — fills
        fh.setSectionResizeMode(2, QHeaderView.Interactive)   # status
        fh.setStretchLastSection(False)
        self.file_table.setColumnWidth(0, 240)
        self.file_table.setColumnWidth(2, 90)
        self.file_table.setWordWrap(False)
        self.file_table.setTextElideMode(Qt.ElideNone)
        self.file_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.file_table.itemSelectionChanged.connect(self._on_file_selected)
        self.file_table.setToolTip(
            "Click a .vxl / .shp here to preview that specific asset.")
        al.addWidget(self.file_table, 1)
        hrow = QHBoxLayout()
        hrow.addWidget(QLabel("House colour:"))
        self.house_combo = QComboBox()
        self.house_combo.addItem("None", None)
        for name in HOUSE_COLORS:
            self.house_combo.addItem(name, name)
        self.house_combo.setCurrentText("Red")
        self.house_combo.currentIndexChanged.connect(
            lambda _i: self._show_detail())
        hrow.addWidget(self.house_combo)
        hrow.addWidget(QLabel("View:"))
        self.view_combo = QComboBox()
        for deg in (0, 45, 90, 135, 180, 225, 270, 315):
            self.view_combo.addItem(f"{deg}°", deg)
        self.view_combo.setToolTip("Rotate the voxel view about the vertical axis")
        self.view_combo.currentIndexChanged.connect(
            lambda _i: self._show_detail())
        hrow.addWidget(self.view_combo)
        hrow.addWidget(QLabel("Palette:"))
        self.pal_combo = QComboBox()
        self.pal_combo.setToolTip(
            "Palette used to colour the SHP/voxel preview. The calculated default "
            "for this source is pre-selected; pick another to compare.")
        self.pal_combo.currentIndexChanged.connect(self._on_palette_changed)
        hrow.addWidget(self.pal_combo)
        hrow.addStretch(1)
        self._pal_source = None   # which source the combo is currently populated for
        al.addLayout(hrow)

        imgs = QHBoxLayout()
        self.art_img = QLabel()
        self.art_img.setAlignment(Qt.AlignCenter)
        self.art_img.setMinimumHeight(140)
        self.art_img.setStyleSheet(
            "border:1px solid palette(mid); background:palette(base);")
        imgs.addWidget(self.art_img, 1)
        # Cameo column: Cameo on top, AltCameo below, pushed to the top so both fit.
        camcol = QVBoxLayout()
        _cam_css = "border:1px solid palette(mid); background:palette(base);"
        self.cameo_img = QLabel()
        self.cameo_img.setAlignment(Qt.AlignCenter)
        self.cameo_img.setFixedWidth(72)
        self.cameo_img.setToolTip("Cameo")
        self.cameo_img.setStyleSheet(_cam_css)
        self.altcameo_img = QLabel()
        self.altcameo_img.setAlignment(Qt.AlignCenter)
        self.altcameo_img.setFixedWidth(72)
        self.altcameo_img.setToolTip("AltCameo (upgraded)")
        self.altcameo_img.setStyleSheet(_cam_css)
        camcol.addWidget(self.cameo_img)
        camcol.addWidget(self.altcameo_img)
        camcol.addStretch(1)
        imgs.addLayout(camcol)
        al.addLayout(imgs)
        self.preview = QLabel("Select an item to preview its art / audio.")
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet("color:#888;")
        al.addWidget(self.preview)

        arow = QHBoxLayout()
        arow.addWidget(QLabel("Audio:"))
        self.audio_combo = QComboBox()
        self.audio_combo.setMinimumWidth(120)
        arow.addWidget(self.audio_combo, 1)
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.clicked.connect(self._play_audio)
        self.export_btn = QPushButton("Export WAV…")
        self.export_btn.clicked.connect(self._export_audio)
        arow.addWidget(self.play_btn)
        arow.addWidget(self.export_btn)
        al.addLayout(arow)
        self._player = _find_audio_player()
        self._tmp_wavs = []
        if not self._player:
            self.play_btn.setToolTip(
                "No system audio player found (paplay/aplay/afplay). Use Export.")
        tabs.addTab(assets, "Assets")

        # 4) Balance Culture — how this item's source mod is tuned vs vanilla, and
        #    a preview of converting its stats into another mod's scale.
        bc = QWidget()
        bl = QVBoxLayout(bc)
        self.bc_text = QPlainTextEdit()
        self.bc_text.setReadOnly(True)
        bl.addWidget(self.bc_text, 1)
        crow = QHBoxLayout()
        crow.addWidget(QLabel("Convert to:"))
        self.bc_target = QComboBox()
        self.bc_target.setMinimumWidth(160)
        crow.addWidget(self.bc_target, 1)
        self.bc_btn = QPushButton("Show conversion")
        self.bc_btn.clicked.connect(self._bc_convert)
        crow.addWidget(self.bc_btn)
        bl.addLayout(crow)
        self.bc_convert_text = QPlainTextEdit()
        self.bc_convert_text.setReadOnly(True)
        self.bc_convert_text.setFont(QFont("monospace"))
        bl.addWidget(self.bc_convert_text, 1)
        tabs.addTab(bc, "Balance Culture")

        return tabs

    def _on_tab_changed(self, _i):
        self._apply_filter()
        self._show_detail()

    _STATUS = {"present": "✓ present", "vanilla": "· vanilla",
               "missing": "✗ missing", "sound": "♪ audio"}

    def _show_detail(self):
        it = self._selected_item()
        if it is None:
            self.detail_text.setPlainText("")
            self.ini_text.setPlainText("")
            self.file_table.setRowCount(0)
            self.art_img.clear()
            self.cameo_img.clear()
            self.tags_edit.clear()
            self.tags_edit.setEnabled(False)
            self._clear_audio()
            self.preview.setText("Select an item to preview its art / audio.")
            self.bc_text.setPlainText("")
            self.bc_convert_text.setPlainText("")
            self.bc_target.clear()
            return
        self.tags_edit.setEnabled(True)
        self.tags_edit.setText(", ".join(self._item_tags(it)))
        scan = self._scan_by_source.get(it.source_id)
        if it.kind == "ai":
            self._detail_ai(it, scan)
        else:
            self._detail_techno(it, scan)

    def _detail_techno(self, it, scan):
        entry = None
        if scan is not None and scan.index is not None:
            entry = scan.index.units.get(it.name.lower())
        # Remember the current item so clicking a .vxl/.shp in the Files table can
        # re-render that specific asset (_on_file_selected).
        self._detail_entry = entry
        self._detail_sid = getattr(it, "source_id", None)
        self._detail_scan = scan
        if entry is None:
            self.detail_text.setPlainText(f"[{it.name}] — no detail available "
                                          "(source not scanned).")
            self.ini_text.setPlainText("")
            self.file_table.setRowCount(0)
            self.preview.setText("")
            self.bc_text.setPlainText("")
            self.bc_convert_text.setPlainText("")
            self.bc_target.clear()
            return
        self.detail_text.setPlainText(unit_report(entry))
        self.ini_text.setPlainText(self._ini_text(entry, scan))
        self._fill_files(entry.assets)
        self._render_art(entry, it.source_id, scan)
        self._render_cameo(entry, it.source_id, scan)
        self._populate_audio(entry, scan)
        self.preview.setText("")
        self._fill_balance_culture(it, scan)

    def _ini_text(self, entry, scan):
        """Raw rulesmd → artmd → soundmd sections for this item, verbatim."""
        idx = scan.index if (scan and scan.index) else None
        if idx is None:
            return "(source not scanned)"

        def block(smap, name):
            s = smap.get(name.lower()) if smap else None
            if not s:
                return []
            out = [f"[{s['name_raw']}]"]
            out += [f"{kr}={vr.rstrip()}" for (_li, kr, vr) in s["keys"].values()]
            out.append("")
            return out

        L = ["; ===== rulesmd.ini ====="]
        L += block(idx.rules_map, entry.name)
        seen = set()
        for _slot, w, wh, pr in (getattr(entry, "weapons", []) or []):
            for sec in (w, wh, pr):
                if sec and sec.lower() not in seen:
                    seen.add(sec.lower())
                    L += block(idx.rules_map, sec)
        if getattr(entry, "art_name", ""):
            L += ["; ===== artmd.ini ====="] + block(idx.art_map, entry.art_name)
        sounds = getattr(entry, "sound_names", []) or []
        if sounds:
            L += ["; ===== soundmd.ini ====="]
            for sn in sounds:
                L += block(idx.sound_map, sn)
        return "\n".join(L).rstrip() + "\n"

    def _fill_balance_culture(self, it, scan):
        """Describe the item's source-mod balance culture + set up target picker."""
        self.bc_convert_text.setPlainText("")
        self.bc_target.clear()
        self._bc_src = self._bc_item = self._bc_scan = None
        src = next((s for s in self.sources if s.id == it.source_id), None)
        if src is None:
            self.bc_text.setPlainText("(unknown source)")
            return
        bal = getattr(src, "balance", None)
        if not bal:
            self.bc_text.setPlainText(
                f"No balance culture for “{src.label}” — needs a vanilla baseline "
                "to compare against (add one when the source was added).")
        else:
            lines = [f"Source mod: {src.label}", "",
                     getattr(src, "balance_summary", "") or describe_culture(bal),
                     "", "Per-kind factors vs vanilla (mod / vanilla):"]
            for kind, stats in bal.items():
                bits = ", ".join(
                    f"{st} {d['factor']:.2f}×" for st, d in stats.items()
                    if isinstance(d, dict) and d.get("samples"))
                if bits:
                    lines.append(f"  {kind}: {bits}")
            lines += ["", "Pick a target mod below and Show conversion to see how "
                      "this item's Cost/Strength/Speed would rescale into its scale."]
            self.bc_text.setPlainText("\n".join(lines))
        self._bc_src, self._bc_item, self._bc_scan = src, it, scan
        for s in self.sources:
            if s.id != src.id and getattr(s, "balance", None):
                self.bc_target.addItem(s.label, s.id)
        self.bc_btn.setEnabled(self.bc_target.count() > 0
                               and it.kind in ("infantry", "vehicle",
                                               "aircraft", "building"))

    def _bc_convert(self):
        src, it, scan = (getattr(self, "_bc_src", None),
                         getattr(self, "_bc_item", None),
                         getattr(self, "_bc_scan", None))
        tid = self.bc_target.currentData()
        if not (src and it and scan and scan.index and tid):
            return
        tgt = next((s for s in self.sources if s.id == tid), None)
        if tgt is None or not getattr(tgt, "balance", None):
            self.bc_convert_text.setPlainText("Target has no balance culture.")
            return
        conv = conversion_factors(src.balance, tgt.balance)
        rows = convert_unit_stats(scan.index.rules_map, it.name, it.kind, conv)
        if not rows:
            self.bc_convert_text.setPlainText(
                "No shared-culture overlap between these mods — can't derive "
                "conversion factors (need vanilla + comparable stats on both).")
            return
        out = [f"{it.name}:  {src.label}  →  {tgt.label}", ""]
        for stat, old, new, factor in rows:
            out.append(f"  {stat:<10} {old:>9} → {str(new):<9} (×{factor:.2f})")
        out += ["", "To actually apply this (with media), use Tools → Port mod "
                "content or the Convert action."]
        self.bc_convert_text.setPlainText("\n".join(out))

    def _render_art(self, entry, sid, scan, override=None):
        """Body art preview (VXL preferred, else the unit's SHP) into art_img. The
        cameo is shown separately and is never used as body art here. Uses the
        palette picked in pal_combo. When `override` is a specific .vxl/.shp asset
        name (from clicking the Files table) that asset is previewed instead of the
        auto-picked body art. Never raises."""
        self.art_img.clear()
        if scan is None or scan.index is None:
            self.art_img.setText("(no art to preview)")
            return
        self._ensure_palettes(sid, scan)

        # The cameo .shp has its own box — exclude it from the body-art candidate,
        # otherwise a voxel unit's cameo gets rendered big and the voxel is skipped.
        art_map = getattr(scan.index, "art_map", None) or {}
        art_name = getattr(entry, "art_name", "") or getattr(entry, "name", "")
        cameo_shps = set()
        for key in ("Cameo", "AltCameo"):
            c = (get_val(art_map, art_name, key) or "").strip()
            if c:
                cameo_shps.add(f"{c.upper()}.SHP")

        def _ok(a):
            return a.status in ("present", "vanilla")

        if override:
            low = override.lower()
            vxl = override if low.endswith(".vxl") else None
            shp = override if low.endswith(".shp") else None
        else:
            vxl = next((a.name for a in entry.assets
                        if a.name.lower().endswith(".vxl") and _ok(a)), None)
            shp = next((a.name for a in entry.assets
                        if a.name.lower().endswith(".shp") and _ok(a)
                        and a.name.upper() not in cameo_shps), None)
        try:
            if vxl and self._draw_vxl(vxl, entry, sid, scan):
                return
            if shp and self._draw_shp(shp, sid, scan):
                return
            self.art_img.setText("(no body SHP/VXL art to preview)")
        except Exception as e:
            self.art_img.setText(f"(preview failed: {type(e).__name__})")

    def _resolve_media(self, cat, name):
        found, _ = resolve_media(cat, [name])
        return found.get(name) or next(
            (v for k, v in found.items() if k.lower() == name.lower()), None)

    def _draw_vxl(self, vxl, entry, sid, scan):
        """Render a voxel (body + its TUR/BARL siblings) into art_img. Returns True
        on success."""
        cat = scan.index.catalog
        pal = self._house_palette(self._selected_palette(sid, scan))
        # body + turret + barrel voxels, each placed by its own HVA; art.ini
        # TurretOffset shifts the turret/barrel forward (world X).
        base = os.path.splitext(vxl)[0]
        turret_dx = 0.18 * self._turret_offset(entry, scan)
        parts = []
        for suffix in ("", "TUR", "BARL"):
            vdata = self._resolve_media(cat, f"{base}{suffix}.VXL")
            if not vdata:
                continue
            try:
                mdl = parse_vxl(vdata)
            except Exception:
                continue
            if voxel_count(mdl) == 0:
                continue
            hva = None
            hdata = self._resolve_media(cat, f"{base}{suffix}.HVA")
            if hdata:
                try:
                    hva = parse_hva(hdata)
                except Exception:
                    hva = None
            off = (turret_dx, 0.0, 0.0) if suffix in ("TUR", "BARL") \
                else (0.0, 0.0, 0.0)
            parts.append((mdl, hva, off))
        if not parts:
            return False
        # Higher max_px than the 200 default → crisper, less grainy preview.
        w, h, rgba = render_parts(
            parts, max_px=360, palette=pal, vpl=self._preview_vpl(sid),
            view_deg=self.view_combo.currentData() or 0)
        self._set_art_pixmap(w, h, rgba)
        return True

    def _draw_shp(self, shp, sid, scan):
        """Render a unit SHP's best frame into art_img. Returns True on success."""
        cat = scan.index.catalog
        pal = self._house_palette(self._selected_palette(sid, scan))
        data = self._resolve_media(cat, shp)
        if not data:
            return False
        img = parse_shp(data)
        w, h, rgba = frame_to_rgba(img, img.frames[best_frame_index(img)], pal)
        self._set_art_pixmap(w, h, rgba)
        return True

    def _on_file_selected(self):
        """Clicking a .vxl / .shp in the Files table previews THAT asset in the big
        art box (lets you flip between a unit's multiple voxel/SHP assets)."""
        entry = getattr(self, "_detail_entry", None)
        scan = getattr(self, "_detail_scan", None)
        sid = getattr(self, "_detail_sid", None)
        if entry is None or scan is None:
            return
        items = self.file_table.selectedItems()
        if not items:
            return
        name = items[0].text().strip()
        if not name.lower().endswith((".vxl", ".shp")):
            return
        self._render_art(entry, sid, scan, override=name)

    def _render_cameo(self, entry, sid, scan):
        """Show the unit's cameos — Cameo (top) and AltCameo (below) — from its art
        fields. Each is PCX or SHP; SHP cameos use the dedicated cameo palette.
        Never raises."""
        self._render_one_cameo(self.cameo_img, entry, sid, scan, "Cameo")
        self._render_one_cameo(self.altcameo_img, entry, sid, scan, "AltCameo")

    def _render_one_cameo(self, widget, entry, sid, scan, key):
        widget.clear()
        art_map = getattr(getattr(scan, "index", None), "art_map", None)
        cat = getattr(getattr(scan, "index", None), "catalog", None)
        art_name = getattr(entry, "art_name", "") or getattr(entry, "name", "")
        if not art_map or not cat or not art_name:
            widget.setVisible(key == "Cameo")
            return
        cameo = get_val(art_map, art_name, key)
        cameo = str(cameo).strip() if cameo else ""
        if not cameo or cameo.lower() == "none":
            # Hide the AltCameo box entirely when there's no AltCameo, so the single
            # cameo doesn't float over an empty second slot.
            widget.setVisible(key == "Cameo")
            return
        widget.setVisible(True)
        try:
            pdata = self._resolve_media(cat, cameo.upper() + ".PCX")
            if pdata:
                w, h, rgba = parse_pcx(pdata)
            else:
                sdata = self._resolve_media(cat, cameo.upper() + ".SHP")
                if not sdata:
                    widget.setVisible(key == "Cameo")
                    return
                img = parse_shp(sdata)
                # SHP cameos use the dedicated cameo palette, NOT the unit/terrain
                # palette, and are not house-coloured.
                pal = self._cameo_palette(sid, scan)
                w, h, rgba = frame_to_rgba(
                    img, img.frames[best_frame_index(img)], pal)
            qimg = QImage(bytes(rgba), w, h, QImage.Format_RGBA8888).copy()
            pm = QPixmap.fromImage(qimg)
            if pm.width() > 68 or pm.height() > 68:
                pm = pm.scaled(68, 68, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            widget.setPixmap(pm)
        except Exception:
            widget.clear()

    def _set_art_pixmap(self, w, h, rgba):
        qimg = QImage(bytes(rgba), w, h, QImage.Format_RGBA8888).copy()
        pm = QPixmap.fromImage(qimg)
        # scale up to fill the (now-larger) preview space, keeping aspect
        box = max(220, self.art_img.width() - 8)
        if pm.width() > box or pm.height() > box:
            pm = pm.scaled(box, box, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.art_img.setPixmap(pm)

    # ── palette picker ───────────────────────────────────────────────────────
    _KNOWN_PALS = ("unittem.pal", "temperat.pal", "isotemp.pal", "unitsno.pal",
                   "snow.pal", "isosnow.pal", "uniturb.pal", "urban.pal",
                   "cameo.pal")

    def _available_palettes(self, sid, scan):
        """Palette names that exist for this source (loose folder + mod mixes),
        the calculated default (unittem.pal) first."""
        folder = self._folder_for(sid)
        found, seen = [], set()
        try:
            for fn in sorted(os.listdir(folder)):
                if fn.lower().endswith(".pal") and fn.lower() not in seen:
                    seen.add(fn.lower())
                    found.append(fn)
        except OSError:
            pass
        cat = self._catalog(scan)
        if cat:
            for name in self._KNOWN_PALS:
                if name.lower() not in seen and (
                        name.upper() in cat or mix_id(name) in cat):
                    seen.add(name.lower())
                    found.append(name)
        found.sort(key=lambda n: (n.lower() != "unittem.pal", n.lower()))
        return found

    def _ensure_palettes(self, sid, scan):
        """Populate the palette combo once per source, calculated default first."""
        if self._pal_source == sid:
            return
        self._pal_source = sid
        self.pal_combo.blockSignals(True)
        self.pal_combo.clear()
        pals = self._available_palettes(sid, scan)
        if pals:
            for n in pals:
                self.pal_combo.addItem(n, n)
        else:
            self.pal_combo.addItem("(auto)", None)
        self.pal_combo.setCurrentIndex(0)
        self.pal_combo.blockSignals(False)

    def _selected_palette(self, sid, scan):
        name = self.pal_combo.currentData()
        if name:
            pal = self._load_named_palette(sid, scan, name)
            if pal:
                return pal
        return self._preview_palette(sid, scan)

    def _load_named_palette(self, sid, scan, name):
        key = (sid, "pal", name.lower())
        if key in self._pal_cache:
            return self._pal_cache[key]
        pal = None
        p = os.path.join(self._folder_for(sid), name)
        if os.path.isfile(p):
            try:
                pal = load_palette(open(p, "rb").read())
            except Exception:
                pal = None
        if pal is None:
            cat = self._catalog(scan)
            if cat:
                try:
                    found, _ = resolve_media(cat, [name])
                    for k, v in found.items():
                        if k.lower() == name.lower():
                            pal = load_palette(v)
                            break
                except Exception:
                    pal = None
        self._pal_cache[key] = pal
        return pal

    def _on_palette_changed(self, _i):
        self._show_detail()

    def _turret_offset(self, entry, scan):
        """TurretOffset (a single float) from the unit's art entry, or 0. The game
        reads it with ReadFloat for voxel turrets, so take the first number."""
        art_map = getattr(getattr(scan, "index", None), "art_map", None)
        art_name = getattr(entry, "art_name", "") or getattr(entry, "name", "")
        if not art_map or not art_name:
            return 0.0
        raw = get_val(art_map, art_name, "TurretOffset")
        if not raw:
            return 0.0
        try:
            return float(str(raw).split(",")[0].strip())
        except (ValueError, TypeError):
            return 0.0

    def _folder_for(self, sid):
        src = next((s for s in self.sources if s.id == sid), None)
        return os.path.dirname(src.rules_path) if src else ""

    @staticmethod
    def _catalog(scan):
        return scan.index.catalog if (scan and scan.index) else None

    def _cameo_palette(self, sid, scan):
        """Cached cameo palette (see media_preview.cameo_palette)."""
        key = (sid, "cameo")
        if key not in self._pal_cache:
            self._pal_cache[key] = _cameo_pal(self._folder_for(sid),
                                              self._catalog(scan))
        return self._pal_cache[key]

    def _preview_palette(self, sid, scan):
        """Cached unit/terrain palette (see media_preview.unit_palette)."""
        if sid not in self._pal_cache:
            self._pal_cache[sid] = _unit_palette(self._folder_for(sid),
                                                 self._catalog(scan))
        return self._pal_cache[sid]

    def _house_palette(self, base):
        """Apply the selected house colour to the remap range (16-31); returns the
        palette unchanged if 'None' is selected."""
        name = self.house_combo.currentData()
        if not name or name not in HOUSE_COLORS:
            return base
        return house_remap_palette(base, HOUSE_COLORS[name])

    def _preview_vpl(self, sid):
        """A parsed voxels.vpl for game-accurate voxel lighting, or None (→
        render_vxl uses diffuse lighting). Order: a voxels.vpl the mod ships loose,
        then a shared copy imported once from the game mixes to
        ~/.yrbalancetool/voxels.vpl. If none is cached yet, a one-time background
        import is kicked off from the source's game mixes (non-blocking)."""
        src = next((s for s in self.sources if s.id == sid), None)
        folder = os.path.dirname(src.rules_path) if src else ""
        loose = os.path.join(folder, "voxels.vpl")
        if os.path.isfile(loose):
            try:
                return parse_vpl(open(loose, "rb").read())
            except Exception:
                pass
        if self._shared_vpl is not None:
            return self._shared_vpl
        cache = _vpl_cache_path()
        if os.path.isfile(cache):
            try:
                self._shared_vpl = parse_vpl(open(cache, "rb").read())
                return self._shared_vpl
            except Exception:
                pass
        self._maybe_import_vpl(folder)
        return None

    def _maybe_import_vpl(self, folder):
        """Kick a one-time background extraction of voxels.vpl from the game mixes
        in `folder` (ra2*.mix / local.mix), caching it for all sources."""
        if self._vpl_import_started or not folder:
            return
        import glob as _glob
        has_game_mixes = (_glob.glob(os.path.join(folder, "ra2*.mix"))
                          or os.path.isfile(os.path.join(folder, "local.mix")))
        if not has_game_mixes:
            return
        self._vpl_import_started = True
        self.status.setText("Importing voxels.vpl from game mixes (one-time)…")
        cache = _vpl_cache_path()

        def work():
            data = find_in_mixes([folder], "voxels.vpl")
            if data:
                os.makedirs(os.path.dirname(cache), exist_ok=True)
                with open(cache, "wb") as fh:
                    fh.write(data)
            return bool(data)

        self._vpl_worker = _Worker(work)
        self._vpl_worker.done.connect(self._vpl_imported)
        self._vpl_worker.failed.connect(
            lambda m: self.status.setText(f"voxels.vpl import failed: {m}"))
        self._vpl_worker.start()

    def _vpl_imported(self, found):
        if found:
            try:
                self._shared_vpl = parse_vpl(open(_vpl_cache_path(), "rb").read())
            except Exception:
                self._shared_vpl = None
            self.status.setText(
                "voxels.vpl imported — voxel previews now use game lighting.")
            try:
                self._show_detail()      # re-render current item with VPL
            except Exception:
                pass
        else:
            self.status.setText(
                "voxels.vpl not found in game mixes; using diffuse lighting.")

    def _detail_ai(self, it, scan):
        self.file_table.setRowCount(0)
        self.art_img.clear()
        self.cameo_img.clear()
        self._clear_audio()
        self.bc_text.setPlainText("AI entries don't carry balance-culture stats.")
        self.bc_convert_text.setPlainText("")
        self.bc_target.clear()
        self.bc_btn.setEnabled(False)
        if scan is None or scan.aimd_map is None:
            self.detail_text.setPlainText(f"{it.name} — no aimd found for source.")
            self.ini_text.setPlainText("")
            self.preview.setText("")
            return
        try:
            ent = extract_bundle(scan.aimd_map, it.name)
        except Exception as e:
            self.detail_text.setPlainText(f"{it.name}: {type(e).__name__}: {e}")
            self.ini_text.setPlainText("")
            self.preview.setText("")
            return
        L = [f"[AITriggerTypes] {ent.trigger_id}", f"  {ent.trigger_def}", ""]
        for b in ent.blocks:
            L.append(f"[{b.id}]  ; {b.kind}")
            L += [f"  {k}={v}" for k, v in b.keys]
            L.append("")
        if ent.units:
            L.append("units fielded: " + ", ".join(ent.units))
        text = "\n".join(L)
        self.detail_text.setPlainText(text)
        self.ini_text.setPlainText(text)            # the AI bundle IS its INI
        self.preview.setText("AI entry — no media; units above carry the art.")

    def _fill_files(self, assets):
        self.file_table.setRowCount(len(assets))
        for r, a in enumerate(assets):
            name = QTableWidgetItem(a.name)
            name.setToolTip(a.name)                 # full name on hover
            loc = QTableWidgetItem(a.location)
            loc.setToolTip(a.location)
            self.file_table.setItem(r, 0, name)
            self.file_table.setItem(r, 1, loc)
            self.file_table.setItem(r, 2, QTableWidgetItem(
                self._STATUS.get(a.status, a.status)))

    # ── audio ──────────────────────────────────────────────────────────────
    def _clear_audio(self):
        self.audio_combo.clear()
        self._audio_catalog = None
        self.play_btn.setEnabled(False)
        self.export_btn.setEnabled(False)

    def _populate_audio(self, entry, scan):
        self._clear_audio()
        cat = scan.index.catalog if (scan and scan.index) else None
        if not cat:
            return
        self._audio_catalog = cat
        for name in _playable_bag_sounds(entry.assets, cat):
            self.audio_combo.addItem(name, name)
        has = self.audio_combo.count() > 0
        self.play_btn.setEnabled(has and bool(self._player))
        self.export_btn.setEnabled(has)

    def _current_wav(self):
        name = self.audio_combo.currentData()
        if not name or not getattr(self, "_audio_catalog", None):
            return None, None
        try:
            return name, bag_sample_to_wav(self._audio_catalog, name)
        except Exception as e:
            self.status.setText(f"Audio decode failed: {type(e).__name__}: {e}")
            return name, None

    def _play_audio(self):
        name, wav = self._current_wav()
        if not wav or not self._player:
            return
        try:
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="yrbt_")
            with os.fdopen(fd, "wb") as f:
                f.write(wav)
            self._tmp_wavs.append(path)
            _play_wav_file(self._player, path)
            self.status.setText(f"Playing {name}…")
        except Exception as e:
            self.status.setText(f"Playback failed: {type(e).__name__}: {e}")

    def _export_audio(self):
        name, wav = self._current_wav()
        if not wav:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export WAV",
                                              f"{name}.wav", "WAV (*.wav)")
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(wav)
            self.status.setText(f"Exported {os.path.basename(path)}.")
        except Exception as e:
            self.status.setText(f"Export failed: {type(e).__name__}: {e}")

    # ── addon tabs ─────────────────────────────────────────────────────────
    def _addon_rules_maps(self):
        """[(mod label, rules section_map)] for every scanned source."""
        out = []
        scans = getattr(self, "_scan_by_source", None) or {}
        for sid, scan in scans.items():
            idx = getattr(scan, "index", None)
            rmap = getattr(idx, "rules_map", None) if idx else None
            if rmap:
                out.append((self._source_label(sid), rmap))
        return out

    def _rebuild_addon_tabs(self):
        """One tab per stored addon, aggregated across every scanned mod."""
        self.atabs.clear()
        self._addon_tables = {}
        specs = load_addons()
        self.atabs.setVisible(bool(specs))
        self.addon_del_btn.setEnabled(bool(specs))
        if not specs:
            self.addon_hint.setText(
                "none yet — describe one and Eva will build it")
            return
        maps = self._addon_rules_maps()
        for spec in specs:
            tbl = QTableWidget(0, 0)
            tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
            tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
            tbl.verticalHeader().setVisible(False)
            tbl.setToolTip(spec.notes or spec_summary(spec))
            self._fill_addon_table(spec, tbl, maps)
            self.atabs.addTab(tbl, spec.name)
            self._addon_tables[spec.name] = tbl
        self.addon_hint.setText(f"{len(specs)} addon tab(s)")

    def _fill_addon_table(self, spec, tbl, maps):
        headers = ["Name", "Source"] + [c.label for c in spec.columns]
        tbl.setColumnCount(len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        rows = []
        for label, rmap in maps:
            for r in scan_addon(spec, rmap):
                rows.append((label, r))
        if spec.group_by:
            rows.sort(key=lambda lr: (lr[1].group, lr[1].name))
        tbl.setRowCount(len(rows))
        for i, (label, r) in enumerate(rows):
            tbl.setItem(i, 0, QTableWidgetItem(r.name))
            tbl.setItem(i, 1, QTableWidgetItem(label))
            for j, c in enumerate(spec.columns):
                tbl.setItem(i, 2 + j,
                            QTableWidgetItem(r.values.get(c.label, "")))
        h = tbl.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Interactive)
        for j in range(1, len(headers)):
            h.setSectionResizeMode(j, QHeaderView.Interactive)
        h.setStretchLastSection(True)

    def _new_addon(self):
        from .addon_dialog import AddonRequestDialog
        dlg = AddonRequestDialog(self, self._addon_rules_maps())
        if dlg.exec() == QDialog.Accepted and dlg.spec is not None:
            add_addon(dlg.spec)
            self._rebuild_addon_tabs()
            idx = self.atabs.count() - 1
            for i in range(self.atabs.count()):
                if self.atabs.tabText(i) == dlg.spec.name:
                    idx = i
            self.atabs.setCurrentIndex(max(idx, 0))
            self.status.setText(f"Added the “{dlg.spec.name}” addon tab.")

    def _remove_addon(self):
        i = self.atabs.currentIndex()
        if i < 0:
            return
        name = self.atabs.tabText(i)
        if QMessageBox.question(
                self, "Remove addon tab",
                f"Remove the “{name}” tab? The addon is deleted from your "
                "library; your mod is not touched.") != QMessageBox.Yes:
            return
        remove_addon(name)
        self._rebuild_addon_tabs()
        self.status.setText(f"Removed the “{name}” addon tab.")

    # ── data ─────────────────────────────────────────────────────────────────
    def _reload(self, keep_row=None):
        self.sources, self.collections = load_catalog()
        self._items_by_source = {}
        self._scan_by_source = {}
        self._pal_cache = {}
        self._pal_source = None     # force palette combo to repopulate
        for s in self.sources:
            try:
                scan = scan_source(s)
            except Exception:
                scan = None
            self._scan_by_source[s.id] = scan
            self._items_by_source[s.id] = scan.items if scan else []
            try:
                self._tags_by_source[s.id] = load_tags(s.rules_path)
            except Exception:
                self._tags_by_source[s.id] = {}
        self._rebuild_collections(keep_row)
        self._rebuild_tables()
        self._refresh_tag_combo()
        self._apply_filter()
        self._rebuild_addon_tabs()

    def _item_tags(self, item):
        return self._tags_by_source.get(item.source_id, {}).get(
            item.name.lower(), [])

    def _all_tag_names(self):
        names = set()
        for tagmap in self._tags_by_source.values():
            for tags in tagmap.values():
                names.update(tags)
        return sorted(names)

    def _refresh_tag_combo(self):
        cur = self.tag_combo.currentData()
        self.tag_combo.blockSignals(True)
        self.tag_combo.clear()
        self.tag_combo.addItem("All tags", None)
        for t in self._all_tag_names():
            self.tag_combo.addItem(t, t)
        i = self.tag_combo.findData(cur)
        self.tag_combo.setCurrentIndex(i if i >= 0 else 0)
        self.tag_combo.blockSignals(False)

    def _all_items(self):
        for sid, items in self._items_by_source.items():
            for it in items:
                yield it

    def _source_label(self, sid):
        s = next((x for x in self.sources if x.id == sid), None)
        return s.label if s else sid

    def _source_display(self, sid):
        """Column text: a renamed source shows its custom label; otherwise the last
        two folder components, so a shelf full of '…/RA2' mods stays
        distinguishable by their parent folder."""
        s = next((x for x in self.sources if x.id == sid), None)
        if s is None:
            return sid
        folder = os.path.dirname(s.orig_path or s.rules_path)
        base = os.path.basename(folder)
        if s.label and s.label != base:        # user gave it a custom name
            return s.label
        parts = [p for p in folder.replace("\\", "/").split("/") if p]
        return "/".join(parts[-2:]) if len(parts) >= 2 else (s.label or base)

    def _source_folder(self, sid):
        s = next((x for x in self.sources if x.id == sid), None)
        return os.path.dirname(s.orig_path or s.rules_path) if s else ""

    def _rebuild_collections(self, keep_row=None):
        self.col_list.blockSignals(True)
        self.col_list.clear()
        self._col_desc = []
        total = sum(len(v) for v in self._items_by_source.values())

        def _add(text, desc):
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, desc)        # (mode, key) for drag/menu mapping
            # Only mod folders are drag-reorderable; All/collections are pinned.
            flags = it.flags() & ~Qt.ItemIsDropEnabled
            if desc[0] != "auto":
                flags &= ~Qt.ItemIsDragEnabled
            it.setFlags(flags)
            self.col_list.addItem(it)
            self._col_desc.append(desc)

        _add(f"All sources  ({total})", ("all", None))
        for s in self.sources:
            n = len(self._items_by_source.get(s.id, []))
            _add(f"📁 {s.label}  ({n})", ("auto", s.id))
        for c in self.collections:
            if c.auto_source:
                continue
            _add(f"★ {c.name}  ({len(c.items)})", ("col", c.name))
        self.col_list.blockSignals(False)
        self.col_list.setCurrentRow(
            keep_row if keep_row is not None and keep_row < self.col_list.count()
            else 0)

    def _rebuild_tables(self):
        self._has_art = {}
        for sid, scan in self._scan_by_source.items():
            if scan is None or scan.index is None:
                continue
            for u in scan.index.units.values():
                has = any(a.name.lower().endswith((".shp", ".vxl"))
                          for a in u.assets)
                self._has_art[(sid, u.name.lower())] = has
        for kind, (tbl, _rows) in self._tables.items():
            items = [it for it in self._all_items() if it.kind == kind]
            items.sort(key=lambda i: i.name.lower())
            tbl.setRowCount(len(items))
            for r, it in enumerate(items):
                name = QTableWidgetItem(it.name)
                name.setToolTip(it.name)
                tbl.setItem(r, 0, name)
                srcit = QTableWidgetItem(self._source_display(it.source_id))
                srcit.setToolTip(self._source_folder(it.source_id))
                tbl.setItem(r, 1, srcit)
                tbl.setItem(r, 2, QTableWidgetItem(it.detail or it.obtain))
            self._tables[kind] = (tbl, items)

    # ── filtering ──────────────────────────────────────────────────────────
    def _current_predicate(self):
        row = self.col_list.currentRow()
        if not (0 <= row < len(self._col_desc)):
            return lambda it: True
        mode, key = self._col_desc[row]
        if mode == "all":
            return lambda it: True
        if mode == "auto":
            return lambda it: it.source_id == key
        col = next((c for c in self.collections if c.name == key), None)
        keyset = {(k[0], k[1], k[2].lower()) for k in (col.items if col else [])}
        return lambda it: (it.source_id, it.kind, it.name.lower()) in keyset

    def _passes_checks(self, it):
        """Obtain/no-art hide checkboxes. AI items are exempt (no obtain/art)."""
        if it.kind == "ai":
            return True
        ob = (it.obtain or "").lower()
        if self.hide_nonbuild.isChecked() and ob != "normal":
            return False
        if self.hide_dummy.isChecked() and ob == "dummy":
            return False
        if self.hide_civilian.isChecked() and ob == "civilian":
            return False
        if self.hide_noart.isChecked() and \
                not self._has_art.get((it.source_id, it.name.lower()), False):
            return False
        if self.hide_vanilla.isChecked() and it.name.lower() in self._vanilla_names():
            return False
        return True

    def _vanilla_names(self):
        """Lowercased set of vanilla-YR TechnoType names (cached), read from the
        bundled vanilla rules, so the 'vanilla' hide filter can drop stock units."""
        if getattr(self, "_vanilla_name_cache", None) is not None:
            return self._vanilla_name_cache
        names = set()
        try:
            smap = build_section_map(parse_ini_lines(_DEFAULT_VANILLA))[0]
            for sec in ("InfantryTypes", "VehicleTypes", "AircraftTypes",
                        "BuildingTypes"):
                names.update(n.lower() for n in parse_type_list(smap, sec))
        except Exception:
            pass
        self._vanilla_name_cache = names
        return names

    def _on_hide_toggled(self, key, on):
        QSettings("YRBalanceTool", "YRBalanceTool").setValue(key, on)
        self._apply_filter()

    def _apply_filter(self):
        pred = self._current_predicate()
        text = self.search.text().strip().lower()
        tag = self.tag_combo.currentData()
        for kind, title in _KIND_ORDER:
            tbl, items = self._tables[kind]
            shown = 0
            for r, it in enumerate(items):
                vis = pred(it) and self._passes_checks(it) and (
                    not tag or tag in self._item_tags(it)) and (
                    not text or text in it.name.lower()
                    or text in self._source_display(it.source_id).lower()
                    or text in (it.detail or it.obtain).lower())
                tbl.setRowHidden(r, not vis)
                shown += 1 if vis else 0
            idx = [k for k, _ in _KIND_ORDER].index(kind)
            self.ktabs.setTabText(idx, f"{title} ({shown})")

    # ── collection selection / balance ───────────────────────────────────────
    def _on_collection(self, _row):
        self._apply_filter()
        self._show_balance()

    def _selected_source(self):
        row = self.col_list.currentRow()
        if 0 <= row < len(self._col_desc):
            mode, key = self._col_desc[row]
            if mode == "auto":
                return next((s for s in self.sources if s.id == key), None)
        return None

    def _show_balance(self):
        s = self._selected_source()
        if s is None:
            self.balance_text.setPlainText(
                "Select a mod folder (📁) to see its balance culture.")
            self.eva_btn.setEnabled(False)
            return
        lines = [f"{s.label}  [{s.mode}]", ""]
        lines.append(s.balance_summary or describe_culture(s.balance))
        lines.append("")
        lines.append("factors vs vanilla (mod / vanilla):")
        if s.balance:
            for kind, kd in s.balance.items():
                bits = "  ".join(f"{stat} {d['factor']:.2f}× ({d['confidence']})"
                                 for stat, d in kd.items())
                lines.append(f"  {kind:9} {bits}")
        else:
            lines.append("  (no vanilla baseline found)")
        self.balance_text.setPlainText("\n".join(lines))
        self.eva_btn.setEnabled(bool(s.balance))

    # ── actions ──────────────────────────────────────────────────────────────
    def _add_source(self):
        start = os.path.dirname(getattr(self.mw, "source_path", "") or "")
        dlg = _AddSourceDialog(self, start)
        if dlg.exec() != QDialog.Accepted:
            return
        path, mode = dlg.rules_path, dlg.mode
        if not path:
            return
        van = _DEFAULT_VANILLA if os.path.isfile(_DEFAULT_VANILLA) else None
        stamp = datetime.date.today().isoformat()
        self.status.setText("Scanning source…")
        try:
            add_source(path, mode=mode, added_at=stamp, vanilla_path=van)
        except Exception as e:
            QMessageBox.critical(self, "Add source", f"{type(e).__name__}: {e}")
            self.status.setText("")
            return
        self._reload()
        self.status.setText(f"Added {os.path.basename(os.path.dirname(path))} "
                            f"({mode}).")

    def _new_collection(self):
        name, ok = QInputDialog.getText(self, "New collection", "Name:")
        name = name.strip()
        if ok and name:
            create_collection(name)
            self._reload()

    def _selected_item(self):
        kind = _KIND_ORDER[self.ktabs.currentIndex()][0]
        tbl, items = self._tables[kind]
        row = tbl.currentRow()
        return items[row] if 0 <= row < len(items) else None

    def _add_selected_to_collection(self):
        it = self._selected_item()
        if it is None:
            self.status.setText("Select an item in a kind tab first.")
            return
        user_cols = [c.name for c in self.collections if not c.auto_source]
        if not user_cols:
            QMessageBox.information(self, "Collections",
                                    "Create a collection first (New collection…).")
            return
        name, ok = QInputDialog.getItem(self, "Add to collection",
                                        f"Add '{it.name}' to:", user_cols, 0, False)
        if ok and name:
            add_to_collection(name, it)
            row = self.col_list.currentRow()
            self._reload(keep_row=row)
            self.status.setText(f"Added {it.name} → {name}.")

    def _set_item_tags(self):
        it = self._selected_item()
        if it is None:
            return
        src = next((s for s in self.sources if s.id == it.source_id), None)
        if src is None:
            return
        tags = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
        set_unit_tags(src.rules_path, it.name, tags)
        self._tags_by_source[it.source_id] = load_tags(src.rules_path)
        self._refresh_tag_combo()
        self._apply_filter()
        self.status.setText(f"Tagged {it.name}: {', '.join(tags) or '(none)'}")

    def _open_in_porter(self):
        it = self._selected_item()
        if it is None:
            self.status.setText("Select an item to open in the porter.")
            return
        src = next((s for s in self.sources if s.id == it.source_id), None)
        if src is None:
            return
        if not getattr(self.mw, "source_path", None) or \
                getattr(self.mw, "section_map", None) is None:
            QMessageBox.information(self, "Porter", "Load the target rules in the "
                                    "main window first (the mod you're importing "
                                    "INTO).")
            return
        from .port_dialog import PortDialog
        dlg = PortDialog(self.mw)
        dlg.src_edit.setText(src.rules_path)
        if it.kind != "ai":
            dlg.filter_edit.setText(it.name)
        dlg._analyze()
        # once the background scan fills the tree, filter it to this unit
        if it.kind != "ai" and getattr(dlg, "_worker", None) is not None:
            dlg._worker.done.connect(lambda _r: dlg._apply_filter(it.name))
        dlg.show()
        self.status.setText(f"Opened {it.name} in the porter "
                            f"(source: {src.label}).")

    def _open_in_encyclopedia(self):
        it = self._selected_item()
        if it is None:
            self.status.setText("Select a unit to open in the encyclopedia.")
            return
        if it.kind == "ai":
            self.status.setText("AI triggers aren't in the unit encyclopedia — "
                                "use Open in Porter or the AI library.")
            return
        src = next((s for s in self.sources if s.id == it.source_id), None)
        if src is None:
            return
        from .encyclopedia_dialog import EncyclopediaDialog
        dlg = EncyclopediaDialog(self.mw)
        name = it.name

        def focus(*_):
            dlg.search_edit.setText(name)        # triggers the dialog's _refresh
            for r, u in enumerate(getattr(dlg, "_results", [])):
                if u.name.lower() == name.lower():
                    dlg.table.selectRow(r)
                    dlg._show_detail()
                    break

        dlg._load_index(src.rules_path)
        if getattr(dlg, "_worker", None) is not None:
            dlg._worker.done.connect(focus)
        else:
            focus()
        dlg.show()
        self.status.setText(f"Opened {name} in the encyclopedia.")

    def _convert_selected(self):
        it = self._selected_item()
        if it is None or it.kind not in ("infantry", "vehicle", "aircraft",
                                         "building"):
            self.status.setText("Select a TechnoType (infantry/vehicle/aircraft/"
                                "building) to convert.")
            return
        target_rules = getattr(self.mw, "source_path", None)
        if not target_rules:
            QMessageBox.information(self, "Convert", "Load the target mod's rules "
                                    "in the main window first (File → Load rules).")
            return
        src = next((s for s in self.sources if s.id == it.source_id), None)
        scan = self._scan_by_source.get(it.source_id)
        if src is None or scan is None or scan.index is None:
            return
        tgt_culture = balance_culture(target_rules)
        conv = conversion_factors(src.balance, tgt_culture)
        rmap = scan.index.rules_map
        # unit TechnoType stats + the unit's weapons' stats
        sections = []
        urows = convert_unit_stats(rmap, it.name, it.kind, conv)
        if urows:
            sections.append((it.name, urows))
        entry = scan.index.units.get(it.name.lower())
        seen = set()
        for slot, wpn, _wh, _pr in (getattr(entry, "weapons", []) or []):
            if wpn and wpn.lower() not in seen and wpn.lower() in rmap:
                seen.add(wpn.lower())
                wrows = convert_weapon_stats(rmap, wpn, conv)
                if wrows:
                    sections.append((wpn, wrows))
        if not sections:
            self.status.setText("No shared-culture overlap — can't derive "
                                "conversion factors (need vanilla + shared units/"
                                "weapons in both mods).")
            return

        def apply_fn():
            """Port the unit + its weapon chain into the current mod's rules with
            the culture factors (rules only; use the full Porter for media)."""
            tlines = parse_ini_lines(target_rules)
            tmap = build_section_map(tlines)[0]
            plan = build_port_plan(rmap, tmap,
                                   factors=culture_statfactors(conv))
            names = {it.name.lower()}
            for _slot, w, wh, pr in (getattr(entry, "weapons", []) or []):
                for n in (w, wh, pr):
                    if n:
                        names.add(n.lower())
            selected = [o.name for o in plan.objects
                        if o.name.lower() in names]
            if not selected:
                return (f"'{it.name}' isn't new to the target (already present) "
                        "— nothing ported.")
            make_backup(target_rules, None, keep=10)
            summ = apply_port_plan(tlines, tmap, plan, selected=selected)
            with open(target_rules, "w", encoding="latin-1",
                      errors="replace", newline="") as fh:
                fh.write("".join(tlines))
            if hasattr(self.mw, "_load_rules_path"):
                self.mw._load_rules_path(target_rules)
            return (f"Ported {summ['sections']} section(s) into "
                    f"{os.path.basename(target_rules)} (backup kept): "
                    f"{', '.join(selected)}.")

        _ConvertDialog(self, it, src.label,
                       os.path.basename(os.path.dirname(target_rules)),
                       src.balance, tgt_culture, sections, rmap,
                       apply_fn=apply_fn).exec()

    def _rename_collection_row(self, item):
        row = self.col_list.row(item)
        if not (0 <= row < len(self._col_desc)):
            return
        mode, key = self._col_desc[row]
        if mode == "all":
            return
        if mode == "auto":
            s = next((x for x in self.sources if x.id == key), None)
            if s is None:
                return
            new, ok = QInputDialog.getText(self, "Rename mod folder",
                                           "Display name:", text=s.label)
            if ok and new.strip():
                rename_source(key, new.strip())
                self._reload(keep_row=row)
        else:
            new, ok = QInputDialog.getText(self, "Rename collection",
                                           "Name:", text=key)
            if ok and new.strip() and new.strip() != key:
                rename_collection(key, new.strip())
                self._reload(keep_row=row)

    def _remove_selected(self):
        row = self.col_list.currentRow()
        if not (0 <= row < len(self._col_desc)):
            return
        mode, key = self._col_desc[row]
        if mode == "all":
            return
        if mode == "auto":
            s = next((x for x in self.sources if x.id == key), None)
            if s and QMessageBox.question(
                    self, "Remove source",
                    f"Remove '{s.label}' from the library?"
                    + (" Its snapshot copy will be deleted."
                       if s.mode == "snapshot" else "")) == QMessageBox.Yes:
                remove_source(key)
                self._reload()
        else:
            if QMessageBox.question(self, "Delete collection",
                                    f"Delete collection '{key}'?") == QMessageBox.Yes:
                delete_collection(key)
                self._reload()

    def _col_context_menu(self, pos):
        """Right-click a mod folder / collection → Rename / Remove."""
        item = self.col_list.itemAt(pos)
        if item is None:
            return
        desc = item.data(Qt.UserRole)
        if not desc or desc[0] == "all":
            return                       # "All sources" can't be renamed/removed
        self.col_list.setCurrentRow(self.col_list.row(item))
        menu = QMenu(self)
        menu.addAction("Rename…", lambda: self._rename_collection_row(item))
        menu.addAction("Remove", self._remove_selected)
        menu.exec(self.col_list.mapToGlobal(pos))

    def _on_sources_reordered(self, *_args):
        # A drag reordered the mod-folder rows — persist the new source order.
        # Deferred so we don't mutate the list from inside its own move signal.
        QTimer.singleShot(0, self._persist_source_order)

    def _persist_source_order(self):
        order = [self.col_list.item(i).data(Qt.UserRole)[1]
                 for i in range(self.col_list.count())
                 if (self.col_list.item(i).data(Qt.UserRole) or ("", ""))[0]
                 == "auto"]
        by_id = {s.id: s for s in self.sources}
        new = [by_id[sid] for sid in order if sid in by_id]
        if len(new) != len(self.sources):
            self._rebuild_collections()          # unexpected — restore canonical
            return
        if [s.id for s in new] == [s.id for s in self.sources]:
            return                               # no actual change
        self.sources = new
        save_catalog(self.sources, self.collections)
        self._rebuild_collections()              # re-lay canonically, new order

    # ── Eva summary ──────────────────────────────────────────────────────────
    def _eva_summarize(self):
        s = self._selected_source()
        if s is None or not s.balance:
            return
        st = QSettings("YRBalanceTool", "YRBalanceTool")
        model = st.value("eva_model", "")
        if not model:
            QMessageBox.information(self, "Eva", "Set up Eva's model in the Eva "
                                    "tab first (Refresh models).")
            return
        client = OllamaClient(base_url=st.value("eva_url",
                                                "http://localhost:11434"))
        try:
            ctx = int(st.value("eva_num_ctx", 8192))
        except (TypeError, ValueError):
            ctx = 8192
        self.eva_btn.setEnabled(False)
        self.status.setText("Eva is summarizing the balance culture…")
        sid, bal, label = s.id, s.balance, s.label

        def work():
            return summarize_balance(client, model, label, bal, num_ctx=ctx)

        self._worker = _Worker(work)
        self._worker.done.connect(lambda text: self._eva_ready(sid, text))
        self._worker.failed.connect(
            lambda m: (self.eva_btn.setEnabled(True),
                       self.status.setText(f"Eva failed: {m}")))
        self._worker.start()

    def _eva_ready(self, sid, text):
        self.eva_btn.setEnabled(True)
        set_source_summary(sid, text)
        for s in self.sources:
            if s.id == sid:
                s.balance_summary = text
        self._show_balance()
        self.status.setText("Eva summarized the balance culture.")


class _ConvertDialog(QDialog):
    """Preview a unit (and its weapons) rescaled from its mod's balance culture
    into the current mod's, and copy the converted rules sections."""

    def __init__(self, parent, item, src_label, tgt_label, src_culture,
                 tgt_culture, sections, rules_map, apply_fn=None):
        super().__init__(parent)
        self.item = item
        self.sections = sections          # [(section_name, [(stat,old,new,f)])]
        self.rules_map = rules_map
        self._apply_fn = apply_fn
        self.setWindowTitle(f"Convert {item.name} → {tgt_label} culture")
        self.resize(580, 520)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            f"<b>{item.name}</b> ({item.kind}):  {src_label} → {tgt_label}"))
        lay.addWidget(QLabel(f"<i>from:</i> {describe_culture(src_culture)}"))
        lay.addWidget(QLabel(f"<i>to:</i> {describe_culture(tgt_culture)}"))

        flat = [(sec, stat, old, new, f)
                for sec, rows in sections for (stat, old, new, f) in rows]
        tbl = QTableWidget(len(flat), 5)
        tbl.setHorizontalHeaderLabels(["Section", "Stat", "From", "To", "×factor"])
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for r, (sec, stat, old, new, f) in enumerate(flat):
            tbl.setItem(r, 0, QTableWidgetItem(sec))
            tbl.setItem(r, 1, QTableWidgetItem(stat))
            tbl.setItem(r, 2, QTableWidgetItem(str(old)))
            tbl.setItem(r, 3, QTableWidgetItem(str(new)))
            tbl.setItem(r, 4, QTableWidgetItem(f"{f:.3f}"))
        lay.addWidget(tbl, 1)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("monospace"))
        self.preview.setPlainText(self._converted_text())
        lay.addWidget(QLabel("<b>Converted sections</b> "
                             "(unit + its weapons)"))
        lay.addWidget(self.preview, 1)

        bb = QDialogButtonBox()
        copy = bb.addButton("Copy converted sections",
                            QDialogButtonBox.ActionRole)
        copy.clicked.connect(self._copy)
        if self._apply_fn is not None:
            applyb = bb.addButton("Apply to current mod (porter)",
                                  QDialogButtonBox.ApplyRole)
            applyb.setToolTip("Port the unit + its weapons into the current mod's "
                              "rules with these factors (rules only; use Tools → "
                              "Port mod content for art/sound/media).")
            applyb.clicked.connect(self._do_apply)
        bb.addButton(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _do_apply(self):
        try:
            msg = self._apply_fn()
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
        QMessageBox.information(self, "Apply to current mod", msg)

    def _converted_section(self, sec_name, rows):
        conv_map = {stat.lower(): new for (stat, _o, new, _f) in rows}
        sec = self.rules_map.get(sec_name.lower())
        if not sec:
            return ""
        lines = [f"[{sec['name_raw']}]"]
        for _klow, (li, kr, vr) in sorted(sec["keys"].items(),
                                          key=lambda kv: kv[1][0]):
            v = str(conv_map[kr.lower()]) if kr.lower() in conv_map else vr
            lines.append(f"{kr}={v}")
        return "\n".join(lines)

    def _converted_text(self):
        return "\n\n".join(self._converted_section(name, rows)
                           for name, rows in self.sections)

    def _copy(self):
        QApplication.clipboard().setText(self.preview.toPlainText())


def _vpl_cache_path():
    return os.path.join(os.path.expanduser("~"), ".yrbalancetool", "voxels.vpl")


