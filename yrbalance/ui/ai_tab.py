"""
AI (aimd) tab container — nests the AI tooling into three sub-tabs so the single
overwhelming view is split by task:

  • AI Builder  — browse / edit / decode the loaded mod's aimd.ini and generate
                  waves, taskforces and strategic triggers into it (aimd_tab.py).
  • AI Library  — a reusable collection of AITrigger bundles / taskforces /
                  scripts / teamtypes you can extract, bookmark and push into any
                  mod (full collection surface lands in a later phase; for now it
                  launches the existing AI library dialog).
  • Unit Roles  — user-defined unit role lists (Rush, Bombardment, …) that drive
                  one-click AITrigger generation for a whole role.

The container keeps the external `main_window.aimd_tab` handle; its sub-tabs are
reachable as `.builder`, `.library`, `.roles`.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QLineEdit, QListWidget, QListWidgetItem, QSplitter, QPlainTextEdit,
    QMenu, QMessageBox, QInputDialog, QAbstractItemView, QFileDialog, QDialog,
    QComboBox,
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
    load_roles, set_role_units, create_role, delete_role, rename_role,
    parse_type_list, get_val,
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
        from ..engine import working_path
        container = self.parent()
        while container is not None and not isinstance(container, AiTab):
            container = container.parent()
        builder = container.builder if isinstance(container, AiTab) else None

        # If the Builder has an active working file, pull into THAT (checking units
        # against the loaded mod's rules); otherwise pull into the loaded mod's aimd.
        if builder is not None and getattr(builder, "_working", False):
            rules_map = getattr(self.mw, "section_map", None)
            _PullDialog(self, e, working_path(), rules_map=rules_map).exec()
        else:
            tgt_aimd = _sibling_aimd(getattr(self.mw, "source_path", None))
            if not tgt_aimd:
                QMessageBox.information(
                    self, "Send to Builder",
                    "Load a mod with an aimd.ini, or open a working file in the AI "
                    "Builder first — the entry is pulled into that aimd (with "
                    "dedup / merge and a backup).")
                return
            _PullDialog(self, e, tgt_aimd).exec()

        if builder is not None:
            builder._reload()
            container.show_builder()


class UnitRolesPanel(QWidget):
    """User-defined unit role lists → one-click AITrigger generation.

    Left: the role lists (Rush, Bombardment/Anti-*, …), add/rename/delete. Right:
    a checkable list of the loaded mod's units for the selected role — the saved
    picks are pre-checked, and "Suggest units" pre-checks the ones the engine's role
    detector thinks fit, minus any it has graded too weak to be worth fielding
    (freely editable afterwards; what was filtered is named in the status line).
    "Generate AITriggers…" builds
    a TaskForce + Script + TeamType + AITrigger for the checked units with
    role-appropriate weights and pulls it into the AI Builder's current aimd (the
    working file if one is open, else the loaded mod's) with the usual dedup/merge
    preview + backup. Role lists are stored in the unit_roles.json sidecar."""

    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        self.roles = {}
        self._loaded_units_for = None

        root = QHBoxLayout(self)
        # ── roles column ──
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>Roles</b>"))
        self.role_list = QListWidget()
        self.role_list.currentRowChanged.connect(lambda _r: self._show_role())
        self.role_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.role_list.customContextMenuRequested.connect(self._role_menu)
        ll.addWidget(self.role_list, 1)
        newb = QPushButton("New role…")
        newb.clicked.connect(self._new_role)
        ll.addWidget(newb)
        left.setMaximumWidth(220)
        root.addWidget(left)

        # ── units column ──
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.role_title = QLabel("<b>Units</b>")
        rl.addWidget(self.role_title)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Filter:"))
        self.unit_search = QLineEdit()
        self.unit_search.setClearButtonEnabled(True)
        self.unit_search.textChanged.connect(self._filter_units)
        srow.addWidget(self.unit_search, 1)
        rl.addLayout(srow)
        self.unit_list = QListWidget()
        self.unit_list.itemChanged.connect(
            lambda _i: self._refresh_trigger_opts())
        rl.addWidget(self.unit_list, 1)

        # Side and condition: both were previously hardcoded, and both to values
        # that appear in NO vanilla trigger (Side=0, and credits>=0 for
        # everything). Vanilla has no "any side", and ~60% of its triggers are
        # conditional on battlefield state — but vanilla can only test a
        # specific object ID, not a class, so the choice has to be the user's.
        crow = QHBoxLayout()
        crow.addWidget(QLabel("Side:"))
        self.side_combo = QComboBox()
        self.side_combo.setToolTip(
            "AITrigger Side field. All 165 vanilla triggers use 1, 2 or 3 — "
            "there is no 'any side' value, so the trigger has to name one.")
        crow.addWidget(self.side_combo)
        crow.addWidget(QLabel("Fires when:"))
        self.cond_combo = QComboBox()
        self.cond_combo.setMinimumWidth(240)
        self.cond_combo.setToolTip(
            "Condition on the generated AITrigger. 'Always' is the old "
            "behaviour and leaves the team permanently eligible; the owner-side "
            "and enemy-side options make the AI react to what is on the field.")
        crow.addWidget(self.cond_combo, 1)
        rl.addLayout(crow)

        brow = QHBoxLayout()
        suggest = QPushButton("Suggest units")
        suggest.setToolTip("Pre-check units the engine's role detector thinks fit "
                           "this role (from the loaded rules). You can still edit.")
        suggest.clicked.connect(self._suggest)
        save = QPushButton("Save role")
        save.clicked.connect(self._save_role)
        gen = QPushButton("Generate AITriggers…")
        gen.setToolTip("Build a TaskForce + Script + TeamType + AITrigger for the "
                       "checked units and pull it into the AI Builder's aimd.")
        gen.clicked.connect(self._generate)
        brow.addWidget(suggest)
        brow.addStretch(1)
        brow.addWidget(save)
        brow.addWidget(gen)
        rl.addLayout(brow)
        self.status = QLabel("")
        rl.addWidget(self.status)
        root.addWidget(right, 1)

        self.reload()

    def showEvent(self, event):
        super().showEvent(event)
        self._fill_units()      # loaded mod may have changed since last shown

    # ── roles ──
    def reload(self):
        self.roles = load_roles()
        cur = self._current_role()
        self.role_list.blockSignals(True)
        self.role_list.clear()
        for name, units in self.roles.items():
            self.role_list.addItem(QListWidgetItem(f"{name}  ({len(units)})"))
        self.role_list.blockSignals(False)
        row = 0
        if cur:
            for r in range(self.role_list.count()):
                if self.role_list.item(r).text().rsplit("  (", 1)[0] == cur:
                    row = r
                    break
        self.role_list.setCurrentRow(row if self.role_list.count() else -1)

    def _current_role(self):
        it = self.role_list.currentItem()
        return it.text().rsplit("  (", 1)[0] if it else None

    def _fill_units(self):
        """Populate the checkable unit list from the loaded mod's TechnoTypes."""
        sm = getattr(self.mw, "section_map", None)
        src = getattr(self.mw, "source_path", None)
        if sm is self._loaded_units_for:
            self._apply_role_checks()
            return
        self._loaded_units_for = sm
        self.unit_list.clear()
        if not sm:
            self.role_title.setText("<b>Units</b> — load a mod's rules to pick units")
            return
        units = []
        for reg in ("InfantryTypes", "VehicleTypes", "AircraftTypes"):
            units += list(parse_type_list(sm, reg))
        seen = set()
        for u in units:
            if u.lower() in seen:
                continue
            seen.add(u.lower())
            it = QListWidgetItem(u)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            it.setData(Qt.UserRole, u)
            self.unit_list.addItem(it)
        self.role_title.setText(f"<b>Units</b> — {self.unit_list.count()} from "
                                f"{os.path.basename(src or 'rules')}")
        self._apply_role_checks()

    def _apply_role_checks(self):
        """Check the units saved for the current role; uncheck the rest."""
        role = self._current_role()
        saved = {u.lower() for u in self.roles.get(role, [])}
        for r in range(self.unit_list.count()):
            it = self.unit_list.item(r)
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole).lower() in saved
                             else Qt.Unchecked)

    # ── trigger options (side + condition) ─────────────────────────────────
    def _sides(self):
        """[(label, side_number)] from the mod's [Sides], else the YR default."""
        sm = getattr(self.mw, "section_map", None)
        if sm and "sides" in sm:
            # [Sides] is `SideName=countries`, so the KEYS are the side names,
            # and their file order is the 1-based side number.
            ordered = sorted(sm["sides"]["keys"].items(),
                             key=lambda kv: kv[1][0])
            names = [kv[1][1] for kv in ordered]
            if names:
                return [(n, i + 1) for i, n in enumerate(names)]
        return [("Allied", 1), ("Soviet", 2), ("Yuri", 3)]

    def _enemy_objects(self):
        """IDs worth watching on the enemy side, from the loaded rules."""
        sm = getattr(self.mw, "section_map", None)
        if not sm:
            return []
        out = []
        for lst, tags in (("BuildingTypes",
                           ("isbasedefense", "basedefense", "harvester",
                            "constructionyard", "factory")),
                          ("VehicleTypes", ("harvester", "unloads"))):
            for uid in parse_type_list(sm, lst):
                for tag in tags:
                    v = (get_val(sm, uid, tag) or "").strip().lower()
                    if v and v not in ("no", "none", "0", "false"):
                        out.append(uid)
                        break
        # stable, de-duplicated
        seen, uniq = set(), []
        for u in out:
            if u.upper() not in seen:
                seen.add(u.upper())
                uniq.append(u)
        return uniq

    def _refresh_trigger_opts(self):
        from ..wavegen.role_bundles import role_trigger_variants
        if self.side_combo.count() == 0:
            for label, n in self._sides():
                self.side_combo.addItem(f"{n} — {label}", n)
        keep = self.cond_combo.currentText()
        self._cond_variants = role_trigger_variants(
            self._current_role() or "", units=self._checked_units(),
            enemy_objects=self._enemy_objects())
        self.cond_combo.blockSignals(True)
        self.cond_combo.clear()
        for i, v in enumerate(self._cond_variants):
            self.cond_combo.addItem(v["label"], i)
        idx = self.cond_combo.findText(keep)
        self.cond_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.cond_combo.blockSignals(False)

    def _selected_condition(self):
        vs = getattr(self, "_cond_variants", None)
        if not vs:
            return None
        i = self.cond_combo.currentData()
        return vs[i] if isinstance(i, int) and 0 <= i < len(vs) else vs[0]

    def _show_role(self):
        self._apply_role_checks()
        role = self._current_role()
        n = len(self.roles.get(role, []))
        self.status.setText(f"“{role}”: {n} unit(s) saved." if role else "")
        self._refresh_trigger_opts()

    def _checked_units(self):
        return [self.unit_list.item(r).data(Qt.UserRole)
                for r in range(self.unit_list.count())
                if self.unit_list.item(r).checkState() == Qt.Checked]

    def _filter_units(self):
        t = self.unit_search.text().strip().lower()
        for r in range(self.unit_list.count()):
            it = self.unit_list.item(r)
            self.unit_list.setRowHidden(r, bool(t) and t not in it.text().lower())

    def _role_menu(self, pos):
        it = self.role_list.itemAt(pos)
        if it is None:
            return
        name = it.text().rsplit("  (", 1)[0]
        menu = QMenu(self)
        ren = menu.addAction("Rename…")
        dele = menu.addAction("Delete role")
        chosen = menu.exec(self.role_list.mapToGlobal(pos))
        if chosen == ren:
            new, ok = QInputDialog.getText(self, "Rename role", "New name:",
                                           text=name)
            if ok and new.strip():
                if rename_role(name, new.strip()) is None:
                    QMessageBox.warning(self, "Rename",
                                        f"A role named “{new.strip()}” exists.")
                else:
                    self.reload()
        elif chosen == dele:
            if QMessageBox.question(self, "Delete", f"Delete role “{name}”?") \
                    == QMessageBox.Yes:
                delete_role(name)
                self.reload()

    def _new_role(self):
        name, ok = QInputDialog.getText(self, "New role", "Role name:")
        if ok and name.strip():
            create_role(name.strip())
            self.reload()

    def _save_role(self):
        role = self._current_role()
        if not role:
            return
        set_role_units(role, self._checked_units())
        self.reload()
        self.status.setText(f"Saved {len(self._checked_units())} unit(s) to "
                            f"“{role}”.")

    def _suggest(self):
        role = self._current_role()
        src = getattr(self.mw, "source_path", None)
        if not role or not src:
            QMessageBox.information(self, "Suggest",
                                   "Load a mod's rules first, then pick a role.")
            return
        from ..wavegen.role_bundles import suggest_units_for_role
        self.status.setText("Detecting units for this role…")
        self.status.repaint()
        found, rejected = suggest_units_for_role(role, src, with_reasons=True)
        picks = {u.lower() for u in found}
        # Say what was filtered out. A role tag only means "this weapon can hurt
        # that armor class", so weak units match too and get gated — silently
        # shrinking the list would look like the detector missing them.
        skipped = (f"  {len(rejected)} skipped as too weak "
                   f"({', '.join(u for u, _ in rejected[:4])}"
                   + ("…)" if len(rejected) > 4 else ")")) if rejected else ""
        if not picks:
            self.status.setText(
                "No auto-suggestions for this role (custom role, or the detector "
                "found none) — pick units manually." + skipped)
            return
        n = 0
        for r in range(self.unit_list.count()):
            it = self.unit_list.item(r)
            if it.data(Qt.UserRole).lower() in picks:
                it.setCheckState(Qt.Checked)
                n += 1
        self.status.setText(f"Suggested {n} unit(s) — review, then Save or "
                            f"Generate.{skipped}")

    def _generate(self):
        role = self._current_role()
        if not role:
            return
        units = self._checked_units()
        if not units:
            QMessageBox.information(self, "Generate",
                                   "Check at least one unit for this role.")
            return
        from ..wavegen.role_bundles import (build_role_entries, QUEUE_LABEL,
                                            role_one_queue, split_by_queue)
        from ..engine import working_path, target_countries
        from .ai_library_dialog import _PullDialog, _sibling_aimd

        container = self.parent()
        while container is not None and not isinstance(container, AiTab):
            container = container.parent()
        builder = container.builder if isinstance(container, AiTab) else None
        sm = getattr(self.mw, "section_map", None)
        countries = target_countries(sm) if sm else None

        # A role flagged one_queue yields one bundle PER FACTORY QUEUE: a rush
        # mixing infantry, armour and aircraft would trickle out of three build
        # queues instead of massing. Each bundle is pulled separately so the
        # user sees and approves each one.
        entries = build_role_entries(
            role, units,
            side=self.side_combo.currentData() or 1,
            condition=self._selected_condition(),
            rules_map=sm)

        if len(entries) > 1:
            qs = ", ".join(f"{QUEUE_LABEL[q]} ({len(m)})"
                           for q, m in split_by_queue(units, sm))
            if QMessageBox.question(
                    self, "Separate by factory queue",
                    f"“{role}” must not mix factory types, so these units "
                    f"split into {len(entries)} bundles — {qs}.\n\n"
                    "Each gets its own TaskForce, Script, TeamType and "
                    "AITrigger, and is previewed separately. Continue?"
            ) != QMessageBox.Yes:
                return
        elif role_one_queue(role) and not sm:
            QMessageBox.information(
                self, "Generate",
                "No rules are loaded, so factory queues cannot be told apart — "
                "building one combined bundle. Load the mod's rulesmd to have "
                f"“{role}” split by factory type.")

        tgt = None
        if not (builder is not None and getattr(builder, "_working", False)):
            tgt = _sibling_aimd(getattr(self.mw, "source_path", None))
            if not tgt:
                QMessageBox.information(
                    self, "Generate",
                    "Load a mod with an aimd.ini, or open a working file in the AI "
                    "Builder first — the generated triggers are pulled into that "
                    "aimd (with a backup).")
                return

        for entry in entries:
            dest = working_path() if tgt is None else tgt
            if _PullDialog(self, entry, dest, rules_map=sm,
                           countries=countries).exec() != QDialog.Accepted:
                break          # cancelling one bundle abandons the rest

        if builder is not None:
            builder._reload()
            container.show_builder()
