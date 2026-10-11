#!/usr/bin/env python3
"""
AimdTab smoke test (offscreen Qt): point it at a mod with an aimd.ini, confirm the
four registries fill, a trigger decodes in the detail pane, cross-references list,
jump-to-ref switches tabs, and live search filters.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_aimd_ui.py
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

AIMD = """\
[TaskForces]
0=TF1
[ScriptTypes]
0=SC1
[TeamTypes]
0=TM1
[AITriggerTypes]
TR1=Guard Base,TM1,Americans,7,0,GATECH,0100,70.0,10.0,70.0,1,0,1,0,TM1,0,1,1

[TF1]
Name=guard force
0=2,GGI
[SC1]
Name=the script
0=5,3
[TM1]
Name=guard team
TaskForce=TF1
Script=SC1
House=Americans
"""


def main():
    ok = True
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtCore import Qt
    app = QApplication.instance() or QApplication([])

    d = tempfile.mkdtemp()
    mod = os.path.join(d, "mod")
    os.makedirs(mod)
    rules = os.path.join(mod, "rulesmd.ini")
    open(rules, "w").write("[GI]\n")
    open(os.path.join(mod, "aimd.ini"), "w").write(AIMD)

    from yrbalance.ui.aimd_tab import AimdTab
    mw = QWidget()
    mw.source_path = rules
    mw.section_map = None
    tab = AimdTab(mw)
    tab.show()                 # fire showEvent → load
    app.processEvents()

    counts = {k: tab._lists[k].count() for k in tab._lists}
    fill_ok = (counts["TaskForces"] == 1 and counts["ScriptTypes"] == 1
               and counts["TeamTypes"] == 1 and counts["AITriggerTypes"] == 1)
    print(f"registries fill: {'OK' if fill_ok else 'FAIL'}  {counts}")
    ok = ok and fill_ok

    # select the trigger → decoded detail
    tidx = [k for k, _ in __import__("yrbalance.ui.aimd_tab",
            fromlist=["_REG_TABS"])._REG_TABS].index("AITriggerTypes")
    tab.reg_tabs.setCurrentIndex(tidx)
    tab._lists["AITriggerTypes"].setCurrentRow(0)
    det = tab.detail.toPlainText()
    dec_ok = "Guard Base" in det and "Primary team" in det and "TM1" in det
    print(f"trigger decoded in detail: {'OK' if dec_ok else 'FAIL'}")
    ok = ok and dec_ok

    # referenced-by: the trigger refs TM1 (primary + secondary) → refs list has TM1
    # select TM1 and check its refs include the trigger
    tmidx = [k for k, _ in __import__("yrbalance.ui.aimd_tab",
             fromlist=["_REG_TABS"])._REG_TABS].index("TeamTypes")
    tab.reg_tabs.setCurrentIndex(tmidx)
    tab._lists["TeamTypes"].setCurrentRow(0)
    refs = [tab.refs.item(r).text() for r in range(tab.refs.count())]
    refs_ok = any("TR1" in t for t in refs)
    print(f"cross-refs (team ← trigger): {'OK' if refs_ok else 'FAIL'}  {refs}")
    ok = ok and refs_ok

    # double-click the ref jumps to the trigger tab + selects it
    tab._jump_ref(tab.refs.item(0))
    jumped = (tab.reg_tabs.currentIndex() == tidx
              and tab._lists["AITriggerTypes"].currentItem() is not None)
    print(f"jump-to-ref switches tab + selects: {'OK' if jumped else 'FAIL'}")
    ok = ok and jumped

    # live search filters the TeamTypes list
    tab.search.setText("zzz-nomatch")
    hidden = tab._lists["TeamTypes"].isRowHidden(0)
    tab.search.clear()
    print(f"live search hides non-matches: {'OK' if hidden else 'FAIL'}")
    ok = ok and hidden

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
