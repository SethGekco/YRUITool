#!/usr/bin/env python3
"""
MIX archive library: filename hash, round-trip write/read (names preserved),
encrypted-mix refusal, and corruption guard.

    python3 tests/test_mix.py
"""

import sys
import os
import struct
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import mix_id, MixFile, write_mix, MixError, MixEncrypted


def main():
    ok = True

    # ── hash: stable, case-insensitive, matches observed real values ────────
    id_ok = (mix_id("test.ini") == 3148389924
             and mix_id("TEST.INI") == mix_id("test.ini")
             and mix_id("gtnk01.vxl") == 175036260
             and mix_id("a/b.shp") == mix_id("a\\b.shp"))   # '/'→'\\'
    print(f"mix_id hash (stable, case-insensitive, slash-norm): "
          f"{'OK' if id_ok else 'FAIL'}")
    ok = ok and id_ok

    with tempfile.TemporaryDirectory() as d:
        # ── round-trip write → read, with name preservation ────────────────
        files = {
            "rulesmd.ini": b"[General]\nName=Test\n",
            "gtnk01.vxl": b"Voxel Animation\x1a" + b"\x00" * 40,
            "fire.wav": b"RIFF\x10\x00\x00\x00WAVE",
        }
        p = os.path.join(d, "out.mix")
        n = write_mix(p, files)
        m = MixFile(p)
        data_ok = all(m.read(k) == v for k, v in files.items())
        names = {e.name for e in m.entries if e.name}
        names_ok = names >= set(files)
        has_ok = m.has("rulesmd.ini") and not m.has("nope.ini")
        print(f"round-trip write({n})+read: data={data_ok} names={names_ok} "
              f"has={has_ok}  {'OK' if data_ok and names_ok and has_ok else 'FAIL'}")
        ok = ok and data_ok and names_ok and has_ok

        # listing sniffs types for the (named) entries
        types = {name: t for _id, _sz, name, t in m.listing() if name}
        sniff_ok = (types.get("gtnk01.vxl") == "vxl"
                    and types.get("fire.wav") == "wav"
                    and types.get("rulesmd.ini") == "ini")
        print(f"listing type sniff: {types}  {'OK' if sniff_ok else 'FAIL'}")
        ok = ok and sniff_ok

        # extract_all round-trips the bytes to disk
        outdir = os.path.join(d, "ex")
        written = m.extract_all(outdir)
        ex_ok = (set(files) <= set(written)
                 and open(os.path.join(outdir, "rulesmd.ini"), "rb").read()
                 == files["rulesmd.ini"])
        print(f"extract_all ({len(written)} files): {'OK' if ex_ok else 'FAIL'}")
        ok = ok and ex_ok

        # ── encrypted mix refused ───────────────────────────────────────────
        enc = os.path.join(d, "enc.mix")
        with open(enc, "wb") as f:
            f.write(struct.pack("<I", 0x00020000))   # flags: encrypted, c0==0
            f.write(b"\x00" * 100)
        try:
            MixFile(enc)
            print("encrypted refusal: FAIL (did not raise)")
            ok = False
        except MixEncrypted:
            print("encrypted refusal: OK")

        # ── corruption guard: bogus index offset → MixError ─────────────────
        bad = os.path.join(d, "bad.mix")
        with open(bad, "wb") as f:
            f.write(struct.pack("<I", 0))             # new format, flags 0
            f.write(struct.pack("<Hi", 1, 10))        # 1 file, body=10
            f.write(struct.pack("<Iii", 123, 1 << 30, 10))  # offset way past EOF
            f.write(b"\x00" * 10)
        try:
            MixFile(bad)
            print("corruption guard: FAIL (did not raise)")
            ok = False
        except MixError:
            print("corruption guard: OK")

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
