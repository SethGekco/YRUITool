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

from PySide6.QtCore import Qt, QSettings, QDir
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QTabWidget, QTableWidget, QTableWidgetItem,
    QSplitter, QFileDialog, QMessageBox, QInputDialog, QGroupBox, QPlainTextEdit,
    QHeaderView, QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox,
    QRadioButton, QComboBox, QApplication,
)

from ..engine import (
    load_catalog, add_source, remove_source, set_source_summary,
    create_collection, delete_collection, rename_collection, rename_source,
    add_to_collection, scan_source,
    describe_culture, balance_culture, conversion_factors, convert_unit_stats,
    convert_weapon_stats, culture_statfactors, load_tags, set_unit_tags, all_tags,
    parse_ini_lines, build_section_map, build_port_plan, apply_port_plan,
    make_backup, unit_report, extract_bundle, resolve_media, get_val,
    parse_shp, load_palette, grayscale_palette, frame_to_rgba, best_frame_index,
    house_remap_palette, HOUSE_COLORS, parse_pcx,
    bag_sample_to_wav, parse_vxl, render_vxl, render_parts, voxel_count,
    parse_vpl, parse_hva, find_in_mixes,
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
        for cb in (self.hide_nonbuild, self.hide_dummy, self.hide_civilian,
                   self.hide_noart):
            cb.toggled.connect(self._apply_filter)
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
        self.col_list.setToolTip("Double-click a mod folder or collection to rename")
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
        center.addWidget(self._build_detail())
        center.setSizes([360, 260])

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

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self._reload()

    # ── detail pane ───────────────────────────────────────────────────────────
    def _build_detail(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>Detail</b> — rules preview"))
        self.detail_text = QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setFont(QFont("monospace"))
        ll.addWidget(self.detail_text, 1)
        trow = QHBoxLayout()
        trow.addWidget(QLabel("Tags:"))
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("comma-separated — cross-mod labels")
        self.tags_edit.returnPressed.connect(self._set_item_tags)
        trow.addWidget(self.tags_edit, 1)
        set_tags = QPushButton("Set")
        set_tags.clicked.connect(self._set_item_tags)
        trow.addWidget(set_tags)
        ll.addLayout(trow)
        lay.addWidget(left, 3)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("<b>Files</b>"))
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
        rl.addWidget(self.file_table, 1)
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
        hrow.addStretch(1)
        rl.addLayout(hrow)

        imgs = QHBoxLayout()
        self.art_img = QLabel()
        self.art_img.setAlignment(Qt.AlignCenter)
        self.art_img.setMinimumHeight(140)
        self.art_img.setStyleSheet(
            "border:1px solid palette(mid); background:palette(base);")
        imgs.addWidget(self.art_img, 1)
        self.cameo_img = QLabel()
        self.cameo_img.setAlignment(Qt.AlignCenter)
        self.cameo_img.setFixedWidth(72)
        self.cameo_img.setToolTip("Cameo")
        self.cameo_img.setStyleSheet(
            "border:1px solid palette(mid); background:palette(base);")
        imgs.addWidget(self.cameo_img)
        rl.addLayout(imgs)
        self.preview = QLabel("Art / audio preview: select an item.")
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet("color:#888;")
        rl.addWidget(self.preview)

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
        rl.addLayout(arow)
        self._player = _find_audio_player()
        self._tmp_wavs = []
        if not self._player:
            self.play_btn.setToolTip(
                "No system audio player found (paplay/aplay/afplay). Use Export.")
        lay.addWidget(right, 2)
        return w

    def _on_tab_changed(self, _i):
        self._apply_filter()
        self._show_detail()

    _STATUS = {"present": "✓ present", "vanilla": "· vanilla",
               "missing": "✗ missing", "sound": "♪ audio"}

    def _show_detail(self):
        it = self._selected_item()
        if it is None:
            self.detail_text.setPlainText("")
            self.file_table.setRowCount(0)
            self.art_img.clear()
            self.cameo_img.clear()
            self.tags_edit.clear()
            self.tags_edit.setEnabled(False)
            self._clear_audio()
            self.preview.setText("Art / audio preview: select an item.")
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
        if entry is None:
            self.detail_text.setPlainText(f"[{it.name}] — no detail available "
                                          "(source not scanned).")
            self.file_table.setRowCount(0)
            self.preview.setText("")
            return
        self.detail_text.setPlainText(unit_report(entry))
        self._fill_files(entry.assets)
        self._render_art(entry, it.source_id, scan)
        self._render_cameo(entry, it.source_id, scan)
        self._populate_audio(entry, scan)
        self.preview.setText("")

    def _render_art(self, entry, sid, scan):
        """Best-effort SHP/VXL still preview into art_img. Never raises."""
        self.art_img.clear()
        if scan is None or scan.index is None:
            self.art_img.setText("(no art to preview)")
            return
        cat = scan.index.catalog

        def _resolve(name):
            found, _ = resolve_media(cat, [name])
            return found.get(name) or next(
                (v for k, v in found.items() if k.lower() == name.lower()), None)

        shp = next((a.name for a in entry.assets
                    if a.name.lower().endswith(".shp")
                    and a.status in ("present", "vanilla")), None)
        vxl = next((a.name for a in entry.assets
                    if a.name.lower().endswith(".vxl")
                    and a.status in ("present", "vanilla")), None)
        try:
            if shp:
                data = _resolve(shp)
                if not data:
                    self.art_img.setText("(SHP not found in sources)")
                    return
                img = parse_shp(data)
                pal = self._house_palette(self._preview_palette(sid, scan))
                w, h, rgba = frame_to_rgba(
                    img, img.frames[best_frame_index(img)], pal)
                self._set_art_pixmap(w, h, rgba)
                return
            if vxl:
                # composite the unit's body + turret + barrel voxels, each placed
                # by its own HVA (frame 0), like the game draws a turreted unit.
                # art.ini TurretOffset shifts the turret/barrel forward (world X).
                base = os.path.splitext(vxl)[0]
                turret_dx = 0.18 * self._turret_offset(entry, scan)
                parts = []
                for suffix in ("", "TUR", "BARL"):
                    vdata = _resolve(f"{base}{suffix}.VXL")
                    if not vdata:
                        continue
                    try:
                        mdl = parse_vxl(vdata)
                    except Exception:
                        continue
                    if voxel_count(mdl) == 0:
                        continue
                    hva = None
                    hdata = _resolve(f"{base}{suffix}.HVA")
                    if hdata:
                        try:
                            hva = parse_hva(hdata)
                        except Exception:
                            hva = None
                    off = (turret_dx, 0.0, 0.0) if suffix in ("TUR", "BARL") \
                        else (0.0, 0.0, 0.0)
                    parts.append((mdl, hva, off))
                if not parts:
                    self.art_img.setText("(VXL not found / empty)")
                    return
                pal = self._house_palette(self._preview_palette(sid, scan))
                w, h, rgba = render_parts(parts, palette=pal,
                                          vpl=self._preview_vpl(sid),
                                          view_deg=self.view_combo.currentData()
                                          or 0)
                self._set_art_pixmap(w, h, rgba)
                return
            self.art_img.setText("(no SHP/VXL art to preview)")
        except Exception as e:
            self.art_img.setText(f"(preview failed: {type(e).__name__})")

    def _render_cameo(self, entry, sid, scan):
        """Show the unit's cameo (PCX or SHP) from its art Cameo= field. Never
        raises."""
        self.cameo_img.clear()
        art_map = getattr(getattr(scan, "index", None), "art_map", None)
        cat = getattr(getattr(scan, "index", None), "catalog", None)
        art_name = getattr(entry, "art_name", "") or getattr(entry, "name", "")
        if not art_map or not cat or not art_name:
            return
        cameo = get_val(art_map, art_name, "Cameo")
        cameo = str(cameo).strip() if cameo else ""
        if not cameo or cameo.lower() == "none":
            return

        def _resolve(name):
            found, _ = resolve_media(cat, [name])
            return found.get(name) or next(
                (v for k, v in found.items() if k.lower() == name.lower()), None)
        try:
            pdata = _resolve(cameo.upper() + ".PCX")
            if pdata:
                w, h, rgba = parse_pcx(pdata)
            else:
                sdata = _resolve(cameo.upper() + ".SHP")
                if not sdata:
                    return
                img = parse_shp(sdata)
                pal = self._house_palette(self._preview_palette(sid, scan))
                w, h, rgba = frame_to_rgba(
                    img, img.frames[best_frame_index(img)], pal)
            qimg = QImage(bytes(rgba), w, h, QImage.Format_RGBA8888).copy()
            pm = QPixmap.fromImage(qimg)
            if pm.width() > 68 or pm.height() > 68:
                pm = pm.scaled(68, 68, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.cameo_img.setPixmap(pm)
        except Exception:
            self.cameo_img.clear()

    def _set_art_pixmap(self, w, h, rgba):
        qimg = QImage(bytes(rgba), w, h, QImage.Format_RGBA8888).copy()
        pm = QPixmap.fromImage(qimg)
        if pm.width() > 240 or pm.height() > 200:
            pm = pm.scaled(240, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.art_img.setPixmap(pm)

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

    _PAL_NAMES = ("unittem.pal", "temperat.pal", "isotemp.pal", "cameo.pal")

    def _preview_palette(self, sid, scan):
        if sid in self._pal_cache:
            return self._pal_cache[sid]
        pal = None
        src = next((s for s in self.sources if s.id == sid), None)
        folder = os.path.dirname(src.rules_path) if src else ""
        for name in self._PAL_NAMES:
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                try:
                    pal = load_palette(open(p, "rb").read())
                    break
                except Exception:
                    pal = None
        if pal is None and scan is not None and scan.index is not None:
            try:
                found, _ = resolve_media(scan.index.catalog, ["unittem.pal"])
                for k, v in found.items():
                    if k.lower() == "unittem.pal":
                        pal = load_palette(v)
                        break
            except Exception:
                pal = None
        if pal is None:
            pal = grayscale_palette()
        self._pal_cache[sid] = pal
        return pal

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
        if scan is None or scan.aimd_map is None:
            self.detail_text.setPlainText(f"{it.name} — no aimd found for source.")
            self.preview.setText("")
            return
        try:
            ent = extract_bundle(scan.aimd_map, it.name)
        except Exception as e:
            self.detail_text.setPlainText(f"{it.name}: {type(e).__name__}: {e}")
            self.preview.setText("")
            return
        L = [f"[AITriggerTypes] {ent.trigger_id}", f"  {ent.trigger_def}", ""]
        for b in ent.blocks:
            L.append(f"[{b.id}]  ; {b.kind}")
            L += [f"  {k}={v}" for k, v in b.keys]
            L.append("")
        if ent.units:
            L.append("units fielded: " + ", ".join(ent.units))
        self.detail_text.setPlainText("\n".join(L))
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
        seen = set()
        for a in entry.assets:
            if a.status != "sound":
                continue
            loc = cat.get(a.name.upper())
            if loc is not None and loc[0] == "bag" and a.name.upper() not in seen:
                seen.add(a.name.upper())
                self.audio_combo.addItem(a.name, a.name)
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

    # ── data ─────────────────────────────────────────────────────────────────
    def _reload(self, keep_row=None):
        self.sources, self.collections = load_catalog()
        self._items_by_source = {}
        self._scan_by_source = {}
        self._pal_cache = {}
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
        self.col_list.addItem(QListWidgetItem(f"All sources  ({total})"))
        self._col_desc.append(("all", None))
        for s in self.sources:
            n = len(self._items_by_source.get(s.id, []))
            self.col_list.addItem(QListWidgetItem(f"📁 {s.label}  ({n})"))
            self._col_desc.append(("auto", s.id))
        for c in self.collections:
            if c.auto_source:
                continue
            self.col_list.addItem(QListWidgetItem(f"★ {c.name}  ({len(c.items)})"))
            self._col_desc.append(("col", c.name))
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
        return True

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


def _find_audio_player():
    """Best-effort system WAV player (QtMultimedia isn't bundled). Returns an
    identifier used by _play_wav_file, or None."""
    if sys.platform == "win32":
        return "winsound"
    if sys.platform == "darwin":
        return shutil.which("afplay")
    for cmd in ("paplay", "aplay", "ffplay", "mpv", "cvlc"):
        p = shutil.which(cmd)
        if p:
            return p
    return None


def _play_wav_file(player, path):
    """Play a WAV file asynchronously via the detected player."""
    if player == "winsound":
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        return
    args = [player, path]
    if player.endswith("ffplay"):
        args = [player, "-nodisp", "-autoexit", path]
    elif player.endswith("cvlc"):
        args = [player, "--play-and-exit", path]
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class _AddSourceDialog(QDialog):
    """Pick a mod to add. The path field accepts a pasted FOLDER or rules file;
    Browse uses the non-native dialog with hidden (dot-)folders shown, so paths
    like …/.wine/… are reachable. Mode is chosen here too."""

    def __init__(self, parent, start=""):
        super().__init__(parent)
        self.setWindowTitle("Add source to library")
        self.resize(640, 220)
        self.rules_path = ""
        self.mode = "reference"

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Paste a mod folder or a rules(md).ini path, or Browse:"))
        row = QHBoxLayout()
        self.edit = QLineEdit(start)
        self.edit.setPlaceholderText(
            "/home/you/…/mod   or   /home/you/…/rulesmd.ini")
        self.edit.setClearButtonEnabled(True)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.edit, 1)
        row.addWidget(browse)
        lay.addLayout(row)

        self.ref = QRadioButton("Reference — read live from its folder "
                                "(breaks if the folder moves/deletes)")
        self.snap = QRadioButton("Snapshot — copy rules/aimd/art/sound into a "
                                 "private library dir (self-contained)")
        self.ref.setChecked(True)
        lay.addWidget(self.ref)
        lay.addWidget(self.snap)

        self.err = QLabel("")
        self.err.setStyleSheet("color:#c62828;")
        lay.addWidget(self.err)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _browse(self):
        start = self.edit.text().strip() or QDir.homePath()
        if os.path.isfile(start):
            start = os.path.dirname(start)
        dlg = QFileDialog(self, "Choose a mod's rules(md).ini", start)
        dlg.setOption(QFileDialog.DontUseNativeDialog, True)   # Qt dialog = paste + hidden
        dlg.setFileMode(QFileDialog.ExistingFile)
        dlg.setNameFilters(["Rules INI (rules*.ini)", "All files (*)"])
        dlg.setFilter(dlg.filter() | QDir.Hidden)              # show .wine etc.
        if dlg.exec() == QFileDialog.Accepted:
            files = dlg.selectedFiles()
            if files:
                self.edit.setText(files[0])

    def _resolve(self, text):
        p = os.path.expanduser(text.strip().strip('"').strip("'"))
        if not p:
            return "", "Enter a folder or rules file path."
        if os.path.isdir(p):
            low = {f.lower(): f for f in os.listdir(p)}
            for n in ("rulesmd.ini", "rules.ini"):
                if n in low:
                    return os.path.join(p, low[n]), ""
            return "", "No rulesmd.ini / rules.ini in that folder."
        if os.path.isfile(p):
            return p, ""
        return "", "That path doesn't exist."

    def _accept(self):
        path, err = self._resolve(self.edit.text())
        if err:
            self.err.setText(err)
            return
        self.rules_path = path
        self.mode = "reference" if self.ref.isChecked() else "snapshot"
        self.accept()
