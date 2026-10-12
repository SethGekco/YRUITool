"""Add-source dialog for the Library tab.

Split out of library_tab.py (cleanup C3). Pick a mod to add: the path field
accepts a pasted FOLDER or rules file; Browse uses the non-native dialog with
hidden (dot-)folders shown, so paths like …/.wine/… are reachable."""

import os

from PySide6.QtCore import QDir
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QRadioButton, QDialogButtonBox, QFileDialog,
)


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
