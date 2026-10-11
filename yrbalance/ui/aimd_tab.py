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
    QDialog, QMessageBox, QCheckBox,
)

from ..engine import (
    build_section_map, load_aimd, ai_registry, decode_trigger, trigger_fields,
    entry_text, references_to, lint_ai, format_findings, has_ai_sections,
    replace_trigger, replace_section, sibling_aimd, make_backup,
)

_TRIG_ORDER = ("Name, PrimaryTeam, Owner, TechLevel, CondType, CondObject, "
               "Comparator, StartWeight, MinWeight, MaxWeight, Skirmish, "
               "(unused), Side, BaseDefense, SecondaryTeam, Easy, Medium, Hard")

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
        gen_tf_b = QPushButton("Generate TaskForces…")
        gen_tf_b.clicked.connect(self._generate_taskforces)
        gen_wave_b = QPushButton("Generate Waves…")
        gen_wave_b.clicked.connect(self._generate_waves)
        gen_spec_b = QPushButton("Specialty Waves…")
        gen_spec_b.clicked.connect(self._generate_specialty)
        gen_strat_b = QPushButton("Strategic Triggers…")
        gen_strat_b.clicked.connect(self._generate_strategic)
        gen_all_b = QPushButton("Generate Everything…")
        gen_all_b.clicked.connect(self._generate_everything)
        bar.addWidget(reload_b)
        bar.addWidget(lint_b)
        bar.addWidget(lib_b)
        bar.addWidget(gen_tf_b)
        bar.addWidget(gen_wave_b)
        bar.addWidget(gen_spec_b)
        bar.addWidget(gen_strat_b)
        bar.addWidget(gen_all_b)
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
        drow = QHBoxLayout()
        drow.addWidget(QLabel("<b>Definition</b>"))
        drow.addStretch(1)
        self.edit_chk = QCheckBox("Edit")
        self.edit_chk.toggled.connect(self._toggle_edit)
        self.save_btn = QPushButton("Save to aimd.ini")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_entry)
        drow.addWidget(self.edit_chk)
        drow.addWidget(self.save_btn)
        rl.addLayout(drow)
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
        self.aimd_lines = None
        self.aimd_path = None
        self.edit_chk.setChecked(False)
        for reg in self._lists:
            self._lists[reg].clear()
            self._ids[reg] = []
        self.detail.setPlainText("")
        self.refs.clear()
        if not rules:
            self.path_label.setText("Load a rules file — its aimd.ini appears here.")
            return
        try:
            lines, amap = load_aimd(rules)
        except Exception as e:
            self.path_label.setText(f"aimd load failed: {type(e).__name__}: {e}")
            return
        if amap is None or not has_ai_sections(amap):
            self.path_label.setText("No aimd.ini (with AI sections) beside the "
                                    "loaded rules.")
            return
        self.aimd_map = amap
        self.aimd_lines = lines
        self.aimd_path = sibling_aimd(rules)
        self.path_label.setText(os.path.basename(self.aimd_path or "aimd.ini"))
        self._refresh_lists()
        counts = {k: len(v) for k, v in self._ids.items()}
        self.status.setText(
            f"{counts['TaskForces']} taskforces, {counts['ScriptTypes']} scripts, "
            f"{counts['TeamTypes']} teamtypes, {counts['AITriggerTypes']} triggers")

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
        editing = self.edit_chk.isChecked()
        self.save_btn.setEnabled(editing and bool(eid))
        if not eid:
            self.detail.setPlainText("")
            return
        if editing:
            # editable raw form: a trigger's comma def, or the section text
            if reg == "AITriggerTypes":
                self.detail.setPlainText(",".join(trigger_fields(self.aimd_map, eid)))
                self.status.setText("Editing trigger fields → " + _TRIG_ORDER)
            else:
                self.detail.setPlainText(entry_text(self.aimd_map, eid))
                self.status.setText(f"Editing [{eid}] — edit key=value lines, Save.")
        elif reg == "AITriggerTypes":
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

    def _toggle_edit(self, on):
        self.detail.setReadOnly(not on)
        self._show_entry()

    def _save_entry(self):
        if self.aimd_map is None or self.aimd_lines is None or not self.aimd_path:
            return
        reg = self._current_reg()
        eid = self._selected_id()
        if not eid:
            return
        text = self.detail.toPlainText()
        try:
            if reg == "AITriggerTypes":
                ok = replace_trigger(self.aimd_lines, self.aimd_map, eid, text)
            else:
                ok = replace_section(self.aimd_lines, self.aimd_map, eid, text)
            if not ok:
                self.status.setText(f"{eid} not found — not saved.")
                return
            make_backup(self.aimd_path, None, keep=10)
            with open(self.aimd_path, "w", encoding="latin-1",
                      errors="replace", newline="") as fh:
                fh.write("".join(self.aimd_lines))
        except Exception as e:
            QMessageBox.critical(self, "Save", f"{type(e).__name__}: {e}")
            return
        # re-parse + re-lint so the edit is validated immediately
        self.aimd_map = build_section_map(self.aimd_lines)[0]
        errs = sum(1 for f in lint_ai(self.aimd_map,
                                      getattr(self.mw, "section_map", None))
                   if f.severity == "error")
        self.edit_chk.setChecked(False)
        row = self.reg_tabs.currentWidget().currentRow()
        self._refresh_lists()
        self.reg_tabs.currentWidget().setCurrentRow(row)
        self.status.setText(f"Saved {eid} to {os.path.basename(self.aimd_path)} "
                            f"(backup kept). AI lint: {errs} error(s).")

    def _refresh_lists(self):
        """Rebuild the registry lists from the current aimd_map (after an edit)."""
        reg = ai_registry(self.aimd_map)
        for key, _title in _REG_TABS:
            lst = self._lists[key]
            lst.clear()
            ids = []
            if key == "AITriggerTypes":
                for tid, name in reg["AITriggerTypes"]:
                    it = QListWidgetItem(f"{name}  [{tid}]")
                    it.setData(Qt.UserRole, tid)
                    lst.addItem(it)
                    ids.append(tid)
            else:
                for eid2 in reg[key]:
                    it = QListWidgetItem(eid2)
                    it.setData(Qt.UserRole, eid2)
                    lst.addItem(it)
                    ids.append(eid2)
            self._ids[key] = ids
        self._apply_filter()

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

    def _generate_taskforces(self):
        rules = getattr(self.mw, "source_path", None)
        if not rules:
            QMessageBox.information(self, "Generate", "Load a rules file first.")
            return
        aimd_path = getattr(self, "aimd_path", None)
        if not aimd_path:
            QMessageBox.information(self, "Generate", "This mod has no aimd.ini to "
                                    "write TaskForces into.")
            return
        from .wavegen_taskforce_dialog import TaskforceGenDialog
        TaskforceGenDialog(self, rules, aimd_path,
                           on_inserted=self._reload).exec()

    def _generate_waves(self):
        rules = getattr(self.mw, "source_path", None)
        aimd_path = getattr(self, "aimd_path", None)
        if not rules:
            QMessageBox.information(self, "Generate", "Load a rules file first.")
            return
        if not aimd_path:
            QMessageBox.information(self, "Generate", "This mod has no aimd.ini to "
                                    "write waves into.")
            return
        from .wavegen_wave_dialog import WaveGenDialog
        WaveGenDialog(self, rules, aimd_path, on_inserted=self._reload).exec()

    def _generate_specialty(self):
        rules = getattr(self.mw, "source_path", None)
        aimd_path = getattr(self, "aimd_path", None)
        if not rules:
            QMessageBox.information(self, "Generate", "Load a rules file first.")
            return
        if not aimd_path:
            QMessageBox.information(self, "Generate", "This mod has no aimd.ini to "
                                    "write waves into.")
            return
        from .wavegen_specialty_dialog import SpecialtyWaveDialog
        SpecialtyWaveDialog(self, rules, aimd_path,
                            on_inserted=self._reload).exec()

    def _generate_strategic(self):
        rules = getattr(self.mw, "source_path", None)
        aimd_path = getattr(self, "aimd_path", None)
        if not rules:
            QMessageBox.information(self, "Generate", "Load a rules file first.")
            return
        if not aimd_path:
            QMessageBox.information(self, "Generate", "This mod has no aimd.ini to "
                                    "write triggers into.")
            return
        from .wavegen_strategic_dialog import StrategicTriggerDialog
        StrategicTriggerDialog(self, rules, aimd_path,
                               on_inserted=self._reload).exec()

    def _generate_everything(self):
        rules = getattr(self.mw, "source_path", None)
        aimd_path = getattr(self, "aimd_path", None)
        if not rules:
            QMessageBox.information(self, "Generate", "Load a rules file first.")
            return
        if not aimd_path:
            QMessageBox.information(self, "Generate", "This mod has no aimd.ini to "
                                    "write into.")
            return
        from .wavegen_everything_dialog import EverythingDialog
        EverythingDialog(self, rules, aimd_path, on_inserted=self._reload).exec()
