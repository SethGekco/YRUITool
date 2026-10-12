#!/usr/bin/env python3
"""
Westwood bag audio: IMA-ADPCM decode (2 samples/byte, per-chunk state reset) and
RIFF/WAVE wrapping. The decoder is validated structurally here and, when a real
RA2 audio.bag is present on this machine, by the per-chunk 'railed' heuristic that
pinned the format (continuous ≈22% railed vs per-chunk 0%).

    python3 tests/test_audio.py
"""

import sys
import os
import struct

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import decode_ima_adpcm, to_wav

_REAL_IDX = ("/mnt/wwn-0x50014ee6b383afb8-part1/(3) Games Data/RA2 & Yuri's "
             "Revenge/0 World Powers/000-RA2YR Data - cncnet edition/audio.idx")


def main():
    ok = True

    # decode: each input byte → 2 nibbles → 2 samples → 4 PCM bytes
    data = bytes([0x11, 0x22, 0x00, 0xF0])
    pcm = decode_ima_adpcm(data, chunk_size=512)
    size_ok = len(pcm) == len(data) * 2 * 2
    print(f"decode size (2 samples/byte, 16-bit): {'OK' if size_ok else 'FAIL'}  "
          f"{len(pcm)}B for {len(data)}B in")
    ok = ok and size_ok

    # per-chunk reset actually resets: first sample of each chunk starts from 0,
    # so a chunk of all-zero nibbles stays at 0
    z = decode_ima_adpcm(bytes([0] * 8), chunk_size=4)
    zero_ok = all(b == 0 for b in z)
    print(f"zero nibbles decode to silence: {'OK' if zero_ok else 'FAIL'}")
    ok = ok and zero_ok

    # WAV header fields
    wav = to_wav(pcm, 22050, channels=1, bits=16)
    riff, sz, wave = wav[:4], struct.unpack_from("<I", wav, 4)[0], wav[8:12]
    fmt_sr = struct.unpack_from("<I", wav, 24)[0]
    ch = struct.unpack_from("<H", wav, 22)[0]
    bits = struct.unpack_from("<H", wav, 34)[0]
    data_tag = wav[36:40]
    data_len = struct.unpack_from("<I", wav, 40)[0]
    wav_ok = (riff == b"RIFF" and wave == b"WAVE" and data_tag == b"data"
              and fmt_sr == 22050 and ch == 1 and bits == 16
              and data_len == len(pcm) and sz == 36 + len(pcm))
    print(f"WAV header (RIFF/WAVE/fmt/data, rate/ch/bits/len): "
          f"{'OK' if wav_ok else 'FAIL'}")
    ok = ok and wav_ok

    # real-sample validation (only if the game bag is on this machine)
    if os.path.isfile(_REAL_IDX):
        from yrbalance.engine import parse_audio_idx
        samples = parse_audio_idx(_REAL_IDX)
        raw_all = open(_REAL_IDX[:-4] + ".bag", "rb").read()
        name = next(iter(samples))
        off, size, sr, fl, cs = samples[name]
        raw = raw_all[off:off + size]
        pcm_cont = decode_ima_adpcm(raw, chunk_size=0)      # wrong: continuous
        pcm_chunk = decode_ima_adpcm(raw, chunk_size=cs)    # right: per-chunk

        def railed(p):
            n = len(p) // 2
            v = struct.unpack(f"<{n}h", p)
            return sum(1 for x in v if abs(x) >= 32760) / n
        r_cont, r_chunk = railed(pcm_cont), railed(pcm_chunk)
        real_ok = r_chunk < 0.02 and r_cont > r_chunk
        print(f"real sample {name}: railed continuous={r_cont:.1%} "
              f"per-chunk={r_chunk:.1%}  {'OK' if real_ok else 'FAIL'}")
        ok = ok and real_ok
    else:
        print("real-sample check: SKIP (no game audio.bag on this machine)")

    # mono output regardless of the stereo flag: our decoder emits one mono
    # stream, so a stereo-flagged sample must NOT be written as 2 channels
    # (which would play ~2x fast / half length).
    from yrbalance.engine.audio import decode_bag_sample
    import struct as _st
    s = decode_bag_sample(b"\x00" * 64, 22050, 0x0D, 512)   # 0x0D has stereo bit
    wav = to_wav(s.pcm, s.sample_rate, s.channels, s.bits)
    hdr_channels = _st.unpack_from("<H", wav, 22)[0]         # WAVE fmt channels
    mono_ok = s.channels == 1 and hdr_channels == 1
    print(f"stereo-flag sample stays mono: {'OK' if mono_ok else 'FAIL'} "
          f"(ch={s.channels}, hdr={hdr_channels})")
    ok = ok and mono_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
