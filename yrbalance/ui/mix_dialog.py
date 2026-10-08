"""
MIX browser — open a .mix, list/extract its files, add/replace/remove, and save
(repack). Reads plain and encrypted mixes; saves as a plain mix, atomically, with
a backup of any file it overwrites.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QTableWidget, QTableWidgetItem, QMessageBox, QAbstractItemView,
    QHeaderView,
)

from ..engine import (
    MixFile, write_mix, make_backup, MixError, is_stock_mix, mix_id,
)

_LMD_ID = mix_id("local mix database.dat")


def _size_str(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n}{u}" if u == "B" else f"{n/1024:.1f}{u}"
        n //= 1024
    return f"{n}B"


class MixBrowserDialog(QDialog):
    def __init__(self, parent=None, path=None):
        super().__init__(parent)
        self.mix = None
        self.path = None
        self.adds = {}            # name -> source file path (staged)
        self.removed = set()      # ids staged for removal
        self.setWindowTitle("MIX browser")
        self.resize(760, 560)

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("open a .mix file…")
        self.path_edit.setReadOnly(True)
        top.addWidget(self.path_edit, 1)
        b = QPushButton("Open…")
        b.clicked.connect(self._open)
        top.addWidget(b)
        root.addLayout(top)

        self.info = QLabel("No mix open.")
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        frow = QHBoxLayout()
        frow.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.textChanged.connect(self._fill)
        frow.addWidget(self.filter_edit, 1)
        root.addLayout(frow)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["name", "id", "size", "type"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        brow = QHBoxLayout()
        for label, fn in (("Extract selected…", self._extract_sel),
                          ("Extract all…", self._extract_all),
                          ("Add / replace files…", self._add),
                          ("Remove selected", self._remove)):
            btn = QPushButton(label)
            btn.clicked.connect(fn)
            brow.addWidget(btn)
        root.addLayout(brow)

        srow = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("color: gray;")
        srow.addWidget(self.status, 1)
        save = QPushButton("Save as…")
        save.clicked.connect(self._save)
        srow.addWidget(save)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        srow.addWidget(close)
        root.addLayout(srow)

        if path:
            self._load(path)

    # ── open ─────────────────────────────────────────────────────────────────
    def _open(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open MIX", "",
                                           "MIX archives (*.mix *.MIX);;All (*)")
        if p:
            self._load(p)

    def _load(self, p):
        try:
            self.mix = MixFile(p)
        except MixError as e:
            QMessageBox.critical(self, "MIX", f"Could not open:\n{e}")
            return
        self.path = p
        self.adds.clear()
        self.removed.clear()
        self.path_edit.setText(p)
        named = sum(1 for e in self.mix.entries if e.name)
        flags = []
        if self.mix.encrypted:
            flags.append("encrypted")
        if self.mix.checksum:
            flags.append("checksum")
        if is_stock_mix(p):
            flags.append("STOCK/framework — editing not recommended")
        self.info.setText(
            f"{len(self.mix.entries)} files, {named} named"
            + (f"  [{', '.join(flags)}]" if flags else ""))
        self._fill()

    def _fill(self):
        if not self.mix:
            return
        flt = self.filter_edit.text().lower().strip()
        rows = []
        for e in self.mix.entries:
            label = e.name or f"{e.id:08X}"
            if flt and flt not in label.lower():
                continue
            rows.append(e)
        self.table.setRowCount(len(rows))
        self._rows = rows
        for r, e in enumerate(rows):
            label = e.name or f"(unnamed)"
            if e.id in self.removed:
                label += "  [removed]"
            typ = os.path.splitext(e.name)[1].lstrip(".") if e.name else "?"
            for c, text in enumerate((label, f"{e.id:08X}",
                                      _size_str(e.size), typ)):
                self.table.setItem(r, c, QTableWidgetItem(text))
        extra = (f"  +{len(self.adds)} to add" if self.adds else "")
        extra += (f"  -{len(self.removed)} to remove" if self.removed else "")
        self.status.setText(("Unsaved changes:" + extra) if extra else "")

    def _selected_entries(self):
        return [self._rows[i.row()] for i in self.table.selectionModel().selectedRows()]

    # ── extract ──────────────────────────────────────────────────────────────
    def _extract_sel(self):
        ents = self._selected_entries()
        if not ents:
            self.status.setText("Select rows to extract.")
            return
        d = QFileDialog.getExistingDirectory(self, "Extract to…")
        if not d:
            return
        n = 0
        for e in ents:
            fn = e.name or f"{e.id:08X}.bin"
            with open(os.path.join(d, fn), "wb") as f:
                f.write(self.mix.read_id(e.id))
            n += 1
        self.status.setText(f"Extracted {n} file(s) to {d}")

    def _extract_all(self):
        if not self.mix:
            return
        d = QFileDialog.getExistingDirectory(self, "Extract all to…")
        if not d:
            return
        written = self.mix.extract_all(d)
        self.status.setText(f"Extracted {len(written)} file(s) to {d}")

    # ── edit (staged) ─────────────────────────────────────────────────────────
    def _add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add / replace files")
        for p in paths:
            self.adds[os.path.basename(p)] = p
        if paths:
            self.status.setText(f"Staged {len(paths)} file(s) to add/replace.")
            self._fill()

    def _remove(self):
        for e in self._selected_entries():
            if e.id in self.removed:
                self.removed.discard(e.id)
            else:
                self.removed.add(e.id)
        self._fill()

    # ── save (repack) ─────────────────────────────────────────────────────────
    def _save(self):
        if not self.mix:
            return
        files = {}
        dropped = 0
        for e in self.mix.entries:
            if e.id in self.removed or e.id == _LMD_ID:
                continue           # the name DB is regenerated by write_mix
            if not e.name:
                dropped += 1
                continue
            files[e.name] = self.mix.read_id(e.id)
        for name, p in self.adds.items():
            with open(p, "rb") as f:
                files[name] = f.read()
        if not files:
            QMessageBox.information(self, "MIX", "Nothing to save.")
            return
        warn = []
        if dropped:
            warn.append(f"{dropped} unnamed file(s) can't be repacked by name and "
                        "will be DROPPED.")
        if self.mix.encrypted:
            warn.append("Saved as a plain (unencrypted) mix.")
        dest, _ = QFileDialog.getSaveFileName(
            self, "Save MIX as…", self.path, "MIX archives (*.mix)")
        if not dest:
            return
        if is_stock_mix(dest):
            if QMessageBox.warning(
                    self, "MIX", f"{os.path.basename(dest)} is a stock/framework "
                    "mix name. Overwrite anyway?",
                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        if warn and QMessageBox.warning(
                self, "MIX", "\n".join(warn) + "\n\nContinue?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            if os.path.isfile(dest):
                make_backup(dest, None, keep=10)
            n = write_mix(dest, files)
        except OSError as e:
            QMessageBox.critical(self, "MIX", f"Could not write:\n{e}")
            return
        QMessageBox.information(self, "MIX",
                               f"Wrote {n} file(s) to {os.path.basename(dest)} "
                               "(backup kept if overwritten).")
        self._load(dest)
