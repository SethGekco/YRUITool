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

from yrbalance.engine import (
    mix_id, MixFile, write_mix, MixError, is_vanilla_mix, is_stock_mix,
    build_media_catalog, resolve_media, pack_media_mixes,
)
from yrbalance.engine._blowfish import Blowfish


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

        # ── Blowfish cipher: standard known-answer vector ───────────────────
        bf = Blowfish(bytes(8))                        # all-zero key
        el, er = bf._enc(0, 0)                         # encipher all-zero block
        kat_ok = (el, er) == (0x4EF99745, 0x6198DD78)  # canonical Schneier vector
        dl, dr = bf._dec(el, er)                        # round-trips
        kat_ok = kat_ok and (dl, dr) == (0, 0)
        print(f"Blowfish KAT (0-key -> 4EF99745 6198DD78): "
              f"{'OK' if kat_ok else 'FAIL'}")
        ok = ok and kat_ok

        # ── vanilla-mix policy (name-based, not encryption-based) ───────────
        van_ok = (is_vanilla_mix("ra2md.mix") and is_vanilla_mix("/x/RA2.MIX")
                  and not is_vanilla_mix("mymod_ecache.mix")
                  # community frameworks are stock but not "vanilla"
                  and is_stock_mix("ares.mix") and is_stock_mix("expandspawn01.mix")
                  and not is_vanilla_mix("ares.mix")
                  and not is_stock_mix("mymod_ecache.mix"))
        print(f"vanilla/stock mix policy (cncnet+ares skipped): "
              f"{'OK' if van_ok else 'FAIL'}")
        ok = ok and van_ok

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

    # ── media transfer: catalog (loose + mix) → resolve → pack (split) ──────
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "srcmod")
        os.makedirs(src)
        # a loose voxel, and two more media files packed inside a mod mix
        open(os.path.join(src, "unit.vxl"), "wb").write(b"Voxel Animation\x1a" + b"V")
        write_mix(os.path.join(src, "ecacheX.mix"),
                  {"unit.hva": b"HVA-DATA", "icon.shp": b"SHP-DATA"})
        # stock/community mixes that must be ignored even if they have wanted files
        write_mix(os.path.join(src, "ra2md.mix"), {"icon.shp": b"VANILLA-WRONG"})
        write_mix(os.path.join(src, "ares.mix"), {"unit.hva": b"ARES-WRONG"})
        write_mix(os.path.join(src, "expandspawn01.mix"), {"unit.vxl": b"CNCNET-WRONG"})

        cat = build_media_catalog(src)
        cat_ok = (cat.get("UNIT.VXL", ("",))[0] == "loose"
                  and cat.get("UNIT.HVA", ("",))[0] == "mix"
                  and cat.get("ICON.SHP", ("", "x"))[1].endswith("ecacheX.mix"))
        print(f"catalog (loose+mix, vanilla skipped): "
              f"{'OK' if cat_ok else 'FAIL'}")
        ok = ok and cat_ok

        found, missing = resolve_media(cat, ["unit.vxl", "unit.hva", "icon.shp",
                                             "nope.shp"])
        res_ok = (found["unit.hva"] == b"HVA-DATA"
                  and found["icon.shp"] == b"SHP-DATA"      # from mod, not vanilla
                  and found["unit.vxl"].startswith(b"Voxel")
                  and missing == ["nope.shp"])
        print(f"resolve (mod wins over vanilla; missing tracked): "
              f"{'OK' if res_ok else 'FAIL'}")
        ok = ok and res_ok

        dest = os.path.join(d, "dest")
        os.makedirs(dest)
        paths = pack_media_mixes(found, dest, "ecacheimp", per_mix_limit=2)
        split_ok = len(paths) == 2 and all(p.endswith(".mix") for p in paths)
        # the three files round-trip out of the packed mixes
        back = {}
        for p in paths:
            mf = MixFile(p)
            for e in mf.entries:
                if e.name:
                    back[e.name] = mf.read_id(e.id)
        pack_ok = (split_ok and back.get("unit.hva") == b"HVA-DATA"
                   and back.get("icon.shp") == b"SHP-DATA")
        print(f"pack into {len(paths)} mix(es) (limit 2) + round-trip: "
              f"{'OK' if pack_ok else 'FAIL'}")
        ok = ok and pack_ok

    # ── optional: read a REAL encrypted mix if present (Rex's vanilla) ──────
    real = ("/mnt/wwn-0x50014ee6b383afb8-part1/(3) Games Data/"
            "RA2 & Yuri's Revenge/000-RA2YR Vanilla/ra2md.mix")
    if os.path.isfile(real):
        m = MixFile(real)
        enc_ok = m.encrypted and len(m.entries) > 0
        print(f"real encrypted ra2md.mix: {len(m.entries)} entries, "
              f"encrypted={m.encrypted}  {'OK' if enc_ok else 'FAIL'}")
        ok = ok and enc_ok
    else:
        print("real encrypted mix: SKIP (not present)")

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
