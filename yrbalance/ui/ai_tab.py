"""
AI (aimd) tab container — nests the AI tooling into three sub-tabs so the single
overwhelming view is split by task:

  • AI Builder  — browse / edit / decode the loaded mod's aimd.ini and generate
                  waves, taskforces and strategic triggers into it (aimd_tab.py).
  • AI Library  — a reusable collection of AITrigger bundles / taskforces /
                  scripts / teamtypes you can extract, bookmark and push into any
                  mod (full collection surface lands in a later phase; for now it
                  launches the existing AI library dialog).
  • Unit Roles  — user-defined role lists (Rush, Bombardment, …) that will drive
                  quick AITrigger generation (lands in a later phase).

The container keeps the external `main_window.aimd_tab` handle; its sub-tabs are
reachable as `.builder`, `.library`, `.roles`.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
)

from .aimd_tab import AiBuilderTab


class AiTab(QWidget):
    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.sub = QTabWidget()
        self.builder = AiBuilderTab(mw)
        self.library = AiLibraryPanel(mw)
        self.roles = UnitRolesPanel(mw)
        self.sub.addTab(self.builder, "AI Builder")
        self.sub.addTab(self.library, "AI Library")
        self.sub.addTab(self.roles, "Unit Roles")
        root.addWidget(self.sub)

    def show_builder(self):
        self.sub.setCurrentWidget(self.builder)


class AiLibraryPanel(QWidget):
    """Placeholder surface for the AI library. For now it launches the existing
    modal AI library dialog (extract / pull / notes / credits); a full browsable
    collection — bookmarks, collections, drag-reorder, Send-to-Builder — replaces
    this in a later phase."""

    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        title = QLabel("<h3>AI Library</h3>")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        blurb = QLabel(
            "Store AITrigger bundles, TaskForces, Scripts and TeamTypes in a "
            "reusable private library, annotate them, and push them into any mod's "
            "aimd.ini with automatic dedup / merge.\n\n"
            "A full browsable collection (bookmarks, collections, drag-reorder and "
            "Send&nbsp;to&nbsp;Builder) is coming to this tab. For now, open the "
            "library dialog:")
        blurb.setWordWrap(True)
        blurb.setAlignment(Qt.AlignCenter)
        blurb.setMaximumWidth(560)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(blurb)
        row.addStretch(1)
        lay.addLayout(row)
        brow = QHBoxLayout()
        brow.addStretch(1)
        open_b = QPushButton("Open AI library…")
        open_b.clicked.connect(self._open)
        brow.addWidget(open_b)
        brow.addStretch(1)
        lay.addLayout(brow)
        lay.addStretch(2)

    def _open(self):
        from .ai_library_dialog import AILibraryDialog
        AILibraryDialog(self.mw).exec()


class UnitRolesPanel(QWidget):
    """Placeholder for user-defined unit role lists (Rush, Bombardment, Anti-air,
    …). Lands in a later phase: curate units per role (auto-suggested from the
    engine's role detection, freely editable) and generate AITriggers for them."""

    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        title = QLabel("<h3>Unit Roles</h3>")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        blurb = QLabel(
            "Define reusable unit role lists — rushing units, bombardment units, "
            "anti-air, and so on — and generate AITriggerTypes for a whole role at "
            "once.\n\nComing in a later update. Units will be auto-suggested per "
            "role from the loaded rules and freely editable.")
        blurb.setWordWrap(True)
        blurb.setAlignment(Qt.AlignCenter)
        blurb.setMaximumWidth(560)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(blurb)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(2)
