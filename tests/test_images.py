#!/usr/bin/env python3
"""
SHP(TS) + palette decode: parse a synthesized raw (format 1) and RLE-Zero
(format 3) SHP, load a 6-bit palette, and render a frame to RGBA with index-0
transparency. Verified against hand-built bytes (no external fixtures).

    python3 tests/test_images.py
"""

import sys
import os
import struct

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import (
    parse_shp, load_palette, frame_to_rgba, best_frame_index, grayscale_palette,
)


def _file_header(w, h, count):
    return struct.pack("<HHHH", 0, w, h, count)


def _frame_header(x, y, w, h, flags, offset):
    return struct.pack("<HHHH", x, y, w, h) + struct.pack("<I", flags) \
        + b"\x00" * 8 + struct.pack("<I", offset)


def _encode_rle_zero(indices, w, h):
    """Encode row-major indices into RLE-Zero rows (0 byte + run-length)."""
    out = bytearray()
    for row in range(h):
        line = bytearray()
        col = 0
        while col < w:
            v = indices[row * w + col]
            if v == 0:
                run = 0
                while col < w and indices[row * w + col] == 0 and run < 255:
                    run += 1
                    col += 1
                line += bytes([0, run])
            else:
                line.append(v)
                col += 1
        out += struct.pack("<H", len(line) + 2) + line
    return bytes(out)


def main():
    ok = True
    # 2x2 frame: top row [0,1], bottom [2,0]
    px = bytes([0, 1, 2, 0])
    W = H = 2

    # ── format 1 (raw) ────────────────────────────────────────────────────────
    data = bytearray(_file_header(W, H, 1))
    data += _frame_header(0, 0, W, H, 0x01, 8 + 24)   # offset after headers
    data += px
    img = parse_shp(bytes(data))
    raw_ok = (img.width == 2 and len(img.frames) == 1
              and img.frames[0].indices == px)
    print(f"parse raw SHP (format 1): {'OK' if raw_ok else 'FAIL'}  "
          f"{img.frames[0].indices!r}")
    ok = ok and raw_ok

    # ── format 3 (RLE-Zero) ───────────────────────────────────────────────────
    body = _encode_rle_zero(px, W, H)
    data3 = bytearray(_file_header(W, H, 1))
    data3 += _frame_header(0, 0, W, H, 0x03, 8 + 24)
    data3 += body
    img3 = parse_shp(bytes(data3))
    rle_ok = img3.frames[0].indices == px
    print(f"parse RLE-Zero SHP (format 3): {'OK' if rle_ok else 'FAIL'}  "
          f"{img3.frames[0].indices!r}")
    ok = ok and rle_ok

    # ── palette (6-bit → 8-bit) + render with transparency ────────────────────
    pal_bytes = bytearray(768)
    pal_bytes[1 * 3:1 * 3 + 3] = bytes([63, 0, 0])     # idx1 full red (6-bit)
    pal_bytes[2 * 3:2 * 3 + 3] = bytes([0, 63, 0])     # idx2 full green
    pal = load_palette(bytes(pal_bytes))
    pal_ok = pal[1] == (255, 0, 0) and pal[2] == (0, 255, 0)
    print(f"palette 6-bit→8-bit expand: {'OK' if pal_ok else 'FAIL'}  "
          f"{pal[1]} {pal[2]}")
    ok = ok and pal_ok

    w, h, rgba = frame_to_rgba(img, img.frames[0], pal)
    # pixel (0,0)=idx0 transparent; (1,0)=idx1 red; (0,1)=idx2 green; (1,1)=idx0
    def at(x, y):
        o = (y * w + x) * 4
        return tuple(rgba[o:o + 4])
    render_ok = (at(0, 0) == (0, 0, 0, 0) and at(1, 0) == (255, 0, 0, 255)
                 and at(0, 1) == (0, 255, 0, 255) and at(1, 1) == (0, 0, 0, 0))
    print(f"render RGBA (idx0 transparent, colors placed): "
          f"{'OK' if render_ok else 'FAIL'}")
    ok = ok and render_ok

    # ── best_frame_index skips an empty leading frame ─────────────────────────
    # two frames: frame0 empty (offset 0), frame1 raw with data
    off1 = 8 + 24 * 2
    multi = bytearray(_file_header(W, H, 2))
    multi += _frame_header(0, 0, W, H, 0x01, 0)        # empty
    multi += _frame_header(0, 0, W, H, 0x01, off1)
    multi += px
    bimg = parse_shp(bytes(multi))
    bfi_ok = best_frame_index(bimg) == 1
    print(f"best_frame_index skips empty frame: {'OK' if bfi_ok else 'FAIL'}")
    ok = ok and bfi_ok

    # ── house-colour remap (indices 16-31) ───────────────────────────────────
    from yrbalance.engine import house_remap_palette, HOUSE_COLORS
    base = grayscale_palette()
    red = house_remap_palette(base, HOUSE_COLORS["Red"])
    blue = house_remap_palette(base, HOUSE_COLORS["Blue"])
    # only 16-31 change; shade 0 bright, shade 15 black; hue-dominant
    r16, r31 = red[16], red[31]
    b16 = blue[16]
    house_ok = (red[0] == base[0] and red[40] == base[40]      # non-remap intact
                and r16 != base[16]                            # remap changed
                and r31 == (0, 0, 0)                           # darkest ≈ black
                and r16[0] > r16[1] and r16[0] > r16[2]        # red dominant
                and b16[2] > b16[0] and b16[2] > b16[1])       # blue dominant
    print(f"house remap (16-31 tinted, 0/40 intact, hue-dominant): "
          f"{'OK' if house_ok else 'FAIL'}  red16={r16} blue16={b16}")
    ok = ok and house_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
