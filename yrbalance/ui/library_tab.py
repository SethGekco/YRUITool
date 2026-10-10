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
    QRadioButton, QComboBox,
)

from ..engine import (
    load_catalog, add_source, remove_source, set_source_summary,
    create_collection, delete_collection, add_to_collection, scan_source,
    describe_culture, unit_report, extract_bundle, resolve_media,
    parse_shp, load_palette, grayscale_palette, frame_to_rgba, best_frame_index,
    bag_sample_to_wav,
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
        ll.addWidget(self.col_list, 1)
        self.add_to_btn = QPushButton("Add selected → collection…")
        self.add_to_btn.clicked.connect(self._add_selected_to_collection)
        ll.addWidget(self.add_to_btn)
        self.remove_btn = QPushButton("Remove selected folder/collection")
        self.remove_btn.clicked.connect(self._remove_selected)
        ll.addWidget(self.remove_btn)

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
        self.art_img = QLabel()
        self.art_img.setAlignment(Qt.AlignCenter)
        self.art_img.setMinimumHeight(140)
        self.art_img.setStyleSheet(
            "border:1px solid palette(mid); background:palette(base);")
        rl.addWidget(self.art_img)
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
            self._clear_audio()
            self.preview.setText("Art / audio preview: select an item.")
            return
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
        self._populate_audio(entry, scan)
        vxl = [a.name for a in entry.assets if a.name.lower().endswith(".vxl")]
        self.preview.setText("voxel: " + ", ".join(vxl[:4]) + " (VXL render pending)"
                             if vxl else "")

    def _render_art(self, entry, sid, scan):
        """Best-effort SHP still preview into art_img. Never raises."""
        self.art_img.clear()
        shp = next((a.name for a in entry.assets
                    if a.name.lower().endswith(".shp")
                    and a.status in ("present", "vanilla")), None)
        if not shp or scan is None or scan.index is None:
            self.art_img.setText("(no SHP art to preview)")
            return
        try:
            found, _missing = resolve_media(scan.index.catalog, [shp])
            data = found.get(shp) or next(
                (v for k, v in found.items() if k.lower() == shp.lower()), None)
            if not data:
                self.art_img.setText("(SHP not found in sources)")
                return
            img = parse_shp(data)
            pal = self._preview_palette(sid, scan)
            w, h, rgba = frame_to_rgba(img, img.frames[best_frame_index(img)], pal)
            qimg = QImage(bytes(rgba), w, h, QImage.Format_RGBA8888).copy()
            pm = QPixmap.fromImage(qimg)
            if pm.width() > 240 or pm.height() > 180:
                pm = pm.scaled(240, 180, Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
            self.art_img.setPixmap(pm)
        except Exception as e:
            self.art_img.setText(f"(SHP preview failed: {type(e).__name__})")

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

    def _detail_ai(self, it, scan):
        self.file_table.setRowCount(0)
        self.art_img.clear()
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
        self._rebuild_collections(keep_row)
        self._rebuild_tables()
        self._apply_filter()

    def _all_items(self):
        for sid, items in self._items_by_source.items():
            for it in items:
                yield it

    def _source_label(self, sid):
        s = next((x for x in self.sources if x.id == sid), None)
        return s.label if s else sid

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
                tbl.setItem(r, 1, QTableWidgetItem(self._source_label(it.source_id)))
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
        if self.hide_nonbuild.isChecked() and ob != "buildable":
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
        for kind, title in _KIND_ORDER:
            tbl, items = self._tables[kind]
            shown = 0
            for r, it in enumerate(items):
                vis = pred(it) and self._passes_checks(it) and (
                    not text or text in it.name.lower()
                    or text in self._source_label(it.source_id).lower()
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
