"""
SectionFormTab — a structured editor for tags on a single rules section
(e.g. [General], a super-weapon section). Each row is an "apply" checkbox, a tag
name, and a value; only ticked rows with a tag are emitted.

It compiles to modifier-script text ("[Section]\nTag = value\n…"), so it runs
through the same engine as the custom-script box — meaning values can be literals
(0.9, yes) or formulas that reference the section's own tags (BuildSpeed * 0.9).
Used for the General / Gameplay / Super Weapons tabs.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QCheckBox, QPushButton, QGroupBox,
)


class SectionFormTab(QWidget):
    def __init__(self, section_default="General", seed_rows=None, help_text="",
                 parent=None):
        super().__init__(parent)
        self._rows = []  # (row_widget, use_cb, tag_edit, value_edit)

        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Section:"))
        self.section_edit = QLineEdit(section_default)
        self.section_edit.setToolTip("The rules section these tags are written to, "
                                     "e.g. General or a super-weapon like NUKE.")
        top.addWidget(self.section_edit, 1)
        outer.addLayout(top)

        if help_text:
            hint = QLabel(help_text)
            hint.setWordWrap(True)
            hint.setStyleSheet("color: gray;")
            outer.addWidget(hint)

        self.box = QGroupBox("Tags")
        self.grid = QGridLayout(self.box)
        self.grid.addWidget(QLabel("apply"), 0, 0)
        self.grid.addWidget(QLabel("tag"), 0, 1)
        self.grid.addWidget(QLabel("value"), 0, 2)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnStretch(2, 1)
        outer.addWidget(self.box)

        for tag, value in (seed_rows or []):
            self.add_row(tag, value, used=False)

        btns = QHBoxLayout()
        add_btn = QPushButton("Add tag")
        add_btn.clicked.connect(lambda: self.add_row("", "", used=True))
        btns.addWidget(add_btn)
        btns.addStretch(1)
        outer.addLayout(btns)
        outer.addStretch(1)

    def add_row(self, tag="", value="", used=True):
        row = len(self._rows) + 1
        use_cb = QCheckBox()
        use_cb.setChecked(used)
        tag_edit = QLineEdit(tag)
        tag_edit.setPlaceholderText("BuildSpeed")
        value_edit = QLineEdit(value)
        value_edit.setPlaceholderText("0.9  or  BuildSpeed * 0.9")
        remove = QPushButton("×")
        remove.setFixedWidth(28)
        remove.setToolTip("Remove this row")

        self.grid.addWidget(use_cb, row, 0, Qt.AlignCenter)
        self.grid.addWidget(tag_edit, row, 1)
        self.grid.addWidget(value_edit, row, 2)
        self.grid.addWidget(remove, row, 3)

        entry = [use_cb, tag_edit, value_edit, remove]
        self._rows.append(entry)
        remove.clicked.connect(lambda: self._remove_row(entry))

    def _remove_row(self, entry):
        if entry in self._rows:
            self._rows.remove(entry)
        for w in entry:
            w.setParent(None)
            w.deleteLater()

    def to_script_text(self):
        """Compile ticked, named rows into modifier-script text (or '')."""
        section = self.section_edit.text().strip()
        used = [(t.text().strip(), v.text().strip())
                for (cb, t, v, _rm) in self._rows
                if cb.isChecked() and t.text().strip()]
        if not section or not used:
            return ""
        body = "".join(f"{tag} = {value}\n" for tag, value in used)
        return f"[{section}]\n{body}"

    # ── Blueprint state (used later by the blueprint system) ─────────────────
    def get_state(self):
        return {
            "section": self.section_edit.text().strip(),
            "rows": [(cb.isChecked(), t.text(), v.text())
                     for (cb, t, v, _rm) in self._rows],
        }

    def set_state(self, state):
        self.section_edit.setText(state.get("section", ""))
        for entry in list(self._rows):
            self._remove_row(entry)
        for used, tag, value in state.get("rows", []):
            self.add_row(tag, value, used=used)
