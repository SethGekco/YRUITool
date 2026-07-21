"""
ScriptPanel — the bottom custom-script area: a monospace editor, a saved-script
dropdown, and Import / Export / Preview. This is where AttachEffect / platform /
host-squad generator scripts and any custom formula tweaks live.

The panel emits nothing itself; the main window reads .text() during Apply and
save, and calls preview via the controller.
"""

import os

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QComboBox, QFileDialog, QMessageBox, QDialog, QTextBrowser, QDialogButtonBox,
)
from PySide6.QtGui import QFont

from .help_text import SCRIPT_HELP_HTML

_PLACEHOLDER = (
    "[$INFANTRY, $VEHICLE]\n"
    "Cost = TechLevel * 100\n"
    "Soylent = Cost * 0.5\n"
)


class ScriptPanel(QWidget):
    preview_requested = Signal()

    def __init__(self, presets_dirs=None, parent=None):
        super().__init__(parent)
        self.presets_dirs = presets_dirs or []
        self._preset_paths = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        header.addWidget(QLabel("Custom script"))
        self.preset_box = QComboBox()
        self.preset_box.setMinimumWidth(180)
        self.preset_box.currentIndexChanged.connect(self._load_selected_preset)
        header.addWidget(self.preset_box)
        header.addStretch(1)

        self.help_btn = QPushButton("?")
        self.help_btn.setFixedWidth(28)
        self.help_btn.setToolTip("What can I type here?")
        self.help_btn.clicked.connect(self.show_help)
        self.import_btn = QPushButton("Import…")
        self.export_btn = QPushButton("Export…")
        self.preview_btn = QPushButton("Preview")
        self.import_btn.clicked.connect(self.import_script)
        self.export_btn.clicked.connect(self.export_script)
        self.preview_btn.clicked.connect(self.preview_requested.emit)
        for b in (self.help_btn, self.import_btn, self.export_btn, self.preview_btn):
            header.addWidget(b)
        layout.addLayout(header)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(_PLACEHOLDER)
        self.editor.setFont(QFont("monospace", 10))
        self.editor.setTabChangesFocus(False)
        layout.addWidget(self.editor)

        hint = QLabel("Formulas auto-detect: TechLevel * 100 computes; "
                      "Elite = yes is literal. See docs/SCRIPTS.md.")
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)

        self.reload_presets()

    # ── Presets dropdown ─────────────────────────────────────────────────────
    def reload_presets(self):
        self.preset_box.blockSignals(True)
        self.preset_box.clear()
        self._preset_paths.clear()
        self.preset_box.addItem("— saved scripts —", None)
        for d in self.presets_dirs:
            if not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                if fn.endswith(".yrs"):
                    path = os.path.join(d, fn)
                    name = self._script_name(path) or fn
                    self.preset_box.addItem(name, path)
                    self._preset_paths[name] = path
        self.preset_box.blockSignals(False)

    @staticmethod
    def _script_name(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    s = line.strip()
                    if s.lower().startswith("#name:"):
                        return s.split(":", 1)[1].strip()
                    if s and not s.startswith("#"):
                        break
        except OSError:
            pass
        return None

    def _load_selected_preset(self, index):
        path = self.preset_box.itemData(index)
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                self.editor.setPlainText(f.read())
        except OSError as e:
            QMessageBox.warning(self, "Import", f"Could not read:\n{e}")

    # ── Import / Export ──────────────────────────────────────────────────────
    def import_script(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import script", "", "YRBalanceTool scripts (*.yrs);;All files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                self.editor.setPlainText(f.read())
        except OSError as e:
            QMessageBox.warning(self, "Import", f"Could not read:\n{e}")

    def export_script(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export script", "modifier.yrs",
            "YRBalanceTool scripts (*.yrs);;All files (*)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".yrs"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.editor.toPlainText())
        except OSError as e:
            QMessageBox.warning(self, "Export", f"Could not write:\n{e}")

    def text(self):
        return self.editor.toPlainText()

    def show_help(self):
        show_script_help(self)


def show_script_help(parent):
    """Open the custom-script help dialog (shared by the panel and the Help menu)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Custom script help")
    dlg.resize(640, 620)
    lay = QVBoxLayout(dlg)
    browser = QTextBrowser()
    browser.setOpenExternalLinks(False)
    browser.setHtml(SCRIPT_HELP_HTML)
    lay.addWidget(browser)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dlg.reject)
    buttons.accepted.connect(dlg.accept)
    lay.addWidget(buttons)
    dlg.exec()
