"""
AI Builder sub-tab — browse the loaded mod's aimd.ini: its TaskForces,
ScriptTypes, TeamTypes and AITriggerTypes, with live search, a human-readable
decode of the cryptic inline trigger line, each entry's raw definition, and
cross-references (what uses what, double-click to jump). Buttons lint the AI and
generate waves/triggers. This is one of the three sub-tabs inside the AI (aimd)
tab (see ai_tab.AiTab); the AI library and Unit Roles live in their own sub-tabs.
"""

from ..engine.ini_model import guard_ini_text
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTabWidget, QListWidget, QListWidgetItem, QSplitter, QPlainTextEdit,
    QDialog, QMessageBox, QCheckBox, QFileDialog, QInputDialog,
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


class AiBuilderTab(QWidget):
    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        self._loaded_path = None
        self.aimd_map = None
        self._working = False     # True => editing the working file, not a mod aimd
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

        # ── working-file row ───────────────────────────────────────────────────
        wbar = QHBoxLayout()
        wbar.addWidget(QLabel("<b>Working file:</b>"))
        self.work_label = QLabel("off — editing the loaded mod's aimd.ini")
        self.work_label.setStyleSheet("color:palette(mid);")
        wbar.addWidget(self.work_label, 1)
        new_blank_b = QPushButton("New blank")
        new_blank_b.setToolTip("Start an empty working file (built separately from "
                               "any mod; nothing is written to a mod until Export).")
        new_blank_b.clicked.connect(self._work_new_blank)
        new_van_b = QPushButton("New from vanilla")
        new_van_b.setToolTip("Start a working file seeded from the vanilla YR aimd.")
        new_van_b.clicked.connect(self._work_new_vanilla)
        new_mod_b = QPushButton("From loaded mod")
        new_mod_b.setToolTip("Copy the loaded mod's aimd.ini into a working file so "
                             "you can build on it without touching the original.")
        new_mod_b.clicked.connect(self._work_from_mod)
        add_lib_b = QPushButton("Add from library…")
        add_lib_b.setToolTip("Pull a saved AI library entry into the working file, "
                             "with dedup / merge-or-keep-both conflict handling.")
        add_lib_b.clicked.connect(self._work_add_from_library)
        export_b = QPushButton("Export…")
        export_b.setToolTip("Write the working file out to a mod's aimd.ini "
                            "(the destination is backed up first).")
        export_b.clicked.connect(self._work_export)
        self.work_close_b = QPushButton("Close working file")
        self.work_close_b.clicked.connect(self._work_close)
        for b in (new_blank_b, new_van_b, new_mod_b, add_lib_b, export_b,
                  self.work_close_b):
            wbar.addWidget(b)
        self._work_edit_btns = [add_lib_b, export_b, self.work_close_b]
        root.addLayout(wbar)

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
        self._update_work_ui()

    def _update_work_ui(self):
        for b in self._work_edit_btns:
            b.setEnabled(self._working)
        if self._working:
            self.work_label.setStyleSheet("color:palette(text); font-weight:bold;")
        else:
            self.work_label.setText("off — editing the loaded mod's aimd.ini")
            self.work_label.setStyleSheet("color:palette(mid);")

    def showEvent(self, event):
        super().showEvent(event)
        if self._working:
            return                 # don't clobber the working file on re-show
        cur = getattr(self.mw, "source_path", None)
        if cur != self._loaded_path or not self._shown:
            self._shown = True
            self._reload()

    # ── load / fill ────────────────────────────────────────────────────────
    def _reload(self):
        if self._working:
            self._load_working()
            return
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

    # ── working file ───────────────────────────────────────────────────────────
    def _load_working(self):
        """Populate the registry views from the working file (sidecar)."""
        from ..engine import load_working, working_path
        self.edit_chk.setChecked(False)
        for reg in self._lists:
            self._lists[reg].clear()
            self._ids[reg] = []
        self.detail.setPlainText("")
        self.refs.clear()
        lines, amap = load_working()
        self.aimd_lines = lines
        self.aimd_map = amap
        self.aimd_path = working_path()
        if amap is None:
            self.path_label.setText("Working file (empty)")
            return
        self._refresh_lists()
        counts = {k: len(v) for k, v in self._ids.items()}
        total = sum(counts.values())
        self.path_label.setText("● Working file (not yet exported)")
        self.work_label.setText(
            f"ON — {counts['TaskForces']} TF · {counts['ScriptTypes']} scripts · "
            f"{counts['TeamTypes']} teams · {counts['AITriggerTypes']} triggers")
        self.status.setText(f"Working file: {total} AI entr"
                            f"{'y' if total == 1 else 'ies'}. Add from library or "
                            "generate into it, then Export.")

    def _enter_working(self):
        self._working = True
        self._update_work_ui()
        self._reload()

    def _work_new_blank(self):
        from ..engine import new_blank, working_exists
        if working_exists() and QMessageBox.question(
                self, "New working file",
                "Replace the current working file with a new blank one?") \
                != QMessageBox.Yes:
            return
        new_blank()
        self._enter_working()

    def _work_new_vanilla(self):
        from ..engine import new_from_vanilla, working_exists
        if working_exists() and QMessageBox.question(
                self, "New working file",
                "Replace the current working file with a vanilla-seeded one?") \
                != QMessageBox.Yes:
            return
        game_dir = os.path.dirname(getattr(self.mw, "source_path", "") or "")
        try:
            new_from_vanilla(game_dir=game_dir or None)
        except FileNotFoundError as e:
            QMessageBox.information(self, "New from vanilla", str(e))
            return
        self._enter_working()

    def _work_from_mod(self):
        from ..engine import new_from_file, working_exists
        src = sibling_aimd(getattr(self.mw, "source_path", None))
        if not src:
            QMessageBox.information(self, "From loaded mod",
                                   "Load a mod with an aimd.ini first.")
            return
        if working_exists() and QMessageBox.question(
                self, "New working file",
                "Replace the current working file with a copy of the loaded mod's "
                "aimd.ini?") != QMessageBox.Yes:
            return
        new_from_file(src)
        self._enter_working()

    def _work_add_from_library(self):
        from ..engine import load_library, working_path
        from .ai_library_dialog import _PullDialog
        lib = load_library()
        if not lib:
            QMessageBox.information(self, "Add from library",
                                   "The AI library is empty. Extract some entries "
                                   "in the AI Library tab first.")
            return
        labels = [f"[{e.kind}] {e.name}" for e in lib]
        label, ok = QInputDialog.getItem(self, "Add from library",
                                         "Pull which entry into the working file?",
                                         labels, 0, False)
        if not ok:
            return
        entry = lib[labels.index(label)]
        rules_map = getattr(self.mw, "section_map", None)
        dlg = _PullDialog(self, entry, working_path(), rules_map=rules_map)
        dlg.exec()
        self._reload()

    def _work_export(self):
        from ..engine import export_working, working_path
        default = sibling_aimd(getattr(self.mw, "source_path", None)) or "aimd.ini"
        dest, _ = QFileDialog.getSaveFileName(
            self, "Export working file to aimd.ini",
            default, "INI files (*.ini);;All files (*)")
        if not dest:
            return
        try:
            if os.path.isfile(dest):
                make_backup(dest, None, keep=10)
            export_working(dest)
        except Exception as e:
            QMessageBox.critical(self, "Export", f"{type(e).__name__}: {e}")
            return
        QMessageBox.information(
            self, "Export",
            f"Wrote the working file to {os.path.basename(dest)}"
            + (" (previous version backed up)." if os.path.isfile(dest) else "."))

    def _work_close(self):
        self._working = False
        self._update_work_ui()
        self.path_label.setText("")
        self._reload()

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
                fh.write(guard_ini_text("".join(self.aimd_lines)))
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
