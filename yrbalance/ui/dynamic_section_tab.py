"""
DynamicSectionTab — lists every tag of a rules section, populated from the LOADED
rules with each tag's current value as the default. Tick a row (or just edit its
value, which auto-ticks it) to include it; only ticked rows are applied.

Used for the General tab ([General]) and the Gameplay tab (a section picker over
[CombatDamage] etc.). Optional fixed "extra toggles" emit canned script snippets
(e.g. the voice-restore option on Gameplay).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QCheckBox, QComboBox, QScrollArea, QGroupBox,
)


class DynamicSectionTab(QWidget):
    def __init__(self, sections=("General",), editable_section=False,
                 exclude=None, help_text="", extra_toggles=None, parent=None):
        super().__init__(parent)
        self.exclude = {e.lower() for e in (exclude or [])}
        self._section_map = None
        self._rows = []  # (apply_cb, tag, value_edit, original)

        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Section:"))
        self.section_combo = QComboBox()
        self.section_combo.setEditable(editable_section)
        for s in sections:
            self.section_combo.addItem(s)
        self.section_combo.currentIndexChanged.connect(self._repopulate)
        if editable_section:
            self.section_combo.lineEdit().editingFinished.connect(self._repopulate)
        top.addWidget(self.section_combo, 1)
        outer.addLayout(top)

        if help_text:
            h = QLabel(help_text)
            h.setWordWrap(True)
            h.setStyleSheet("color: gray;")
            outer.addWidget(h)

        filt = QHBoxLayout()
        filt.addWidget(QLabel("Filter:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("type to filter tags…")
        self.search.textChanged.connect(self._apply_filter)
        filt.addWidget(self.search, 1)
        outer.addLayout(filt)

        # Extra fixed toggles (id, label, script) — e.g. restore voices.
        self.toggles = {}
        if extra_toggles:
            tbox = QGroupBox("Options")
            tlay = QVBoxLayout(tbox)
            for toggle_id, label, script in extra_toggles:
                cb = QCheckBox(label)
                tlay.addWidget(cb)
                self.toggles[toggle_id] = (cb, script)
            outer.addWidget(tbox)

        # Scrollable rows.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.rows_widget = QWidget()
        self.grid = QGridLayout(self.rows_widget)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnStretch(2, 1)
        self.scroll.setWidget(self.rows_widget)
        outer.addWidget(self.scroll, 1)

        self.status = QLabel("Load a rules file to populate this section.")
        self.status.setStyleSheet("color: gray;")
        outer.addWidget(self.status)

    # ── Population ───────────────────────────────────────────────────────────
    def set_rules(self, section_map):
        self._section_map = section_map
        self._repopulate()

    def _repopulate(self):
        self._clear_rows()
        if self._section_map is None:
            self.status.setText("Load a rules file to populate this section.")
            return
        section = self.section_combo.currentText().strip()
        s = self._section_map.get(section.lower())
        if s is None:
            self.status.setText(f"[{section}] not found in the loaded rules.")
            return
        header = QLabel("apply"), QLabel("tag"), QLabel("value")
        for col, w in enumerate(header):
            self.grid.addWidget(w, 0, col)
        count = 0
        for _klow, (li, kr, vr) in sorted(s["keys"].items(), key=lambda x: x[1][0]):
            if kr.lower() in self.exclude:
                continue
            self._add_row(kr, vr.split(";")[0].strip())
            count += 1
        self.status.setText(f"[{section}]: {count} tag(s). Tick or edit a value "
                            f"to include it.")
        self._apply_filter()

    def _clear_rows(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._rows = []

    def _add_row(self, tag, value):
        row = self.grid.rowCount()
        apply_cb = QCheckBox()
        label = QLabel(tag)
        value_edit = QLineEdit(value)
        value_edit.textEdited.connect(
            lambda _t, cb=apply_cb: cb.setChecked(True))
        self.grid.addWidget(apply_cb, row, 0, Qt.AlignCenter)
        self.grid.addWidget(label, row, 1)
        self.grid.addWidget(value_edit, row, 2)
        self._rows.append((apply_cb, tag, value_edit, value))

    def _apply_filter(self):
        needle = self.search.text().strip().lower()
        for apply_cb, tag, value_edit, _orig in self._rows:
            show = needle in tag.lower() if needle else True
            apply_cb.setVisible(show)
            value_edit.setVisible(show)
            # The label is at column 1 of the same row; find and toggle it.
        # Toggle labels too.
        for i in range(self.grid.count()):
            w = self.grid.itemAt(i).widget()
            if isinstance(w, QLabel) and w.text() not in ("apply", "tag", "value"):
                w.setVisible((needle in w.text().lower()) if needle else True)

    # ── Compile to script ────────────────────────────────────────────────────
    def to_script_text(self):
        parts = []
        section = self.section_combo.currentText().strip()
        used = [(tag, ve.text().strip())
                for (cb, tag, ve, _orig) in self._rows
                if cb.isChecked() and tag]
        if section and used:
            parts.append(f"[{section}]\n"
                         + "".join(f"{t} = {v}\n" for t, v in used))
        for _label, (cb, script) in self.toggles.items():
            if cb.isChecked():
                parts.append(script.strip() + "\n")
        return "\n".join(parts)

    # ── State (blueprints) ───────────────────────────────────────────────────
    def get_state(self):
        return {
            "section": self.section_combo.currentText().strip(),
            "rows": [(cb.isChecked(), tag, ve.text())
                     for (cb, tag, ve, _o) in self._rows if cb.isChecked()],
            "toggles": {label: cb.isChecked()
                        for label, (cb, _s) in self.toggles.items()},
        }

    def set_state(self, state):
        if state.get("section"):
            if self.section_combo.isEditable():
                self.section_combo.setCurrentText(state["section"])
        # Overlay saved ticked rows on top of whatever is populated.
        saved = {tag: (used, val) for used, tag, val in state.get("rows", [])}
        for cb, tag, ve, _orig in self._rows:
            if tag in saved:
                used, val = saved[tag]
                ve.setText(val)
                cb.setChecked(bool(used))
        # Rows not currently present (rules not loaded) — add them so they apply.
        present = {tag for _cb, tag, _ve, _o in self._rows}
        for used, tag, val in state.get("rows", []):
            if tag not in present:
                self._add_row(tag, val)
                self._rows[-1][0].setChecked(bool(used))
        for label, checked in state.get("toggles", {}).items():
            if label in self.toggles:
                self.toggles[label][0].setChecked(bool(checked))
