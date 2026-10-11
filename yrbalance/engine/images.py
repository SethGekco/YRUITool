"""
Westwood image decoding — SHP(TS/RA2) sprites and VGA palettes (.pal), pure Python.

Just enough to render a still preview (a chosen frame → RGBA) in the Library detail
pane. References: ModEnc's SHP(TS) spec and the open-source SHP viewers (Katz's C#
viewer, XCC). Rendering/animation and VXL are handled elsewhere / later.

SHP(TS) layout:
  file header (8 bytes): u16 zero, u16 width, u16 height, u16 frame_count
  per frame (24 bytes):  u16 x, u16 y, u16 w, u16 h, u32 flags, 8 bytes misc,
                         u32 offset (0 ⇒ empty/transparent frame)
  pixel data: raw (w*h bytes) when uncompressed, else RLE-Zero rows — each row is
  u16 byte-count (incl. the 2 count bytes) then data where a 0 byte is followed by
  a run-length of zeros to emit.
Palette index 0 is transparent. .pal entries are 6-bit VGA (0-63) → expand to 8-bit.
"""

import struct
from dataclasses import dataclass, field


@dataclass
class ShpFrame:
    x: int
    y: int
    w: int
    h: int
    indices: bytes = b""        # len w*h, palette indices (0 = transparent)


@dataclass
class ShpImage:
    width: int                   # bounding canvas
    height: int
    frames: list = field(default_factory=list)


class ImageError(Exception):
    pass


def parse_shp(data):
    """Parse SHP(TS/RA2) bytes into an ShpImage. Raises ImageError if it doesn't
    look like a TS SHP."""
    if len(data) < 8:
        raise ImageError("too small for a SHP")
    zero, width, height, count = struct.unpack_from("<HHHH", data, 0)
    if zero != 0 or count == 0 or count > 8192:
        raise ImageError("not a SHP(TS) (bad header)")
    frames = []
    for i in range(count):
        base = 8 + i * 24
        if base + 24 > len(data):
            break
        x, y, w, h = struct.unpack_from("<HHHH", data, base)
        flags = struct.unpack_from("<I", data, base + 8)[0]
        offset = struct.unpack_from("<I", data, base + 20)[0]
        if offset == 0 or w == 0 or h == 0:
            frames.append(ShpFrame(x, y, w, h, b"\x00" * (w * h)))
            continue
        if flags & 0x02:                      # formats 2/3 = RLE-Zero rows
            idx = _decode_rle_zero(data, offset, w, h)
        else:                                 # format 1 = raw
            idx = bytes(data[offset:offset + w * h])
            if len(idx) < w * h:
                idx += b"\x00" * (w * h - len(idx))
        frames.append(ShpFrame(x, y, w, h, idx))
    return ShpImage(width, height, frames)


def _decode_rle_zero(data, offset, w, h):
    out = bytearray()
    pos = offset
    for _row in range(h):
        if pos + 2 > len(data):
            break
        row_size = struct.unpack_from("<H", data, pos)[0]
        if row_size < 2:
            row_size = 2
        line = data[pos + 2:pos + row_size]
        pos += row_size
        rb = bytearray()
        i = 0
        while i < len(line):
            v = line[i]
            i += 1
            if v == 0:
                n = line[i] if i < len(line) else 0
                i += 1
                rb.extend(b"\x00" * n)
            else:
                rb.append(v)
        if len(rb) < w:
            rb.extend(b"\x00" * (w - len(rb)))
        out.extend(rb[:w])
    if len(out) < w * h:
        out.extend(b"\x00" * (w * h - len(out)))
    return bytes(out[:w * h])


def load_palette(data):
    """256 (r,g,b) tuples from a .pal. C&C palettes are 6-bit (0-63); expand to
    8-bit. If values exceed 63 it's already 8-bit, so pass them through."""
    n = min(256, len(data) // 3)
    raw = [(data[i * 3], data[i * 3 + 1], data[i * 3 + 2]) for i in range(n)]
    six_bit = all(c <= 63 for rgb in raw for c in rgb)
    pal = []
    for (r, g, b) in raw:
        if six_bit:
            pal.append(((r << 2) | (r >> 4), (g << 2) | (g >> 4),
                        (b << 2) | (b >> 4)))
        else:
            pal.append((r, g, b))
    while len(pal) < 256:
        pal.append((0, 0, 0))
    return pal


def grayscale_palette():
    """Fallback palette (index 0 transparent) when no .pal is available."""
    return [(i, i, i) for i in range(256)]


# RA2 player colours as (hue, saturation, value), each 0-255 (hue split on 255).
HOUSE_COLORS = {
    "Red": (0, 255, 255),
    "Orange": (20, 255, 255),
    "Yellow": (40, 255, 255),
    "Green": (85, 255, 230),
    "Teal": (120, 255, 230),
    "Blue": (160, 255, 255),
    "Purple": (190, 255, 240),
    "Pink": (220, 210, 255),
    "Grey": (0, 0, 180),
}


def _engine_hsv_to_rgb(h, s, v):
    """RA2's own integer HSV→RGB (0x00517440): hue split on 255, truncating."""
    sector = (h * 6) // 255
    frac = (h * 6) % 255
    p = (255 - s) * v // 255
    q = (255 - frac * s // 255) * v // 255
    t = (255 - (255 - frac) * s // 255) * v // 255
    return {1: (q, v, p), 2: (p, v, t), 3: (p, q, v),
            4: (t, p, v), 5: (v, p, q)}.get(sector, (v, t, p))


def house_remap_palette(palette, hsv, first=16, last=31):
    """Return a copy of `palette` with the remap range [first, last] replaced by a
    house colour's 16 shades, matching gamemd's builder (0x0068C3B0): the hue is
    kept while saturation sweeps up a sine and value down a cosine, so a shade
    grows more saturated as it darkens (shade 15 ≈ black). `hsv` is (hue, sat, val)
    each 0-255 (see HOUSE_COLORS)."""
    import math
    hue, sat, val = hsv
    pal = list(palette)
    for i in range(last - first + 1):
        value = 0.19634954084936207 if i == 0 \
            else i * 0.08144869842640204 + 0.3490658503988659
        saturation = i * 0.046542113386515455 + 0.8726646259971648
        r, g, b = _engine_hsv_to_rgb(hue, int(math.sin(saturation) * sat),
                                     int(math.cos(value) * val))
        # game stores 6-bit then displays expanded; match that round-trip
        r6, g6, b6 = r * 63 // 255, g * 63 // 255, b * 63 // 255
        pal[first + i] = ((r6 << 2) | (r6 >> 4), (g6 << 2) | (g6 >> 4),
                          (b6 << 2) | (b6 >> 4))
    return pal


def frame_to_rgba(img, frame, palette, transparent_index=0):
    """Render one frame onto the full canvas as RGBA bytes. Index 0 (or
    `transparent_index`) is fully transparent. Returns (width, height, rgba)."""
    W, H = img.width, img.height
    if W <= 0 or H <= 0:
        W, H = frame.w, frame.h
    buf = bytearray(W * H * 4)               # zero-filled ⇒ transparent
    for row in range(frame.h):
        yy = frame.y + row
        if yy < 0 or yy >= H:
            continue
        base_src = row * frame.w
        for col in range(frame.w):
            xx = frame.x + col
            if xx < 0 or xx >= W:
                continue
            idx = frame.indices[base_src + col]
            if idx == transparent_index:
                continue
            r, g, b = palette[idx]
            o = (yy * W + xx) * 4
            buf[o] = r
            buf[o + 1] = g
            buf[o + 2] = b
            buf[o + 3] = 255
    return W, H, bytes(buf)


def parse_pcx(data):
    """Decode an 8-bit RLE PCX (the format RA2/Ares cameos use) → (w, h, rgba).
    Uses the trailing 256-colour palette (full 8-bit). Fully opaque."""
    if len(data) < 128 or data[0] != 0x0A:
        raise ImageError("not a PCX")
    bpp, nplanes = data[3], data[65]
    xmin, ymin, xmax, ymax = struct.unpack_from("<HHHH", data, 4)
    w, h = xmax - xmin + 1, ymax - ymin + 1
    bpl = struct.unpack_from("<H", data, 66)[0]
    if bpp != 8 or nplanes != 1:
        raise ImageError("unsupported PCX (need 8bpp, 1 plane)")
    has_pal = len(data) >= 769 and data[-769] == 0x0C
    if has_pal:
        pb = data[-768:]
        pal = [(pb[i * 3], pb[i * 3 + 1], pb[i * 3 + 2]) for i in range(256)]
        end = len(data) - 769
    else:
        pal = [(i, i, i) for i in range(256)]
        end = len(data)
    total = bpl * h
    idx = bytearray()
    pos = 128
    while len(idx) < total and pos < end:
        b = data[pos]
        pos += 1
        if (b & 0xC0) == 0xC0:
            cnt = b & 0x3F
            val = data[pos] if pos < end else 0
            pos += 1
            idx.extend([val] * cnt)
        else:
            idx.append(b)
    buf = bytearray(w * h * 4)
    for y in range(h):
        base = y * bpl
        for x in range(w):
            if base + x < len(idx):
                r, g, b2 = pal[idx[base + x]]
                o = (y * w + x) * 4
                buf[o] = r
                buf[o + 1] = g
                buf[o + 2] = b2
                buf[o + 3] = 255
    return w, h, bytes(buf)


def best_frame_index(img):
    """A sensible frame to preview: the first non-empty one."""
    for i, f in enumerate(img.frames):
        if any(b != 0 for b in f.indices):
            return i
    return 0
