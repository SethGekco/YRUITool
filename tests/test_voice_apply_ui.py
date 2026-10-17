#!/usr/bin/env python3
"""
VoiceApplyDialog smoke (V3): the preview-then-insert flow end to end on a real
throwaway mod — conflict rows appear, flipping one to keep-both changes the
preview, Apply writes rulesmd + soundmd, takes a backup of each, and injects
the samples into the mod's own audio.bag.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_voice_apply_ui.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_voicelib import _write_bag
import test_voicelib_apply as va


def main():
    ok = True
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])

        from yrbalance.engine import (
            extract_voice_set, store_samples, build_media_catalog,
            parse_ini_lines, build_section_map, parse_audio_idx,
            add_set, default_backup_dir, list_backups,
        )
        from yrbalance.ui.voice_apply_dialog import VoiceApplyDialog, _BOTH

        # a saved set from a source mod
        src = os.path.join(home, "src")
        os.makedirs(src)
        _w(src, "rulesmd.ini", va.SRC_RULES)
        _w(src, "soundmd.ini", va.SRC_SOUND)
        _write_bag(src, ["GIYES1", "GIYES2"])
        vs = extract_voice_set(
            "GI",
            build_section_map(parse_ini_lines(f"{src}/rulesmd.ini"))[0],
            build_section_map(parse_ini_lines(f"{src}/soundmd.ini"))[0],
            source="src", name="GI voices")
        vs.set_id = "gi"
        store_samples(vs, build_media_catalog([src]))
        add_set(vs)

        # the target mod: same unit, a DIFFERENT [GIYessir], and GIYES1 present
        tgt = os.path.join(home, "tgt")
        os.makedirs(tgt)
        rp = _w(tgt, "rulesmd.ini", va.TGT_RULES)
        sp = _w(tgt, "soundmd.ini", va.TGT_SOUND)
        _write_bag(tgt, ["GIYES1"])

        dlg = VoiceApplyDialog(None, "GI", tgt, rp)
        rows = dlg.conf_table.rowCount()
        pv1 = dlg.preview.toPlainText()
        load_ok = (dlg.set_list.count() == 1 and rows == 2
                   and dlg.apply_btn.isEnabled()
                   and "VoiceSelect=GIYessir" in pv1)
        print(f"dialog loads set, lists 2 conflicts, previews: "
              f"{'OK' if load_ok else 'FAIL'} rows={rows}")
        ok = ok and load_ok

        # flip both conflicts to keep-both → preview must switch to the minted
        # names, and must NOT crash (the combo handler used to rebuild its own
        # row, deleting the widget mid-signal)
        for _key, cb in dlg._choice_rows:
            cb.setCurrentText(_BOTH)
        pv2 = dlg.preview.toPlainText()
        flip_ok = ("[GIYessirV]" in pv2 and "GIYES12" in pv2
                   and "VoiceSelect=GIYessirV" in pv2)
        print(f"keep-both re-plans preview to minted names: "
              f"{'OK' if flip_ok else 'FAIL'}")
        ok = ok and flip_ok

        dlg._apply()
        rules_txt = open(rp).read()
        sound_txt = open(sp).read()
        tidx = parse_audio_idx(f"{tgt}/audio.idx")
        nbk = len(list_backups(default_backup_dir(rp), rp))
        sbk = len(list_backups(default_backup_dir(sp), sp))
        ap_ok = (
            "VoiceSelect=GIYessirV" in rules_txt
            and "Strength=120" in rules_txt         # untouched existing key
            and rules_txt.count("[GI]") == 1        # no duplicate section
            and "[GIYessirV]" in sound_txt
            and "Control=9" in sound_txt            # target's own entry kept
            and set(tidx) == {"GIYES1", "GIYES12", "GIYES2"}
            and nbk == 1 and sbk == 1)              # both files backed up
        print(f"apply writes rules+soundmd, backs both up, injects samples: "
              f"{'OK' if ap_ok else 'FAIL'} backups={nbk}/{sbk}")
        if not ap_ok:
            print(f"   idx={sorted(tidx)}")
        ok = ok and ap_ok

        # a mod with no soundmd.ini must block Apply rather than half-write
        bare = os.path.join(home, "bare")
        os.makedirs(bare)
        rp2 = _w(bare, "rulesmd.ini", va.TGT_RULES)
        d2 = VoiceApplyDialog(None, "GI", bare, rp2)
        guard_ok = (not d2.apply_btn.isEnabled()
                    and "soundmd" in d2.status.text())
        print(f"no soundmd.ini in target blocks Apply: "
              f"{'OK' if guard_ok else 'FAIL'}")
        ok = ok and guard_ok

        app.processEvents()

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def _w(d, name, text):
    p = os.path.join(d, name)
    open(p, "w").write(text)
    return p


if __name__ == "__main__":
    sys.exit(main())
