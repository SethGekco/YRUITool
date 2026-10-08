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
    QTableWidgetItem, QDoubleSpinBox, QCheckBox, QMessageBox, QPlainTextEdit,
    QGroupBox, QSplitter, QWidget,
)

from ..engine import (
    parse_ini_lines, build_section_map, build_port_plan, apply_port_plan,
    plan_to_blueprint, make_backup, get_val,
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

        self.analyze_btn = QPushButton("Analyze")
        self.analyze_btn.clicked.connect(self._analyze)
        root.addWidget(self.analyze_btn)

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
        self.tree.setHeaderLabels(["name / kind", "changes", "notes"])
        self.tree.setColumnWidth(0, 300)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemDoubleClicked.connect(self._show_detail)
        tl.addWidget(self.tree)
        self.sel_label = QLabel("0 selected")
        tl.addWidget(self.sel_label)
        split.addWidget(tbox)
        split.setSizes([200, 420])
        root.addWidget(split, 1)

        # ── delivery ────────────────────────────────────────────────────────
        drow = QHBoxLayout()
        self.apply_chk = QCheckBox("Apply to loaded mod (with backup)")
        self.apply_chk.setChecked(True)
        self.bp_chk = QCheckBox("Export blueprint")
        drow.addWidget(self.apply_chk)
        drow.addWidget(self.bp_chk)
        drow.addStretch(1)
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
        self._busy(True)
        self.status.setText("Analyzing… (parsing mods, computing balance)")

        def work():
            sm = build_section_map(parse_ini_lines(src))[0]
            vm = (build_section_map(parse_ini_lines(van))[0]
                  if van and os.path.isfile(van) else None)
            art_p = _sibling(folder, "artmd.ini", "art.ini")
            snd_p = _sibling(folder, "soundmd.ini", "sound.ini")
            am = build_section_map(parse_ini_lines(art_p))[0] if art_p else None
            sn = build_section_map(parse_ini_lines(snd_p))[0] if snd_p else None
            plan = build_port_plan(sm, self.mw.section_map, vm,
                                   config=self.mw.config,
                                   src_art_map=am, src_sound_map=sn)
            return plan, bool(art_p), bool(snd_p)

        self._worker = _Worker(work)
        self._worker.done.connect(self._plan_ready)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _failed(self, msg):
        self._busy(False)
        self.status.setText(f"Analysis failed: {msg}")

    def _plan_ready(self, result):
        self.plan, had_art, had_snd = result
        self._busy(False)
        self._fill_factors()
        self._fill_tree()
        n = len(self.plan.objects)
        extra = []
        if had_art:
            extra.append("artmd found")
        if had_snd:
            extra.append("soundmd found")
        self.status.setText(
            f"{n} importable object(s). Tick what you want, tune ratios, Run. "
            + (" / ".join(extra) if extra else "no art/sound files beside source"))
        self.run_btn.setEnabled(True)

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
                notes = ("⚠ " + str(len(obj.warnings))) if obj.warnings else ""
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

        self.mw._load_rules_path(rules_path)   # refresh the loaded view
        media = sorted(set(summ["media"]))
        extra = (f"\n{len(media)} media file(s) still to copy by hand "
                 f"(e.g. {', '.join(media[:4])}…)" if media else "")
        return (f"Applied: {summ['sections']} section(s), "
                f"{summ['registered']} registered, {summ['art']} art, "
                f"{summ['sound']} sound. Wrote: {', '.join(written)} "
                f"(backups kept).{extra}")


def _write(path, lines):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)
