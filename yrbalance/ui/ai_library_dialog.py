"""
AI library dialog — store AITrigger bundles and individual TaskForces / Scripts /
TeamTypes out of an aimd.ini into a reusable private library, annotate them with
private notes + a Credits box, and push them back into any mod's aimd.

Pull surfaces the dedup/merge behaviour (identical blocks reused, near-identical
flagged), lets the user remap or bring referenced units, and always backs up the
target aimd before writing.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QComboBox, QListWidget, QListWidgetItem,
    QPlainTextEdit, QMessageBox, QSplitter, QCheckBox, QDoubleSpinBox,
    QDialogButtonBox, QGroupBox, QWidget, QScrollArea,
)

from ..engine import (
    parse_ini_lines, build_section_map, parse_type_list,
    load_library, add_entry, remove_entry, set_entry_meta,
    extract_block, extract_bundle, build_pull_plan, apply_pull, library_path,
    make_backup, lint_ai,
)

_SINGLE_KINDS = {"taskforce": "TaskForces", "script": "ScriptTypes",
                 "teamtype": "TeamTypes"}


def _sibling_aimd(rules_path):
    if not rules_path:
        return None
    folder = os.path.dirname(rules_path)
    for n in ("aimd.ini", "ai.ini"):
        p = os.path.join(folder, n)
        if os.path.isfile(p):
            return p
    return None


def _write(path, lines):
    nl = "" if (lines and lines[-1].endswith("\n")) else "\n"
    with open(path, "w", encoding="latin-1", errors="replace", newline="") as f:
        f.write("".join(lines) + nl if nl else "".join(lines))


class AILibraryDialog(QDialog):
    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self.setWindowTitle("AI library")
        self.resize(900, 620)
        self.entries = []

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(f"<b>Library:</b> {library_path()}"))
        top.addStretch(1)
        ex = QPushButton("Extract from aimd…")
        ex.clicked.connect(self._extract)
        pull = QPushButton("Pull into current mod…")
        pull.clicked.connect(self._pull)
        rm = QPushButton("Remove")
        rm.clicked.connect(self._remove)
        top.addWidget(ex)
        top.addWidget(pull)
        top.addWidget(rm)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        split.addWidget(self.list)

        right = QWidget()
        rl = QVBoxLayout(right)
        self.code = QPlainTextEdit()
        self.code.setReadOnly(True)
        self.code.setFont(QFont("monospace"))
        rl.addWidget(self.code, 3)

        rl.addWidget(QLabel("<b>Private notes</b> (yours only — never written to a mod)"))
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("private details, reminders, how to use…")
        rl.addWidget(self.notes, 1)
        rl.addWidget(QLabel("<b>Credits</b>"))
        self.credits = QPlainTextEdit()
        self.credits.setPlaceholderText("original author / source credits…")
        self.credits.setMaximumHeight(70)
        rl.addWidget(self.credits)
        save = QPushButton("Save notes + credits")
        save.clicked.connect(self._save_meta)
        rl.addWidget(save)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 640])
        root.addWidget(split, 1)

        self._reload()

    # ── list / detail ───────────────────────────────────────────────────────
    def _reload(self, select=None):
        self.entries = load_library()
        self.list.clear()
        for e in self.entries:
            src = f"  ·  {e.source}" if e.source else ""
            it = QListWidgetItem(f"[{e.kind}] {e.name}{src}")
            self.list.addItem(it)
        if self.entries:
            self.list.setCurrentRow(select if select is not None else 0)

    def _current(self):
        i = self.list.currentRow()
        return self.entries[i] if 0 <= i < len(self.entries) else None

    def _show(self, _row):
        e = self._current()
        if e is None:
            self.code.setPlainText("")
            self.notes.setPlainText("")
            self.credits.setPlainText("")
            return
        lines = []
        if e.trigger_def:
            lines.append(f"[AITriggerTypes]\n{e.trigger_id}={e.trigger_def}\n")
        for b in e.blocks:
            lines.append(f"[{b.id}]  ; {b.kind}")
            lines += [f"{k}={v}" for k, v in b.keys]
            lines.append("")
        if e.units:
            lines.append("; referenced units: " + ", ".join(e.units))
        if e.unit_stubs:
            lines.append("; bundled unit stubs: " + ", ".join(sorted(e.unit_stubs)))
        if e.notes:
            lines.append("; auto-notes: " + " | ".join(e.notes))
        self.code.setPlainText("\n".join(lines))
        self.notes.setPlainText(e.user_notes)
        self.credits.setPlainText(e.credits)

    def _save_meta(self):
        e = self._current()
        if e is None:
            return
        set_entry_meta(e.name, e.kind,
                       user_notes=self.notes.toPlainText(),
                       credits=self.credits.toPlainText())
        row = self.list.currentRow()
        self._reload(select=row)
        QMessageBox.information(self, "AI library", "Saved notes + credits.")

    def _remove(self):
        e = self._current()
        if e is None:
            return
        if QMessageBox.question(self, "Remove",
                                f"Remove [{e.kind}] {e.name} from the library?") \
                != QMessageBox.Yes:
            return
        remove_entry(e.name, e.kind)
        self._reload()

    # ── extract ─────────────────────────────────────────────────────────────
    def _extract(self):
        default = _sibling_aimd(getattr(self.mw, "source_path", None))
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an aimd.ini to extract from",
            os.path.dirname(default) if default else "",
            "INI files (*.ini);;All files (*)")
        if not path:
            return
        try:
            src = build_section_map(parse_ini_lines(path))[0]
        except Exception as e:
            QMessageBox.critical(self, "Extract", f"{type(e).__name__}: {e}")
            return
        # optional rules map (for bundling unit stubs) — the sibling rulesmd
        src_rules = None
        for n in ("rulesmd.ini", "rules.ini"):
            rp = os.path.join(os.path.dirname(path), n)
            if os.path.isfile(rp):
                src_rules = build_section_map(parse_ini_lines(rp))[0]
                break
        dlg = _ExtractDialog(self, src, src_rules, label=os.path.basename(
            os.path.dirname(path)))
        if dlg.exec() != QDialog.Accepted or dlg.result_entry is None:
            return
        add_entry(dlg.result_entry)
        self._reload()
        QMessageBox.information(self, "Extract",
                               f"Added [{dlg.result_entry.kind}] "
                               f"{dlg.result_entry.name} to the library.")

    # ── pull ────────────────────────────────────────────────────────────────
    def _pull(self):
        e = self._current()
        if e is None:
            QMessageBox.information(self, "Pull", "Select a library entry first.")
            return
        tgt_aimd = _sibling_aimd(getattr(self.mw, "source_path", None))
        if not tgt_aimd:
            tgt_aimd, _ = QFileDialog.getOpenFileName(
                self, "Choose the target aimd.ini", "",
                "INI files (*.ini);;All files (*)")
            if not tgt_aimd:
                return
        dlg = _PullDialog(self, e, tgt_aimd)
        dlg.exec()


class _ExtractDialog(QDialog):
    """Pick a trigger (bundle) or a single block to save to the library."""

    def __init__(self, parent, src_map, src_rules, label=""):
        super().__init__(parent)
        self.src = src_map
        self.src_rules = src_rules
        self.label = label
        self.result_entry = None
        self.setWindowTitle("Extract AI to library")
        self.resize(460, 240)

        lay = QGridLayout(self)
        lay.addWidget(QLabel("What to save:"), 0, 0)
        self.kind = QComboBox()
        self.kind.addItem("AITrigger bundle (trigger + teams + taskforces + scripts)",
                          "bundle")
        self.kind.addItem("Single TaskForce", "taskforce")
        self.kind.addItem("Single Script", "script")
        self.kind.addItem("Single TeamType", "teamtype")
        self.kind.currentIndexChanged.connect(self._fill_ids)
        lay.addWidget(self.kind, 0, 1)

        lay.addWidget(QLabel("Item:"), 1, 0)
        self.item = QComboBox()
        lay.addWidget(self.item, 1, 1)

        lay.addWidget(QLabel("Save as name:"), 2, 0)
        self.name = QLineEdit()
        lay.addWidget(self.name, 2, 1)

        self.stub = QCheckBox("Also bundle referenced unit sections (stubs)")
        self.stub.setChecked(self.src_rules is not None)
        self.stub.setEnabled(self.src_rules is not None)
        lay.addWidget(self.stub, 3, 0, 1, 2)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb, 4, 0, 1, 2)
        self.item.currentTextChanged.connect(
            lambda t: self.name.setText(t) if not self.name.text() else None)
        self._fill_ids()

    def _fill_ids(self):
        self.item.clear()
        kind = self.kind.currentData()
        if kind == "bundle":
            trig = self.src.get("aitriggertypes")
            if trig:
                for _klow, (_li, kr, vr) in trig["keys"].items():
                    nm = vr.split(",", 1)[0].strip()
                    self.item.addItem(f"{nm}  [{kr}]", kr)
        else:
            for v in parse_type_list(self.src, _SINGLE_KINDS[kind]):
                self.item.addItem(v, v)
        self.name.clear()
        if self.item.count():
            self.name.setText(self.item.currentText().split("  [")[0])

    def _accept(self):
        kind = self.kind.currentData()
        iid = self.item.currentData()
        name = self.name.text().strip() or (iid or "")
        if not iid:
            QMessageBox.information(self, "Extract", "Nothing to save.")
            return
        try:
            if kind == "bundle":
                self.result_entry = extract_bundle(
                    self.src, iid, source=self.label, name=name,
                    src_rules=self.src_rules if self.stub.isChecked() else None)
            else:
                self.result_entry = extract_block(
                    self.src, kind, iid, source=self.label, name=name)
        except Exception as e:
            QMessageBox.critical(self, "Extract", f"{type(e).__name__}: {e}")
            return
        self.accept()


class _PullDialog(QDialog):
    """Preview + configure a pull into the target aimd, then apply with backup."""

    def __init__(self, parent, entry, tgt_aimd):
        super().__init__(parent)
        self.entry = entry
        self.tgt_aimd = tgt_aimd
        self.setWindowTitle(f"Pull '{entry.name}' into {os.path.basename(tgt_aimd)}")
        self.resize(640, 560)

        self.tl = parse_ini_lines(tgt_aimd)
        self.tgt = build_section_map(self.tl)[0]
        # target countries from the sibling rules, if present
        self.countries = self._target_countries()
        self.tgt_units = set()
        for reg in ("InfantryTypes", "VehicleTypes", "AircraftTypes",
                    "BuildingTypes"):
            self.tgt_units.update(u.lower() for u in parse_type_list(self.tgt, reg))

        lay = QVBoxLayout(self)
        opt = QHBoxLayout()
        self.dedup = QCheckBox("Reuse identical blocks")
        self.dedup.setChecked(True)
        self.dedup.toggled.connect(self._preview)
        opt.addWidget(self.dedup)
        opt.addWidget(QLabel("warn ≥"))
        self.thr = QDoubleSpinBox()
        self.thr.setRange(0.5, 1.0)
        self.thr.setSingleStep(0.05)
        self.thr.setValue(0.95)
        self.thr.setDecimals(2)
        self.thr.valueChanged.connect(self._preview)
        opt.addWidget(self.thr)
        opt.addWidget(QLabel("similar"))
        opt.addStretch(1)
        lay.addLayout(opt)

        # per-unit handling (remap / bring stub)
        self.unit_rows = {}
        if self.entry.units:
            box = QGroupBox("Referenced units")
            scr = QScrollArea()
            scr.setWidgetResizable(True)
            inner = QWidget()
            g = QGridLayout(inner)
            g.addWidget(QLabel("<b>unit</b>"), 0, 0)
            g.addWidget(QLabel("<b>remap to (blank = keep)</b>"), 0, 1)
            g.addWidget(QLabel("<b>bring stub</b>"), 0, 2)
            for r, u in enumerate(self.entry.units, start=1):
                g.addWidget(QLabel(u + ("  (missing)" if u.lower()
                            not in self.tgt_units else "")), r, 0)
                remap = QLineEdit()
                remap.setPlaceholderText("existing unit id")
                remap.textChanged.connect(self._preview)
                g.addWidget(remap, r, 1)
                stub = QCheckBox()
                stub.setEnabled(u in self.entry.unit_stubs)
                stub.toggled.connect(self._preview)
                g.addWidget(stub, r, 2)
                self.unit_rows[u] = (remap, stub)
            scr.setWidget(inner)
            bl = QVBoxLayout(box)
            bl.addWidget(scr)
            box.setMaximumHeight(170)
            lay.addWidget(box)

        lay.addWidget(QLabel("<b>Preview</b>"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("monospace"))
        lay.addWidget(self.preview, 1)

        bb = QDialogButtonBox()
        self.apply_btn = bb.addButton("Apply (backup kept)",
                                      QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Close)
        self.apply_btn.clicked.connect(self._apply)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._preview()

    def _target_countries(self):
        folder = os.path.dirname(self.tgt_aimd)
        for n in ("rulesmd.ini", "rules.ini"):
            rp = os.path.join(folder, n)
            if os.path.isfile(rp):
                from ..engine import target_countries
                return target_countries(build_section_map(parse_ini_lines(rp))[0])
        return []

    def _collect(self):
        remap, bring = {}, []
        for u, (re_edit, stub) in self.unit_rows.items():
            v = re_edit.text().strip()
            if v:
                remap[u] = v
            if stub.isChecked():
                bring.append(u)
        return remap, bring

    def _build_plan(self):
        remap, bring = self._collect()
        return build_pull_plan(self.entry, self.tgt,
                               target_countries=self.countries,
                               unit_remap=remap, bring_stubs=bring,
                               dedup=self.dedup.isChecked(),
                               similarity_threshold=self.thr.value())

    def _preview(self):
        try:
            plan = self._build_plan()
        except Exception as e:
            self.preview.setPlainText(f"{type(e).__name__}: {e}")
            return
        out = []
        for it in plan.items:
            tag = (f"REUSE → [{it.new_id}]" if it.reused
                   else f"NEW   → [{it.new_id}]")
            note = f"   ({it.note})" if it.note else ""
            out.append(f"{it.kind:9} {it.old_id:16} {tag}{note}")
        if plan.trigger_def:
            out.append(f"trigger   {self.entry.trigger_id:16} "
                       f"NEW   → [{plan.trigger_new_id}]")
        if plan.stub_units:
            out.append("\nunit stubs to add: " + ", ".join(plan.stub_units))
        if plan.warnings:
            out.append("\n⚠ " + "\n⚠ ".join(plan.warnings))
        self.preview.setPlainText("\n".join(out) or "(nothing to pull)")

    def _apply(self):
        try:
            plan = self._build_plan()
            make_backup(self.tgt_aimd, None, keep=10)
            summ = apply_pull(self.tl, self.tgt, self.entry, plan)
            _write(self.tgt_aimd, self.tl)
        except Exception as e:
            QMessageBox.critical(self, "Pull", f"{type(e).__name__}: {e}")
            return
        errs = sum(1 for f in lint_ai(build_section_map(self.tl)[0], {})
                   if f.severity == "error"
                   and ("=[" in f.message or "team [" in f.message))
        QMessageBox.information(
            self, "Pull",
            f"Pulled into {os.path.basename(self.tgt_aimd)} (backup kept):\n"
            f"  {summ['taskforce']} taskforce, {summ['script']} script, "
            f"{summ['teamtype']} teamtype, {summ['trigger']} trigger added; "
            f"{summ['reused']} reused; {summ['unit_stub']} unit stub(s).\n"
            f"AI ref errors in result: {errs}.")
        self.accept()
