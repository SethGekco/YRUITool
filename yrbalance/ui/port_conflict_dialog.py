"""
Name-conflict review for the porter.

A unit / building / weapon / warhead / projectile the old mod and the loaded
mod both define under the SAME name, differently. Each one is shown three ways
side by side — the loaded mod's (being imported into), the old mod's as
written (being exported from), and the old mod's rebalanced to the loaded mod
— and the user picks: keep the loaded mod's, replace it with either old-mod
version, or keep both by importing either old-mod version under a new name.
Keeping both also lets each ported object that uses the name stay attached to
the loaded mod's version instead of the copy. Identical sections never reach
this dialog; they are simply reused.
"""

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QComboBox, QTextEdit, QSplitter, QWidget, QHeaderView,
    QAbstractItemView,
)

from ..engine import (
    conflict_versions, conflicts_in_selection, conflict_referrers,
    CONFLICT_CHOICES, CONFLICT_KEEP_TARGET, CONFLICT_SOURCE,
    CONFLICT_REBALANCED, CONFLICT_BOTH, CONFLICT_BOTH_SOURCE, ATTACH_COPY,
    ATTACH_TARGET,
)

_KEEP_BOTH = (CONFLICT_BOTH_SOURCE, CONFLICT_BOTH)
_TECHNO_LIST = {"infantry": "InfantryTypes", "vehicle": "VehicleTypes",
                "aircraft": "AircraftTypes", "building": "BuildingTypes",
                "building_dummy": "BuildingTypes"}

_SAME, _DIFF, _ONLY = "#888888", "#c62828", "#1565c0"


def choice_label(choice, conflict):
    return {
        CONFLICT_KEEP_TARGET: "Keep the loaded mod's",
        CONFLICT_SOURCE: "Replace with the old mod's (as written)",
        CONFLICT_REBALANCED: "Replace with the old mod's (rebalanced)",
        CONFLICT_BOTH_SOURCE: f"Keep both — import the old mod's (as written) "
                              f"as [{conflict.new_name}]",
        CONFLICT_BOTH: f"Keep both — import the old mod's (rebalanced) as "
                       f"[{conflict.new_name}]",
    }[choice]


def _kv(lines):
    out = {}
    for ln in lines:
        k, _, v = ln.partition("=")
        out.setdefault(k.strip().lower(), v.strip())
    return out


def _marked_html(lines, other):
    """key=value lines, colored against the other side: grey = same, red =
    different value, blue = only on this side."""
    rows = []
    for ln in lines:
        k, _, v = ln.partition("=")
        kl = k.strip().lower()
        if kl not in other:
            col, mark = _ONLY, "+"
        elif other[kl].lower() != v.strip().lower():
            col, mark = _DIFF, "≠"
        else:
            col, mark = _SAME, " "
        rows.append(f'<span style="color:{col}">{mark} '
                    f'{html.escape(ln)}</span>')
    return ("<pre style='margin:0'>" + "\n".join(rows) + "</pre>"
            if rows else "<i>(empty)</i>")


class PortConflictDialog(QDialog):
    """exec() → accepted; then .resolutions() → {shared name: CONFLICT_*}."""

    def __init__(self, parent, plan, selected, overrides=None,
                 resolutions=None):
        super().__init__(parent)
        self.plan = plan
        self.selected = set(selected)
        self.overrides = overrides
        self.conflicts = conflicts_in_selection(plan, self.selected)
        prior = {k.lower(): v for k, v in (resolutions or {}).items()}
        self._choice, self._attach = {}, {}
        for c in self.conflicts:
            v = prior.get(c.name.lower(), CONFLICT_BOTH)
            if isinstance(v, dict):
                self._attach[c.name] = dict(v.get("attach") or {})
                v = v.get("choice", CONFLICT_BOTH)
            self._choice[c.name] = v
        self._cur = -1
        self.setWindowTitle("Name conflicts — choose per section")
        self.resize(1180, 700)

        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            f"<b>{len(self.conflicts)}</b> unit/building/weapon/warhead/"
            "projectile name(s) in the selection exist in <b>both</b> mods "
            "with different definitions. Pick what happens to each. "
            "Identical ones are reused and not listed. Greyed rows are no "
            "longer used by anything being imported, because of an earlier "
            "choice."))

        split = QSplitter(Qt.Vertical)

        self.table = QTableWidget(len(self.conflicts), 4)
        self.table.setHorizontalHeaderLabels(
            ["name", "kind", "used in the loaded mod by", "choice"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        self._combos = []
        for r, c in enumerate(self.conflicts):
            self.table.setItem(r, 0, QTableWidgetItem(c.name))
            self.table.setItem(r, 1, QTableWidgetItem(c.kind))
            used = ", ".join(c.used_by[:6]) + (
                f" … (+{len(c.used_by) - 6})" if len(c.used_by) > 6 else "")
            self.table.setItem(r, 2, QTableWidgetItem(
                f"{len(c.used_by)}: {used}" if c.used_by else "nothing"))
            box = QComboBox()
            for ch in CONFLICT_CHOICES:
                box.addItem(choice_label(ch, c), ch)
            box.setCurrentIndex(CONFLICT_CHOICES.index(self._choice[c.name]))
            box.currentIndexChanged.connect(
                lambda _i, name=c.name, b=box: self._set(name, b.currentData()))
            self.table.setCellWidget(r, 3, box)
            self._combos.append(box)
        self.table.resizeColumnsToContents()
        self.table.currentCellChanged.connect(lambda r, *_: self._show(r))
        split.addWidget(self.table)

        panes = QWidget()
        pl = QHBoxLayout(panes)
        pl.setContentsMargins(0, 0, 0, 0)
        self._views, self._heads = [], []
        for _ in range(3):
            col = QVBoxLayout()
            head = QLabel()
            head.setWordWrap(True)
            view = QTextEdit()
            view.setReadOnly(True)
            view.setFont(QFont("monospace", 10))
            view.setLineWrapMode(QTextEdit.NoWrap)
            col.addWidget(head)
            col.addWidget(view, 1)
            pl.addLayout(col, 1)
            self._views.append(view)
            self._heads.append(head)
        split.addWidget(panes)
        split.setSizes([260, 420])
        root.addWidget(split, 1)

        self.note = QLabel()
        self.note.setWordWrap(True)
        root.addWidget(self.note)

        # Keep both: which version each ported object that uses it is
        # attached to.
        self.attach_box = QWidget()
        al = QVBoxLayout(self.attach_box)
        al.setContentsMargins(0, 0, 0, 0)
        self.attach_head = QLabel()
        self.attach_head.setWordWrap(True)
        al.addWidget(self.attach_head)
        self.attach_table = QTableWidget(0, 2)
        self.attach_table.setHorizontalHeaderLabels(
            ["ported object using it", "attached to"])
        self.attach_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.attach_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.attach_table.setMaximumHeight(150)
        al.addWidget(self.attach_table)
        root.addWidget(self.attach_box)
        root.addWidget(QLabel(
            f"<span style='color:{_DIFF}'>≠ different value</span> &nbsp; "
            f"<span style='color:{_ONLY}'>+ only on this side</span> &nbsp; "
            f"<span style='color:{_SAME}'>same</span>"))

        row = QHBoxLayout()
        row.addWidget(QLabel("Set every row to:"))
        self.bulk = QComboBox()
        for ch, text in ((CONFLICT_KEEP_TARGET, "Keep the loaded mod's"),
                         (CONFLICT_SOURCE, "Replace — as written"),
                         (CONFLICT_REBALANCED, "Replace — rebalanced"),
                         (CONFLICT_BOTH_SOURCE, "Keep both — as written"),
                         (CONFLICT_BOTH, "Keep both — rebalanced")):
            self.bulk.addItem(text, ch)
        self.bulk.setCurrentIndex(CONFLICT_CHOICES.index(CONFLICT_BOTH))
        row.addWidget(self.bulk)
        b = QPushButton("Apply to all")
        b.clicked.connect(self._apply_all)
        row.addWidget(b)
        row.addStretch(1)
        cancel = QPushButton("Cancel port")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Continue port")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        root.addLayout(row)

        self._refresh_reach()
        if self.conflicts:
            self.table.selectRow(0)
            self._show(0)

    def resolutions(self):
        """{name: choice}, or {name: {"choice", "attach"}} where a keep-both
        has a referrer attached to the loaded mod's version."""
        out = {}
        for name, ch in self._choice.items():
            att = {r: a for r, a in self._attach.get(name, {}).items()
                   if a == ATTACH_TARGET}
            out[name] = ({"choice": ch, "attach": att}
                         if ch in _KEEP_BOTH and att else ch)
        return out

    def _set(self, name, choice):
        self._choice[name] = choice
        self._refresh_reach()
        self._show(self.table.currentRow())

    def _apply_all(self):
        ch = self.bulk.currentData()
        for box in self._combos:
            box.blockSignals(True)
            box.setCurrentIndex(CONFLICT_CHOICES.index(ch))
            box.blockSignals(False)
        for c in self.conflicts:
            self._choice[c.name] = ch
        self._refresh_reach()
        self._show(self.table.currentRow())

    def _refresh_reach(self):
        live = {c.name for c in conflicts_in_selection(
            self.plan, self.selected, self.resolutions())}
        for r, c in enumerate(self.conflicts):
            col = QColor("#000000") if c.name in live else QColor("#9e9e9e")
            for k in range(3):
                it = self.table.item(r, k)
                it.setForeground(col)
                it.setToolTip("" if c.name in live else
                              "no longer used: whatever pointed at it now "
                              "keeps the loaded mod's own")

    def _show(self, r):
        if r < 0 or r >= len(self.conflicts):
            return
        self._cur = r
        c = self.conflicts[r]
        tgt, raw, bal = conflict_versions(self.plan, c, self.overrides)
        kt, kr, kb = _kv(tgt), _kv(raw), _kv(bal)
        same_bal = raw == bal
        heads = [
            f"<b>Loaded mod</b> — [{html.escape(c.name)}] (importing into)",
            f"<b>Old mod, as written</b> — [{html.escape(c.name)}] "
            "(exporting from)",
            f"<b>Old mod, rebalanced</b> — [{html.escape(c.name)}]"
            + (" <i>(same as written at the current factors)</i>"
               if same_bal else ""),
        ]
        bodies = [_marked_html(tgt, kr), _marked_html(raw, kt),
                  _marked_html(bal, kt)]
        for h, v, head, body in zip(self._heads, self._views, heads, bodies):
            h.setText(head)
            v.setHtml(body)
        ch = self._choice[c.name]
        copy = f"[{c.new_name}]"
        reg = _TECHNO_LIST.get(c.kind)
        if ch in (CONFLICT_SOURCE, CONFLICT_REBALANCED) and c.used_by:
            self.note.setText(
                f"⚠ Replacing [{c.name}] changes it for all {len(c.used_by)} "
                f"section(s) of the loaded mod that use it: "
                f"{', '.join(c.used_by)}. A backup is kept.")
        elif ch in _KEEP_BOTH:
            ver = "as written" if ch == CONFLICT_BOTH_SOURCE else "rebalanced"
            what = (f"a new {c.kind} {copy}, registered in [{reg}] and drawn "
                    f"with [{c.name}]'s art" if reg else copy)
            self.note.setText(
                f"The old mod's version ({ver}) is imported as {what}; the "
                f"loaded mod's [{c.name}] is left as it is.")
        elif ch == CONFLICT_KEEP_TARGET:
            self.note.setText(
                f"The loaded mod's [{c.name}] stays; nothing of the old mod's "
                "version is imported" + (", and this unit is not ported."
                                         if reg else "."))
        else:
            self.note.setText(f"[{c.name}] is replaced in place; nothing in "
                              "the loaded mod uses it yet.")
        self._fill_attach(c, ch)

    def _fill_attach(self, c, ch):
        refs = (conflict_referrers(self.plan, c, self.selected,
                                   self.resolutions())
                if ch in _KEEP_BOTH else [])
        self.attach_box.setVisible(bool(refs))
        if not refs:
            return
        self.attach_head.setText(
            f"<b>Attached to</b> — both versions will exist; choose which one "
            f"each ported object that uses [{html.escape(c.name)}] points at.")
        cur = self._attach.setdefault(c.name, {})
        self.attach_table.setRowCount(0)     # drops the previous row widgets
        self.attach_table.setRowCount(len(refs))
        for i, (obj_name, written) in enumerate(refs):
            self.attach_table.setItem(i, 0, QTableWidgetItem(written))
            box = QComboBox()
            box.addItem(f"the old mod's — {c.new_name}", ATTACH_COPY)
            box.addItem(f"the loaded mod's — {c.name}", ATTACH_TARGET)
            box.setCurrentIndex(1 if cur.get(obj_name) == ATTACH_TARGET else 0)
            box.currentIndexChanged.connect(
                lambda _i, n=c.name, o=obj_name, b=box:
                self._attach.setdefault(n, {}).__setitem__(o, b.currentData()))
            self.attach_table.setCellWidget(i, 1, box)
        self.attach_table.resizeColumnToContents(0)
