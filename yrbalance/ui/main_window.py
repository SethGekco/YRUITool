"""
MainWindow — assembles the toolbar, the TechnoType tabs, and the bottom script
panel, and wires "Apply and save" to the controller.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QSplitter, QComboBox, QFileDialog, QMessageBox, QPlainTextEdit,
    QDialog, QDialogButtonBox,
)

from ..engine import ModConfig, BalanceSpec, parse_ini_lines, restore_vanilla
from . import controller, blueprint
from .category_tab import CategoryTab
from .section_form_tab import SectionFormTab
from .script_panel import ScriptPanel

# (key, label, is_building, linkable)
TECHNO_TABS = [
    ("inf", "Infantry", False, False),
    ("veh", "Vehicle", False, False),
    ("nav", "Naval", False, True),      # linkable to Vehicles
    ("air", "Aircraft", False, False),
    ("bld", "Building", True, False),
    ("def", "Defensive", True, False),
]


def _preset_dirs():
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))
    return [
        os.path.join(repo, "presets", "examples"),
        os.path.join(os.path.expanduser("~"), ".yrbalancetool", "presets"),
    ]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YRBalanceTool")
        self.resize(760, 720)

        self.config = ModConfig.default()
        self.source_path = None
        self.out_path = None
        self.section_map = None

        central = QWidget()
        root = QVBoxLayout(central)
        root.addLayout(self._build_toolbar())

        splitter = QSplitter(Qt.Vertical)

        self.tabs = QTabWidget()
        self.category_tabs = {}
        for key, label, is_building, linkable in TECHNO_TABS:
            tab = CategoryTab(key, is_building=is_building, linkable=linkable)
            self.category_tabs[key] = tab
            self.tabs.addTab(tab, label)

        # Section-form tabs (structured tag edits on a single section).
        self.form_tabs = {}
        self.form_tabs["general"] = SectionFormTab(
            "General",
            seed_rows=[("BuildSpeed", ""), ("GrowthRate", ""),
                       ("RepairStep", ""), ("RepairPercent", ""),
                       ("VeteranRatio", "")],
            help_text="Global [General] tags. Tick a row and set a value "
                      "(literal like 0.9, or a formula like BuildSpeed * 0.9).")
        self.form_tabs["gameplay"] = SectionFormTab(
            "General",
            seed_rows=[("AIHateDelay", ""), ("BuildupTime", ""),
                       ("PlacementDelay", "")],
            help_text="Gameplay-pacing tags. Change the section if a tag lives "
                      "elsewhere (e.g. CombatDamage).")
        self.form_tabs["superweapons"] = SectionFormTab(
            "",
            seed_rows=[("RechargeTime", ""), ("Range", "")],
            help_text="Set the section to a super-weapon (e.g. NUKE, IRONCURTAIN) "
                      "and tweak its tags. One super-weapon per tab; use several "
                      "or the custom script for more.")
        self.tabs.addTab(self.form_tabs["general"], "General")
        self.tabs.addTab(self.form_tabs["gameplay"], "Gameplay")
        self.tabs.addTab(self.form_tabs["superweapons"], "Super Weapons")

        # Placeholder for cross-cutting features that span multiple categories.
        self.tabs.addTab(self._placeholder(
            "Global tab — for features that span more than one category "
            "(future cross-category systems and presets)."), "Global")
        splitter.addWidget(self.tabs)

        self.script_panel = ScriptPanel(presets_dirs=_preset_dirs())
        self.script_panel.preview_requested.connect(self.preview_script)
        splitter.addWidget(self.script_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter)

        self.setCentralWidget(central)
        self._build_menu()
        self.statusBar().showMessage("Load a rulesmd.ini to begin.")

    def _build_menu(self):
        mb = self.menuBar()
        file_menu = mb.addMenu("File")
        file_menu.addAction("Load rules…", self.load_rules)
        file_menu.addAction("Restore a file to vanilla…", self.restore_to_vanilla)
        file_menu.addSeparator()
        file_menu.addAction("Quit", self.close)

        bp = mb.addMenu("Blueprint")
        bp.addAction("Save blueprint…", self.save_blueprint)
        bp.addAction("Load blueprint…", self.load_blueprint)

    # ── Toolbar ──────────────────────────────────────────────────────────────
    def _build_toolbar(self):
        bar = QHBoxLayout()
        self.load_btn = QPushButton("Load rules…")
        self.load_btn.clicked.connect(self.load_rules)
        bar.addWidget(self.load_btn)

        self.file_label = QLabel("(no file loaded)")
        self.file_label.setStyleSheet("color: gray;")
        bar.addWidget(self.file_label)
        bar.addStretch(1)

        bar.addWidget(QLabel("Profile:"))
        self.profile_box = QComboBox()
        self.profile_box.addItem("Default", "default")
        self.profile_box.addItem("Dying Earth", "dying_earth")
        self.profile_box.currentIndexChanged.connect(self._change_profile)
        bar.addWidget(self.profile_box)

        self.preview_btn = QPushButton("Preview changes")
        self.preview_btn.setEnabled(False)
        self.preview_btn.clicked.connect(self.preview_changes)
        bar.addWidget(self.preview_btn)

        self.apply_btn = QPushButton("Apply and save…")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self.apply_and_save)
        bar.addWidget(self.apply_btn)
        return bar

    def _placeholder(self, text):
        w = QWidget()
        lay = QVBoxLayout(w)
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("color: gray;")
        lay.addWidget(label)
        return w

    def _change_profile(self, index):
        which = self.profile_box.itemData(index)
        self.config = (ModConfig.dying_earth() if which == "dying_earth"
                       else ModConfig.default())

    # ── Actions ──────────────────────────────────────────────────────────────
    def load_rules(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load rulesmd.ini", "", "INI files (*.ini);;All files (*)")
        if not path:
            return
        try:
            _lines, self.section_map = controller.load_rules(path)
        except OSError as e:
            QMessageBox.critical(self, "Load", f"Could not read:\n{e}")
            return
        self.source_path = path
        self.out_path = None
        self.file_label.setText(os.path.basename(path))
        self.file_label.setStyleSheet("")
        self.apply_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self.statusBar().showMessage(
            f"Loaded {len(self.section_map)} sections from {os.path.basename(path)}.")

    def collect_spec(self):
        veh_ratios = self.category_tabs["veh"].ratios()
        cats = {}
        for key, tab in self.category_tabs.items():
            if key == "nav":
                cats[key] = tab.ratios(link_source=veh_ratios)
            else:
                cats[key] = tab.ratios()
        return BalanceSpec(categories=cats)

    # ── Blueprint ────────────────────────────────────────────────────────────
    def to_blueprint_text(self, name="Untitled"):
        cat_states = {k: t.get_state() for k, t in self.category_tabs.items()}
        form_states = {k: t.get_state() for k, t in self.form_tabs.items()}
        return blueprint.serialize(cat_states, form_states,
                                   self.script_panel.text(), name=name)

    def apply_blueprint_text(self, text):
        _meta, cat_states, form_states, script = blueprint.parse(text)
        for k, tab in self.category_tabs.items():
            if k in cat_states:
                tab.set_state(cat_states[k])
        for k, tab in self.form_tabs.items():
            if k in form_states:
                tab.set_state(form_states[k])
        self.script_panel.editor.setPlainText(script)
        return _meta

    def save_blueprint(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save blueprint", "balance.yrbp",
            "Blueprint (*.yrbp *.txt);;All files (*)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".yrbp"
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.to_blueprint_text(name=name))
        except OSError as e:
            QMessageBox.warning(self, "Blueprint", f"Could not write:\n{e}")
            return
        self.statusBar().showMessage(f"Blueprint saved to {os.path.basename(path)}.")

    def load_blueprint(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load blueprint", "", "Blueprint (*.yrbp *.txt);;All files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                self.apply_blueprint_text(f.read())
        except OSError as e:
            QMessageBox.warning(self, "Blueprint", f"Could not read:\n{e}")
            return
        self.statusBar().showMessage(f"Blueprint loaded from {os.path.basename(path)}.")

    def restore_to_vanilla(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an edited rules file to restore", "",
            "INI files (*.ini);;All files (*)")
        if not path:
            return
        try:
            lines = parse_ini_lines(path)
            reverted = restore_vanilla(lines)
        except OSError as e:
            QMessageBox.critical(self, "Restore", f"Could not read:\n{e}")
            return
        base, ext = os.path.splitext(path)
        out, _ = QFileDialog.getSaveFileName(
            self, "Save restored rules", f"{base}_vanilla{ext or '.ini'}",
            "INI files (*.ini);;All files (*)")
        if not out:
            return
        try:
            with open(out, "w", encoding="utf-8", newline="") as f:
                f.writelines(lines)
        except OSError as e:
            QMessageBox.critical(self, "Restore", f"Could not write:\n{e}")
            return
        QMessageBox.information(
            self, "Restore to vanilla",
            f"Reverted {reverted} edited values to vanilla.\n\nSaved to:\n{out}\n\n"
            "Note: sections/tags added by generator scripts are not removed.")

    def collect_scripts(self):
        """All script text to run: the form tabs first, then the custom box."""
        scripts = [tab.to_script_text() for tab in self.form_tabs.values()]
        scripts.append(self.script_panel.text())
        return [s for s in scripts if s and s.strip()]

    def preview_script(self):
        ok, message = controller.preview_script(
            self.section_map, self.script_panel.text(), self.config)
        box = QMessageBox(self)
        box.setWindowTitle("Script preview")
        box.setIcon(QMessageBox.Information if ok else QMessageBox.Warning)
        box.setText(message)
        box.exec()

    def preview_changes(self):
        if not self.source_path:
            return
        try:
            summary, results, diff = controller.preview_changes(
                self.source_path, self.collect_spec(), self.collect_scripts(),
                self.config)
        except Exception as e:
            QMessageBox.critical(self, "Preview", f"{type(e).__name__}: {e}")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Preview changes")
        dlg.resize(720, 560)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(controller.summarize(summary, results)))
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setFont(QFont("monospace", 9))
        view.setPlainText(diff)
        lay.addWidget(view, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        apply_now = buttons.addButton("Apply and save…", QDialogButtonBox.AcceptRole)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        lay.addWidget(buttons)
        if dlg.exec() == QDialog.Accepted:
            self.apply_and_save()

    def apply_and_save(self):
        if not self.source_path:
            return
        if not self.out_path:
            base, ext = os.path.splitext(self.source_path)
            default_out = f"{base}_edited{ext or '.ini'}"
            path, _ = QFileDialog.getSaveFileName(
                self, "Save edited rules", default_out,
                "INI files (*.ini);;All files (*)")
            if not path:
                return
            self.out_path = path

        spec = self.collect_spec()
        scripts = self.collect_scripts()
        log = []
        try:
            summary, script_results = controller.apply_and_save(
                self.source_path, self.out_path, spec,
                scripts=scripts, config=self.config, progress=log.append)
        except Exception as e:  # surface engine/script errors to the user
            QMessageBox.critical(self, "Apply", f"{type(e).__name__}: {e}")
            return

        text = controller.summarize(summary, script_results)
        self.statusBar().showMessage(f"Saved to {os.path.basename(self.out_path)}.")
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Applied and saved")
        dlg.setIcon(QMessageBox.Information)
        dlg.setText(f"Saved to:\n{self.out_path}\n\n{text}")
        if summary.weapon_conflict_text.strip():
            dlg.setDetailedText(summary.weapon_conflict_text)
        dlg.exec()
