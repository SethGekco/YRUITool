"""Per-file opt-in dialog for a source mod's [#include] / [$include] graph.

Split out of port_dialog.py (cleanup C3). Shows the include tree with a checkbox
per file; returns the set of selected file identifiers (abs path for loose,
``mix:<name>`` for packed), or None for "all" (nothing deselected)."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem,
    QPushButton,
)


class _IncludeSelectDialog(QDialog):
    """Per-file opt-in for the source mod's [#include] / [$include] graph. Shows the
    include tree with a checkbox per file (checked = pull it in). Missing files are
    shown disabled; mix-packed and cyclic files are labelled. Returns the set of
    selected file identifiers (abs path for loose, ``mix:<name>`` for packed), or
    None for "all" (nothing deselected)."""

    def __init__(self, parent, graph):
        super().__init__(parent)
        self.setWindowTitle("Source includes — choose which to import")
        self.resize(560, 420)
        self._graph = graph
        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "Tick the included files to pull into the analysis. Unticking a file "
            "also skips anything it includes. Missing files can't be selected."))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["include file", "source"])
        self.tree.setColumnWidth(0, 360)
        root.addWidget(self.tree, 1)

        # children of the root node are the actual includes (skip the root file).
        for child in graph.get("children", []):
            self._add(self.tree.invisibleRootItem(), child)
        self.tree.expandAll()

        brow = QHBoxLayout()
        all_b = QPushButton("All")
        all_b.clicked.connect(lambda: self._set_all(True))
        none_b = QPushButton("None")
        none_b.clicked.connect(lambda: self._set_all(False))
        brow.addWidget(all_b)
        brow.addWidget(none_b)
        brow.addStretch(1)
        ok = QPushButton("OK")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        brow.addWidget(ok)
        brow.addWidget(cancel)
        root.addLayout(brow)

    def _add(self, parent, node):
        present = node.get("present", True)
        cyc = node.get("cycle", False)
        src = ("missing" if not present else
               "cycle" if cyc else
               "in mix" if node.get("in_mix") else "loose")
        it = QTreeWidgetItem(parent, [node.get("name", "?"), src])
        it.setData(0, Qt.UserRole, node.get("file"))
        if present and not cyc:
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked)
        else:
            it.setFlags(Qt.ItemIsEnabled)   # non-checkable, greyed
        for c in node.get("children", []):
            self._add(it, c)

    def _iter(self, parent=None):
        parent = parent or self.tree.invisibleRootItem()
        for i in range(parent.childCount()):
            it = parent.child(i)
            yield it
            yield from self._iter(it)

    def _set_all(self, on):
        for it in self._iter():
            if it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(0, Qt.Checked if on else Qt.Unchecked)

    def selection(self):
        """Set of selected file ids, or None if every checkable file is checked
        (i.e. nothing was deselected → equivalent to 'all')."""
        sel, any_off = set(), False
        for it in self._iter():
            if not (it.flags() & Qt.ItemIsUserCheckable):
                continue
            if it.checkState(0) == Qt.Checked:
                fid = it.data(0, Qt.UserRole)
                if fid:
                    sel.add(fid)
            else:
                any_off = True
        return sel if any_off else None
