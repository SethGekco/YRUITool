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

    # normal tables loaded (TS=36, RA2=245) and lighting changes the output
    from yrbalance.engine.vxl_normals import NORMALS_BY_MODE
    import math
    tables_ok = (len(NORMALS_BY_MODE[2]) == 36 and len(NORMALS_BY_MODE[4]) == 245
                 and all(abs(math.sqrt(x * x + y * y + z * z) - 1.0) < 1e-3
                         for x, y, z in NORMALS_BY_MODE[4]))
    print(f"normal tables (TS=36, RA2=245, unit vectors): "
          f"{'OK' if tables_ok else 'FAIL'}")
    ok = ok and tables_ok

    _, _, lit = render_vxl(m, palette=m.palette, lighting=True)
    _, _, unlit = render_vxl(m, palette=m.palette, lighting=False)
    light_ok = lit != unlit
    print(f"lighting changes render output: {'OK' if light_ok else 'FAIL'}")
    ok = ok and light_ok

    # HVA parse + placement: a translating HVA shifts a section in world space
    from yrbalance.engine import parse_hva, Hva
    from yrbalance.engine.vxl import _collect_points

    def _build_hva(mats):
        d = b"t".ljust(16, b"\x00") + struct.pack("<ii", 1, len(mats))
        for i in range(len(mats)):
            d += f"s{i}".encode().ljust(16, b"\x00")
        for row in mats:
            d += struct.pack("<12f", *row)
        return d

    hv = parse_hva(_build_hva([(1, 0, 0, 3, 0, 1, 0, 4, 0, 0, 1, 5)]))
    hva_parse_ok = (hv.num_frames == 1 and hv.matrix(0)[3] == 3
                    and hv.matrix(0)[7] == 4 and hv.matrix(9) == hv.matrix(99))
    print(f"parse HVA + identity fallback: {'OK' if hva_parse_ok else 'FAIL'}")
    ok = ok and hva_parse_ok

    # cube hva_multiplier=0.083, scaleX=1 → an x-translation of 100 shifts world x
    # by 100*0.083 = 8.3, and iso ix = wx-wy shifts by the same
    import statistics
    base_pts = _collect_points(m, None, m.palette, None, 32, False, 0)
    thva = Hva(1, ["c"], [[(1, 0, 0, 100.0, 0, 1, 0, 0.0, 0, 0, 1, 0.0)]])
    moved_pts = _collect_points(m, thva, m.palette, None, 32, False, 0)
    dx = statistics.mean(p[0] for p in moved_pts) - \
        statistics.mean(p[0] for p in base_pts)
    place_ok = abs(dx - 8.3) < 0.5
    print(f"HVA translation places section (ix shift ≈ 8.3): "
          f"{'OK' if place_ok else 'FAIL'}  dx={dx:.2f}")
    ok = ok and place_ok

    # compositing: both parts contribute points, and the offset part is placed
    # distinctly from the base (turret-on-body mechanics)
    from yrbalance.engine import render_parts
    off = Hva(1, ["c"], [[(1, 0, 0, 60.0, 0, 1, 0, 60.0, 0, 0, 1, 0.0)]])
    a = _collect_points(m, None, m.palette, None, 32, False, 0)
    b = _collect_points(m, off, m.palette, None, 32, False, 0)
    w2, h2, two = render_parts([(m, None), (m, off)], palette=m.palette)
    comp_ok = (len(a) > 0 and len(b) == len(a)
               and {p[:2] for p in a} != {p[:2] for p in b}       # placed apart
               and any(two[i] for i in range(3, len(two), 4)))      # renders
    print(f"composite places two parts distinctly + renders: "
          f"{'OK' if comp_ok else 'FAIL'}")
    ok = ok and comp_ok

    # selectable view angle: rotating 180° about Z mirrors the projection (a
    # non-symmetric offset part lands on the opposite side), and 0° is unchanged
    base_v = _collect_points(m, None, m.palette, None, 32, False, 0,
                             (5.0, 0.0, 0.0), 1.0, 0.0)
    import math as _m
    c, s = _m.cos(_m.radians(180)), _m.sin(_m.radians(180))
    rot_v = _collect_points(m, None, m.palette, None, 32, False, 0,
                            (5.0, 0.0, 0.0), c, s)
    mean_ix0 = statistics.mean(p[0] for p in base_v)
    mean_ix180 = statistics.mean(p[0] for p in rot_v)
    # 180°: (wx,wy)→(-wx,-wy) so ix = rx-ry = -(wx-wy) → mean negates
    view_ok = abs(mean_ix180 + mean_ix0) < 0.01 and abs(mean_ix0) > 0.1
    print(f"view rotation 180° mirrors ix: {'OK' if view_ok else 'FAIL'}  "
          f"{mean_ix0:.2f} → {mean_ix180:.2f}")
    ok = ok and view_ok

    # art.ini TurretOffset: a per-part world offset shifts that part (ix = wx-wy,
    # so a +X offset of 10 moves ix by +10)
    base2 = _collect_points(m, None, m.palette, None, 32, False, 0, (0.0, 0.0, 0.0))
    shifted = _collect_points(m, None, m.palette, None, 32, False, 0,
                              (10.0, 0.0, 0.0))
    dxo = statistics.mean(p[0] for p in shifted) - \
        statistics.mean(p[0] for p in base2)
    off_ok = abs(dxo - 10.0) < 0.01
    # and render_parts accepts 3-tuples (model, hva, offset)
    _, _, r3 = render_parts([(m, None, (0.0, 0.0, 0.0))], palette=m.palette)
    tuple_ok = any(r3[i] for i in range(3, len(r3), 4))
    print(f"TurretOffset world shift + 3-tuple parts: "
          f"{'OK' if off_ok and tuple_ok else 'FAIL'}  dx={dxo:.2f}")
    ok = ok and off_ok and tuple_ok

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
