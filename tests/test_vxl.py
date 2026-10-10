#!/usr/bin/env python3
"""
VXL parse + isometric render: build a synthetic 1-section VXL (a 2x2x2 cube of
coloured voxels), parse it back, and render to RGBA with a z-buffer. Also renders
a real game .vxl when present on this machine (the format was validated across 17
files: 8 rendered, 9 genuinely empty, 0 errors).

    python3 tests/test_vxl.py
"""

import sys
import os
import struct
import glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import parse_vxl, render_vxl, voxel_count


def _build_cube_vxl():
    """One section, size 2x2x2, every voxel colour=5 normal=0."""
    sx = sy = sz = 2
    ncols = sx * sy
    # span data: per column, skip=0, count=2, (colour,normal)*2, trailing count
    col_bytes = bytes([0, 2, 5, 0, 5, 0, 2])
    span_data = b""
    starts = b""
    for c in range(ncols):
        starts += struct.pack("<i", len(span_data))
        span_data += col_bytes
    ends = b""
    acc = 0
    for c in range(ncols):
        acc += len(col_bytes)
        ends += struct.pack("<i", acc)
    body = starts + ends + span_data
    body_size = len(body)

    # header (802): name16, unknown, numLimbs, numLimbs2, bodySize, 2 remap, 768 pal
    hdr = b"cube".ljust(16, b"\x00")
    hdr += struct.pack("<IIII", 1, 1, 1, body_size)
    hdr += bytes([0, 255])
    pal = bytearray(768)
    pal[5 * 3:5 * 3 + 3] = bytes([63, 0, 0])      # colour 5 = red (6-bit)
    hdr += bytes(pal)
    assert len(hdr) == 802, len(hdr)

    # section header (28): name16, limbNumber, u32, u32
    sec_hdr = b"cube".ljust(16, b"\x00") + struct.pack("<III", 0, 0, 0)

    # tailer (92): spanStart, spanEnd, spanData offsets, scale, 12f transform,
    # 3f min, 3f max, sx, sy, sz, normalType
    span_start_off = 0
    span_end_off = len(starts)
    span_data_off = len(starts) + len(ends)
    tailer = struct.pack("<III", span_start_off, span_end_off, span_data_off)
    tailer += struct.pack("<f", 0.083)
    tailer += struct.pack("<12f", *([0.0] * 12))
    tailer += struct.pack("<3f", 0, 0, 0) + struct.pack("<3f", 2, 2, 2)
    tailer += bytes([sx, sy, sz, 4])
    assert len(tailer) == 92, len(tailer)

    return hdr + sec_hdr + body + tailer


def main():
    ok = True
    data = _build_cube_vxl()
    m = parse_vxl(data)
    sec = m.sections[0]
    parse_ok = (len(m.sections) == 1 and (sec.sx, sec.sy, sec.sz) == (2, 2, 2)
                and voxel_count(m) == 8
                and all(c == 5 for (c, n) in sec.voxels.values()))
    print(f"parse synthetic cube (8 voxels, colour 5): "
          f"{'OK' if parse_ok else 'FAIL'}  voxels={voxel_count(m)}")
    ok = ok and parse_ok

    pal_ok = m.palette[5] == (255, 0, 0)
    print(f"embedded palette (6-bit→8-bit): {'OK' if pal_ok else 'FAIL'}  "
          f"{m.palette[5]}")
    ok = ok and pal_ok

    w, h, rgba = render_vxl(m)
    opaque = sum(1 for i in range(3, len(rgba), 4) if rgba[i])
    # some red pixels present
    red = any(rgba[i] > 80 and rgba[i + 1] == 0 and rgba[i + 2] == 0
              and rgba[i + 3] == 255 for i in range(0, len(rgba), 4))
    render_ok = w > 0 and h > 0 and opaque > 0 and red
    print(f"render cube → RGBA ({w}x{h}, {opaque} opaque, red present): "
          f"{'OK' if render_ok else 'FAIL'}")
    ok = ok and render_ok

    # real game vxl, if present
    real = glob.glob("/home/rex/snap/cncra2yr/common/.wine/drive_c/Westwood/"
                     "RA2/usaguntur.vxl")
    if real:
        rm = parse_vxl(open(real[0], "rb").read())
        rw, rh, rrgba = render_vxl(rm)
        rop = sum(1 for i in range(3, len(rrgba), 4) if rrgba[i])
        real_ok = voxel_count(rm) > 100 and rop > 100
        print(f"real usaguntur.vxl: voxels={voxel_count(rm)} render={rw}x{rh} "
              f"opaque={rop}  {'OK' if real_ok else 'FAIL'}")
        ok = ok and real_ok
    else:
        print("real VXL render: SKIP (no sample on this machine)")

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
