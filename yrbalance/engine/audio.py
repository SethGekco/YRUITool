"""
Westwood audio decoding — RA2/YR .bag samples (IMA-ADPCM) → PCM/WAV, pure Python.

A sample's bytes in an audio .bag are a Westwood IMA-ADPCM stream decoded in
independent blocks of `chunk_size` compressed bytes (from the .idx entry): the
ADPCM predictor + step index reset at each block boundary. Verified against real
RA2 voice samples — continuous decoding rails ~22% of samples to full scale, while
per-chunk reset produces clean waveforms (0% railed), and `chunk_size` from the idx
is exactly the right block size.

decode_ima_adpcm → 16-bit mono PCM; to_wav wraps PCM in a RIFF/WAVE header;
bag_sample_to_wav pulls a named sample out of a media catalog and returns WAV bytes.
"""

import struct
from dataclasses import dataclass

# standard IMA-ADPCM step + index-adjust tables (Westwood uses the stock ones)
_STEP = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
    32767]
_INDEX = [-1, -1, -1, -1, 2, 4, 6, 8]


@dataclass
class AudioSample:
    sample_rate: int
    channels: int
    bits: int
    pcm: bytes


def decode_ima_adpcm(data, chunk_size=512):
    """Decode a Westwood IMA-ADPCM stream to 16-bit mono PCM (little-endian).
    State resets every `chunk_size` input bytes (0 = never reset)."""
    out = bytearray()
    idx = 0
    cur = 0
    for i, byte in enumerate(data):
        if chunk_size and i and i % chunk_size == 0:
            idx = 0
            cur = 0
        for nib in (byte & 0x0F, byte >> 4):
            step = _STEP[idx]
            delta = step >> 3
            if nib & 1:
                delta += step >> 2
            if nib & 2:
                delta += step >> 1
            if nib & 4:
                delta += step
            cur = cur - delta if nib & 8 else cur + delta
            if cur > 32767:
                cur = 32767
            elif cur < -32768:
                cur = -32768
            idx += _INDEX[nib & 7]
            if idx < 0:
                idx = 0
            elif idx > 88:
                idx = 88
            out += struct.pack("<h", cur)
    return bytes(out)


def to_wav(pcm, sample_rate, channels=1, bits=16):
    """Wrap raw PCM bytes in a canonical RIFF/WAVE (PCM) container."""
    block_align = channels * bits // 8
    byte_rate = sample_rate * block_align
    data_len = len(pcm)
    out = bytearray()
    out += b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVE"
    out += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate,
                                 byte_rate, block_align, bits)
    out += b"data" + struct.pack("<I", data_len)
    out += pcm
    return bytes(out)


def decode_bag_sample(raw, sample_rate, flags, chunk_size):
    """Decode a raw .bag sample (bytes) using its .idx fields → AudioSample.

    decode_ima_adpcm emits a single mono 16-bit stream, so the WAV is ALWAYS
    mono — honouring a stereo flag here (channels=2) would make the mono data play
    at ~2x speed and half length (the 'fast / short / bad quality' symptom). RA2/YR
    .bag voice samples are mono in practice."""
    pcm = decode_ima_adpcm(raw, chunk_size or 512)
    return AudioSample(sample_rate=sample_rate or 22050, channels=1,
                       bits=16, pcm=pcm)


def bag_sample_to_wav(catalog, name):
    """Return WAV bytes for a named bag sample found in a media catalog (from
    build_media_catalog), or None if the name isn't a bag sample. The catalog's
    bag location carries the idx entry (offset, size, sample_rate, flags,
    chunk_size), so the sample rate and block size are known for decoding."""
    from .mixlib import read_audio_sample     # local: avoid import cycle
    loc = catalog.get(name.upper())
    if loc is None or loc[0] != "bag":
        return None
    _bag, entry = loc[1], loc[2]
    _off, _size, sr, fl, cs = entry
    raw = read_audio_sample(loc[1], entry)
    sample = decode_bag_sample(raw, sr, fl, cs)
    return to_wav(sample.pcm, sample.sample_rate, sample.channels, sample.bits)
