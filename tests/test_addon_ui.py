#!/usr/bin/env python3
"""
Addon tab strip + Eva approval dialog (phase 2).

The dialog's job is to stop a confidently-wrong spec becoming a silently-empty
tab, so the dry run is what's asserted hardest: a good spec reports its real
match count, a spec naming a tag no mod uses reports "matches nothing", and a
spec that fails validation can't be created at all.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_addon_ui.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_addons import RULES, _Stub, _w
import json

GOOD = {"name": "AttachmentTypes", "source": "typelist",
        "list_section": "AttachmentTypes",
        "columns": [{"label": "Attached to", "tag": "AttachTo"},
                    {"label": "Limit", "tag": "LimitPerUnit"}],
        "notes": "Declared attachments and what they ride on."}


def main():
    ok = True
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        from yrbalance.engine import (
            parse_ini_lines, build_section_map, validate_spec, add_addon,
            load_addons,
        )
        from yrbalance.ui.addon_dialog import AddonRequestDialog, render_spec
        from yrbalance.ui.library_tab import LibraryTab

        rmap = build_section_map(parse_ini_lines(_w(home, "r.ini", RULES)))[0]
        maps = [("turkey-mod", rmap)]

        # ── render_spec is the approval text the user actually reads ────────
        spec, _ = validate_spec(GOOD)
        txt = render_spec(spec)
        rend_ok = ("AttachmentTypes" in txt and "AttachTo" in txt
                   and "Attached to" in txt and "index list" in txt)
        print(f"render_spec shows title, source and tag mapping: "
              f"{'OK' if rend_ok else 'FAIL'}")
        ok = ok and rend_ok

        # ── dry run: a good spec reports its real count ────────────────────
        d = AddonRequestDialog(None, maps)
        d._proposal((spec, [], ""))
        good_ok = (d.create_btn.isEnabled() and "2" in d.dry.text()
                   and "turkey-mod" in d.dry.text())
        print(f"valid spec: dry run counts rows, Create enabled: "
              f"{'OK' if good_ok else 'FAIL'} {d.dry.text()[:60]!r}")
        ok = ok and good_ok

        # ── the real failure mode: plausible spec, nonexistent tag ─────────
        ghost, gerr = validate_spec(dict(GOOD, name="Ghosts",
                                         list_section="NoSuchTypes"))
        d._proposal((ghost, gerr, ""))
        ghost_ok = ("Matches nothing" in d.dry.text()
                    and d.create_btn.isEnabled())   # allowed, but warned
        print(f"spec matching nothing is flagged, not silently created: "
              f"{'OK' if ghost_ok else 'FAIL'}")
        ok = ok and ghost_ok

        # ── an invalid spec cannot be created at all ────────────────────────
        bad, berr = validate_spec({"name": "Bad", "source": "typelist",
                                   "list_section": "X]\n[General",
                                   "columns": ["Cost"]})
        d._proposal((bad, berr, ""))
        bad_ok = (not d.create_btn.isEnabled()
                  and "Rejected" in d.dry.text())
        print(f"invalid spec: Create stays disabled with reasons: "
              f"{'OK' if bad_ok else 'FAIL'}")
        ok = ok and bad_ok

        # Eva asking a question is surfaced, not treated as a crash
        d._proposal((None, ["which tag marks an attachment?"], ""))
        ask_ok = ("which tag" in d.notes.text()
                  and not d.create_btn.isEnabled())
        print(f"Eva's follow-up question is surfaced: "
              f"{'OK' if ask_ok else 'FAIL'}")
        ok = ok and ask_ok

        # ── the tab strip itself ───────────────────────────────────────────
        tab = LibraryTab()
        empty_ok = (not tab.atabs.isVisible() and tab.atabs.count() == 0
                    and "none yet" in tab.addon_hint.text()
                    and tab.addon_new_btn.isEnabled())   # still discoverable
        print(f"no addons: strip hidden, New-tab button still offered: "
              f"{'OK' if empty_ok else 'FAIL'}")
        ok = ok and empty_ok

        add_addon(spec)
        import types as _t
        tab._scan_by_source = {"s1": _t.SimpleNamespace(
            index=_t.SimpleNamespace(rules_map=rmap))}
        tab._rebuild_addon_tabs()
        t0 = tab.atabs.widget(0)
        heads = [t0.horizontalHeaderItem(c).text()
                 for c in range(t0.columnCount())]
        cell = {t0.item(r, 0).text(): r for r in range(t0.rowCount())}
        strip_ok = (tab.atabs.count() == 1
                    and tab.atabs.tabText(0) == "AttachmentTypes"
                    and heads == ["Name", "Source", "Attached to", "Limit"]
                    and t0.rowCount() == 2
                    and t0.item(cell["ATTACHGUN"], 3).text() == "2"
                    and t0.item(cell["ATTACHGUN"], 1).text() != "")
        print(f"addon tab renders Name/Source + its columns: "
              f"{'OK' if strip_ok else 'FAIL'} {heads}")
        if not strip_ok:
            print(f"   rows={t0.rowCount()} tabs={tab.atabs.count()}")
        ok = ok and strip_ok

        # a spec stored but matching nothing still gets a tab (0 rows, no crash)
        ghost2, _ = validate_spec(dict(GOOD, name="Ghosts",
                                       list_section="NoSuchTypes"))
        add_addon(ghost2)
        tab._rebuild_addon_tabs()
        two_ok = (tab.atabs.count() == 2 and len(load_addons()) == 2)
        print(f"a second, empty addon tab coexists: "
              f"{'OK' if two_ok else 'FAIL'}")
        ok = ok and two_ok

        app.processEvents()

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
