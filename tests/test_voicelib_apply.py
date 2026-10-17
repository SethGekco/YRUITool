#!/usr/bin/env python3
"""
Voice library (V3): the audio .bag/.idx writer and applying a saved voice set
onto a unit in another mod — conflict resolution (merge vs keep-both) across
all three layers, Sounds= rewriting when a sample is re-minted, and injection
into the target's own audio.bag.

Also pins the append-only invariant: injecting must never move bytes that are
already in the bag, because every offset in the existing .idx would break.

    python3 tests/test_voicelib_apply.py
"""

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_voicelib import _write_bag, _BLOCK        # synthetic bag fixture

SRC_RULES = """\
[InfantryTypes]
0=GI
[GI]
Cost=200
VoiceSelect=GIYessir
"""
SRC_SOUND = """\
[GIYessir]
Control=2
Sounds=$giyes1,giyes2
"""
# Target already has a GI, and a DIFFERENT [GIYessir] — the conflict case.
TGT_RULES = """\
[InfantryTypes]
0=GI
[GI]
Cost=250
Strength=120
"""
TGT_SOUND = """\
[GIYessir]
Control=9
Sounds=somethingelse
"""


def main():
    ok = True
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home
        from yrbalance.engine import (
            extract_voice_set, store_samples, build_media_catalog,
            parse_ini_lines, build_section_map, parse_audio_idx,
            read_audio_sample, inject_audio_samples, MixError,
            build_voice_plan, apply_voice_plan, voice_plan_to_text,
        )

        # ── the bag writer ──────────────────────────────────────────────────
        bd = os.path.join(home, "bagtest")
        os.makedirs(bd)
        bag, idx = os.path.join(bd, "audio.bag"), os.path.join(bd, "audio.idx")
        inject_audio_samples(bag, idx, [("ONE", b"a" * 10, 22050, 0, 20)])
        e1 = parse_audio_idx(idx)
        first_off = e1["ONE"][0]
        inject_audio_samples(bag, idx, [("TWO", b"b" * 6, 11025, 1, 8)])
        e2 = parse_audio_idx(idx)
        w_ok = (set(e2) == {"ONE", "TWO"}
                and e2["ONE"][0] == first_off            # append-only
                and e2["ONE"][:2] == (first_off, 10)
                and e2["TWO"][1:] == (6, 11025, 1, 8)
                and read_audio_sample(bag, e2["TWO"]) == b"b" * 6)
        print(f"bag writer: create, append, idx round-trip: "
              f"{'OK' if w_ok else 'FAIL'}")
        ok = ok and w_ok

        # replacing a name appends + repoints, leaving the old bytes orphaned
        added, replaced = inject_audio_samples(
            bag, idx, [("ONE", b"c" * 99, 22050, 0, 20)])
        e3 = parse_audio_idx(idx)
        rep_ok = ((added, replaced) == (0, 1)
                  and e3["ONE"][0] != first_off          # repointed
                  and read_audio_sample(bag, e3["ONE"]) == b"c" * 99
                  and read_audio_sample(bag, e3["TWO"]) == b"b" * 6)
        print(f"bag writer: replace repoints, other samples intact: "
              f"{'OK' if rep_ok else 'FAIL'}")
        ok = ok and rep_ok

        try:
            inject_audio_samples(bag, idx, [("A" * 17, b"x", 22050, 0, 0)])
            long_ok = False
        except MixError:
            long_ok = True
        print(f"bag writer rejects a >16-byte name: "
              f"{'OK' if long_ok else 'FAIL'}")
        ok = ok and long_ok

        # ── fixtures: a saved source set, and a colliding target mod ───────
        src = os.path.join(home, "src")
        os.makedirs(src)
        _w(src, "rulesmd.ini", SRC_RULES)
        _w(src, "soundmd.ini", SRC_SOUND)
        _write_bag(src, ["GIYES1", "GIYES2"])
        srmap = build_section_map(parse_ini_lines(f"{src}/rulesmd.ini"))[0]
        ssmap = build_section_map(parse_ini_lines(f"{src}/soundmd.ini"))[0]
        vset = extract_voice_set("GI", srmap, ssmap, source="src",
                                 name="GI voices")
        vset.set_id = "gi"
        store_samples(vset, build_media_catalog([src]))

        tgt = os.path.join(home, "tgt")
        os.makedirs(tgt)
        tr_p = _w(tgt, "rulesmd.ini", TGT_RULES)
        ts_p = _w(tgt, "soundmd.ini", TGT_SOUND)
        _write_bag(tgt, ["GIYES1"])            # target already has GIYES1
        trmap = build_section_map(parse_ini_lines(tr_p))[0]
        tsmap = build_section_map(parse_ini_lines(ts_p))[0]
        tcat = build_media_catalog([tgt])

        # ── default plan: merge (keep the target's versions) ────────────────
        p1 = build_voice_plan(vset, "GI", trmap, tsmap, tcat)
        d = {(i.kind, i.name): i for i in p1.items}
        m_ok = (d[("entry", "GIYessir")].action == "reuse"
                and d[("entry", "GIYessir")].conflict
                and d[("sample", "GIYES1")].action == "skip"
                and d[("sample", "GIYES1")].conflict
                and d[("sample", "GIYES2")].action == "add"
                and d[("tag", "VoiceSelect")].value == "GIYessir"
                and len(p1.conflicts()) == 2)
        print(f"plan (merge default): reuse entry, skip sample, 2 conflicts: "
              f"{'OK' if m_ok else 'FAIL'}")
        ok = ok and m_ok

        # ── keep-both: both layers re-minted, tag follows the new entry ─────
        choices = {("entry", "GIYessir"): "keepboth",
                   ("sample", "GIYES1"): "keepboth"}
        p2 = build_voice_plan(vset, "GI", trmap, tsmap, tcat, choices)
        d2 = {(i.kind, i.name): i for i in p2.items}
        new_entry = d2[("entry", "GIYessir")].new_name
        new_samp = d2[("sample", "GIYES1")].new_name
        kb_ok = (d2[("entry", "GIYessir")].action == "rename"
                 and new_entry == "GIYessirV"
                 and d2[("sample", "GIYES1")].action == "rename"
                 and new_samp == "GIYES12" and len(new_samp) <= 16
                 and d2[("tag", "VoiceSelect")].value == new_entry)
        print(f"plan (keep-both): entry→{new_entry}, sample→{new_samp}, tag "
              f"follows: {'OK' if kb_ok else 'FAIL'}")
        ok = ok and kb_ok

        # preview must show the rewritten Sounds=, not the original
        txt = voice_plan_to_text(vset, p2)
        pv_ok = (f"[{new_entry}]" in txt and new_samp in txt
                 and "VoiceSelect=GIYessirV" in txt)
        print(f"preview shows renamed entry + rewritten Sounds: "
              f"{'OK' if pv_ok else 'FAIL'}")
        ok = ok and pv_ok

        # ── apply keep-both ────────────────────────────────────────────────
        tlines = parse_ini_lines(tr_p)
        tmap2 = build_section_map(tlines)[0]
        slines = parse_ini_lines(ts_p)
        smap2 = build_section_map(slines)[0]
        summ = apply_voice_plan(
            tlines, tmap2, vset, p2, sound=(slines, smap2),
            bag=(f"{tgt}/audio.bag", f"{tgt}/audio.idx"), folder=tgt)
        rules_txt = "".join(tlines)
        sound_txt = "".join(slines)
        tidx = parse_audio_idx(f"{tgt}/audio.idx")
        ap_ok = (
            summ["tags"] == 1 and summ["entries"] == 1
            and summ["samples"] == 2 and not summ["errors"]
            # tag written INTO the existing [GI], not a new section
            and rules_txt.count("[GI]") == 1
            and "VoiceSelect=GIYessirV" in rules_txt
            and "Strength=120" in rules_txt          # existing keys preserved
            # new section appended; the target's own GIYessir is untouched
            and "[GIYessirV]" in sound_txt
            and "Control=9" in sound_txt
            and f"Sounds=$GIYES12,GIYES2" in sound_txt
            # both samples injected under their final names
            and set(tidx) == {"GIYES1", "GIYES12", "GIYES2"}
            and read_audio_sample(f"{tgt}/audio.bag",
                                  tidx["GIYES12"]) == _BLOCK * 2)
        print(f"apply: tag into existing [GI], section appended, 2 samples "
              f"injected: {'OK' if ap_ok else 'FAIL'}")
        if not ap_ok:
            print(f"   summary={summ}")
        ok = ok and ap_ok

        # ── guard: no bag given → reported, not silently dropped ───────────
        tl2 = parse_ini_lines(tr_p)
        tm2 = build_section_map(tl2)[0]
        s3 = apply_voice_plan(tl2, tm2, vset,
                              build_voice_plan(vset, "GI", trmap, {}, {}))
        nb_ok = s3["samples"] == 0 and any("audio.bag" in e
                                           for e in s3["errors"])
        print(f"apply without a bag reports the samples it couldn't inject: "
              f"{'OK' if nb_ok else 'FAIL'}")
        ok = ok and nb_ok

        # ── guard: unit missing from the target rulesmd is warned about ─────
        p4 = build_voice_plan(vset, "NOPE", trmap, tsmap, tcat)
        warn_ok = any("does not exist" in w for w in p4.warnings)
        print(f"plan warns when the target unit doesn't exist: "
              f"{'OK' if warn_ok else 'FAIL'}")
        ok = ok and warn_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def _w(d, name, text):
    p = os.path.join(d, name)
    open(p, "w").write(text)
    return p


if __name__ == "__main__":
    sys.exit(main())
