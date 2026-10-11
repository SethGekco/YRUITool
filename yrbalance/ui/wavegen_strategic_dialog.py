"""
Generate Strategic Triggers dialog — condition-reactive AI behaviour the regular
wave generator doesn't emit: the AI watches enemy/own base state (no base defenses,
no anti-air, MCV lost, enemy power/CY down, naval dominance, …) and launches the
matching attack or defense chain. aimd-only — nothing is written to rules.ini.
Pick factions/difficulties, Generate → preview, Insert into aimd.ini (backup kept).
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QSpinBox, QCheckBox, QPlainTextEdit, QMessageBox,
    QApplication, QGroupBox, QAbstractItemView,
)

from ..engine import make_backup
from ..wavegen.generate import (
    mod_factions, strategic_scenarios, strategic_preview_text,
    insert_strategic_triggers, wave_counts,
)


class StrategicTriggerDialog(QDialog):
    def __init__(self, parent, rules_path, aimd_path, on_inserted=None):
        super().__init__(parent)
        self.rules_path = rules_path
        self.aimd_path = aimd_path
        self.on_inserted = on_inserted
        self.setWindowTitle("Generate Strategic Triggers")
        self.resize(880, 680)

        root = QVBoxLayout(self)

        blurb = QLabel(
            "Condition-reactive triggers — the AI reacts to the battlefield "
            "state rather than firing on a fixed timer. Scenarios covered:")
        blurb.setWordWrap(True)
        root.addWidget(blurb)
        scn = QLabel("<ul>" + "".join(
            f"<li><b>{lbl}</b> → {eff}</li>"
            for lbl, eff in strategic_scenarios()) + "</ul>")
        scn.setWordWrap(True)
        scn.setTextFormat(Qt.RichText)
        root.addWidget(scn)

        cfg = QGroupBox("Factions")
        cv = QVBoxLayout(cfg)
        self.fac_list = QListWidget()
        self.fac_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.fac_list.setMaximumHeight(120)
        try:
            facs = mod_factions(rules_path)
        except Exception:
            facs = []
        for f in facs:
            it = QListWidgetItem(f)
            it.setData(Qt.UserRole, f)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.fac_list.addItem(it)
        cv.addWidget(self.fac_list)
        frow = QHBoxLayout()
        all_b = QPushButton("All")
        all_b.clicked.connect(lambda: self._set_all(True))
        none_b = QPushButton("None")
        none_b.clicked.connect(lambda: self._set_all(False))
        frow.addWidget(all_b)
        frow.addWidget(none_b)
        frow.addStretch(1)
        cv.addLayout(frow)
        root.addWidget(cfg)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("Difficulties:"))
        self.diff_boxes = {}
        for dname in ("Easy", "Medium", "Hard"):
            cb = QCheckBox(dname)
            cb.setChecked(True)
            self.diff_boxes[dname] = cb
            opts.addWidget(cb)
        self.phobos = QCheckBox("Phobos script actions")
        self.phobos.setChecked(True)
        self.phobos.setToolTip(
            "Use Phobos transport-drop action (10103,0). Leave on for "
            "Phobos mods; the generator auto-detects but you can override.")
        opts.addSpacing(16)
        opts.addWidget(self.phobos)
        opts.addSpacing(16)
        opts.addWidget(QLabel("Seed:"))
        self.seed = QSpinBox()
        self.seed.setRange(0, 999999)
        self.seed.setValue(42)
        opts.addWidget(self.seed)
        opts.addStretch(1)
        gen_btn = QPushButton("Generate")
        gen_btn.clicked.connect(self._generate)
        opts.addWidget(gen_btn)
        root.addLayout(opts)

        self.summary = QLabel("Pick factions and Generate.")
        root.addWidget(self.summary)
        root.addWidget(QLabel("<b>Generated strategic chain (preview)</b>"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("monospace"))
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        root.addWidget(self.preview, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.insert_btn = QPushButton("Insert into aimd.ini")
        self.insert_btn.setEnabled(False)
        self.insert_btn.clicked.connect(self._insert)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(self.insert_btn)
        row.addWidget(close)
        root.addLayout(row)

    def _set_all(self, on):
        for i in range(self.fac_list.count()):
            self.fac_list.item(i).setCheckState(
                Qt.Checked if on else Qt.Unchecked)

    def _factions(self):
        return [self.fac_list.item(i).data(Qt.UserRole)
                for i in range(self.fac_list.count())
                if self.fac_list.item(i).checkState() == Qt.Checked] or None

    def _diffs(self):
        d = [n for n, cb in self.diff_boxes.items() if cb.isChecked()]
        return d or ["Hard"]

    def _generate(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._gen, text = strategic_preview_text(
                self.rules_path, self.aimd_path, factions=self._factions(),
                difficulties=self._diffs(), phobos=self.phobos.isChecked(),
                seed=self.seed.value())
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Generate", f"{type(e).__name__}: {e}")
            return
        QApplication.restoreOverrideCursor()
        self.preview.setPlainText(text)
        c = wave_counts(self._gen)
        self.summary.setText(
            f"<b>{c['triggers']} triggers</b>, {c['taskforces']} taskforces, "
            f"{c['scripts']} scripts, {c['teams']} teamtypes.")
        self.insert_btn.setEnabled(c["triggers"] > 0)

    def _insert(self):
        if not self.aimd_path or not os.path.isfile(self.aimd_path):
            QMessageBox.information(self, "Insert", "No aimd.ini to write to.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            make_backup(self.aimd_path, None, keep=10)
            cnt = insert_strategic_triggers(
                self.rules_path, self.aimd_path, factions=self._factions(),
                difficulties=self._diffs(), phobos=self.phobos.isChecked(),
                seed=self.seed.value())
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Insert", f"{type(e).__name__}: {e}")
            return
        QApplication.restoreOverrideCursor()
        QMessageBox.information(
            self, "Insert",
            f"Inserted {cnt['triggers']} triggers, {cnt['taskforces']} taskforces, "
            f"{cnt['scripts']} scripts, {cnt['teams']} teamtypes into "
            f"{os.path.basename(self.aimd_path)} (backup kept).")
        if self.on_inserted:
            self.on_inserted()
        self.accept()
