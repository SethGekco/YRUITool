"""
Generate Everything dialog — the full aimd pipeline in one pass (shared ids):
regular attack waves + recruitment pool teams + procedural extras (base patrols /
tech capture / destroy-tech) + a coverage pass (drop redundant triggers, add filler
waves so every buildable unit appears somewhere) + all applicable specialty waves.
Generate → counts breakdown + attack-chain preview, Insert into aimd.ini (backup).
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QSpinBox, QDoubleSpinBox, QCheckBox, QPlainTextEdit, QMessageBox,
    QApplication, QGroupBox, QWidget,
)

from ..engine import make_backup
from ..wavegen.generate import (
    specialty_wave_types, everything_preview_text, insert_everything,
    everything_counts,
)


class EverythingDialog(QDialog):
    def __init__(self, parent, rules_path, aimd_path, on_inserted=None):
        super().__init__(parent)
        self.rules_path = rules_path
        self.aimd_path = aimd_path
        self.on_inserted = on_inserted
        self.setWindowTitle("Generate Everything (full AI pipeline)")
        self.resize(880, 680)

        root = QVBoxLayout(self)
        cfg = QGroupBox("Pipeline")
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
        dw = QWidget()
        dw.setLayout(drow)
        g.addWidget(dw, 1, 1, 1, 3)

        g.addWidget(QLabel("Scale:"), 2, 0)
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

        inc = QHBoxLayout()
        self.pool = QCheckBox("Pool teams")
        self.patrols = QCheckBox("Base patrols")
        self.capture = QCheckBox("Tech capture")
        self.destroy = QCheckBox("Destroy-tech")
        self.coverage = QCheckBox("Coverage pass")
        self.specialty = QCheckBox("All specialty waves")
        for cb in (self.pool, self.patrols, self.capture, self.destroy,
                   self.coverage, self.specialty):
            cb.setChecked(True)
            inc.addWidget(cb)
        inc.addStretch(1)
        gen_btn = QPushButton("Generate")
        gen_btn.clicked.connect(self._generate)
        inc.addWidget(gen_btn)
        iw = QWidget()
        iw.setLayout(inc)
        g.addWidget(iw, 3, 0, 1, 4)
        root.addWidget(cfg)

        self.summary = QLabel("Configure and Generate.")
        root.addWidget(self.summary)
        root.addWidget(QLabel("<b>Generated attack chain (preview)</b> — pool / "
                              "coverage / patrol infrastructure is added on Insert"))
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

    def _wave_cfg(self):
        diffs = [d for d, cb in self.diff_boxes.items() if cb.isChecked()]
        return dict(
            general_waves_per_difficulty=self.general.value(),
            country_waves_per_difficulty=self.country.value(),
            difficulties=diffs or ["Hard"],
            taskforce_scale=self.scale.value(),
        )

    def _opts(self):
        return dict(pool_teams=self.pool.isChecked(),
                    patrols=self.patrols.isChecked(),
                    capture=self.capture.isChecked(),
                    destroy_tech=self.destroy.isChecked(),
                    coverage=self.coverage.isChecked())

    def _keys(self):
        return [k for k, _l in specialty_wave_types()] \
            if self.specialty.isChecked() else []

    def _generate(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            wg, trg, text = everything_preview_text(
                self.rules_path, self.aimd_path, self._wave_cfg(), self._keys(),
                options=self._opts(), seed=self.seed.value())
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Generate", f"{type(e).__name__}: {e}")
            return
        QApplication.restoreOverrideCursor()
        self._wg, self._trg = wg, trg
        self.preview.setPlainText(text)
        c = everything_counts(wg, trg)
        self.summary.setText(
            f"<b>{c['triggers']} triggers</b>, {c['taskforces']} taskforces, "
            f"{c['scripts']} scripts, {c['teams']} teamtypes total "
            "(incl. pool / coverage / patrols).")
        self.insert_btn.setEnabled(c["triggers"] > 0)

    def _insert(self):
        if not self.aimd_path or not os.path.isfile(self.aimd_path):
            QMessageBox.information(self, "Insert", "No aimd.ini to write to.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            make_backup(self.aimd_path, None, keep=10)
            cnt = insert_everything(
                self.rules_path, self.aimd_path, self._wave_cfg(), self._keys(),
                options=self._opts(), seed=self.seed.value())
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Insert", f"{type(e).__name__}: {e}")
            return
        QApplication.restoreOverrideCursor()
        QMessageBox.information(
            self, "Insert",
            f"Inserted the full pipeline into {os.path.basename(self.aimd_path)} "
            f"(backup kept): {cnt['triggers']} triggers, {cnt['taskforces']} "
            f"taskforces, {cnt['scripts']} scripts, {cnt['teams']} teamtypes.")
        if self.on_inserted:
            self.on_inserted()
        self.accept()
