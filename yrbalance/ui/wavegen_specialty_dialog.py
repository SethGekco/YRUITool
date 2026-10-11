"""
Generate Specialty Waves dialog — the thematic, unit-specific wave compositions the
WaveGenerator doesn't emit by default (attack-dog harass, Terror Drone / parasite,
BFRT & naval transports, mind control, engineer rush, deploy-to-fire, hero combos,
…). Tick the types, Generate → preview, Insert into aimd.ini (backup kept). Types
whose required units aren't in the mod are skipped.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QSpinBox, QDoubleSpinBox, QCheckBox, QPlainTextEdit, QMessageBox,
    QApplication, QGroupBox, QWidget, QAbstractItemView,
)

from ..engine import make_backup
from ..wavegen.generate import (
    specialty_wave_types, specialty_preview_text, insert_specialty_waves,
    wave_counts,
)


class SpecialtyWaveDialog(QDialog):
    def __init__(self, parent, rules_path, aimd_path, on_inserted=None):
        super().__init__(parent)
        self.rules_path = rules_path
        self.aimd_path = aimd_path
        self.on_inserted = on_inserted
        self.setWindowTitle("Generate Specialty Waves")
        self.resize(860, 660)

        root = QVBoxLayout(self)
        cfg = QGroupBox("Specialty wave types")
        cv = QVBoxLayout(cfg)
        self.type_list = QListWidget()
        self.type_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.type_list.setMaximumHeight(170)
        for key, label in specialty_wave_types():
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.type_list.addItem(it)
        cv.addWidget(self.type_list)

        opts = QHBoxLayout()
        all_b = QPushButton("All")
        all_b.clicked.connect(lambda: self._set_all(True))
        none_b = QPushButton("None")
        none_b.clicked.connect(lambda: self._set_all(False))
        opts.addWidget(all_b)
        opts.addWidget(none_b)
        opts.addSpacing(16)
        opts.addWidget(QLabel("Difficulties:"))
        self.diff_boxes = {}
        for dname in ("Easy", "Medium", "Hard"):
            cb = QCheckBox(dname)
            cb.setChecked(True)
            self.diff_boxes[dname] = cb
            opts.addWidget(cb)
        opts.addWidget(QLabel("Scale:"))
        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.1, 10.0)
        self.scale.setSingleStep(0.1)
        self.scale.setValue(1.0)
        opts.addWidget(self.scale)
        opts.addWidget(QLabel("Seed:"))
        self.seed = QSpinBox()
        self.seed.setRange(0, 999999)
        self.seed.setValue(42)
        opts.addWidget(self.seed)
        opts.addStretch(1)
        gen_btn = QPushButton("Generate")
        gen_btn.clicked.connect(self._generate)
        opts.addWidget(gen_btn)
        cv.addLayout(opts)
        root.addWidget(cfg)

        self.summary = QLabel("Pick types and Generate.")
        root.addWidget(self.summary)
        root.addWidget(QLabel("<b>Generated specialty chain (preview)</b>"))
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
        for i in range(self.type_list.count()):
            self.type_list.item(i).setCheckState(
                Qt.Checked if on else Qt.Unchecked)

    def _keys(self):
        return [self.type_list.item(i).data(Qt.UserRole)
                for i in range(self.type_list.count())
                if self.type_list.item(i).checkState() == Qt.Checked]

    def _diffs(self):
        d = [n for n, cb in self.diff_boxes.items() if cb.isChecked()]
        return d or ["Hard"]

    def _generate(self):
        keys = self._keys()
        if not keys:
            self.summary.setText("Tick at least one specialty type.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._gen, text = specialty_preview_text(
                self.rules_path, self.aimd_path, keys, self._diffs(),
                seed=self.seed.value(), taskforce_scale=self.scale.value())
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Generate", f"{type(e).__name__}: {e}")
            return
        QApplication.restoreOverrideCursor()
        self.preview.setPlainText(text)
        c = wave_counts(self._gen)
        self.summary.setText(
            f"<b>{c['triggers']} triggers</b>, {c['taskforces']} taskforces, "
            f"{c['scripts']} scripts, {c['teams']} teamtypes "
            "(types without the needed units were skipped).")
        self.insert_btn.setEnabled(c["triggers"] > 0)

    def _insert(self):
        if not self.aimd_path or not os.path.isfile(self.aimd_path):
            QMessageBox.information(self, "Insert", "No aimd.ini to write to.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            make_backup(self.aimd_path, None, keep=10)
            cnt = insert_specialty_waves(
                self.rules_path, self.aimd_path, self._keys(), self._diffs(),
                seed=self.seed.value(), taskforce_scale=self.scale.value())
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
