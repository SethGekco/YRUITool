"""
Mod encyclopedia dialog — load one or more mods, search units across them, see a
unit's stats / weapon chain / art+sound / asset locations (loose or which .mix) /
missing-vs-vanilla / obtainability, and export a unit (code + assets) into a
library folder.
"""

import os

from PySide6.QtCore import Qt, QThread, Signal, QSettings
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QComboBox, QTableWidget, QTableWidgetItem, QPlainTextEdit,
    QMessageBox, QSplitter, QAbstractItemView, QHeaderView, QApplication,
)

from ..engine import (
    build_mod_index, search_indexes, unit_report, export_unit, vanilla_asset_ids,
    load_bookmarks, add_bookmark, unit_history, effective_tags,
    add_unit_tag, remove_unit_tag, bulk_add_tag, bulk_remove_tag, load_tags,
    all_tags,
)
from ..ai.eva import plan_tags
from ..ai.ollama_client import OllamaClient

_KINDS = ["infantry", "vehicle", "aircraft", "building"]
_OBTAIN = ["buildable", "crate", "other", "dummy", "civilian", "unknown"]

_STATUS_COLOR = {"present": "#2e7d32", "vanilla": "#888888", "missing": "#c62828"}
_OBTAIN_COLOR = {"buildable": "#2e7d32", "crate": "#1565c0", "other": "#6a1b9a",
                 "dummy": "#999999", "civilian": "#8d6e63", "unknown": "#c62828"}


class _Worker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")


class EncyclopediaDialog(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.mw = main_window
        self.indexes = []
        self._worker = None
        self.setWindowTitle("Mod encyclopedia")
        self.resize(940, 680)

        root = QVBoxLayout(self)

        top = QHBoxLayout()
        add = QPushButton("Add mod…")
        add.clicked.connect(self._add_mod)
        top.addWidget(add)
        top.addWidget(QLabel("Vanilla (for asset check):"))
        self.van_edit = QLineEdit()
        self.van_edit.setPlaceholderText("optional — a vanilla install folder's "
                                         "rulesmd.ini")
        top.addWidget(self.van_edit, 1)
        vb = QPushButton("…")
        vb.setMaximumWidth(32)
        vb.clicked.connect(self._browse_vanilla)
        top.addWidget(vb)
        root.addLayout(top)

        # bookmarks (a library of mod sources)
        brow = QHBoxLayout()
        brow.addWidget(QLabel("Bookmarked mods:"))
        self.bm_box = QComboBox()
        self.bm_box.setMinimumWidth(220)
        brow.addWidget(self.bm_box, 1)
        load_bm = QPushButton("Load")
        load_bm.clicked.connect(self._load_bookmark)
        brow.addWidget(load_bm)
        star = QPushButton("★ Bookmark loaded")
        star.clicked.connect(self._bookmark_loaded)
        brow.addWidget(star)
        root.addLayout(brow)
        self._refresh_bookmarks()

        self.mods_label = QLabel("No mods loaded.")
        self.mods_label.setWordWrap(True)
        root.addWidget(self.mods_label)

        frow = QHBoxLayout()
        frow.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("unit name or obtain class…")
        self.search_edit.textChanged.connect(self._refresh)
        frow.addWidget(self.search_edit, 1)
        frow.addWidget(QLabel("Kind:"))
        self.kind_box = QComboBox()
        self.kind_box.addItems(["(all)", "infantry", "vehicle", "aircraft",
                                "building"])
        self.kind_box.currentIndexChanged.connect(self._refresh)
        frow.addWidget(self.kind_box)
        frow.addWidget(QLabel("Mod:"))
        self.mod_box = QComboBox()
        self.mod_box.addItem("(all)")
        self.mod_box.currentIndexChanged.connect(self._refresh)
        frow.addWidget(self.mod_box)
        frow.addWidget(QLabel("Tag:"))
        self.tag_filter = QLineEdit()
        self.tag_filter.setMaximumWidth(140)
        self.tag_filter.setPlaceholderText("e.g. obtain:crate")
        self.tag_filter.textChanged.connect(self._refresh)
        frow.addWidget(self.tag_filter)
        root.addLayout(frow)

        # tag editing: one tag, applied to the selected unit or all shown
        trow = QHBoxLayout()
        trow.addWidget(QLabel("Tag:"))
        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("a label, e.g. reviewed / from-oldmod / OP")
        trow.addWidget(self.tag_edit, 1)
        for label, fn in (("Tag selected", self._tag_selected),
                          ("Untag selected", self._untag_selected),
                          ("Tag all shown", self._tag_all_shown)):
            btn = QPushButton(label)
            btn.clicked.connect(fn)
            trow.addWidget(btn)
        root.addLayout(trow)

        # Eva-driven tagging: describe what to tag, Eva works out the filter
        erow = QHBoxLayout()
        erow.addWidget(QLabel("Ask Eva to tag:"))
        self.eva_edit = QLineEdit()
        self.eva_edit.setPlaceholderText(
            "e.g. tag all crate-only vehicles as needs-review")
        self.eva_edit.returnPressed.connect(self._eva_tag)
        erow.addWidget(self.eva_edit, 1)
        self.eva_btn = QPushButton("Ask Eva")
        self.eva_btn.clicked.connect(self._eva_tag)
        erow.addWidget(self.eva_btn)
        root.addLayout(erow)

        split = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["unit", "mod", "kind", "obtain",
                                              "tags"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._show_detail)
        split.addWidget(self.table)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(QFont("monospace", 10))
        split.addWidget(self.detail)
        split.setSizes([460, 480])
        root.addWidget(split, 1)

        self.status = QLabel("Add a mod to begin.")
        self.status.setStyleSheet("color: gray;")
        root.addWidget(self.status)

        brow = QHBoxLayout()
        self.export_btn = QPushButton("Export unit…")
        self.export_btn.clicked.connect(self._export)
        self.copy_btn = QPushButton("Copy report")
        self.copy_btn.clicked.connect(self._copy)
        self.porter_btn = QPushButton("Open in Porter as source")
        self.porter_btn.clicked.connect(self._send_to_porter)
        brow.addWidget(self.export_btn)
        brow.addWidget(self.copy_btn)
        brow.addWidget(self.porter_btn)
        brow.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        brow.addWidget(close)
        root.addLayout(brow)

        default_van = os.path.expanduser(
            "~/.yrbalancetool/knowledge/read/vanilla_yr_rulesmd.ini")
        if os.path.isfile(default_van):
            self.van_edit.setText(default_van)
        if self.mw.source_path:
            self._load_index(self.mw.source_path)

    # ── mods ─────────────────────────────────────────────────────────────────
    def _browse_vanilla(self):
        p, _ = QFileDialog.getOpenFileName(self, "Vanilla rulesmd.ini", "",
                                           "Rules (*.ini);;All (*)")
        if p:
            self.van_edit.setText(p)

    def _add_mod(self):
        p, _ = QFileDialog.getOpenFileName(self, "Mod rulesmd.ini", "",
                                           "Rules (*.ini);;All (*)")
        if p:
            self._load_index(p)

    def _vanilla_ids(self):
        v = self.van_edit.text().strip()
        d = os.path.dirname(v) if v else ""
        return vanilla_asset_ids([d]) if d and os.path.isdir(d) else set()

    def _load_index(self, rules_path):
        if any(os.path.samefile(i.rules_path, rules_path)
               for i in self.indexes if os.path.exists(i.rules_path)):
            self.status.setText("That mod is already loaded.")
            return
        self.status.setText(f"Indexing {os.path.basename(os.path.dirname(rules_path))}…")
        self.export_btn.setEnabled(False)
        van_ids = self._vanilla_ids()

        def work():
            return build_mod_index(rules_path, vanilla_ids=van_ids)

        self._worker = _Worker(work)
        self._worker.done.connect(self._index_ready)
        self._worker.failed.connect(
            lambda m: self.status.setText(f"Index failed: {m}"))
        self._worker.start()

    def _index_ready(self, idx):
        self.indexes.append(idx)
        self.export_btn.setEnabled(True)
        labels = [i.label for i in self.indexes]
        self.mods_label.setText("Loaded: " + ", ".join(
            f"{i.label} ({len(i.units)} units)" for i in self.indexes))
        cur = self.mod_box.currentText()
        self.mod_box.blockSignals(True)
        self.mod_box.clear()
        self.mod_box.addItem("(all)")
        self.mod_box.addItems(labels)
        self.mod_box.setCurrentText(cur if cur in ["(all)"] + labels else "(all)")
        self.mod_box.blockSignals(False)
        self._refresh()

    # ── list / detail ─────────────────────────────────────────────────────────
    def _refresh(self):
        kind = self.kind_box.currentText()
        mod = self.mod_box.currentText()
        results = search_indexes(
            self.indexes, self.search_edit.text(),
            kind=None if kind == "(all)" else kind,
            mod=None if mod == "(all)" else mod,
            tag=self.tag_filter.text() or None)
        self._results = results
        self.table.setRowCount(len(results))
        for r, u in enumerate(results):
            self.table.setItem(r, 0, QTableWidgetItem(u.name))
            self.table.setItem(r, 1, QTableWidgetItem(u.mod))
            self.table.setItem(r, 2, QTableWidgetItem(u.kind))
            ob = QTableWidgetItem(u.obtain)
            ob.setForeground(QColor(_OBTAIN_COLOR.get(u.obtain, "#000")))
            miss = sum(1 for a in u.assets if a.status == "missing")
            if miss:
                ob.setText(f"{u.obtain}  ⚠{miss}")
            self.table.setItem(r, 3, ob)
            self.table.setItem(r, 4, QTableWidgetItem(", ".join(u.tags)))
        self.status.setText(f"{len(results)} unit(s)")

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self._results[rows[0].row()] if rows else None

    def _show_detail(self):
        u = self._selected()
        if not u:
            self.detail.setPlainText("")
            return
        text = unit_report(u)
        idx = next((i for i in self.indexes if i.label == u.mod), None)
        if idx is not None:
            hist = unit_history(idx.rules_path, u.name)
            if hist:
                text += "\n\nhistory:\n" + "\n".join(f"  {h}" for h in hist[:12])
        self.detail.setPlainText(text)

    # ── tags ───────────────────────────────────────────────────────────────────
    def _idx_for(self, u):
        return next((i for i in self.indexes if i.label == u.mod), None)

    def _reload_tags(self, idx):
        stored = load_tags(idx.rules_path)
        for u in idx.units.values():
            u.tags = list(stored.get(u.name.lower(), []))

    def _tag_selected(self):
        u = self._selected()
        t = self.tag_edit.text().strip()
        if not u or not t:
            self.status.setText("Pick a unit and type a tag.")
            return
        idx = self._idx_for(u)
        add_unit_tag(idx.rules_path, u.name, t)
        self._reload_tags(idx)
        self._refresh()
        self._show_detail()
        self.status.setText(f"Tagged {u.name} '{t}'.")

    def _untag_selected(self):
        u = self._selected()
        t = self.tag_edit.text().strip()
        if not u or not t:
            return
        idx = self._idx_for(u)
        remove_unit_tag(idx.rules_path, u.name, t)
        self._reload_tags(idx)
        self._refresh()
        self._show_detail()
        self.status.setText(f"Removed '{t}' from {u.name}.")

    def _units_for_plan(self, plan):
        """Resolve a TagPlan's filters to the matching units across loaded mods."""
        res = search_indexes(self.indexes, query=plan.name_contains,
                             kind=plan.kind or None)
        if plan.obtain:
            res = [u for u in res if u.obtain == plan.obtain]
        if plan.has_tag:
            hl = plan.has_tag.lower()
            res = [u for u in res if any(hl in t for t in effective_tags(u))]
        return res

    def _apply_tag_to_units(self, units, tag, action):
        by_mod = {}
        for u in units:
            by_mod.setdefault(u.mod, []).append(u.name)
        total = 0
        fn = bulk_add_tag if action == "add" else bulk_remove_tag
        for mod, names in by_mod.items():
            idx = next((i for i in self.indexes if i.label == mod), None)
            if idx:
                total += fn(idx.rules_path, names, tag)
                self._reload_tags(idx)
        self._refresh()
        return total

    def _eva_tag(self):
        req = self.eva_edit.text().strip()
        if not req or not self.indexes:
            self.status.setText("Load a mod and describe what to tag.")
            return
        s = QSettings("YRBalanceTool", "YRBalanceTool")
        model = s.value("eva_model", "")
        if not model:
            QMessageBox.information(self, "Eva", "Set up Eva's model in the Eva tab "
                                    "first (Refresh models).")
            return
        client = OllamaClient(base_url=s.value("eva_url", "http://localhost:11434"))
        existing = sorted({t for i in self.indexes for t in all_tags(i.rules_path)})
        try:
            ctx = int(s.value("eva_num_ctx", 8192))
        except (TypeError, ValueError):
            ctx = 8192
        self.eva_btn.setEnabled(False)
        self.status.setText("Eva is working out the filter…")

        def work():
            return plan_tags(client, model, req, _KINDS, _OBTAIN, existing,
                             num_ctx=ctx)

        self._tag_worker = _Worker(work)
        self._tag_worker.done.connect(self._eva_tag_ready)
        self._tag_worker.failed.connect(
            lambda m: (self.eva_btn.setEnabled(True),
                       self.status.setText(f"Eva failed: {m}")))
        self._tag_worker.start()

    def _eva_tag_ready(self, plan):
        self.eva_btn.setEnabled(True)
        if plan.error:
            self.status.setText(plan.error)
            return
        units = self._units_for_plan(plan)
        if not units:
            self.status.setText(
                f"Eva's filter (name~'{plan.name_contains}', kind={plan.kind or '-'}, "
                f"obtain={plan.obtain or '-'}) matched no units.")
            return
        verb = "add" if plan.action == "add" else "remove"
        sample = ", ".join(u.name for u in units[:15])
        resp = QMessageBox.question(
            self, "Eva tagging",
            f"{verb.capitalize()} tag '{plan.tag}' on {len(units)} unit(s)?\n\n"
            f"filter: name~'{plan.name_contains or '-'}'  kind={plan.kind or '-'}  "
            f"obtain={plan.obtain or '-'}  has_tag={plan.has_tag or '-'}\n\n"
            f"{sample}{'…' if len(units) > 15 else ''}")
        if resp != QMessageBox.Yes:
            self.status.setText("Cancelled.")
            return
        n = self._apply_tag_to_units(units, plan.tag, plan.action)
        self.status.setText(f"Eva {verb}ed '{plan.tag}' on {n} unit(s).")

    def _tag_all_shown(self):
        t = self.tag_edit.text().strip()
        if not t or not self._results:
            self.status.setText("Type a tag; it applies to every unit shown.")
            return
        by_mod = {}
        for u in self._results:
            by_mod.setdefault(u.mod, []).append(u.name)
        total = 0
        for mod, names in by_mod.items():
            idx = next((i for i in self.indexes if i.label == mod), None)
            if idx:
                total += bulk_add_tag(idx.rules_path, names, t)
                self._reload_tags(idx)
        self._refresh()
        self.status.setText(f"Tagged {total} unit(s) '{t}'.")

    # ── bookmarks / bridge ─────────────────────────────────────────────────────
    def _refresh_bookmarks(self):
        self.bm_box.clear()
        for b in load_bookmarks():
            tag = "" if b["exists"] else "  (missing)"
            self.bm_box.addItem(f"{b['label']}{tag}", b["rules_path"])

    def _load_bookmark(self):
        p = self.bm_box.currentData()
        if p and os.path.isfile(p):
            self._load_index(p)
        elif p:
            self.status.setText("Bookmarked file no longer exists.")

    def _bookmark_loaded(self):
        for i in self.indexes:
            add_bookmark(i.rules_path, i.label)
        self._refresh_bookmarks()
        self.status.setText(f"Bookmarked {len(self.indexes)} loaded mod(s).")

    def _send_to_porter(self):
        u = self._selected()
        idx = next((i for i in self.indexes if i.label == u.mod), None) if u else None
        if idx is None:
            self.status.setText("Select a unit whose mod to use as porter source.")
            return
        from .port_dialog import PortDialog
        if not self.mw.source_path:
            QMessageBox.information(self, "Porter",
                                   "Load a target rules file first (the mod you're "
                                   "importing INTO).")
            return
        dlg = PortDialog(self.mw)
        dlg.src_edit.setText(idx.rules_path)
        dlg.show()

    def _copy(self):
        u = self._selected()
        if u:
            QApplication.clipboard().setText(unit_report(u))
            self.status.setText(f"Copied {u.name} report to clipboard.")

    # ── export ─────────────────────────────────────────────────────────────────
    def _export(self):
        u = self._selected()
        if not u:
            self.status.setText("Select a unit to export.")
            return
        idx = next((i for i in self.indexes if i.label == u.mod), None)
        if idx is None:
            return
        dest = QFileDialog.getExistingDirectory(self, "Export unit into library…")
        if not dest:
            return
        try:
            summ = export_unit(idx, u.name, dest)
        except Exception as e:
            QMessageBox.critical(self, "Export", f"{type(e).__name__}: {e}")
            return
        msg = (f"Exported {u.name}:\n  {summ['ini']} + weapon chain\n"
               f"  {len(summ['media'])} media file(s)")
        if summ["vanilla_assets"]:
            msg += f"\n  {len(summ['vanilla_assets'])} vanilla asset(s) skipped (in game)"
        if summ["missing"]:
            msg += (f"\n  ⚠ {len(summ['missing'])} missing: "
                    f"{', '.join(summ['missing'][:5])}")
        QMessageBox.information(self, "Export", msg)
        self.status.setText(f"Exported {u.name} → {dest}")
