"""
Apply a saved voice set onto a unit in a mod.

Preview-then-insert, like the porter: picking a set builds a plan, the plan is
rendered as the exact INI that will be written, and every conflict gets an
explicit merge-vs-keep-both choice before anything is touched. On Apply the
rulesmd/soundmd edits are made IN MEMORY first, then each file is backed up and
written — so a failure part-way leaves the mod alone.

The one thing that can't be staged is the audio.bag: it is a binary archive, not
a line list, so injection writes it directly. Injection is append-only (see
mixlib.inject_audio_samples), which is why that is safe to do last.
"""

import os
import zipfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSplitter, QComboBox, QDialogButtonBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMessageBox, QWidget, QFileDialog,
)

from ..engine import (
    load_sets, build_voice_plan, apply_voice_plan, voice_plan_to_text,
    parse_ini_lines, build_section_map, sibling_file, make_backup,
    build_media_catalog, export_set, import_set, VOICE_FILE_EXT,
)

_MERGE = "Keep the target's"
_BOTH = "Keep both (rename mine)"


def _write(path, lines):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)


class VoiceApplyDialog(QDialog):
    def __init__(self, parent, unit, folder, rules_path):
        super().__init__(parent)
        self.setWindowTitle(f"Apply a voice set to [{unit}]")
        self.resize(900, 640)
        self.unit = unit
        self.folder = folder
        self.rules_path = rules_path
        self.sets = load_sets()
        self._plan = None
        self._choice_rows = []          # [((kind, name), QComboBox)]

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            f"Target: <b>[{unit}]</b> in {os.path.basename(folder) or folder}"))

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("<b>Saved voice sets</b>"))
        self.set_list = QListWidget()
        self.set_list.currentRowChanged.connect(lambda _r: self._rebuild())
        ll.addWidget(self.set_list, 1)
        xrow = QHBoxLayout()
        self.export_btn = QPushButton("Export…")
        self.export_btn.setToolTip(
            f"Save the selected set as a shareable {VOICE_FILE_EXT} file "
            "(manifest + the original sample bytes).")
        self.export_btn.clicked.connect(self._export)
        self.import_btn = QPushButton("Import…")
        self.import_btn.setToolTip(
            f"Add a {VOICE_FILE_EXT} file someone shared to your library.")
        self.import_btn.clicked.connect(self._import)
        xrow.addWidget(self.export_btn)
        xrow.addWidget(self.import_btn)
        xrow.addStretch(1)
        ll.addLayout(xrow)
        ll.addWidget(QLabel("<b>Conflicts</b>"))
        self.conf_table = QTableWidget(0, 3)
        self.conf_table.setHorizontalHeaderLabels(["What", "Name", "Resolution"])
        self.conf_table.verticalHeader().setVisible(False)
        self.conf_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.conf_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        ll.addWidget(self.conf_table, 1)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(QLabel("<b>Preview</b> — exactly what will be written"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("monospace"))
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        rl.addWidget(self.preview, 1)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#888;")
        lay.addWidget(self.status)

        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setDefault(True)
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._apply)
        bb.addButton(self.apply_btn, QDialogButtonBox.AcceptRole)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        # Populated last: selecting a row drives _rebuild(), which needs every
        # widget above to already exist.
        self._reload_sets()
        if not self.sets:
            self.status.setText(
                "No voice sets saved yet — use “Save as voice set…” on a unit's "
                "Voices tab first, or Import one.")

    # ── plan ───────────────────────────────────────────────────────────────
    def _current_set(self):
        it = self.set_list.currentItem()
        if it is None:
            return None
        name = it.data(Qt.UserRole)
        return next((s for s in self.sets if s.name == name), None)

    def _maps(self):
        """(rules_map, sound_map, sound_path, catalog) for the target."""
        rmap = build_section_map(parse_ini_lines(self.rules_path))[0]
        sp = sibling_file(self.folder, "soundmd.ini", "sound.ini")
        smap = build_section_map(parse_ini_lines(sp))[0] if sp else {}
        return rmap, smap, sp, build_media_catalog([self.folder])

    def _choices(self):
        """Current conflict resolutions, keyed the way build_voice_plan wants."""
        out = {}
        for key, cb in self._choice_rows:
            both = cb.currentText() == _BOTH
            if key[0] == "entry":
                out[key] = "keepboth" if both else "reuse"
            else:
                out[key] = "keepboth" if both else "skip"
        return out

    def _rebuild(self):
        """Selected set changed — re-read the target and rebuild everything.

        The conflict rows are only built here, never from a combo's own signal:
        replacing a cell widget deletes it, and doing that inside its own
        handler is a crash.
        """
        vset = self._current_set()
        if vset is None:
            return
        try:
            _rmap, _smap, _sp, _cat = self._maps()
        except OSError as e:
            self.status.setText(f"Could not read the target: {e}")
            return

        # Conflicts depend only on what the target already has, not on how the
        # user resolves them, so this first pass fixes the row set for good.
        base = build_voice_plan(vset, self.unit, _rmap, _smap, _cat)
        self._choice_rows = []
        confs = base.conflicts()
        self.conf_table.setRowCount(len(confs))
        for r, i in enumerate(confs):
            self.conf_table.setItem(r, 0, QTableWidgetItem(i.kind))
            nm = QTableWidgetItem(i.name)
            nm.setToolTip(i.detail)
            self.conf_table.setItem(r, 1, nm)
            cb = QComboBox()
            cb.addItems([_MERGE, _BOTH])
            cb.setToolTip(i.detail)
            cb.currentIndexChanged.connect(lambda _x: self._refresh())
            self.conf_table.setCellWidget(r, 2, cb)
            self._choice_rows.append(((i.kind, i.name), cb))
        self._refresh()

    def _refresh(self):
        """Re-plan with the current choices and redraw the preview only."""
        vset = self._current_set()
        if vset is None:
            return
        try:
            rmap, smap, sp, cat = self._maps()
        except OSError as e:
            self.status.setText(f"Could not read the target: {e}")
            return
        plan = build_voice_plan(vset, self.unit, rmap, smap, cat,
                                self._choices())
        self._plan = plan
        self.preview.setPlainText(voice_plan_to_text(vset, plan))

        can = any(i.kind == "tag" and i.action == "set" for i in plan.items)
        bits = []
        if sp is None:
            bits.append("no soundmd.ini in the target folder — sound entries "
                        "cannot be written")
            can = False
        if not os.path.isfile(os.path.join(self.folder, "audio.idx")):
            bits.append("no audio.bag/.idx here — one will be created")
        if self._choice_rows:
            bits.append(f"{len(self._choice_rows)} conflict(s)")
        bits += plan.warnings
        self.apply_btn.setEnabled(can)
        self.status.setText("  ·  ".join(bits))

    # ── export / import ────────────────────────────────────────────────────
    def _reload_sets(self, select=None):
        self.sets = load_sets()
        self.set_list.clear()
        for s in self.sets:
            it = QListWidgetItem(
                f"{s.name}  ({len(s.entries)} entries, {len(s.samples)} samples)")
            it.setData(Qt.UserRole, s.name)
            self.set_list.addItem(it)
            if select and s.name == select:
                self.set_list.setCurrentItem(it)
        if select is None and self.sets:
            self.set_list.setCurrentRow(0)

    def _export(self):
        vset = self._current_set()
        if vset is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export voice set", f"{vset.name}{VOICE_FILE_EXT}",
            f"Voice set (*{VOICE_FILE_EXT})")
        if not path:
            return
        try:
            _p, n = export_set(vset, path)
        except OSError as e:
            QMessageBox.warning(self, "Export failed", str(e))
            return
        self.status.setText(f"Exported “{vset.name}” with {n} samples to "
                            f"{os.path.basename(path)}.")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import voice set", "", f"Voice set (*{VOICE_FILE_EXT})")
        if not path:
            return
        try:
            vset, missing = import_set(path)
        except (OSError, ValueError, zipfile.BadZipFile) as e:
            QMessageBox.warning(self, "Import failed",
                                f"{type(e).__name__}: {e}")
            return
        self._reload_sets(select=vset.name)
        msg = f"Imported “{vset.name}”"
        if missing:
            msg += (f" — but {len(missing)} sample(s) weren't in the file "
                    f"({', '.join(missing[:4])}"
                    + (", …)" if len(missing) > 4 else ")"))
        self.status.setText(msg + ".")

    # ── apply ──────────────────────────────────────────────────────────────
    def _apply(self):
        vset = self._current_set()
        if vset is None or self._plan is None:
            return
        try:
            rmap, smap, sp, cat = self._maps()
        except OSError as e:
            QMessageBox.warning(self, "Apply failed", str(e))
            return
        if sp is None:
            QMessageBox.warning(self, "Apply failed",
                                "No soundmd.ini beside the target rulesmd.")
            return

        # Re-plan against freshly read maps so what we apply matches the files
        # as they are right now, not as they were when the preview was built.
        plan = build_voice_plan(vset, self.unit, rmap, smap, cat,
                                self._choices())
        rlines = parse_ini_lines(self.rules_path)
        rmap2 = build_section_map(rlines)[0]
        slines = parse_ini_lines(sp)
        smap2 = build_section_map(slines)[0]
        bag = (os.path.join(self.folder, "audio.bag"),
               os.path.join(self.folder, "audio.idx"))
        try:
            summ = apply_voice_plan(rlines, rmap2, vset, plan,
                                    sound=(slines, smap2), bag=bag,
                                    folder=self.folder)
        except Exception as e:
            QMessageBox.warning(self, "Apply failed",
                                f"{type(e).__name__}: {e}")
            return

        # In-memory apply succeeded — now back up and write, same order as the
        # porter: rules first, soundmd only if it actually gained a section.
        try:
            make_backup(self.rules_path, None, keep=10)
            _write(self.rules_path, rlines)
            if summ["entries"]:
                make_backup(sp, None, keep=10)
                _write(sp, slines)
        except OSError as e:
            QMessageBox.warning(self, "Write failed",
                                f"The mod may be partly updated: {e}")
            return

        msg = (f"Applied “{vset.name}” to [{self.unit}] — {summ['tags']} tags, "
               f"{summ['entries']} sound entries added, {summ['reused']} reused, "
               f"{summ['samples']} samples injected")
        if summ["replaced"]:
            msg += f", {summ['replaced']} replaced"
        if summ["loose"]:
            msg += f", {summ['loose']} written loose"
        if summ["skipped"]:
            msg += f", {summ['skipped']} skipped"
        if summ["errors"]:
            QMessageBox.warning(self, "Applied with problems",
                                msg + ".\n\n" + "\n".join(summ["errors"]))
        self.result_message = msg + "."
        self.accept()
