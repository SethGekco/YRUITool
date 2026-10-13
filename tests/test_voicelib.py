#!/usr/bin/env python3
"""
Voice library (V1): sample-token parsing, the .idx 16-byte name limit, voice-tag
discovery (a superset of the porter's), voice-set extraction across all three
layers (tag -> soundmd entry -> samples), storing original sample bytes out of a
synthetic audio.bag + a loose .wav, WAV decode-on-read, and sidecar CRUD.

Covers the gap left by test_portmod.py, whose only sound assertion is a single
"[SuperReady] in soundmd.ini" substring check — nothing there exercises the
'$'-stripping or the sample-name derivation.

    python3 tests/test_voicelib.py
"""

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RULES = """\
[InfantryTypes]
0=GI
[GI]
Cost=200
VoiceSelect=GIYessir
VoiceMove=GIYessir
VoiceAttack=GIAttack       ; inline comment must be stripped
VoiceFeedback=GIFeedback
DieSound=GIDie
Report=none
CrushSound=NoSuchEntry
"""

SOUND = """\
[GIYessir]
Control=2
Type=12
Sounds=$giyes1,giyes2
[GIAttack]
Sounds=giattack1 giattack2
Control=2
[GIFeedback]
Sounds=gifeed1
[GIDie]
Sounds=gidie1
"""

# One IMA-ADPCM block: <h predictor, B step index, B pad> then nibble bytes.
_BLOCK_DATA = bytes(range(16))
_BLOCK = struct.pack("<hBB", 0, 0, 0) + _BLOCK_DATA
_CHUNK = len(_BLOCK)                        # 20 — two of these per sample


def _write_bag(folder, names):
    """Write a synthetic audio.bag + audio.idx holding `names`, two blocks each.
    Returns {NAME: (offset, size, rate, flags, chunk)}."""
    payload = _BLOCK * 2
    blob, entries, off = bytearray(), {}, 0
    for n in names:
        entries[n] = (off, len(payload), 22050, 0, _CHUNK)
        blob += payload
        off += len(payload)
    with open(os.path.join(folder, "audio.bag"), "wb") as fh:
        fh.write(bytes(blob))
    idx = bytearray(b"GABA" + struct.pack("<ii", 2, len(entries)))
    for n, (o, sz, sr, fl, cs) in entries.items():
        idx += struct.pack("<16siiiii", n.encode("latin-1"), o, sz, sr, fl, cs)
    with open(os.path.join(folder, "audio.idx"), "wb") as fh:
        fh.write(bytes(idx))
    return entries


def main():
    ok = True
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home
        from yrbalance.engine import (
            parse_sound_tokens, idx_name_ok, voice_tags, extract_voice_set,
            store_samples, sample_wav, samples_dir, build_media_catalog,
            parse_ini_lines, build_section_map,
            add_set, load_sets, rename_set, remove_set,
            create_voice_collection, add_to_voice_collection,
            load_voice_collections, remove_from_voice_collection,
            IDX_NAME_MAX,
        )

        # ── token parsing: '$' ids, comma AND space separators, dedupe, case ──
        toks = parse_sound_tokens("$giyes1, giyes2 giyes3.wav $GIYES1")
        tok_ok = toks == ["GIYES1", "GIYES2", "GIYES3"]
        print(f"Sounds= token parse ('$', comma+space, .wav, dedupe): "
              f"{'OK' if tok_ok else 'FAIL'} {toks}")
        ok = ok and tok_ok

        # ── the audio .idx fixed-width name field ────────────────────────────
        name_ok = (idx_name_ok("A" * IDX_NAME_MAX)
                   and not idx_name_ok("A" * (IDX_NAME_MAX + 1))
                   and not idx_name_ok(""))
        print(f"idx name limit ({IDX_NAME_MAX} bytes): "
              f"{'OK' if name_ok else 'FAIL'}")
        ok = ok and name_ok

        mod = os.path.join(home, "mod")
        os.makedirs(mod)
        rules_p = _w(mod, "rulesmd.ini", RULES)
        sound_p = _w(mod, "soundmd.ini", SOUND)
        rmap, _ = build_section_map(parse_ini_lines(rules_p))
        smap, _ = build_section_map(parse_ini_lines(sound_p))

        # ── tag discovery: superset of portmod's, 'none' skipped ─────────────
        tags = voice_tags("GI", rmap)
        names = [t for t, _ in tags]
        tag_ok = ("VoiceFeedback" in names        # porter's tuple omits this
                  and "Report" not in names       # =none is skipped
                  and dict(tags).get("VoiceAttack") == "GIAttack")  # comment cut
        print(f"voice tags (VoiceFeedback kept, none skipped, comment "
              f"stripped): {'OK' if tag_ok else 'FAIL'} {names}")
        ok = ok and tag_ok

        # ── extraction: three layers, entry dedupe, missing-section note ─────
        vs = extract_voice_set("GI", rmap, smap, source="mod",
                               name="GI voices")
        ent_names = [e.name for e in vs.entries]
        yess = next(e for e in vs.entries if e.name == "GIYessir")
        samp = [s.name for s in vs.samples]
        ex_ok = (
            len(vs.tags) == 6                       # 6 tags set (Report=none out)
            and ent_names == ["GIYessir", "GIAttack", "GIFeedback", "GIDie"]
            and [k for k, _ in yess.keys] == ["Control", "Type", "Sounds"]
            and samp == ["GIYES1", "GIYES2", "GIATTACK1", "GIATTACK2",
                         "GIFEED1", "GIDIE1"]
            and any("NoSuchEntry" in n for n in vs.notes))
        print(f"extract: tags+entries verbatim, 2 tags->1 entry, samples, "
              f"missing-section note: {'OK' if ex_ok else 'FAIL'}")
        if not ex_ok:
            print(f"   tags={len(vs.tags)} entries={ent_names} samples={samp}")
            print(f"   notes={vs.notes}")
        ok = ok and ex_ok

        # ── store bytes: 4 in a bag, 1 loose, 1 genuinely absent ────────────
        _write_bag(mod, ["GIYES1", "GIYES2", "GIATTACK1", "GIFEED1"])
        _w(mod, "GIATTACK2.WAV", "RIFFplaceholder")
        cat = build_media_catalog([mod])
        vs.set_id = "gi-voices"
        stored, missing = store_samples(vs, cat)
        by = {s.name: s for s in vs.samples}
        store_ok = (
            stored == 5 and missing == ["GIDIE1"]
            and by["GIYES1"].provenance == "bag"
            and by["GIYES1"].chunk_size == _CHUNK
            and by["GIYES1"].sample_rate == 22050
            and by["GIYES1"].size == len(_BLOCK) * 2
            and by["GIATTACK2"].provenance == "loose"
            and os.path.isfile(os.path.join(samples_dir("gi-voices"),
                                            by["GIYES1"].file)))
        print(f"store samples (bag raw+idx fields, loose as-is, 1 missing): "
              f"{'OK' if store_ok else 'FAIL'} stored={stored} "
              f"missing={missing}")
        ok = ok and store_ok

        # ── decode on read: bag -> real RIFF, loose -> untouched ─────────────
        wav = sample_wav(vs, by["GIYES1"])
        loose = sample_wav(vs, by["GIATTACK2"])
        # two blocks, each 1 header sample + 16 bytes * 2 nibbles = 33 samples
        want_pcm = 2 * (1 + len(_BLOCK_DATA) * 2) * 2
        wav_ok = (wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
                  and struct.unpack_from("<I", wav, 4)[0] == 36 + want_pcm
                  and loose == b"RIFFplaceholder")
        print(f"sample_wav (bag decoded to RIFF, loose passthrough): "
              f"{'OK' if wav_ok else 'FAIL'}")
        ok = ok and wav_ok

        # ── sidecar CRUD + samples dir lifecycle ────────────────────────────
        add_set(vs)
        sets = load_sets()
        round_ok = (len(sets) == 1 and sets[0].name == "GI voices"
                    and len(sets[0].entries) == 4
                    and len(sets[0].samples) == 6
                    and sets[0].samples[0].provenance == "bag")
        print(f"sidecar round-trip (entries+samples+provenance): "
              f"{'OK' if round_ok else 'FAIL'}")
        ok = ok and round_ok

        create_voice_collection("Allied")
        add_to_voice_collection("Allied", "GI voices")
        rename_set("GI voices", "Allied GI")
        cols = load_voice_collections()
        ren_ok = (load_sets()[0].name == "Allied GI"
                  and cols[0].items == ["Allied GI"])   # membership follows
        print(f"rename set keeps collection membership: "
              f"{'OK' if ren_ok else 'FAIL'}")
        ok = ok and ren_ok

        remove_from_voice_collection("Allied", "Allied GI")
        empty_ok = load_voice_collections()[0].items == []
        remove_set("Allied GI")
        rm_ok = (load_sets() == [] and empty_ok
                 and not os.path.isdir(samples_dir("gi-voices")))
        print(f"remove set drops samples dir + membership: "
              f"{'OK' if rm_ok else 'FAIL'}")
        ok = ok and rm_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def _w(d, name, text):
    p = os.path.join(d, name)
    open(p, "w").write(text)
    return p


if __name__ == "__main__":
    sys.exit(main())
