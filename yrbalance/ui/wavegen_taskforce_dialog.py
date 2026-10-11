"""
Generate TaskForces dialog — drive the vendored role-based composer from a form:
pick factions × phases × roles (+ count / min-grade / seed), generate, preview
each taskforce's composition, tick the ones you want, and insert them into the
loaded aimd.ini (backup kept). Powerful composer, comfortable preview-then-insert.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMessageBox, QApplication, QGroupBox,
    QCheckBox, QPlainTextEdit, QSplitter, QWidget,
)

from ..engine import make_backup
from ..wavegen.generate import (
    taskforce_vocab, mod_factions, generate_taskforces, entry_composition,
    entry_section_text,
)


def _checkable_list(items, checked=()):
    lw = QListWidget()
    lw.setSelectionMode(QAbstractItemView.NoSelection)
    for it in items:
        item = QListWidgetItem(it)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if it in checked else Qt.Unchecked)
        lw.addItem(item)
    return lw


def _checked(lw):
    return [lw.item(i).text() for i in range(lw.count())
            if lw.item(i).checkState() == Qt.Checked]


class TaskforceGenDialog(QDialog):
    def __init__(self, parent, rules_path, aimd_path, on_inserted=None):
        super().__init__(parent)
        self.rules_path = rules_path
        self.aimd_path = aimd_path
        self.on_inserted = on_inserted
        self._gen = None
        self._parser = None
        self._entries = []
        self.setWindowTitle("Generate TaskForces")
        self.resize(900, 640)

        vocab = taskforce_vocab()
        try:
            factions = mod_factions(rules_path)
        except Exception:
            factions = []

        root = QVBoxLayout(self)

        # ── config form ───────────────────────────────────────────────────────
        cfg = QGroupBox("What to generate (faction × phase × role)")
        cg = QGridLayout(cfg)
        self.fac_list = _checkable_list(factions, checked=factions[:1])
        self.phase_list = _checkable_list(vocab["phases"], checked=["Mid"])
        self.role_list = _checkable_list(vocab["roles"],
                                         checked=["GENERAL_ATTACK"])
        for col, (title, lw) in enumerate(
                [("Factions", self.fac_list), ("Phases", self.phase_list),
                 ("Roles", self.role_list)]):
            cg.addWidget(QLabel(f"<b>{title}</b>"), 0, col)
            lw.setMaximumHeight(150)
            cg.addWidget(lw, 1, col)
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Count per combo:"))
        self.count = QSpinBox()
        self.count.setRange(1, 50)
        self.count.setValue(3)
        opts.addWidget(self.count)
        opts.addWidget(QLabel("Min grade:"))
        self.min_grade = QSpinBox()
        self.min_grade.setRange(0, 100)
        self.min_grade.setValue(0)
        opts.addWidget(self.min_grade)
        opts.addWidget(QLabel("Seed:"))
        self.seed = QSpinBox()
        self.seed.setRange(0, 999999)
        self.seed.setValue(42)
        opts.addWidget(self.seed)
        self.suicide = QCheckBox("Allow suicide rushes")
        opts.addWidget(self.suicide)
        opts.addStretch(1)
        gen_btn = QPushButton("Generate")
        gen_btn.clicked.connect(self._generate)
        opts.addWidget(gen_btn)
        cg.addLayout(opts, 2, 0, 1, 3)
        root.addWidget(cfg)

        # ── preview (table + section text) ────────────────────────────────────
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("<b>Generated taskforces</b> — tick to insert"))
        hdr.addStretch(1)
        all_btn = QPushButton("All")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn = QPushButton("None")
        none_btn.clicked.connect(lambda: self._set_all(False))
        hdr.addWidget(all_btn)
        hdr.addWidget(none_btn)
        ll.addLayout(hdr)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["✓", "Name", "Faction/Phase/Role/Diff", "Units", "Composition"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.currentCellChanged.connect(lambda *_: self._show_section())
        ll.addWidget(self.table, 1)
        split.addWidget(left)

        self.section = QPlainTextEdit()
        self.section.setReadOnly(True)
        self.section.setFont(QFont("monospace"))
        split.addWidget(self.section)
        split.setSizes([620, 280])
        root.addWidget(split, 1)

        row = QHBoxLayout()
        self.status = QLabel("")
        row.addWidget(self.status, 1)
        self.insert_btn = QPushButton("Insert ticked into aimd.ini")
        self.insert_btn.setEnabled(False)
        self.insert_btn.clicked.connect(self._insert)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(self.insert_btn)
        row.addWidget(close)
        root.addLayout(row)

    # ── generate ──────────────────────────────────────────────────────────────
    def _specs(self):
        specs = []
        count = self.count.value()
        mg = self.min_grade.value()
        suic = self.suicide.isChecked()
        for fac in _checked(self.fac_list):
            for phase in _checked(self.phase_list):
                for role in _checked(self.role_list):
                    specs.append({
                        "faction": fac, "phase": phase, "role": role,
                        "count": count, "min_grade": mg,
                        "allow_suicide_easy": suic,
                        "allow_suicide_medium": suic,
                        "allow_suicide_hard": suic,
                    })
        return specs

    def _generate(self):
        specs = self._specs()
        if not specs:
            self.status.setText("Pick at least one faction, phase and role.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._gen, self._parser, self._entries = generate_taskforces(
                self.rules_path, self.aimd_path, specs, seed=self.seed.value())
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Generate", f"{type(e).__name__}: {e}")
            return
        QApplication.restoreOverrideCursor()
        self._fill_table()
        self.insert_btn.setEnabled(bool(self._entries))
        self.status.setText(f"{len(self._entries)} taskforce(s) generated from "
                            f"{len(specs)} spec(s).")

    def _fill_table(self):
        self.table.setRowCount(len(self._entries))
        for r, e in enumerate(self._entries):
            chk = QTableWidgetItem()
            chk.setFlags(chk.flags() | Qt.ItemIsUserCheckable)
            chk.setCheckState(Qt.Checked)
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, QTableWidgetItem(e.name))
            self.table.setItem(r, 2, QTableWidgetItem(
                f"{e.faction}/{e.phase}/{e.role}/{e.difficulty}"))
            self.table.setItem(r, 3, QTableWidgetItem(
                str(sum(c for c, _ in e.units))))
            self.table.setItem(r, 4, QTableWidgetItem(
                entry_composition(e, self._parser)))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        if self._entries:
            self.table.selectRow(0)
            self._show_section()

    def _show_section(self):
        r = self.table.currentRow()
        if 0 <= r < len(self._entries):
            self.section.setPlainText(entry_section_text(self._entries[r]))

    def _set_all(self, on):
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _checked_entries(self):
        return [self._entries[r] for r in range(self.table.rowCount())
                if self.table.item(r, 0).checkState() == Qt.Checked]

    def _insert(self):
        chosen = self._checked_entries()
        if not chosen:
            self.status.setText("Tick at least one taskforce to insert.")
            return
        if not self.aimd_path or not os.path.isfile(self.aimd_path):
            QMessageBox.information(self, "Insert", "No aimd.ini to write to.")
            return
        try:
            make_backup(self.aimd_path, None, keep=10)
            self._gen.write_to_ini(chosen, self.aimd_path)
        except Exception as e:
            QMessageBox.critical(self, "Insert", f"{type(e).__name__}: {e}")
            return
        QMessageBox.information(
            self, "Insert",
            f"Inserted {len(chosen)} taskforce(s) into "
            f"{os.path.basename(self.aimd_path)} (backup kept).")
        if self.on_inserted:
            self.on_inserted()
        self.accept()
