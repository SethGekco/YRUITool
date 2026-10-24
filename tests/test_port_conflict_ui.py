#!/usr/bin/env python3
"""
PortConflictDialog smoke: a weapon and its warhead both named the same in the
old and loaded mods, differently. The window lists both, shows the three
versions with the differing value marked, greys the warhead once the weapon
is kept as the loaded mod's, and hands back the choices.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_port_conflict_ui.py [shot.png]
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="yrbt_cfg_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = """\
[InfantryTypes]
0=E1
[E1]
Cost=200
Strength=125
Primary=M60
[M60]
Damage=15
ROF=20
Range=5
Warhead=SA
Projectile=Inv
[SA]
Verses=100%,100%,100%,100%,100%,100%,100%,100%,100%,100%,100%
[Inv]
AG=yes
"""
TGT = BASE + """\
[GI]
Primary=ROCK
[ROCK]
Damage=50
ROF=40
Range=6
Warhead=HE
Projectile=Inv
[HE]
Verses=50%,50%,50%,50%,50%,50%,50%,50%,50%,50%,50%
;
"""
SRC = BASE.replace("0=E1", "0=E1\n1=TANK") + """\
[TANK]
Cost=900
Primary=ROCK
[ROCK]
Damage=90
ROF=40
Range=6
Warhead=HE
Projectile=Inv
[HE]
Verses=100%,100%,100%,100%,100%,100%,100%,100%,100%,100%,100%
"""


def main():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from yrbalance.engine import (parse_ini_lines, build_section_map,
                                  build_port_plan, CONFLICT_KEEP_TARGET,
                                  CONFLICT_BOTH)
    from yrbalance.ui.port_conflict_dialog import PortConflictDialog

    d = tempfile.mkdtemp(prefix="yrbt_pc_")

    def m(name, text):
        p = os.path.join(d, name)
        open(p, "w").write(text)
        return build_section_map(parse_ini_lines(p))[0]

    plan = build_port_plan(m("s.ini", SRC), m("t.ini", TGT), m("v.ini", BASE))
    dlg = PortConflictDialog(None, plan, {"TANK"})
    dlg.show()
    app.processEvents()
    ok = True

    names = [c.name for c in dlg.conflicts]
    rows_ok = names == ["ROCK", "HE"] and dlg.table.rowCount() == 2
    rock = dlg.conflicts[0]
    used_ok = rock.used_by == ["GI"] and "GI" in dlg.table.item(0, 2).text()
    print(f"lists both conflicts, with who uses them in the loaded mod: "
          f"{'OK' if rows_ok and used_ok else 'FAIL'} {names} {rock.used_by}")
    ok = ok and rows_ok and used_ok

    t_txt = dlg._views[0].toPlainText()
    s_txt = dlg._views[1].toPlainText()
    b_txt = dlg._views[2].toPlainText()
    pane_ok = ("Damage=50" in t_txt and "≠ Damage=90" in s_txt
               and "Damage=90" not in b_txt and "Damage=" in b_txt
               and "Loaded mod" in dlg._heads[0].text()
               and "as written" in dlg._heads[1].text()
               and "rebalanced" in dlg._heads[2].text())
    print(f"three panes: loaded / as written / rebalanced, differences "
          f"marked: {'OK' if pane_ok else 'FAIL'}")
    ok = ok and pane_ok

    if len(sys.argv) > 1:
        dlg.grab().save(sys.argv[1])

    default_ok = dlg.resolutions() == {"ROCK": CONFLICT_BOTH,
                                       "HE": CONFLICT_BOTH}
    dlg._combos[0].setCurrentIndex(0)          # ROCK -> keep loaded mod's
    app.processEvents()
    grey = dlg.table.item(1, 0).foreground().color().name()
    reach_ok = (dlg.resolutions()["ROCK"] == CONFLICT_KEEP_TARGET
                and grey == "#9e9e9e")
    dlg.bulk.setCurrentIndex(3)
    dlg._apply_all()
    bulk_ok = set(dlg.resolutions().values()) == {CONFLICT_BOTH}
    print(f"defaults to keep-both; keeping the weapon greys its warhead; "
          f"set-all works: {'OK' if default_ok and reach_ok and bulk_ok else 'FAIL'}"
          f" grey={grey}")
    ok = ok and default_ok and reach_ok and bulk_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
