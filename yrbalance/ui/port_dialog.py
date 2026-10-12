"""
Port mod content dialog — pick an old mod, choose what to import, review the
per-kind balance rescale (editable), and apply (with backup) and/or export a
blueprint.

The heavy analysis runs on the engine's portmod core in a worker thread so the
UI stays responsive on big mods.
"""

import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QTreeWidget, QTreeWidgetItem, QTableWidget,
    QTableWidgetItem, QDoubleSpinBox, QSpinBox, QCheckBox, QMessageBox,
    QPlainTextEdit, QGroupBox, QSplitter, QWidget, QComboBox, QTabWidget,
)

from ..engine import (
    parse_ini_lines, build_section_map, build_port_plan, apply_port_plan,
    plan_to_blueprint, plan_to_files, make_backup, get_val,
    build_media_catalog, resolve_media, pack_media_mixes,
    vanilla_asset_ids, classify_missing, load_bookmarks,
    build_ai_import_plan, apply_ai_import, lint_ai, parse_type_list,
    load_with_includes, include_graph, flatten_graph,
)

_FACTOR_KINDS = ("infantry", "vehicle", "aircraft", "building", "weapon")
_CONF_COLOR = {"high": "#2e7d32", "medium": "#f9a825", "low": "#c62828",
               "none": "#888888"}


def _sibling(folder, *names):
    """First existing file in `folder` matching any of `names` (case-insensitive)."""
    if not folder or not os.path.isdir(folder):
        return None
    low = {f.lower(): f for f in os.listdir(folder)}
    for n in names:
        if n.lower() in low:
            return os.path.join(folder, low[n.lower()])
    return None


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


class _IncludeSelectDialog(QDialog):
    """Per-file opt-in for the source mod's [#include] / [$include] graph. Shows the
    include tree with a checkbox per file (checked = pull it in). Missing files are
    shown disabled; mix-packed and cyclic files are labelled. Returns the set of
    selected file identifiers (abs path for loose, ``mix:<name>`` for packed), or
    None for "all" (nothing deselected)."""

    def __init__(self, parent, graph):
        super().__init__(parent)
        self.setWindowTitle("Source includes — choose which to import")
        self.resize(560, 420)
        self._graph = graph
        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "Tick the included files to pull into the analysis. Unticking a file "
            "also skips anything it includes. Missing files can't be selected."))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["include file", "source"])
        self.tree.setColumnWidth(0, 360)
        root.addWidget(self.tree, 1)

        # children of the root node are the actual includes (skip the root file).
        for child in graph.get("children", []):
            self._add(self.tree.invisibleRootItem(), child)
        self.tree.expandAll()

        brow = QHBoxLayout()
        all_b = QPushButton("All")
        all_b.clicked.connect(lambda: self._set_all(True))
        none_b = QPushButton("None")
        none_b.clicked.connect(lambda: self._set_all(False))
        brow.addWidget(all_b)
        brow.addWidget(none_b)
        brow.addStretch(1)
        ok = QPushButton("OK")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        brow.addWidget(ok)
        brow.addWidget(cancel)
        root.addLayout(brow)

    def _add(self, parent, node):
        present = node.get("present", True)
        cyc = node.get("cycle", False)
        src = ("missing" if not present else
               "cycle" if cyc else
               "in mix" if node.get("in_mix") else "loose")
        it = QTreeWidgetItem(parent, [node.get("name", "?"), src])
        it.setData(0, Qt.UserRole, node.get("file"))
        if present and not cyc:
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked)
        else:
            it.setFlags(Qt.ItemIsEnabled)   # non-checkable, greyed
        for c in node.get("children", []):
            self._add(it, c)

    def _iter(self, parent=None):
        parent = parent or self.tree.invisibleRootItem()
        for i in range(parent.childCount()):
            it = parent.child(i)
            yield it
            yield from self._iter(it)

    def _set_all(self, on):
        for it in self._iter():
            if it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(0, Qt.Checked if on else Qt.Unchecked)

    def selection(self):
        """Set of selected file ids, or None if every checkable file is checked
        (i.e. nothing was deselected → equivalent to 'all')."""
        sel, any_off = set(), False
        for it in self._iter():
            if not (it.flags() & Qt.ItemIsUserCheckable):
                continue
            if it.checkState(0) == Qt.Checked:
                fid = it.data(0, Qt.UserRole)
                if fid:
                    sel.add(fid)
            else:
                any_off = True
        return sel if any_off else None


class PortDialog(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.mw = main_window
        self.plan = None
        self._worker = None
        self._factor_spins = {}   # (kind, stat) -> QDoubleSpinBox
        self.setWindowTitle("Port mod content")
        self.resize(900, 680)

        root = QVBoxLayout(self)

        # ── source / vanilla pickers ────────────────────────────────────────
        pick = QGridLayout()
        pick.addWidget(QLabel("Old mod rulesmd:"), 0, 0)
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("the mod you're importing FROM")
        pick.addWidget(self.src_edit, 0, 1)
        b1 = QPushButton("Browse…")
        b1.clicked.connect(self._browse_source)
        pick.addWidget(b1, 0, 2)
        self.bm_box = QComboBox()
        self.bm_box.addItem("bookmarks…", "")
        for b in load_bookmarks():
            if b["exists"]:
                self.bm_box.addItem(b["label"], b["rules_path"])
        self.bm_box.currentIndexChanged.connect(self._pick_bookmark)
        pick.addWidget(self.bm_box, 0, 3)
        pick.addWidget(QLabel("Vanilla baseline:"), 1, 0)
        self.van_edit = QLineEdit(self._default_vanilla())
        self.van_edit.setPlaceholderText("optional — improves balance confidence")
        pick.addWidget(self.van_edit, 1, 1)
        b2 = QPushButton("Browse…")
        b2.clicked.connect(self._browse_vanilla)
        pick.addWidget(b2, 1, 2)
        root.addLayout(pick)

        tgt = self.mw.source_path or "(none — load a rules file first)"
        root.addWidget(QLabel(f"Importing INTO (loaded): <b>{tgt}</b>"))

        arow = QHBoxLayout()
        self.analyze_btn = QPushButton("Analyze")
        self.analyze_btn.clicked.connect(self._analyze)
        arow.addWidget(self.analyze_btn)
        self.inc_chk = QCheckBox("Resolve source [#include] / [$include] files")
        self.inc_chk.setChecked(True)
        self.inc_chk.setToolTip(
            "Expand the old mod's Ares [#include] / Phobos [$include] graph so "
            "objects defined in included files (country / SW / split rules) are "
            "available to port, not just those in the main rulesmd.ini. Resolves "
            "loose files relative to the source rules, then the mod's .mix "
            "archives; missing ones are reported.")
        self.inc_chk.toggled.connect(self._inc_toggled)
        arow.addWidget(self.inc_chk)
        self.inc_btn = QPushButton("Includes…")
        self.inc_btn.setToolTip(
            "Choose per file which of the source mod's includes to pull in "
            "(default: all).")
        self.inc_btn.clicked.connect(self._choose_includes)
        arow.addWidget(self.inc_btn)
        arow.addStretch(1)
        root.addLayout(arow)
        # None = pull in every include (default); a set = only these file ids.
        self._include_selection = None
        self.src_edit.textChanged.connect(self._src_changed)

        self.status = QLabel("Pick the old mod and press Analyze.")
        self.status.setStyleSheet("color: gray;")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        split = QSplitter(Qt.Vertical)

        # ── factor table (editable "use these ratios") ─────────────────────
        fbox = QGroupBox("Balance rescale per kind — edit any value to override")
        fl = QVBoxLayout(fbox)
        self.factor_table = QTableWidget(0, 4)
        self.factor_table.setHorizontalHeaderLabels(
            ["kind.stat", "factor (×)", "confidence", "samples"])
        self.factor_table.horizontalHeader().setStretchLastSection(True)
        fl.addWidget(self.factor_table)
        split.addWidget(fbox)

        # ── object tree (checkable, grouped by kind) ────────────────────────
        tbox = QGroupBox("Content to import — tick what you want")
        tl = QVBoxLayout(tbox)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("type to filter by name…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        frow.addWidget(self.filter_edit, 1)
        tl.addLayout(frow)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["name / kind", "changes", "obtain / notes"])
        self.tree.setColumnWidth(0, 300)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemDoubleClicked.connect(self._show_detail)
        tl.addWidget(self.tree)
        self.sel_label = QLabel("0 selected")
        tl.addWidget(self.sel_label)
        split.addWidget(tbox)
        split.setSizes([200, 420])
        root.addWidget(split, 1)

        # ── media transfer ──────────────────────────────────────────────────
        mrow = QHBoxLayout()
        self.media_chk = QCheckBox("Copy media into mix:")
        self.media_chk.setChecked(True)
        self.media_chk.setToolTip(
            "Collect the selected objects' .vxl/.hva/.shp/.wav (incl. cameos) from "
            "the source mod's loose files and mixes (vanilla mixes skipped) and pack "
            "them into new mix(es) in the target folder.")
        mrow.addWidget(self.media_chk)
        mrow.addWidget(QLabel("base name:"))
        self.media_base = QLineEdit("ecacheimport")
        self.media_base.setMaximumWidth(140)
        mrow.addWidget(self.media_base)
        mrow.addWidget(QLabel("max files/mix (0 = one):"))
        self.media_limit = QSpinBox()
        self.media_limit.setRange(0, 100000)
        self.media_limit.setValue(0)
        mrow.addWidget(self.media_limit)
        mrow.addStretch(1)
        root.addLayout(mrow)

        # ── delivery ────────────────────────────────────────────────────────
        drow = QHBoxLayout()
        self.apply_chk = QCheckBox("Apply to loaded mod (with backup)")
        self.apply_chk.setChecked(True)
        self.bp_chk = QCheckBox("Export blueprint")
        self.ai_chk = QCheckBox("Also import AI (aimd)")
        self.ai_chk.setToolTip(
            "Bring the source mod's existing AI (TaskForces/Scripts/TeamTypes/"
            "triggers) for the imported units into the target aimd.ini — re-mints "
            "ids, rewrites references, cleans Owner/House. Backup kept.")
        drow.addWidget(self.apply_chk)
        drow.addWidget(self.bp_chk)
        drow.addWidget(self.ai_chk)
        drow.addStretch(1)
        self.raw_btn = QPushButton("Raw INI…")
        self.raw_btn.setToolTip(
            "Show the actual INI text that would be written to each associated "
            "file (rulesmd / artmd / soundmd) for the current selection.")
        self.raw_btn.clicked.connect(self._show_raw_files)
        self.raw_btn.setEnabled(False)
        drow.addWidget(self.raw_btn)
        self.run_btn = QPushButton("Run")
        self.run_btn.clicked.connect(self._run)
        self.run_btn.setEnabled(False)
        drow.addWidget(self.run_btn)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        drow.addWidget(close)
        root.addLayout(drow)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _default_vanilla(self):
        cand = os.path.expanduser(
            "~/.yrbalancetool/knowledge/read/vanilla_yr_rulesmd.ini")
        return cand if os.path.isfile(cand) else ""

    def _pick_bookmark(self):
        p = self.bm_box.currentData()
        if p:
            self.src_edit.setText(p)

    def _browse_source(self):
        start = os.path.dirname(self.src_edit.text()) or ""
        p, _ = QFileDialog.getOpenFileName(
            self, "Old mod rulesmd.ini", start, "Rules (*.ini);;All files (*)")
        if p:
            self.src_edit.setText(p)

    def _browse_vanilla(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Vanilla rulesmd.ini", "", "Rules (*.ini);;All files (*)")
        if p:
            self.van_edit.setText(p)

    def _busy(self, busy):
        self.analyze_btn.setEnabled(not busy)
        self.run_btn.setEnabled(not busy and self.plan is not None)
        self.raw_btn.setEnabled(not busy and self.plan is not None)
        self.inc_btn.setEnabled(not busy and self.inc_chk.isChecked())

    def _src_changed(self, *_):
        # a different source mod → reset the per-file include selection to "all".
        self._include_selection = None

    def _inc_toggled(self, on):
        self.inc_btn.setEnabled(on)
        if not on:
            self._include_selection = None

    def _choose_includes(self):
        src = self.src_edit.text().strip()
        if not src or not os.path.isfile(src):
            QMessageBox.information(
                self, "Includes", "Pick the old mod's rulesmd.ini first.")
            return
        try:
            graph = include_graph(src)
        except Exception as e:
            QMessageBox.critical(self, "Includes", f"{type(e).__name__}: {e}")
            return
        if not graph.get("children"):
            QMessageBox.information(
                self, "Includes",
                "This mod's rulesmd.ini has no [#include] / [$include] files.")
            return
        dlg = _IncludeSelectDialog(self, graph)
        if self._include_selection is not None:
            sel = self._include_selection
            for it in dlg._iter():
                if it.flags() & Qt.ItemIsUserCheckable:
                    fid = it.data(0, Qt.UserRole)
                    it.setCheckState(
                        0, Qt.Checked if fid in sel else Qt.Unchecked)
        if dlg.exec():
            self._include_selection = dlg.selection()
            n = ("all" if self._include_selection is None
                 else str(len(self._include_selection)))
            self.status.setText(
                f"Include selection updated ({n} file(s) will be pulled in). "
                "Press Analyze.")

    # ── analyze ─────────────────────────────────────────────────────────────
    def _analyze(self):
        src = self.src_edit.text().strip()
        if not src or not os.path.isfile(src):
            QMessageBox.information(self, "Port", "Pick the old mod's rulesmd.ini.")
            return
        if not self.mw.source_path or self.mw.section_map is None:
            QMessageBox.information(self, "Port", "Load your target rules first.")
            return
        van = self.van_edit.text().strip()
        folder = os.path.dirname(src)
        resolve_inc = self.inc_chk.isChecked()
        # Per-file opt-in applies to the rules include graph; None = pull all.
        selection = self._include_selection if resolve_inc else None
        allow = (lambda fid: fid in selection) if selection is not None else None
        self._busy(True)
        self.status.setText("Analyzing… (parsing mods, computing balance)")

        def _map(path, allow=None):
            """Section map with includes resolved (when the toggle is on), honouring
            the optional per-file `allow` opt-in."""
            if not path:
                return None, []
            if resolve_inc:
                missing = []
                _l, smap, _o, _s = load_with_includes(
                    path, allow=allow, missing=missing)
                return smap, missing
            return build_section_map(parse_ini_lines(path))[0], []

        def work():
            sm, src_missing = _map(src, allow=allow)      # rules: per-file opt-in
            vm = (build_section_map(parse_ini_lines(van))[0]
                  if van and os.path.isfile(van) else None)
            art_p = _sibling(folder, "artmd.ini", "art.ini")
            snd_p = _sibling(folder, "soundmd.ini", "sound.ini")
            am, _ = _map(art_p) if art_p else (None, [])   # art/sound: fully resolved
            sn, _ = _map(snd_p) if snd_p else (None, [])
            inc_files = []
            if resolve_inc:
                inc_files = [os.path.basename(f) for f in
                             flatten_graph(include_graph(src, allow=allow))][1:]
            plan = build_port_plan(sm, self.mw.section_map, vm,
                                   config=self.mw.config,
                                   src_art_map=am, src_sound_map=sn)
            return plan, bool(art_p), bool(snd_p), inc_files, src_missing

        self._worker = _Worker(work)
        self._worker.done.connect(self._plan_ready)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _failed(self, msg):
        self._busy(False)
        self.status.setText(f"Analysis failed: {msg}")

    def _plan_ready(self, result):
        self.plan, had_art, had_snd, inc_files, src_missing = result
        self._busy(False)
        self._fill_factors()
        self._fill_tree()
        n = len(self.plan.objects)
        extra = []
        if had_art:
            extra.append("artmd found")
        if had_snd:
            extra.append("soundmd found")
        if inc_files:
            extra.append(f"{len(inc_files)} include(s) resolved")
        if src_missing:
            extra.append(f"{len(src_missing)} include(s) missing")
        self.status.setText(
            f"{n} importable object(s). Tick what you want, tune ratios, Run. "
            + (" / ".join(extra) if extra else "no art/sound files beside source"))
        self.run_btn.setEnabled(True)
        self.raw_btn.setEnabled(True)

    def _fill_factors(self):
        rows = []
        for kind in _FACTOR_KINDS:
            for stat, sf in self.plan.factors.get(kind, {}).items():
                if sf.samples:
                    rows.append((kind, stat, sf))
        self.factor_table.setRowCount(len(rows))
        self._factor_spins.clear()
        for r, (kind, stat, sf) in enumerate(rows):
            self.factor_table.setItem(r, 0, QTableWidgetItem(f"{kind}.{stat}"))
            spin = QDoubleSpinBox()
            spin.setRange(0.01, 1000.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setValue(sf.factor)
            self.factor_table.setCellWidget(r, 1, spin)
            self._factor_spins[(kind, stat)] = spin
            citem = QTableWidgetItem(f"{sf.confidence}  ({sf.note})")
            citem.setForeground(Qt.GlobalColor.black)
            from PySide6.QtGui import QColor
            citem.setForeground(QColor(_CONF_COLOR.get(sf.confidence, "#000")))
            self.factor_table.setItem(r, 2, citem)
            self.factor_table.setItem(r, 3, QTableWidgetItem(str(sf.samples)))
        self.factor_table.resizeColumnsToContents()

    def _fill_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        bk = self.plan.by_kind()
        for kind in sorted(bk):
            top = QTreeWidgetItem(self.tree, [f"{kind}  ({len(bk[kind])})", "", ""])
            top.setFlags(top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            top.setCheckState(0, Qt.Unchecked)
            top.setData(0, Qt.UserRole, None)
            for obj in bk[kind]:
                changes = ", ".join(
                    f"{c.stat} {c.old:g}→{c.new:g}" for c in obj.stat_changes)
                oc = obj.obtainability.cls if obj.obtainability else ""
                notes = oc + (f"  ⚠{len(obj.warnings)}" if obj.warnings else "")
                child = QTreeWidgetItem(top, [obj.name, changes, notes])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked)
                child.setData(0, Qt.UserRole, obj.name)
        self.tree.blockSignals(False)
        self._update_sel()

    def _apply_filter(self, text):
        text = text.lower().strip()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            any_vis = False
            for j in range(top.childCount()):
                ch = top.child(j)
                vis = text in ch.text(0).lower()
                ch.setHidden(not vis)
                any_vis = any_vis or vis
            top.setHidden(bool(text) and not any_vis)

    def _item_changed(self, item, col):
        self._update_sel()

    def _update_sel(self):
        self.sel_label.setText(f"{len(self._selected_names())} selected")

    def _selected_names(self):
        names = set()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                ch = top.child(j)
                if ch.checkState(0) == Qt.Checked:
                    names.add(ch.data(0, Qt.UserRole))
        return names

    def _overrides(self):
        # The factor table is the source of truth: every shown value is applied.
        return {k: spin.value() for k, spin in self._factor_spins.items()}

    def _show_detail(self, item, col):
        name = item.data(0, Qt.UserRole)
        if not name:
            return
        obj = next((o for o in self.plan.objects if o.name == name), None)
        if obj is None:
            return
        parts = [f"[{obj.name}]  ({obj.kind})", ""]
        if obj.obtainability is not None:
            ob = obj.obtainability
            also = (" + " + ", ".join(ob.also)) if ob.also else ""
            parts.append(f"  obtain: {ob.cls}{also}  — {'; '.join(ob.reasons)}")
            parts.append("")
        for c in obj.stat_changes:
            parts.append(f"  {c.stat}: {c.old:g} → {c.new:g}  "
                         f"(×{c.factor} {c.confidence})")
        if obj.owner_before:
            parts.append(f"  Owner: {obj.owner_before}  →  {obj.owner_after}")
        if obj.prereq_dropped:
            parts.append(f"  Prerequisite dropped: {', '.join(obj.prereq_dropped)}")
        if obj.art_entry:
            parts.append(f"\n  Art: [{obj.art_entry[0]}] ({len(obj.art_entry[1])} keys)")
        if obj.sound_entries:
            parts.append("  Sound: " + ", ".join(n for n, _ in obj.sound_entries))
        if obj.media_files:
            parts.append("  Media to copy: " + ", ".join(obj.media_files))
        if obj.warnings:
            parts.append("\n  Warnings:")
            parts += [f"   • {w}" for w in obj.warnings]
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{obj.name} — detail")
        dlg.resize(560, 420)
        lay = QVBoxLayout(dlg)
        view = QPlainTextEdit("\n".join(parts))
        view.setReadOnly(True)
        view.setFont(QFont("monospace", 10))
        lay.addWidget(view)
        b = QPushButton("Close")
        b.clicked.connect(dlg.accept)
        lay.addWidget(b)
        dlg.exec()

    def _show_raw_files(self):
        """Tabbed raw INI view: the exact text each associated file receives for the
        current selection (deps expanded, overrides applied)."""
        if self.plan is None:
            return
        files = plan_to_files(self.plan, self._selected_names(), self._overrides())
        if not files:
            QMessageBox.information(
                self, "Raw INI", "Nothing selected to show. Tick some content.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Raw INI — files to be written")
        dlg.resize(840, 640)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(
            "Exactly what each associated file receives for the current selection "
            "(built-in InitialPayload units and overrides included)."))
        tabs = QTabWidget()
        for fn, text in files.items():
            te = QPlainTextEdit(text)
            te.setReadOnly(True)
            te.setFont(QFont("monospace", 10))
            te.setLineWrapMode(QPlainTextEdit.NoWrap)
            tabs.addTab(te, f"{fn}  ({text.count(chr(10)) + 1} lines)")
        lay.addWidget(tabs, 1)
        b = QPushButton("Close")
        b.clicked.connect(dlg.accept)
        lay.addWidget(b)
        dlg.exec()

    # ── run ─────────────────────────────────────────────────────────────────
    def _run(self):
        if self.plan is None:
            return
        selected = self._selected_names()
        if not selected:
            QMessageBox.information(self, "Port", "Tick at least one object.")
            return
        if not (self.apply_chk.isChecked() or self.bp_chk.isChecked()):
            QMessageBox.information(self, "Port", "Choose Apply and/or Export.")
            return
        overrides = self._overrides()
        msgs = []

        if self.bp_chk.isChecked():
            p, _ = QFileDialog.getSaveFileName(
                self, "Export blueprint", "port_blueprint.ini", "INI (*.ini)")
            if p:
                # render the blueprint from only the selected objects
                orig = self.plan.objects
                self.plan.objects = [o for o in orig if o.name in selected]
                try:
                    text = plan_to_blueprint(self.plan)
                finally:
                    self.plan.objects = orig
                open(p, "w", encoding="utf-8").write(text)
                msgs.append(f"Blueprint: {os.path.basename(p)} "
                            f"({len(selected)} objs)")

        if self.apply_chk.isChecked():
            try:
                msgs.append(self._apply(selected, overrides))
            except Exception as e:
                QMessageBox.critical(self, "Port", f"Apply failed: {e}")
                return

        QMessageBox.information(self, "Port", "\n".join(msgs) or "Nothing to do.")

    def _apply(self, selected, overrides):
        rules_path = self.mw.source_path
        folder = os.path.dirname(rules_path)
        tlines = parse_ini_lines(rules_path)
        tmap = build_section_map(tlines)[0]

        # target art/sound (optional) — only if some selected object carries them
        need_art = any(o.art_entry for o in self.plan.objects
                       if o.name in selected)
        need_snd = any(o.sound_entries for o in self.plan.objects
                       if o.name in selected)
        art = snd = None
        art_path = _sibling(folder, "artmd.ini", "art.ini") if need_art else None
        snd_path = _sibling(folder, "soundmd.ini", "sound.ini") if need_snd else None
        if art_path:
            al = parse_ini_lines(art_path)
            art = (al, build_section_map(al)[0])
        if snd_path:
            sl = parse_ini_lines(snd_path)
            snd = (sl, build_section_map(sl)[0])

        summ = apply_port_plan(tlines, tmap, self.plan, selected=selected,
                               overrides=overrides, art=art, sound=snd)

        # back up then write each touched file
        written = []
        make_backup(rules_path, None, keep=10)
        _write(rules_path, tlines)
        written.append(os.path.basename(rules_path))
        if art and summ["art"]:
            make_backup(art_path, None, keep=10)
            _write(art_path, art[0])
            written.append(os.path.basename(art_path))
        if snd and summ["sound"]:
            make_backup(snd_path, None, keep=10)
            _write(snd_path, snd[0])
            written.append(os.path.basename(snd_path))

        media_msg = ""
        if self.media_chk.isChecked():
            media_msg = self._transfer_media(selected, folder)

        ai_msg = ""
        if self.ai_chk.isChecked():
            ai_msg = self._import_ai(selected, folder, tmap)

        self.mw._load_rules_path(rules_path)   # refresh the loaded view
        return (f"Applied: {summ['sections']} section(s), "
                f"{summ['registered']} registered, {summ['art']} art, "
                f"{summ['sound']} sound. Wrote: {', '.join(written)} "
                f"(backups kept).{media_msg}{ai_msg}")

    def _import_ai(self, selected, target_folder, target_rules_map):
        """Transplant the source mod's AI for the ported units into the target's
        aimd.ini (sibling of the target rules). Backup kept; result is linted."""
        techno_kinds = {"infantry", "vehicle", "aircraft", "building",
                        "building_dummy"}
        ported = {o.name for o in self.plan.objects
                  if o.name in selected and o.kind in techno_kinds}
        if not ported:
            return "\nAI: no units selected — skipped."

        src_ai = _sibling(os.path.dirname(self.src_edit.text()),
                          "aimd.ini", "ai.ini")
        tgt_ai = _sibling(target_folder, "aimd.ini", "ai.ini")
        if not src_ai:
            return "\nAI: source mod has no aimd.ini — skipped."
        if not tgt_ai:
            return "\nAI: target has no aimd.ini — skipped."

        src_map = build_section_map(parse_ini_lines(src_ai))[0]
        tl = parse_ini_lines(tgt_ai)
        tgt_map = build_section_map(tl)[0]

        avail = set(ported)
        for reg in ("InfantryTypes", "VehicleTypes", "AircraftTypes",
                    "BuildingTypes"):
            avail.update(parse_type_list(target_rules_map, reg))
        countries = list(self.plan.target_countries)

        plan = build_ai_import_plan(src_map, tgt_map, ported_units=ported,
                                    available_units=avail,
                                    target_countries=countries)
        if not plan.entries:
            return "\nAI: no AI in the source references the imported units."

        make_backup(tgt_ai, None, keep=10)
        summ = apply_ai_import(tl, tgt_map, plan)
        _write(tgt_ai, tl)

        errs = sum(1 for f in lint_ai(build_section_map(tl)[0], target_rules_map)
                   if f.severity == "error")
        notes = sum(1 for e in plan.entries if e.warnings)
        return (f"\nAI: imported {summ['teamtype']} team(s), "
                f"{summ['taskforce']} taskforce(s), {summ['script']} script(s), "
                f"{summ['trigger']} trigger(s) into {os.path.basename(tgt_ai)} "
                f"(backup kept); {notes} with notes; lint: {errs} error(s).")

    def _transfer_media(self, selected, target_folder):
        names = set()
        for o in self.plan.objects:
            if o.name in selected:
                names.update(o.media_files)
        if not names:
            return "\nNo media references for the selected objects."
        src_folder = os.path.dirname(self.src_edit.text().strip())
        catalog = build_media_catalog([src_folder])
        found, missing = resolve_media(catalog, sorted(names))
        if not found:
            return (f"\nMedia: 0 of {len(names)} files found in the source "
                    "(nothing packed).")
        base = self.media_base.text().strip() or "import"
        limit = self.media_limit.value()
        # back up any target mixes we'd overwrite
        for i in range(32):
            cand = os.path.join(target_folder, f"{base}{i:02d}.mix")
            if os.path.isfile(cand):
                make_backup(cand, None, keep=10)
        single = os.path.join(target_folder, f"{base}.mix")
        if os.path.isfile(single):
            make_backup(single, None, keep=10)
        paths = pack_media_mixes(found, target_folder, base, per_mix_limit=limit)
        # Classify what we couldn't find: a reference the mod doesn't ship but
        # vanilla provides is fine (reused stock asset); the rest are worth a warning.
        van_dir = os.path.dirname(self.van_edit.text().strip())
        van_ids = vanilla_asset_ids([van_dir]) if van_dir else set()
        vanilla_miss, truly_miss = classify_missing(missing, van_ids)
        notes = ""
        if vanilla_miss:
            notes += (f"; {len(vanilla_miss)} are vanilla assets (already in the "
                      "game) — fine")
        if truly_miss:
            notes += (f"; ⚠ {len(truly_miss)} missing & not vanilla "
                      f"(e.g. {', '.join(truly_miss[:4])}…) — add these by hand")
        if missing and not van_ids:
            notes += (f"; {len(missing)} not found (set a vanilla baseline to tell "
                      "which are stock assets)")
        return (f"\nMedia: packed {len(found)} file(s) into "
                f"{', '.join(os.path.basename(p) for p in paths)}{notes}")


def _write(path, lines):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)
