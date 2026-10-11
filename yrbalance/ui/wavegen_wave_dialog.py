"""
Generate Waves dialog — the full working AI chain (TaskForces + Scripts + TeamTypes
+ AITriggerTypes) via the vendored WaveGenerator, driven by a form. Configure wave
counts / difficulties / scale / options, Generate → preview the generated chain,
Insert into aimd.ini (backup kept). Unlike the TaskForce dialog this writes the
whole chain atomically, so the generated taskforces actually run in-game.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QSpinBox, QDoubleSpinBox, QCheckBox, QPlainTextEdit, QMessageBox,
    QApplication, QGroupBox,
)

from ..engine import make_backup
from ..wavegen.generate import waves_preview_text, insert_waves, wave_counts


class WaveGenDialog(QDialog):
    def __init__(self, parent, rules_path, aimd_path, on_inserted=None):
        super().__init__(parent)
        self.rules_path = rules_path
        self.aimd_path = aimd_path
        self.on_inserted = on_inserted
        self._gen = None
        self.setWindowTitle("Generate Waves (TaskForces + Scripts + Teams + Triggers)")
        self.resize(860, 660)

        root = QVBoxLayout(self)
        cfg = QGroupBox("Wave configuration")
        g = QGridLayout(cfg)
        g.addWidget(QLabel("General waves / difficulty:"), 0, 0)
        self.general = QSpinBox()
        self.general.setRange(0, 100)
        self.general.setValue(10)
        g.addWidget(self.general, 0, 1)
        g.addWidget(QLabel("Country waves / difficulty:"), 0, 2)
        self.country = QSpinBox()
        self.country.setRange(0, 100)
        self.country.setValue(5)
        g.addWidget(self.country, 0, 3)

        g.addWidget(QLabel("Difficulties:"), 1, 0)
        drow = QHBoxLayout()
        self.diff_boxes = {}
        for dname in ("Easy", "Medium", "Hard"):
            cb = QCheckBox(dname)
            cb.setChecked(True)
            self.diff_boxes[dname] = cb
            drow.addWidget(cb)
        drow.addStretch(1)
        from PySide6.QtWidgets import QWidget
        dw = QWidget()
        dw.setLayout(drow)
        g.addWidget(dw, 1, 1, 1, 3)

        g.addWidget(QLabel("Taskforce scale:"), 2, 0)
        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.1, 10.0)
        self.scale.setSingleStep(0.1)
        self.scale.setValue(1.0)
        g.addWidget(self.scale, 2, 1)
        g.addWidget(QLabel("Seed:"), 2, 2)
        self.seed = QSpinBox()
        self.seed.setRange(0, 999999)
        self.seed.setValue(42)
        g.addWidget(self.seed, 2, 3)

        orow = QHBoxLayout()
        self.diversify = QCheckBox("Diversify mono taskforces")
        self.diversify.setChecked(True)
        self.gunner = QCheckBox("Gunner IFVs for Hard infantry")
        self.gunner.setChecked(True)
        self.debug = QCheckBox("Debug messages (AIExt)")
        orow.addWidget(self.diversify)
        orow.addWidget(self.gunner)
        orow.addWidget(self.debug)
        orow.addStretch(1)
        gen_btn = QPushButton("Generate")
        gen_btn.clicked.connect(self._generate)
        orow.addWidget(gen_btn)
        ow = QWidget()
        ow.setLayout(orow)
        g.addWidget(ow, 3, 0, 1, 4)
        root.addWidget(cfg)

        self.summary = QLabel("Configure and Generate.")
        root.addWidget(self.summary)
        root.addWidget(QLabel("<b>Generated chain (preview)</b>"))
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

    def _cfg(self):
        diffs = [d for d, cb in self.diff_boxes.items() if cb.isChecked()]
        return dict(
            general_waves_per_difficulty=self.general.value(),
            country_waves_per_difficulty=self.country.value(),
            difficulties=diffs or ["Hard"],
            taskforce_scale=self.scale.value(),
            diversify_mono=self.diversify.isChecked(),
            gunner_hard_infantry=self.gunner.isChecked(),
            debug_messages=self.debug.isChecked(),
        )

    def _generate(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._gen, text = waves_preview_text(
                self.rules_path, self.aimd_path, self._cfg(),
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
            f"{c['scripts']} scripts, {c['teams']} teamtypes — review below, then "
            "Insert.")
        self.insert_btn.setEnabled(c["triggers"] > 0)

    def _insert(self):
        if not self.aimd_path or not os.path.isfile(self.aimd_path):
            QMessageBox.information(self, "Insert", "No aimd.ini to write to.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            make_backup(self.aimd_path, None, keep=10)
            cnt = insert_waves(self.rules_path, self.aimd_path, self._cfg(),
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
