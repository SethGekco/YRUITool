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
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QLineEdit, QListWidget, QListWidgetItem, QSplitter, QPlainTextEdit,
    QMenu, QMessageBox, QInputDialog, QAbstractItemView, QFileDialog, QDialog,
)

from ..engine import (
    load_library, remove_entry, set_entry_meta, rename_entry,
    set_bookmark, reorder_entries, library_path,
    ai_load_collections as load_collections,
    ai_save_collections as save_collections,
    ai_create_collection as create_collection,
    ai_delete_collection as delete_collection,
    ai_rename_collection as rename_collection,
    ai_add_to_collection as add_to_collection,
    ai_remove_from_collection as remove_from_collection,
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
    """Browsable AI library collection surface — mirrors the regular Library tab.

    A left column lists collections (All entries / ★ Bookmarked / user
    collections); selecting one filters the entry list. Entries drag-reorder, and
    a right-click menu bookmarks / renames / removes them and manages collection
    membership. The right pane shows the entry's INI + private notes + credits.
    Extract-from-aimd and Send-to-Builder (pull into the loaded mod's aimd) live in
    the toolbar. All storage is the ailibrary.json sidecar (never a mod's INI)."""

    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        self.entries = []          # currently displayed LibEntry list
        self._shown = False

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(f"<b>Library:</b> {library_path()}"))
        top.addStretch(1)
        ex = QPushButton("Extract from aimd…")
        ex.clicked.connect(self._extract)
        send = QPushButton("Send to Builder…")
        send.setToolTip("Pull the selected entry into the loaded mod's aimd.ini "
                        "(dedup / merge), then show it in the AI Builder.")
        send.clicked.connect(self._send_to_builder)
        newc = QPushButton("New collection…")
        newc.clicked.connect(self._new_collection)
        top.addWidget(ex)
        top.addWidget(send)
        top.addWidget(newc)
        top.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(220)
        self.search.textChanged.connect(self._refill_entries)
        top.addWidget(self.search)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>Collections</b>"))
        self.col_list = QListWidget()
        self.col_list.currentRowChanged.connect(lambda _r: self._refill_entries())
        self.col_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.col_list.customContextMenuRequested.connect(self._col_context_menu)
        ll.addWidget(self.col_list, 1)
        ll.addWidget(QLabel("<b>Entries</b>"))
        self.entry_list = QListWidget()
        self.entry_list.currentRowChanged.connect(lambda _r: self._show())
        self.entry_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.entry_list.customContextMenuRequested.connect(self._entry_context_menu)
        self.entry_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.entry_list.model().rowsMoved.connect(self._on_entries_reordered)
        ll.addWidget(self.entry_list, 3)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
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
        self.credits.setMaximumHeight(64)
        rl.addWidget(self.credits)
        save = QPushButton("Save notes + credits")
        save.clicked.connect(self._save_meta)
        rl.addWidget(save)
        split.addWidget(right)
        split.setSizes([300, 600])
        root.addWidget(split, 1)

        self.status = QLabel("")
        root.addWidget(self.status)
        self.reload()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._shown:
            self._shown = True
        self.reload()

    # ── load / fill ───────────────────────────────────────────────────────────
    def reload(self):
        """Re-read the library + collections from disk and rebuild both lists."""
        self._all = load_library()
        self._collections = load_collections()
        self._refill_collections()
        self._refill_entries()

    def _refill_collections(self):
        prev = self._current_collection()
        self.col_list.blockSignals(True)
        self.col_list.clear()
        n_all = len(self._all)
        n_bm = sum(1 for e in self._all if e.bookmarked)
        it = QListWidgetItem(f"All entries  ({n_all})")
        it.setData(Qt.UserRole, ("all", None))
        self.col_list.addItem(it)
        it = QListWidgetItem(f"★ Bookmarked  ({n_bm})")
        it.setData(Qt.UserRole, ("bookmarked", None))
        self.col_list.addItem(it)
        want_row = 0
        for c in self._collections:
            it = QListWidgetItem(f"📁 {c.name}  ({len(c.items)})")
            it.setData(Qt.UserRole, ("collection", c.name))
            self.col_list.addItem(it)
        self.col_list.blockSignals(False)
        # restore selection
        if prev:
            for r in range(self.col_list.count()):
                if self.col_list.item(r).data(Qt.UserRole) == prev:
                    want_row = r
                    break
        self.col_list.setCurrentRow(want_row)

    def _current_collection(self):
        it = self.col_list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _entries_for(self, sel):
        """LibEntry list for the selected collection view, in stored order."""
        if sel is None or sel[0] == "all":
            return list(self._all)
        if sel[0] == "bookmarked":
            return [e for e in self._all if e.bookmarked]
        # a user collection: entries in the collection's item order
        col = next((c for c in self._collections if c.name == sel[1]), None)
        if col is None:
            return []
        index = {(e.name, e.kind): e for e in self._all}
        out = []
        for name, kind in col.items:
            e = index.get((name, kind))
            if e is not None:
                out.append(e)
        return out

    def _refill_entries(self):
        sel = self._current_collection()
        text = self.search.text().strip().lower()
        entries = self._entries_for(sel)
        if text:
            entries = [e for e in entries
                       if text in e.name.lower() or text in e.kind.lower()
                       or text in e.source.lower()]
        self.entries = entries
        self.entry_list.blockSignals(True)
        self.entry_list.clear()
        for e in entries:
            star = "★ " if e.bookmarked else ""
            src = f"  ·  {e.source}" if e.source else ""
            it = QListWidgetItem(f"{star}[{e.kind}] {e.name}{src}")
            it.setData(Qt.UserRole, (e.name, e.kind))
            self.entry_list.addItem(it)
        self.entry_list.blockSignals(False)
        if entries:
            self.entry_list.setCurrentRow(0)
        else:
            self._show()
        self.status.setText(f"{len(entries)} entr{'y' if len(entries)==1 else 'ies'}"
                            f" shown · {len(self._all)} total")

    def _current_entry(self):
        i = self.entry_list.currentRow()
        return self.entries[i] if 0 <= i < len(self.entries) else None

    def _show(self):
        e = self._current_entry()
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
        e = self._current_entry()
        if e is None:
            return
        set_entry_meta(e.name, e.kind, user_notes=self.notes.toPlainText(),
                       credits=self.credits.toPlainText())
        self.reload()
        self.status.setText("Saved notes + credits.")

    # ── entry actions ─────────────────────────────────────────────────────────
    def _entry_context_menu(self, pos):
        it = self.entry_list.itemAt(pos)
        if it is None:
            return
        name, kind = it.data(Qt.UserRole)
        e = next((x for x in self._all if x.name == name and x.kind == kind), None)
        if e is None:
            return
        menu = QMenu(self)
        bm = menu.addAction("Remove bookmark" if e.bookmarked else "Bookmark")
        ren = menu.addAction("Rename…")
        rm = menu.addAction("Remove from library")
        menu.addSeparator()
        add_menu = menu.addMenu("Add to collection")
        for c in self._collections:
            add_menu.addAction(c.name).setData(("add", c.name))
        add_menu.addSeparator()
        add_menu.addAction("New collection…").setData(("add_new", None))
        sel = self._current_collection()
        rem_from = None
        if sel and sel[0] == "collection":
            rem_from = menu.addAction(f"Remove from “{sel[1]}”")
        chosen = menu.exec(self.entry_list.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == bm:
            set_bookmark(name, kind, not e.bookmarked)
        elif chosen == ren:
            self._rename_entry(name, kind)
            return
        elif chosen == rm:
            if QMessageBox.question(self, "Remove",
                                    f"Remove [{kind}] {name} from the library?") \
                    != QMessageBox.Yes:
                return
            remove_entry(name, kind)
        elif chosen == rem_from:
            remove_from_collection(sel[1], (name, kind))
        else:
            data = chosen.data()
            if isinstance(data, tuple) and data[0] == "add":
                add_to_collection(data[1], (name, kind))
            elif isinstance(data, tuple) and data[0] == "add_new":
                cname, ok = QInputDialog.getText(self, "New collection",
                                                 "Collection name:")
                if ok and cname.strip():
                    add_to_collection(cname.strip(), (name, kind))
        self.reload()

    def _rename_entry(self, name, kind):
        new, ok = QInputDialog.getText(self, "Rename entry", "New name:", text=name)
        if not ok or not new.strip():
            return
        res = rename_entry(name, kind, new.strip())
        if res is None:
            QMessageBox.warning(self, "Rename",
                                f"Another {kind} named “{new.strip()}” already "
                                "exists in the library.")
            return
        self.reload()

    def _on_entries_reordered(self, *args):
        """Persist the new drag order. In the All view this reorders the whole
        library; in a collection view it reorders that collection's items. Ignored
        while a search filter or the Bookmarked view is active (the visible list is
        a partial subset, so a reorder would be ambiguous)."""
        sel = self._current_collection()
        if self.search.text().strip() or (sel and sel[0] == "bookmarked"):
            self.reload()          # revert the visual move
            return
        order = [self.entry_list.item(r).data(Qt.UserRole)
                 for r in range(self.entry_list.count())]
        if sel and sel[0] == "collection":
            col = next((c for c in self._collections if c.name == sel[1]), None)
            if col is not None:
                col.items = [list(k) for k in order]
                save_collections(self._collections)
        else:
            reorder_entries(order)
        self.reload()

    # ── collection actions ────────────────────────────────────────────────────
    def _col_context_menu(self, pos):
        it = self.col_list.itemAt(pos)
        if it is None:
            return
        mode, name = it.data(Qt.UserRole)
        if mode != "collection":
            return
        menu = QMenu(self)
        ren = menu.addAction("Rename…")
        dele = menu.addAction("Delete collection")
        chosen = menu.exec(self.col_list.mapToGlobal(pos))
        if chosen == ren:
            new, ok = QInputDialog.getText(self, "Rename collection",
                                           "New name:", text=name)
            if ok and new.strip():
                rename_collection(name, new.strip())
                self.reload()
        elif chosen == dele:
            if QMessageBox.question(self, "Delete",
                                    f"Delete collection “{name}”? (entries are "
                                    "kept in the library)") == QMessageBox.Yes:
                delete_collection(name)
                self.reload()

    def _new_collection(self):
        name, ok = QInputDialog.getText(self, "New collection", "Collection name:")
        if ok and name.strip():
            create_collection(name.strip())
            self.reload()

    # ── extract / send ────────────────────────────────────────────────────────
    def _extract(self):
        from .ai_library_dialog import _ExtractDialog, _sibling_aimd
        from ..engine import (parse_ini_lines, build_section_map, add_entry)
        import os as _os
        default = _sibling_aimd(getattr(self.mw, "source_path", None))
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an aimd.ini to extract from",
            _os.path.dirname(default) if default else "",
            "INI files (*.ini);;All files (*)")
        if not path:
            return
        try:
            src = build_section_map(parse_ini_lines(path))[0]
        except Exception as e:
            QMessageBox.critical(self, "Extract", f"{type(e).__name__}: {e}")
            return
        src_rules = None
        for n in ("rulesmd.ini", "rules.ini"):
            rp = _os.path.join(_os.path.dirname(path), n)
            if _os.path.isfile(rp):
                src_rules = build_section_map(parse_ini_lines(rp))[0]
                break
        dlg = _ExtractDialog(self, src, src_rules,
                             label=_os.path.basename(_os.path.dirname(path)))
        if dlg.exec() != QDialog.Accepted or dlg.result_entry is None:
            return
        add_entry(dlg.result_entry)
        self.reload()
        self.status.setText(f"Added [{dlg.result_entry.kind}] "
                            f"{dlg.result_entry.name} to the library.")

    def _send_to_builder(self):
        e = self._current_entry()
        if e is None:
            QMessageBox.information(self, "Send to Builder",
                                   "Select a library entry first.")
            return
        from .ai_library_dialog import _PullDialog, _sibling_aimd
        tgt_aimd = _sibling_aimd(getattr(self.mw, "source_path", None))
        if not tgt_aimd:
            QMessageBox.information(
                self, "Send to Builder",
                "Load a mod with an aimd.ini first — the entry is pulled into the "
                "loaded mod's aimd (with dedup / merge and a backup).")
            return
        _PullDialog(self, e, tgt_aimd).exec()
        # refresh the Builder so the pulled entry shows up
        container = self.parent()
        while container is not None and not isinstance(container, AiTab):
            container = container.parent()
        if isinstance(container, AiTab):
            container.builder._reload()
            container.show_builder()


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
