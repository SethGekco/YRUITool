"""
AI (aimd) tab — browse the loaded mod's aimd.ini: its TaskForces, ScriptTypes,
TeamTypes and AITriggerTypes, with live search, a human-readable decode of the
cryptic inline trigger line, each entry's raw definition, and cross-references
(what uses what, double-click to jump). Buttons lint the AI and open the AI
library. Read/browse only — editing goes through the AI library / porter.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTabWidget, QListWidget, QListWidgetItem, QSplitter, QPlainTextEdit,
    QDialog, QMessageBox,
)

from ..engine import (
    build_section_map, load_aimd, ai_registry, decode_trigger, trigger_fields,
    entry_text, references_to, lint_ai, format_findings, has_ai_sections,
)

# (registry key, tab title)
_REG_TABS = [("TaskForces", "TaskForces"), ("ScriptTypes", "Scripts"),
             ("TeamTypes", "TeamTypes"), ("AITriggerTypes", "Triggers")]


class AimdTab(QWidget):
    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        self._loaded_path = None
        self.aimd_map = None
        self._lists = {}          # registry -> QListWidget
        self._ids = {}            # registry -> [ids]

        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.path_label = QLabel("No aimd loaded.")
        bar.addWidget(self.path_label, 1)
        reload_b = QPushButton("Reload")
        reload_b.clicked.connect(self._reload)
        lint_b = QPushButton("Lint AI")
        lint_b.clicked.connect(self._lint)
        lib_b = QPushButton("AI library…")
        lib_b.clicked.connect(self._open_library)
        bar.addWidget(reload_b)
        bar.addWidget(lint_b)
        bar.addWidget(lib_b)
        bar.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(240)
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search)
        root.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        self.reg_tabs = QTabWidget()
        for reg, title in _REG_TABS:
            lst = QListWidget()
            lst.currentRowChanged.connect(lambda _r: self._show_entry())
            self.reg_tabs.addTab(lst, title)
            self._lists[reg] = lst
            self._ids[reg] = []
        self.reg_tabs.currentChanged.connect(lambda _i: self._show_entry())
        split.addWidget(self.reg_tabs)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("<b>Definition</b>"))
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(QFont("monospace"))
        rl.addWidget(self.detail, 3)
        rl.addWidget(QLabel("<b>Referenced by</b> (double-click to jump)"))
        self.refs = QListWidget()
        self.refs.itemDoubleClicked.connect(self._jump_ref)
        rl.addWidget(self.refs, 1)
        split.addWidget(right)
        split.setSizes([320, 560])
        root.addWidget(split, 1)

        self.status = QLabel("")
        root.addWidget(self.status)
        self._shown = False

    def showEvent(self, event):
        super().showEvent(event)
        cur = getattr(self.mw, "source_path", None)
        if cur != self._loaded_path or not self._shown:
            self._shown = True
            self._reload()

    # ── load / fill ────────────────────────────────────────────────────────
    def _reload(self):
        rules = getattr(self.mw, "source_path", None)
        self._loaded_path = rules
        self.aimd_map = None
        for reg in self._lists:
            self._lists[reg].clear()
            self._ids[reg] = []
        self.detail.setPlainText("")
        self.refs.clear()
        if not rules:
            self.path_label.setText("Load a rules file — its aimd.ini appears here.")
            return
        try:
            _lines, amap = load_aimd(rules)
        except Exception as e:
            self.path_label.setText(f"aimd load failed: {type(e).__name__}: {e}")
            return
        if amap is None or not has_ai_sections(amap):
            self.path_label.setText("No aimd.ini (with AI sections) beside the "
                                    "loaded rules.")
            return
        self.aimd_map = amap
        from ..engine import sibling_aimd
        self.path_label.setText(os.path.basename(sibling_aimd(rules) or "aimd.ini"))
        reg = ai_registry(amap)
        for key, _title in _REG_TABS:
            lst = self._lists[key]
            ids = []
            if key == "AITriggerTypes":
                for tid, name in reg["AITriggerTypes"]:
                    it = QListWidgetItem(f"{name}  [{tid}]")
                    it.setData(Qt.UserRole, tid)
                    lst.addItem(it)
                    ids.append(tid)
            else:
                for eid in reg[key]:
                    it = QListWidgetItem(eid)
                    it.setData(Qt.UserRole, eid)
                    lst.addItem(it)
                    ids.append(eid)
            self._ids[key] = ids
        self._apply_filter()
        counts = ", ".join(f"{len(reg[k])} {t.lower()}"
                           if k != "AITriggerTypes"
                           else f"{len(reg['AITriggerTypes'])} triggers"
                           for k, t in _REG_TABS)
        self.status.setText(counts)

    def _apply_filter(self):
        text = self.search.text().strip().lower()
        for reg, lst in self._lists.items():
            for r in range(lst.count()):
                it = lst.item(r)
                lst.setRowHidden(r, bool(text) and text not in it.text().lower())

    # ── detail ───────────────────────────────────────────────────────────────
    def _current_reg(self):
        return _REG_TABS[self.reg_tabs.currentIndex()][0]

    def _selected_id(self):
        lst = self.reg_tabs.currentWidget()
        it = lst.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _show_entry(self):
        self.refs.clear()
        if self.aimd_map is None:
            return
        reg = self._current_reg()
        eid = self._selected_id()
        if not eid:
            self.detail.setPlainText("")
            return
        if reg == "AITriggerTypes":
            fields = trigger_fields(self.aimd_map, eid)
            lines = [f"[AITriggerTypes]  {eid}", ""]
            lines += [f"  {lbl:18} {v}" for lbl, v in decode_trigger(fields)]
            lines += ["", "raw:", "  " + ",".join(fields)]
            self.detail.setPlainText("\n".join(lines))
        else:
            self.detail.setPlainText(entry_text(self.aimd_map, eid))
        for kind, rid in references_to(self.aimd_map, eid):
            item = QListWidgetItem(f"{kind}: {rid}")
            item.setData(Qt.UserRole, (kind, rid))
            self.refs.addItem(item)

    def _jump_ref(self, item):
        kind, rid = item.data(Qt.UserRole)
        reg = "AITriggerTypes" if kind == "AITrigger" else \
            ("TeamTypes" if kind == "TeamType" else None)
        if reg is None:
            return
        idx = [k for k, _ in _REG_TABS].index(reg)
        self.reg_tabs.setCurrentIndex(idx)
        lst = self._lists[reg]
        for r in range(lst.count()):
            if lst.item(r).data(Qt.UserRole) == rid:
                lst.setCurrentRow(r)
                break

    # ── actions ──────────────────────────────────────────────────────────────
    def _lint(self):
        if self.aimd_map is None:
            self.status.setText("No aimd loaded to lint.")
            return
        findings = lint_ai(self.aimd_map, getattr(self.mw, "section_map", None))
        errs = sum(1 for f in findings if f.severity == "error")
        warns = sum(1 for f in findings if f.severity == "warning")
        dlg = QDialog(self)
        dlg.setWindowTitle("AI lint results")
        dlg.resize(680, 460)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"<b>{errs} error(s), {warns} warning(s)</b>"))
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setFont(QFont("monospace"))
        box.setPlainText(format_findings(findings)
                         if findings else "No issues found.")
        lay.addWidget(box)
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        lay.addWidget(close)
        dlg.exec()

    def _open_library(self):
        from .ai_library_dialog import AILibraryDialog
        AILibraryDialog(self.mw).exec()
