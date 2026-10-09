"""
MainWindow — assembles the toolbar, the TechnoType tabs, and the bottom script
panel, and wires "Apply and save" to the controller.
"""

import os
import sys

from PySide6.QtCore import Qt, QSettings, QDir, QTimer
from PySide6.QtGui import QFont, QPalette, QColor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QSplitter, QComboBox, QFileDialog, QMessageBox, QPlainTextEdit,
    QDialog, QDialogButtonBox, QApplication, QLineEdit, QSpinBox,
)

from ..engine import (
    ModConfig, BalanceSpec, parse_ini_lines, build_section_map, restore_vanilla,
    make_backup, default_backup_dir,
)
from . import controller, blueprint
from .category_tab import CategoryTab
from .section_form_tab import SectionFormTab
from .global_tab import GlobalTab
from .dynamic_section_tab import DynamicSectionTab
from .history_tab import HistoryTab
from .eva_tab import EvaTab
from .script_panel import ScriptPanel, ScriptTab, show_script_help

VOICE_RESTORE_SCRIPT = """\
[CLNT]
VoiceSelect = ClintSelect
VoiceMove = ClintMove
VoiceAttack = ClintAttackCommand
VoiceFeedback = ClintFear
VoiceSpecialAttack = ClintMove
DieSound = ClintDie

[ARND]
VoiceSelect = ArnoldSelect
VoiceMove = ArnoldMove
VoiceAttack = ArnoldAttackCommand
VoiceFeedback = ArnoldFear
VoiceSpecialAttack = ArnoldMove
DieSound = ArnoldDie

[STLN]
VoiceSelect = SlySelect
VoiceMove = SlyMove
VoiceAttack = SlyAttackCommand
VoiceFeedback = SlyFear
VoiceSpecialAttack = SlyMove
DieSound = SlyDie
"""

# [General] tags to hide from the General tab (obsolete or hardcoded in RA2/YR).
# Intentionally small for now — to be curated against ModENC.
GENERAL_EXCLUDE = set()

GAMEPLAY_SECTIONS = ["CombatDamage", "Radiation", "ElevationModel", "AudioVisual",
                     "SpecialWeapons", "CrateRules", "JumpjetControls"]

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
        self.lines = None    # loaded rules lines (for the History tab)
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
                link_label=link_label or "Vehicles",
                garrison=(key == "inf"), opentopped=(key == "veh"))
        # Populate each tab's "Same as X" dropdown with the other categories.
        cat_display = [(k, lbl) for k, lbl, *_ in TECHNO_TABS]
        for key, tab in self.category_tabs.items():
            tab.set_link_options([(k, lbl) for k, lbl in cat_display if k != key])
        # Defaults: Naval inherits Vehicles, Defenses inherits Structures.
        self.category_tabs["nav"].set_link_source("veh")
        self.category_tabs["def"].set_link_source("bld")

        # General / Gameplay auto-populate from the loaded rules; AI is a small
        # curated form (spans [AI] plus some [General] tags).
        self.form_tabs = {
            "general": DynamicSectionTab(
                sections=["General"], editable_section=False,
                exclude=GENERAL_EXCLUDE,
                help_text="Every [General] tag from your loaded rules, defaulting "
                          "to the imported value. Tick a row (or edit a value, "
                          "which auto-ticks it) to apply it."),
            "ai": SectionFormTab(
                "AI",
                seed_rows=[("RelaxedStray", "3.0"), ("BaseBias", "2"),
                           ("BaseDefenseDelay", "0.25"),
                           ("AIAlternateProductionCreditCutoff", "1000")],
                help_text="[AI] tags (and AI-relevant [General] tags — change the "
                          "section as needed). See modenc.renegadeprojects.com/AI."),
            "gameplay": DynamicSectionTab(
                sections=GAMEPLAY_SECTIONS, editable_section=True,
                help_text="Non-TechnoType gameplay sections. Pick or type a section "
                          "([CombatDamage], [Radiation], a super-weapon, …); its "
                          "tags load from your rules.",
                extra_toggles=[(
                    "voices",
                    "Restore hero voices to vanilla (CLNT / ARND / STLN)",
                    VOICE_RESTORE_SCRIPT)]),
        }

        self.history_tab = HistoryTab()

        self.script_panel = ScriptPanel(presets_dirs=_preset_dirs())
        self.script_panel.preview_requested.connect(self.preview_script)
        self.script_tab = ScriptTab(self.script_panel)
        self.eva_tab = EvaTab(self)

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
        self.tabs.addTab(self.eva_tab, "Eva")
        root.addWidget(self.tabs)

        self.setCentralWidget(central)
        self.setAcceptDrops(True)  # drag a rules file onto the window to load it
        self._build_menu()
        self.settings = QSettings("YRBalanceTool", "YRBalanceTool")
        self._restore_settings()
        self.statusBar().showMessage("Load a rulesmd.ini to begin.")
        # Reopen the last file shortly after the window appears.
        QTimer.singleShot(0, self._auto_load_last)

    def _auto_load_last(self):
        last = self.settings.value("last_rules", "")
        if last and os.path.isfile(last):
            self._load_rules_path(last)
            self.statusBar().showMessage(f"Reopened {os.path.basename(last)}.")

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

    # ── Write-mode / backup settings ─────────────────────────────────────────
    def _write_in_place(self):
        return self.settings.value("write_mode", "inplace") == "inplace"

    def _backup_cfg(self):
        bdir = self.settings.value("backup_dir", "") or None
        try:
            keep = int(self.settings.value("backup_keep", 10))
        except (TypeError, ValueError):
            keep = 10
        return bdir, keep

    def _make_backup(self):
        """Back up the source file. Returns the backup path, or False on failure."""
        bdir, keep = self._backup_cfg()
        try:
            return make_backup(self.source_path, bdir, keep)
        except OSError as e:
            QMessageBox.critical(
                self, "Backup",
                f"Could not create a backup:\n{e}\n\nAborting — nothing was "
                "written (in-place edits always back up first).")
            return False

    def open_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Settings")
        dlg.resize(560, 220)
        lay = QVBoxLayout(dlg)

        mode_box = QComboBox()
        mode_box.addItem("Edit the loaded file in place (back it up first)", "inplace")
        mode_box.addItem("Save to a separate file (leave the original)", "separate")
        idx = mode_box.findData("inplace" if self._write_in_place() else "separate")
        mode_box.setCurrentIndex(max(0, idx))
        lay.addWidget(QLabel("When applying changes:"))
        lay.addWidget(mode_box)

        brow = QHBoxLayout()
        brow.addWidget(QLabel("Backup folder:"))
        bdir_edit = QLineEdit(self.settings.value("backup_dir", ""))
        bdir_edit.setPlaceholderText("(default: a 'yrbt_backups' folder next to the rules)")
        brow.addWidget(bdir_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self._pick_backup_dir(bdir_edit))
        brow.addWidget(browse)
        lay.addLayout(brow)

        krow = QHBoxLayout()
        krow.addWidget(QLabel("Keep this many backups per file (0 = unlimited):"))
        keep_spin = QSpinBox()
        keep_spin.setRange(0, 999)
        keep_spin.setValue(self._backup_cfg()[1])
        krow.addWidget(keep_spin)
        krow.addStretch(1)
        lay.addLayout(krow)

        lay.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)

        if dlg.exec() == QDialog.Accepted:
            self.settings.setValue("write_mode", mode_box.currentData())
            self.settings.setValue("backup_dir", bdir_edit.text().strip())
            self.settings.setValue("backup_keep", keep_spin.value())

    def _pick_backup_dir(self, edit):
        d = QFileDialog.getExistingDirectory(self, "Backup folder",
                                             edit.text().strip() or self._last_dir())
        if d:
            edit.setText(d)

    # ── Drag and drop a rules file onto the window ───────────────────────────
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if not p:
                continue
            if os.path.isdir(p):
                cand = os.path.join(p, "rulesmd.ini")
                if not os.path.isfile(cand):
                    continue
                p = cand
            if os.path.isfile(p):
                self._load_rules_path(p)
                event.acceptProposedAction()
                return

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
        file_menu.addAction("Settings…", self.open_settings)
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

        tools = mb.addMenu("Tools")
        tools.addAction("Lint AI…", self.lint_ai)
        tools.addAction("Port mod content…", self.port_mod_content)
        tools.addAction("Encyclopedia…", self.open_encyclopedia)
        tools.addAction("MIX browser…", self.open_mix_browser)

        view = mb.addMenu("View")
        self.dark_action = view.addAction("Dark theme")
        self.dark_action.setCheckable(True)
        self.dark_action.toggled.connect(self._apply_theme)

        help_menu = mb.addMenu("Help")
        help_menu.addAction("Custom script help…", lambda: show_script_help(self))
        help_menu.addAction("About YRBalanceTool", self._show_about)

    def open_encyclopedia(self):
        """Open the multi-mod unit encyclopedia."""
        from .encyclopedia_dialog import EncyclopediaDialog
        EncyclopediaDialog(self).exec()

    def open_mix_browser(self):
        """Open the MIX archive browser."""
        from .mix_dialog import MixBrowserDialog
        MixBrowserDialog(self).exec()

    def port_mod_content(self):
        """Open the mod-content porter dialog."""
        from .port_dialog import PortDialog
        if not self.source_path or self.section_map is None:
            QMessageBox.information(self, "Port", "Load your target rules first.")
            return
        PortDialog(self).exec()

    def lint_ai(self):
        """Lint the loaded mod's AI (TaskForces/ScriptTypes/TeamTypes/triggers)."""
        if not self.source_path or self.section_map is None:
            QMessageBox.information(self, "Lint AI", "Load a rules file first.")
            return
        try:
            findings, src = controller.lint_ai_for(self.source_path,
                                                   self.section_map)
        except Exception as e:
            QMessageBox.critical(self, "Lint AI", f"{type(e).__name__}: {e}")
            return
        self._show_lint_results(findings, src)

    def _show_lint_results(self, findings, src):
        from ..engine import format_findings
        errs = sum(1 for f in findings if f.severity == "error")
        warns = sum(1 for f in findings if f.severity == "warning")
        where = f" (from {src})" if src else ""
        dlg = QDialog(self)
        dlg.setWindowTitle("AI lint results")
        dlg.resize(680, 460)
        lay = QVBoxLayout(dlg)
        head = QLabel(f"<b>{errs} error(s), {warns} warning(s)</b>{where}")
        head.setWordWrap(True)
        lay.addWidget(head)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setFont(QFont("monospace", 10))
        view.setPlainText(format_findings(findings))
        lay.addWidget(view, 1)
        note = QLabel(
            "Conservative check: only genuine breakage (dangling references, bad "
            "counts/indices). Unknown tags/objects are NOT flagged — they may be "
            "Ares/Phobos/KratosPP features.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        row.addWidget(close)
        lay.addLayout(row)
        dlg.exec()

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

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(
            "…or paste a full path here and press Enter (or drag a file onto the window)")
        self.path_edit.setClearButtonEnabled(True)
        self.path_edit.returnPressed.connect(self._load_from_path_edit)
        bar.addWidget(self.path_edit, 1)

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
        # A non-native dialog so we can force hidden files/folders (e.g. .wine)
        # to be visible — the native dialog hides them unless the user knows Ctrl+H.
        dlg = QFileDialog(self, "Load rules", self._last_dir())
        dlg.setFileMode(QFileDialog.ExistingFile)
        # Default to just rulesmd.ini (+ folders); the other filters are there if
        # the file is named differently.
        dlg.setNameFilters(["Rules file (rulesmd.ini)", "INI files (*.ini)",
                            "All files (*)"])
        dlg.setOption(QFileDialog.DontUseNativeDialog, True)
        dlg.setFilter(dlg.filter() | QDir.Filter.Hidden)
        if dlg.exec() == QDialog.Accepted:
            files = dlg.selectedFiles()
            if files:
                self._load_rules_path(files[0])

    def _load_from_path_edit(self):
        """Load a path typed or pasted into the toolbar field (~ and dirs OK)."""
        raw = self.path_edit.text().strip().strip('"').strip("'")
        if not raw:
            return
        path = os.path.expanduser(raw)
        if os.path.isdir(path):
            cand = os.path.join(path, "rulesmd.ini")
            if os.path.isfile(cand):
                path = cand
            else:
                QMessageBox.warning(self, "Load", f"No rulesmd.ini in:\n{path}")
                return
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Load", f"File not found:\n{path}")
            return
        self._load_rules_path(path)

    def _load_rules_path(self, path):
        try:
            self.lines, self.section_map = controller.load_rules(path)
        except OSError as e:
            QMessageBox.critical(self, "Load", f"Could not read:\n{e}")
            return
        self.source_path = path
        self.out_path = None
        self.session = None
        self.path_edit.setText(path)
        self.apply_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self._refresh_working_state()
        # Populate the dynamic (General / Gameplay) tabs from the loaded rules.
        for tab in self.form_tabs.values():
            if isinstance(tab, DynamicSectionTab):
                tab.set_rules(self.section_map)
        # History tab: scan the loaded rules for tool-annotated changes.
        self.history_tab.set_rules(self.lines, self.section_map, self.config)
        self._archive_rules(path)
        self.settings.setValue("last_rules", path)
        self._record_load_history(path)

    def _record_load_history(self, path):
        """Snapshot sections on load and report what changed since last time."""
        try:
            from ..engine import record_load
            diff = record_load(path, self.lines, self.section_map)
            n = len(diff["changed"]) + len(diff["added"]) + len(diff["removed"])
            if n:
                self.statusBar().showMessage(
                    f"Loaded {os.path.basename(path)} — since last load: "
                    f"{len(diff['changed'])} changed, {len(diff['added'])} added, "
                    f"{len(diff['removed'])} removed.")
        except Exception:
            pass

    def _log_applied_history(self, out_path, note):
        """Log what an apply changed (edit event) + refresh the snapshot so the
        next load doesn't re-report it. Best-effort; never breaks an apply."""
        try:
            from ..engine import (section_fingerprints, diff_fingerprints,
                                   record_changes, update_snapshot,
                                   parse_ini_lines, build_section_map)
            old = section_fingerprints(self.lines, self.section_map)
            nl = parse_ini_lines(out_path)
            nm = build_section_map(nl)[0]
            d = diff_fingerprints(old, section_fingerprints(nl, nm))
            touched = d["changed"] + d["added"]
            if touched:
                record_changes(out_path, touched, note=note)
            update_snapshot(out_path, nl, nm)
        except Exception:
            pass

    def _archive_rules(self, path):
        """Keep a copy of the loaded rules as a historical record (once per file)."""
        try:
            import hashlib
            archive_dir = os.path.join(os.path.expanduser("~"),
                                       ".yrbalancetool", "history")
            os.makedirs(archive_dir, exist_ok=True)
            with open(path, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()[:8]
            base = os.path.splitext(os.path.basename(path))[0]
            dest = os.path.join(archive_dir, f"{base}_{digest}.ini")
            if not os.path.exists(dest):
                with open(path, "rb") as src, open(dest, "wb") as out:
                    out.write(src.read())
        except OSError:
            pass  # archiving is best-effort
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

        return BalanceSpec(
            categories=cats,
            range_threshold=self.global_tab.range_threshold_value(),
            occupant_ratio=self.category_tabs["inf"].occupant_ratio(),
            passenger_ratio=self.category_tabs["veh"].passenger_ratio())

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
        """All script text to run: the form tabs, the OpenTopped globals, the box."""
        scripts = [self.form_tabs[k].to_script_text()
                   for k in ("general", "ai", "gameplay")]
        scripts.append(self.category_tabs["veh"].opentopped_script())
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
        # Reflect the applied edits in the History tab.
        try:
            sm, _ = build_section_map(session.lines)
            self.history_tab.set_rules(session.lines, sm, self.config)
        except Exception:
            pass
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

    def preview_script_text(self, script_text):
        """Preview a single script on its own (used by Eva). Diff only."""
        if not self.source_path:
            QMessageBox.information(self, "Preview", "Load a rules file first.")
            return
        if not script_text.strip():
            return
        try:
            summary, results, diff = controller.preview_changes(
                self.source_path, BalanceSpec(categories={}), [script_text],
                self.config)
        except Exception as e:
            QMessageBox.critical(self, "Preview", f"{type(e).__name__}: {e}")
            return
        self._diff_dialog("Eva script preview",
                          controller.summarize(summary, results), diff)

    def _resolve_target(self):
        """Return (out_path, backup_path_or_None) for a write, or (None, None) to abort."""
        if self._write_in_place():
            backup = self._make_backup()
            if backup is False:
                return None, None
            return self.source_path, backup
        if not self.out_path:
            base, ext = os.path.splitext(self.source_path)
            path, _ = QFileDialog.getSaveFileName(
                self, "Save edited rules", f"{base}_edited{ext or '.ini'}",
                "INI files (*.ini);;All files (*)")
            if not path:
                return None, None
            self.out_path = path
        return self.out_path, None

    def _apply_core(self, spec, scripts, restore_first):
        """Resolve target, back up (in-place), apply, refresh History. No dialog.
        Returns (out, backup, summary, results) or None."""
        out, backup = self._resolve_target()
        if out is None:
            return None
        try:
            summary, results = controller.apply_and_save(
                self.source_path, out, spec, scripts=scripts, config=self.config,
                restore_first=restore_first)
        except Exception as e:
            QMessageBox.critical(self, "Apply", f"{type(e).__name__}: {e}")
            return None
        try:
            olines, osm = controller.load_rules(out)
            self.history_tab.set_rules(olines, osm, self.config)
        except OSError:
            pass
        return out, backup, summary, results

    def _after_write(self, out, backup, summary, results, title):
        self.statusBar().showMessage(
            f"Wrote {os.path.basename(out)}"
            + (f" (backup: {os.path.basename(backup)})" if backup else "") + ".")
        dlg = QMessageBox(self)
        dlg.setWindowTitle(title)
        dlg.setIcon(QMessageBox.Information)
        msg = f"Wrote:\n{out}\n"
        if backup:
            msg += f"\nBacked up the previous file to:\n{backup}\n"
        dlg.setText(msg + "\n" + controller.summarize(summary, results))
        if summary.weapon_conflict_text.strip():
            dlg.setDetailedText(summary.weapon_conflict_text)
        self._log_applied_history(out, title)
        dlg.exec()

    def apply_and_save(self):
        if not self.source_path:
            return
        r = self._apply_core(self.collect_spec(), self.collect_scripts(),
                             self._write_in_place())
        if r:
            self._after_write(*r, "Applied")

    def apply_script_to_rules(self, script_text):
        """Apply a single script (Eva) directly, with a backup. Stacks on current."""
        if not self.source_path:
            QMessageBox.information(self, "Apply", "Load a rules file first.")
            return
        if not script_text.strip():
            return
        r = self._apply_core(BalanceSpec(categories={}), [script_text], False)
        if r:
            self._after_write(*r, "Eva applied")
