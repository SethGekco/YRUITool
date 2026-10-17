#!/usr/bin/env python3
"""
Voice library (V4): export a set to a shareable .yrvoice and import it back.

Round-trips through a *clean* library so the import really rebuilds everything
from the file, and pins the untrusted-input behaviour: a .yrvoice can come from
anyone, so a manifest naming "../evil" must not write outside the samples dir.

    python3 tests/test_voicelib_share.py
"""

import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_voicelib import _write_bag, _BLOCK
import test_voicelib_apply as va


def main():
    ok = True
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home
        from yrbalance.engine import (
            extract_voice_set, store_samples, build_media_catalog,
            parse_ini_lines, build_section_map, add_set, load_sets,
            remove_set, export_set, import_set, sample_bytes, samples_dir,
            VOICE_FILE_EXT,
        )

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

        # ── export ──────────────────────────────────────────────────────────
        box = os.path.join(home, "share" + VOICE_FILE_EXT)
        _p, n = export_set(vs, box)
        with zipfile.ZipFile(box) as z:
            names = set(z.namelist())
            man = json.loads(z.read("voiceset.json").decode())
        exp_ok = (n == 2 and "voiceset.json" in names
                  and sum(1 for x in names if x.startswith("samples/")) == 2
                  and man["name"] == "GI voices" and len(man["entries"]) == 1)
        print(f"export writes manifest + {n} sample payloads: "
              f"{'OK' if exp_ok else 'FAIL'}")
        ok = ok and exp_ok

        # ── import into a CLEAN library — nothing may come from local state ──
        remove_set("GI voices")
        assert load_sets() == []
        got, missing = import_set(box)
        s1 = next(s for s in got.samples if s.name == "GIYES1")
        imp_ok = (
            len(load_sets()) == 1 and not missing
            and got.name == "GI voices"
            and [e.name for e in got.entries] == ["GIYessir"]
            and got.tags == [["VoiceSelect", "GIYessir"]]
            and s1.provenance == "bag" and s1.chunk_size == 20
            and sample_bytes(got, s1) == _BLOCK * 2)   # bytes survived intact
        print(f"import into a clean library restores all three layers: "
              f"{'OK' if imp_ok else 'FAIL'}")
        ok = ok and imp_ok

        # ── importing twice must not overwrite the first ─────────────────────
        again, _ = import_set(box)
        dup_ok = (len(load_sets()) == 2 and again.name == "GI voices (2)"
                  and again.set_id != got.set_id)
        print(f"re-import gets a fresh name + samples dir: "
              f"{'OK' if dup_ok else 'FAIL'} {again.name!r}")
        ok = ok and dup_ok

        # ── untrusted input: a traversal name is refused, not written ────────
        evil = os.path.join(home, "evil" + VOICE_FILE_EXT)
        doc = dict(man)
        doc["name"] = "evil set"
        doc["samples"] = [dict(man["samples"][0], name="PWN",
                               file="../../pwned.bin")]
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("voiceset.json", json.dumps(doc))
            z.writestr("samples/../../pwned.bin", b"owned")
        ev, ev_missing = import_set(evil)
        escaped = os.path.join(os.path.expanduser("~"), ".yrbalancetool",
                               "pwned.bin")
        trav_ok = (ev_missing == ["PWN"]
                   and not os.path.exists(escaped)
                   and not os.path.exists(os.path.join(home, "pwned.bin"))
                   and os.listdir(samples_dir(ev.set_id)) == []
                   and any("without samples" in x for x in ev.notes))
        print(f"traversal name refused, nothing written outside: "
              f"{'OK' if trav_ok else 'FAIL'}")
        ok = ok and trav_ok

        # ── a non-voice-set file fails cleanly ──────────────────────────────
        junk = os.path.join(home, "junk" + VOICE_FILE_EXT)
        with zipfile.ZipFile(junk, "w") as z:
            z.writestr("readme.txt", "hello")
        try:
            import_set(junk)
            junk_ok = False
        except ValueError:
            junk_ok = True
        print(f"a zip that isn't a voice set raises ValueError: "
              f"{'OK' if junk_ok else 'FAIL'}")
        ok = ok and junk_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def _w(d, name, text):
    p = os.path.join(d, name)
    open(p, "w").write(text)
    return p


if __name__ == "__main__":
    sys.exit(main())
