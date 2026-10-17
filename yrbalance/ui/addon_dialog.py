"""
Ask Eva for a Library tab, then approve the spec before it becomes one.

Two-step on purpose. Eva's failure mode here is not crashing, it is confidently
naming a tag that does not exist — which yields a tab that silently finds
nothing and quietly teaches the user to distrust the feature. So the proposal is
shown in plain language AND dry-run against every loaded mod, with the match
count per mod, before the Create button does anything.

A spec that fails engine validation can't be created at all; its reasons are
shown instead. Eva may also answer with a question ("which tag marks an
attachment?"), which is surfaced as-is rather than treated as a failure.
"""

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QDialogButtonBox, QGroupBox, QMessageBox,
)

from ..engine import scan_addon, spec_summary, SRC_TYPELIST, SRC_TECHNOS
from ..ai.ollama_client import OllamaClient
from ..ai.eva import plan_addon
from .encyclopedia_dialog import _Worker


def render_spec(spec):
    """The proposal in plain language — what the tab will do, in INI terms."""
    if spec.source == SRC_TYPELIST:
        where = f"[{spec.list_section}]  (a declared index list)"
    elif spec.source == SRC_TECHNOS:
        where = "every Infantry/Vehicle/Aircraft/BuildingType"
    else:
        where = f"sections whose name starts with {spec.section_prefix!r}"
    L = [f"Tab title    {spec.name}", f"Rows from    {where}"]
    if spec.require_tags:
        L.append("Must set     " + " + ".join(spec.require_tags))
    if spec.any_tags:
        L.append("Any one of   " + ", ".join(spec.any_tags))
    L.append("Columns")
    for c in spec.columns:
        arrow = "" if c.label == c.tag else f"   <- {c.tag}"
        L.append(f"    {c.label}{arrow}")
    if spec.group_by:
        L.append(f"Group by     {spec.group_by}")
    return "\n".join(L)


class AddonRequestDialog(QDialog):
    def __init__(self, parent, rules_maps):
        """`rules_maps` is [(mod label, rules section_map)] for the dry run."""
        super().__init__(parent)
        self.setWindowTitle("New Library tab from Eva")
        self.resize(760, 680)
        self.rules_maps = rules_maps or []
        self.spec = None                # set only once approved
        self._pending = None

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Describe the tab you want. Eva writes the addon; you approve it "
            "before it appears."))

        lay.addWidget(QLabel("<b>What should the tab collect?</b>"))
        self.request = QLineEdit()
        self.request.setPlaceholderText(
            "e.g. a tab for detecting AttachmentTypes")
        self.request.returnPressed.connect(self._ask)
        lay.addWidget(self.request)

        lay.addWidget(QLabel("<b>Reference material</b> — optional, but it is "
                             "what stops Eva guessing tag names"))
        self.reference = QPlainTextEdit()
        self.reference.setFont(QFont("monospace"))
        self.reference.setPlaceholderText(
            "Paste anything that defines the feature:\n"
            "  [AttachmentTypes]\n"
            "  0=ATTACHGUN\n"
            "\n"
            "  [ATTACHGUN]\n"
            "  AttachTo=HTNK\n"
            "  LimitPerUnit=2\n"
            "\n"
            "…a types list, example unit lines, or a tag table from your DLL's "
            "docs. Eva is instructed to use only names that appear here or in "
            "your request.")
        self.reference.setMaximumHeight(190)
        lay.addWidget(self.reference)

        arow = QHBoxLayout()
        self.ask_btn = QPushButton("Ask Eva")
        self.ask_btn.setDefault(True)
        self.ask_btn.clicked.connect(self._ask)
        arow.addWidget(self.ask_btn)
        arow.addStretch(1)
        lay.addLayout(arow)

        box = QGroupBox("Eva's proposal")
        bl = QVBoxLayout(box)
        self.spec_text = QPlainTextEdit()
        self.spec_text.setReadOnly(True)
        self.spec_text.setFont(QFont("monospace"))
        bl.addWidget(self.spec_text, 1)
        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet("color:#888;")
        bl.addWidget(self.notes)
        self.dry = QLabel("")
        self.dry.setWordWrap(True)
        bl.addWidget(self.dry)
        lay.addWidget(box, 1)

        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.create_btn = QPushButton("Create tab")
        self.create_btn.setEnabled(False)
        self.create_btn.clicked.connect(self._accept_spec)
        bb.addButton(self.create_btn, QDialogButtonBox.AcceptRole)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    # ── ask ────────────────────────────────────────────────────────────────
    def _ask(self):
        req = self.request.text().strip()
        if not req:
            return
        st = QSettings("YRBalanceTool", "YRBalanceTool")
        model = st.value("eva_model", "")
        if not model:
            QMessageBox.information(
                self, "Eva",
                "Set up Eva's model in the Eva tab first (Refresh models).")
            return
        client = OllamaClient(
            base_url=st.value("eva_url", "http://localhost:11434"))
        try:
            ctx = int(st.value("eva_num_ctx", 8192))
        except (TypeError, ValueError):
            ctx = 8192
        ref = self.reference.toPlainText()

        self.ask_btn.setEnabled(False)
        self.create_btn.setEnabled(False)
        self.spec_text.setPlainText("")
        self.notes.setText("Eva is designing the tab…")
        self.dry.setText("")

        def work():
            return plan_addon(client, model, req, reference=ref, num_ctx=ctx)

        self._worker = _Worker(work)
        self._worker.done.connect(self._proposal)
        self._worker.failed.connect(
            lambda m: (self.ask_btn.setEnabled(True),
                       self.notes.setText(f"Eva failed: {m}")))
        self._worker.start()

    def _proposal(self, result):
        self.ask_btn.setEnabled(True)
        spec, errors, _raw = result
        if spec is None:
            # Either a dud reply or Eva asking the user a question.
            self.notes.setText("Eva couldn't build a tab from that: "
                               + "; ".join(errors))
            return
        self._pending = spec
        self.spec_text.setPlainText(render_spec(spec))
        self.notes.setText(spec.notes or spec_summary(spec))

        if errors:
            self._pending = None
            self.dry.setText("<b>Rejected:</b> " + "; ".join(errors))
            self.dry.setStyleSheet("color:#c33;")
            self.create_btn.setEnabled(False)
            return

        # Dry run: a tab that matches nothing is the failure we're guarding for.
        counts, total = [], 0
        for label, rmap in self.rules_maps:
            n = len(scan_addon(spec, rmap))
            total += n
            counts.append(f"{label}: {n}")
        if not self.rules_maps:
            self.dry.setText("No mod is loaded, so this can't be dry-run. "
                             "The tab will fill once a source is scanned.")
            self.dry.setStyleSheet("color:#888;")
        elif total == 0:
            self.dry.setText(
                "<b>Matches nothing</b> in your loaded mods (" +
                ", ".join(counts) + "). Either no mod uses this feature yet, "
                "or a tag name is wrong — check the column tags above against "
                "your reference before creating it.")
            self.dry.setStyleSheet("color:#c33;")
        else:
            self.dry.setText(f"Would match <b>{total}</b> row(s) — "
                             + ", ".join(counts))
            self.dry.setStyleSheet("color:#2a2;")
        self.create_btn.setEnabled(True)

    def _accept_spec(self):
        if self._pending is None:
            return
        self.spec = self._pending
        self.accept()
