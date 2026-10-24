"""
HistoryTab — a log of the tool's changes to the loaded rules, discovered by
scanning the inline `; vanilla=` annotations. Group by category/section/tag,
filter by category, and undo individual changes (double-click or "Undo selected");
reverted rows get a checkmark and go italic. Save the reverted rules, or a
vanilla-reference copy.

Date grouping and multi-patch history are noted as future work.
"""

from ..engine.ini_model import guard_ini_text
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox, QPushButton,
    QTreeWidget, QTreeWidgetItem, QFileDialog, QMessageBox,
)

from ..engine import scan_changes, revert_change, restore_vanilla

CATEGORIES = ["Infantry", "Vehicle", "Naval", "Aircraft", "Structure",
              "Defense", "General", "AI", "Other"]


class HistoryTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lines = None
        self._section_map = None
        self._config = None
        self._changes = []

        outer = QVBoxLayout(self)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Group by:"))
        self.group_combo = QComboBox()
        for label, data in [("Category", "category"), ("Section", "section"),
                            ("Tag", "tag")]:
            self.group_combo.addItem(label, data)
        self.group_combo.currentIndexChanged.connect(self._rebuild_tree)
        bar.addWidget(self.group_combo)
        bar.addStretch(1)
        self.timeline_btn = QPushButton("Timeline…")
        self.timeline_btn.setToolTip("Full change history for this mod across "
                                     "sessions (loads + applies).")
        self.timeline_btn.clicked.connect(self._show_timeline)
        bar.addWidget(self.timeline_btn)
        self.undo_btn = QPushButton("Undo selected")
        self.undo_btn.clicked.connect(self._undo_selected)
        bar.addWidget(self.undo_btn)
        self.save_btn = QPushButton("Save reverted rules…")
        self.save_btn.clicked.connect(self._save_reverted)
        bar.addWidget(self.save_btn)
        self.vanilla_btn = QPushButton("Save vanilla reference…")
        self.vanilla_btn.clicked.connect(self._save_vanilla)
        bar.addWidget(self.vanilla_btn)
        outer.addLayout(bar)

        filt = QHBoxLayout()
        filt.addWidget(QLabel("Show:"))
        self.cat_checks = {}
        for cat in CATEGORIES:
            cb = QCheckBox(cat)
            cb.setChecked(True)
            cb.toggled.connect(self._rebuild_tree)
            self.cat_checks[cat] = cb
            filt.addWidget(cb)
        filt.addStretch(1)
        outer.addLayout(filt)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["change", "vanilla", "current"])
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.setColumnWidth(0, 340)
        outer.addWidget(self.tree, 1)

        self.status = QLabel("Load a rules file to see its change history.")
        self.status.setStyleSheet("color: gray;")
        outer.addWidget(self.status)

    # ── Data ─────────────────────────────────────────────────────────────────
    def _show_timeline(self):
        win = self.window()
        if hasattr(win, "show_change_timeline"):
            win.show_change_timeline()

    def set_rules(self, lines, section_map, config):
        self._lines = list(lines)   # working copy we can revert in
        self._section_map = section_map
        self._config = config
        self.rescan()

    def rescan(self):
        if self._lines is None:
            return
        self._changes = scan_changes(self._lines, self._section_map, self._config)
        self._rebuild_tree()

    def working_lines(self):
        return self._lines

    # ── Tree ─────────────────────────────────────────────────────────────────
    def _rebuild_tree(self):
        self.tree.clear()
        if self._lines is None:
            return
        group_key = self.group_combo.currentData()
        shown = {c for c, cb in self.cat_checks.items() if cb.isChecked()}
        groups = {}
        total = 0
        for idx, ch in enumerate(self._changes):
            if ch["category"] not in shown:
                continue
            gval = ch.get(group_key, "?")
            groups.setdefault(gval, []).append((idx, ch))
            total += 1

        for gval in sorted(groups):
            parent = QTreeWidgetItem(self.tree, [f"{gval}  ({len(groups[gval])})"])
            parent.setFirstColumnSpanned(True)
            for idx, ch in groups[gval]:
                self._add_leaf(parent, idx, ch)
        self.tree.expandAll()
        if not self._changes:
            self.status.setText(
                "No YRBalanceTool edits found in this file yet. History fills in "
                "after you Apply changes (or when you load a file the tool has "
                "already edited — it reads the '; vanilla=' annotations).")
            return
        reverted = sum(1 for c in self._changes if c.get("_reverted"))
        self.status.setText(
            f"{total} change(s) shown, {len(self._changes)} total, "
            f"{reverted} reverted. Double-click or select + 'Undo selected'.")

    def _add_leaf(self, parent, idx, ch):
        desc = f"[{ch['section']}] {ch['tag']}"
        item = QTreeWidgetItem(parent, [desc, str(ch["vanilla"]), str(ch["current"])])
        item.setData(0, Qt.UserRole, idx)
        if ch.get("_reverted"):
            self._mark_reverted(item)
        return item

    def _mark_reverted(self, item):
        item.setText(0, "✓ " + item.text(0).lstrip("✓ "))
        f = item.font(0)
        f.setItalic(True)
        for col in range(3):
            item.setFont(col, f)

    # ── Undo ─────────────────────────────────────────────────────────────────
    def _on_double_click(self, item, _col):
        self._undo_item(item)

    def _undo_selected(self):
        for item in self.tree.selectedItems():
            self._undo_item(item)

    def _undo_item(self, item):
        idx = item.data(0, Qt.UserRole)
        if idx is None or not (0 <= idx < len(self._changes)):
            return
        ch = self._changes[idx]
        if ch.get("_reverted"):
            return
        if revert_change(self._lines, ch):
            ch["_reverted"] = True
            item.setText(2, str(ch["vanilla"]))
            self._mark_reverted(item)
            self.status.setText("Reverted 1 change (unsaved). "
                                "Use 'Save reverted rules…' to write it.")

    # ── Save ─────────────────────────────────────────────────────────────────
    def _save_reverted(self):
        if self._lines is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save reverted rules", "rulesmd_reverted.ini",
            "INI files (*.ini);;All files (*)")
        if not path:
            return
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(guard_ini_text(self._lines))
        self.status.setText(f"Saved reverted rules to {os.path.basename(path)}.")

    def _save_vanilla(self):
        if self._lines is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save vanilla reference", "rulesmd_vanilla.ini",
            "INI files (*.ini);;All files (*)")
        if not path:
            return
        copy = list(self._lines)
        n = restore_vanilla(copy)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(guard_ini_text(copy))
        QMessageBox.information(
            self, "Vanilla reference",
            f"Reverted {n} annotated values to vanilla and saved a reference to:\n{path}")
