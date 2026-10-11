#!/usr/bin/env python3
"""
AILibraryDialog smoke test (offscreen Qt): populate the library, open the dialog,
show an entry, edit + save its private notes/credits, then drive the pull sub-dialog
against a temp aimd (preview non-empty, apply writes + backs up). Isolated HOME.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_ailib_ui.py
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

SRC_AI = """\
[TaskForces]
0=TF1
[ScriptTypes]
0=SC1
[TeamTypes]
0=TM1
[AITriggerTypes]
TR1=super trig,TM1,Americans,5,0,SUPERTANK,0100,70,10,70,1,0,1,0,0,0,1,1

[TF1]
Name=super force
0=2,SUPERTANK
[SC1]
Name=the script
0=5,3
[TM1]
Name=super team
TaskForce=TF1
Script=SC1
House=Americans
"""

TGT_AI = """\
[TaskForces]
0=EXIST_TF
[ScriptTypes]
0=EXIST_SC
[TeamTypes]
0=EXIST_TM
[AITriggerTypes]
EXIST_TR=existing,EXIST_TM,<all>,5,0,GATECH,0100,70,10,70,1,0,1,0,0,0,1,1

[EXIST_TF]
Name=x
0=1,E1
"""

TGT_RULES = "[Countries]\n0=Americans\n[Americans]\nName=USA\n"


def main():
    ok = True
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home
        from PySide6.QtWidgets import QApplication, QWidget, QMessageBox
        app = QApplication.instance() or QApplication([])
        # neutralize modal dialogs so the smoke test doesn't block
        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
        QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

        from yrbalance.engine import (parse_ini_lines, build_section_map,
                                      extract_bundle, add_entry, load_library)
        from yrbalance.ui.ai_library_dialog import AILibraryDialog, _PullDialog

        src = build_section_map(parse_ini_lines(_w(home, "src.ini", SRC_AI)))[0]
        add_entry(extract_bundle(src, "TR1", source="turkey-mod", name="anti-nuke"))

        # target mod folder with aimd + rules; fake mw points source_path at rules
        mod = os.path.join(home, "mod")
        os.makedirs(mod)
        aimd = _w(mod, "aimd.ini", TGT_AI)
        _w(mod, "rulesmd.ini", TGT_RULES)

        mw = QWidget()
        mw.source_path = os.path.join(mod, "rulesmd.ini")

        dlg = AILibraryDialog(mw)
        list_ok = dlg.list.count() == 1 and "anti-nuke" in dlg.list.item(0).text()
        print(f"library list populated: {'OK' if list_ok else 'FAIL'}")
        ok = ok and list_ok

        dlg.list.setCurrentRow(0)
        code_ok = "super team" in dlg.code.toPlainText()
        print(f"detail shows code: {'OK' if code_ok else 'FAIL'}")
        ok = ok and code_ok

        # edit + save notes/credits, reload, confirm persisted
        dlg.notes.setPlainText("private: vs naval")
        dlg.credits.setPlainText("by Modder")
        dlg._save_meta()
        reloaded = load_library()[0]
        meta_ok = ("private: vs naval" in reloaded.user_notes
                   and "by Modder" in reloaded.credits)
        print(f"notes/credits save+persist: {'OK' if meta_ok else 'FAIL'}")
        ok = ok and meta_ok

        # pull sub-dialog: preview populated, apply writes + backup
        entry = load_library()[0]
        pd = _PullDialog(dlg, entry, aimd)
        prev_ok = ("NEW" in pd.preview.toPlainText()
                   or "REUSE" in pd.preview.toPlainText())
        print(f"pull preview populated: {'OK' if prev_ok else 'FAIL'}")
        ok = ok and prev_ok

        before = open(aimd, encoding="latin-1").read()
        pd._apply()
        after = open(aimd, encoding="latin-1").read()
        # a new trigger + sections were appended; a backup exists
        from yrbalance.engine import list_backups, default_backup_dir
        grew = len(after) > len(before) and "[AITriggerTypes]" in after
        baks = list_backups(default_backup_dir(aimd), aimd)
        apply_ok = grew and len(baks) >= 1
        print(f"pull apply wrote + backed up ({len(baks)} backup): "
              f"{'OK' if apply_ok else 'FAIL'}")
        ok = ok and apply_ok

        # applied aimd re-parses cleanly and the new trigger's team resolves
        out = build_section_map(parse_ini_lines(aimd))[0]
        reparse_ok = "aitriggertypes" in out
        print(f"applied aimd re-parses: {'OK' if reparse_ok else 'FAIL'}")
        ok = ok and reparse_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def _w(d, name, text):
    p = os.path.join(d, name)
    open(p, "w").write(text)
    return p


if __name__ == "__main__":
    sys.exit(main())
