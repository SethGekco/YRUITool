"""
MainWindow — assembles the toolbar, the TechnoType tabs, and the bottom script
panel, and wires "Apply and save" to the controller.
"""

import os
import sys

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QFont, QPalette, QColor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QSplitter, QComboBox, QFileDialog, QMessageBox, QPlainTextEdit,
    QDialog, QDialogButtonBox, QApplication,
)

from ..engine import ModConfig, BalanceSpec, parse_ini_lines, restore_vanilla
from . import controller, blueprint
from .category_tab import CategoryTab
from .section_form_tab import SectionFormTab
from .global_tab import GlobalTab
from .script_panel import ScriptPanel, ScriptTab, show_script_help

# (key, display label, is_building, linkable, link_label)
TECHNO_TABS = [
    ("inf", "Infantry", False, False, ""),
    ("veh", "Vehicles", False, False, ""),
    ("nav", "Naval", False, True, "Vehicles"),
    ("air", "Aircraft", False, False, ""),
    ("bld", "Structures", True, False, ""),
    ("def", "Defenses", True, True, "Structures"),
]


def _preset_dirs():
    # Bundled examples: from the PyInstaller bundle when frozen, else the repo.
    if getattr(sys, "frozen", False):
        bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "presets", "examples")
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        repo = os.path.dirname(os.path.dirname(here))
        bundled = os.path.join(repo, "presets", "examples")
    # A user presets dir the user can drop their own .yrs scripts into.
    user = os.path.join(os.path.expanduser("~"), ".yrbalancetool", "presets")
    return [bundled, user]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YRBalanceTool")
        self.resize(760, 720)

        self.config = ModConfig.default()
        self.source_path = None
        self.out_path = None
        self.section_map = None
        self.session = None  # controller.WorkingSession for the hybrid apply flow

        central = QWidget()
        root = QVBoxLayout(central)
        root.addLayout(self._build_toolbar())

        self.tabs = QTabWidget()

        # Build the tab widgets.
        self.global_tab = GlobalTab()
        self.category_tabs = {}
        for key, _label, is_building, linkable, link_label in TECHNO_TABS:
            self.category_tabs[key] = CategoryTab(
                key, is_building=is_building, linkable=linkable,
                link_label=link_label or "Vehicles")
        # Populate each tab's "Same as X" dropdown with the other categories.
        cat_display = [(k, lbl) for k, lbl, *_ in TECHNO_TABS]
        for key, tab in self.category_tabs.items():
            tab.set_link_options([(k, lbl) for k, lbl in cat_display if k != key])
        # Defaults: Naval inherits Vehicles, Defenses inherits Structures.
        self.category_tabs["nav"].set_link_source("veh")
        self.category_tabs["def"].set_link_source("bld")

        # Section-form tabs (structured tag edits on a single section).
        self.form_tabs = {
            "general": SectionFormTab(
                "General",
                seed_rows=[("BuildSpeed", ""), ("GrowthRate", ""),
                           ("RepairStep", ""), ("RepairPercent", ""),
                           ("VeteranRatio", "")],
                help_text="[General] tags. Tick a row and set a value (literal "
                          "like 0.9, or a formula like BuildSpeed * 0.9). A "
                          "full auto-populated list of [General] tags is planned."),
            "ai": SectionFormTab(
                "AI",
                seed_rows=[("RelaxedStray", "3.0"), ("BaseBias", "2"),
                           ("BaseDefenseDelay", "0.25"),
                           ("AIAlternateProductionCreditCutoff", "1000")],
                help_text="[AI] tags (and AI-relevant [General] tags — change the "
                          "section as needed). See modenc.renegadeprojects.com/AI."),
            "gameplay": SectionFormTab(
                "CombatDamage",
                seed_rows=[("BallisticScatter", ""), ("TreeStrength", "")],
                help_text="Gameplay sections like [CombatDamage], [Radiation], "
                          "etc. Change the section field to target another one."),
        }

        self.history_tab = self._placeholder(
            "History — a per-change log with individual undo, filtering, and a "
            "stored vanilla reference is planned. For now, use File → Restore a "
            "file to vanilla, and the session ticket next to your rules file.")

        self.script_panel = ScriptPanel(presets_dirs=_preset_dirs())
        self.script_panel.preview_requested.connect(self.preview_script)
        self.script_tab = ScriptTab(self.script_panel)

        # Add tabs in the requested order.
        self.tabs.addTab(self.global_tab, "Global")
        self.tabs.addTab(self.form_tabs["general"], "General")
        self.tabs.addTab(self.form_tabs["ai"], "AI")
        self.tabs.addTab(self.form_tabs["gameplay"], "Gameplay")
        self.tabs.addTab(self.category_tabs["inf"], "Infantry")
        self.tabs.addTab(self.category_tabs["veh"], "Vehicles")
        self.tabs.addTab(self.category_tabs["nav"], "Naval")
        self.tabs.addTab(self.category_tabs["air"], "Aircraft")
        self.tabs.addTab(self.category_tabs["bld"], "Structures")
        self.tabs.addTab(self.category_tabs["def"], "Defenses")
        self.tabs.addTab(self.history_tab, "History")
        self.tabs.addTab(self.script_tab, "Custom Script")
        root.addWidget(self.tabs)

        self.setCentralWidget(central)
        self._build_menu()
        self.settings = QSettings("YRBalanceTool", "YRBalanceTool")
        self._restore_settings()
        self.statusBar().showMessage("Load a rulesmd.ini to begin.")

    # ── Settings persistence ─────────────────────────────────────────────────
    def _restore_settings(self):
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        profile = self.settings.value("profile", "default")
        idx = self.profile_box.findData(profile)
        if idx >= 0:
            self.profile_box.setCurrentIndex(idx)
        last = self.settings.value("last_rules", "")
        if last and os.path.isfile(last):
            self.reload_last_action.setEnabled(True)
            self.reload_last_action.setText(f"Reload {os.path.basename(last)}")
        if self.settings.value("dark", "false") == "true":
            self.dark_action.setChecked(True)  # triggers _apply_theme

    def closeEvent(self, event):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("profile", self.profile_box.currentData())
        self.settings.setValue("dark", "true" if self.dark_action.isChecked() else "false")
        super().closeEvent(event)

    def _last_dir(self):
        last = self.settings.value("last_rules", "")
        return os.path.dirname(last) if last else ""

    def reload_last_rules(self):
        last = self.settings.value("last_rules", "")
        if last and os.path.isfile(last):
            self._load_rules_path(last)

    def _build_menu(self):
        mb = self.menuBar()
        file_menu = mb.addMenu("File")
        file_menu.addAction("Load rules…", self.load_rules)
        self.reload_last_action = file_menu.addAction("Reload last rules",
                                                      self.reload_last_rules)
        self.reload_last_action.setEnabled(False)
        file_menu.addAction("Restore a file to vanilla…", self.restore_to_vanilla)
        file_menu.addSeparator()
        file_menu.addAction("Quit", self.close)

        apply_menu = mb.addMenu("Apply")
        apply_menu.addAction("Apply all → working copy", self.apply_working_all)
        apply_menu.addAction("Apply current tab → working copy",
                             self.apply_working_current)
        apply_menu.addSeparator()
        self.diff_action = apply_menu.addAction("Show working-copy diff…",
                                                self.show_working_diff)
        self.save_working_action = apply_menu.addAction("Save working copy…",
                                                        self.save_working)
        self.discard_action = apply_menu.addAction("Discard working copy",
                                                   self.discard_working)
        apply_menu.addSeparator()
        apply_menu.addAction("Apply all and save… (one-shot)", self.apply_and_save)
        for a in (self.diff_action, self.save_working_action, self.discard_action):
            a.setEnabled(False)

        bp = mb.addMenu("Blueprint")
        bp.addAction("Save blueprint…", self.save_blueprint)
        bp.addAction("Load blueprint…", self.load_blueprint)

        view = mb.addMenu("View")
        self.dark_action = view.addAction("Dark theme")
        self.dark_action.setCheckable(True)
        self.dark_action.toggled.connect(self._apply_theme)

        help_menu = mb.addMenu("Help")
        help_menu.addAction("Custom script help…", lambda: show_script_help(self))
        help_menu.addAction("About YRBalanceTool", self._show_about)

    def _show_about(self):
        QMessageBox.about(
            self, "About YRBalanceTool",
            "YRBalanceTool — general-purpose balance editor for\n"
            "Red Alert 2: Yuri's Revenge.\n\n"
            "Docs: README.md, docs/SCRIPTS.md, docs/BLUEPRINTS.md.")

    def _apply_theme(self, dark):
        app = QApplication.instance()
        if app is None:
            return
        app.setStyle("Fusion")
        if dark:
            pal = QPalette()
            base = QColor(53, 53, 53)
            alt = QColor(42, 42, 42)
            text = QColor(220, 220, 220)
            hl = QColor(42, 130, 218)
            pal.setColor(QPalette.Window, base)
            pal.setColor(QPalette.WindowText, text)
            pal.setColor(QPalette.Base, alt)
            pal.setColor(QPalette.AlternateBase, base)
            pal.setColor(QPalette.ToolTipBase, text)
            pal.setColor(QPalette.ToolTipText, text)
            pal.setColor(QPalette.Text, text)
            pal.setColor(QPalette.Button, base)
            pal.setColor(QPalette.ButtonText, text)
            pal.setColor(QPalette.Highlight, hl)
            pal.setColor(QPalette.HighlightedText, QColor(0, 0, 0))
            pal.setColor(QPalette.Disabled, QPalette.Text, QColor(120, 120, 120))
            pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(120, 120, 120))
            app.setPalette(pal)
        else:
            app.setPalette(app.style().standardPalette())

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
        label.setWordWrap(True)
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
            self, "Load rulesmd.ini", self._last_dir(),
            "INI files (*.ini);;All files (*)")
        if path:
            self._load_rules_path(path)

    def _load_rules_path(self, path):
        try:
            _lines, self.section_map = controller.load_rules(path)
        except OSError as e:
            QMessageBox.critical(self, "Load", f"Could not read:\n{e}")
            return
        self.source_path = path
        self.out_path = None
        self.session = None
        self.file_label.setText(os.path.basename(path))
        self.file_label.setStyleSheet("")
        self.apply_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self._refresh_working_state()
        self.settings.setValue("last_rules", path)
        self.reload_last_action.setEnabled(True)
        self.reload_last_action.setText(f"Reload {os.path.basename(path)}")
        self.statusBar().showMessage(
            f"Loaded {len(self.section_map)} sections from {os.path.basename(path)}.")

    def collect_spec(self, only_keys=None):
        # Pass 1: each tab's own ("independent") ratios, used as link sources.
        self_ratios = {key: tab.ratios(link_source=None)
                       for key, tab in self.category_tabs.items()}
        # Pass 2: resolve each tab against its selected "Same as X" source.
        cats = {}
        for key, tab in self.category_tabs.items():
            if only_keys is not None and key not in only_keys:
                continue
            src_key = tab.link_source_key()
            cats[key] = tab.ratios(link_source=self_ratios.get(src_key)
                                   if src_key else None)

        # Global ratios compound (multiply) into each checked category.
        g = self.global_tab.global_ratios()
        for key, r in cats.items():
            if self.global_tab.applies_to(key):
                r.strength *= g["strength"]
                r.cost *= g["cost"]
                r.speed *= g["speed"]

        return BalanceSpec(categories=cats,
                           range_threshold=self.global_tab.range_threshold_value())

    def _current_category_key(self):
        w = self.tabs.currentWidget()
        for key, tab in self.category_tabs.items():
            if tab is w:
                return key
        return None

    # ── Blueprint ────────────────────────────────────────────────────────────
    def to_blueprint_text(self, name="Untitled"):
        cat_states = {k: t.get_state() for k, t in self.category_tabs.items()}
        form_states = {k: t.get_state() for k, t in self.form_tabs.items()}
        return blueprint.serialize(cat_states, form_states,
                                   self.script_panel.text(), name=name,
                                   global_state=self.global_tab.get_state())

    def apply_blueprint_text(self, text):
        _meta, cat_states, form_states, global_state, script = blueprint.parse(text)
        for k, tab in self.category_tabs.items():
            if k in cat_states:
                tab.set_state(cat_states[k])
        for k, tab in self.form_tabs.items():
            if k in form_states:
                tab.set_state(form_states[k])
        if global_state:
            self.global_tab.set_state(global_state)
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
        """All script text to run: the form tabs (General/AI/Gameplay), then the box."""
        scripts = [self.form_tabs[k].to_script_text()
                   for k in ("general", "ai", "gameplay")]
        scripts.append(self.script_panel.text())
        return [s for s in scripts if s and s.strip()]

    # ── Hybrid working-copy apply ────────────────────────────────────────────
    def _ensure_session(self):
        if self.session is None or self.session.source_path != self.source_path:
            self.session = controller.WorkingSession(self.source_path, self.config)
        self.session.config = self.config
        return self.session

    def apply_working_all(self):
        if not self.source_path:
            QMessageBox.information(self, "Apply", "Load a rules file first.")
            return
        self._apply_working(self.collect_spec(), self.collect_scripts(), "all")

    def apply_working_current(self):
        if not self.source_path:
            QMessageBox.information(self, "Apply", "Load a rules file first.")
            return
        key = self._current_category_key()
        if key is None:
            QMessageBox.information(
                self, "Apply",
                "The current tab isn't a TechnoType category. Switch to one "
                "(Infantry … Defensive) to apply just that tab.")
            return
        self._apply_working(self.collect_spec(only_keys=[key]), [], f"{key} only")

    def _apply_working(self, spec, scripts, label):
        session = self._ensure_session()
        try:
            summary, results = session.apply(spec, scripts)
        except Exception as e:
            QMessageBox.critical(self, "Apply", f"{type(e).__name__}: {e}")
            return
        self._refresh_working_state()
        self.statusBar().showMessage(
            f"Applied {label} to working copy — {session.change_count()} changed "
            f"lines, unsaved. Save it from the Apply menu.")

    def _refresh_working_state(self):
        has = self.session is not None and self.session.lines is not None
        for a in (self.diff_action, self.save_working_action, self.discard_action):
            a.setEnabled(has)
        star = " *" if (has and self.session.dirty) else ""
        base = os.path.basename(self.source_path) if self.source_path else ""
        self.setWindowTitle(f"YRBalanceTool — {base}{star}" if base else "YRBalanceTool")

    def show_working_diff(self):
        if self.session is None or self.session.lines is None:
            return
        self._diff_dialog("Working-copy diff",
                          controller.summarize(self.session.summary, self.session.results),
                          self.session.diff_text())

    def save_working(self):
        if self.session is None or self.session.lines is None:
            return
        base, ext = os.path.splitext(self.source_path)
        default_out = self.out_path or f"{base}_edited{ext or '.ini'}"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save working copy", default_out,
            "INI files (*.ini);;All files (*)")
        if not path:
            return
        try:
            self.session.save(path)
        except OSError as e:
            QMessageBox.critical(self, "Save", f"Could not write:\n{e}")
            return
        self.out_path = path
        self._refresh_working_state()
        self.statusBar().showMessage(f"Working copy saved to {os.path.basename(path)}.")

    def discard_working(self):
        self.session = None
        self._refresh_working_state()
        self.statusBar().showMessage("Working copy discarded.")

    def _diff_dialog(self, title, summary_text, diff_text, offer_apply=False):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(720, 560)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(summary_text))
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setFont(QFont("monospace", 9))
        view.setPlainText(diff_text)
        lay.addWidget(view, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        if offer_apply:
            buttons.addButton("Apply and save…", QDialogButtonBox.AcceptRole)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        lay.addWidget(buttons)
        return dlg.exec()

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

        if self._diff_dialog("Preview changes",
                             controller.summarize(summary, results), diff,
                             offer_apply=True) == QDialog.Accepted:
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
