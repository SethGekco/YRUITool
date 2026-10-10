"""
Westwood VXL (voxel) decoding + a static isometric preview render, pure Python.

Enough to show a recognizable still of a unit's voxel model in the Library detail
pane. A full game-accurate render needs the HVA transforms, the TS/RA2 normal
tables and VPL lighting, and per-section placement — this v1 does a fixed ~2:1
isometric projection with a z-buffer, colours straight from the VXL's embedded
palette, and mild height shading. Refinements (normals/lighting, turret sections,
HVA) are noted in the roadmap.

VXL layout:
  header (802 B): char[16] name, u32 unknown, u32 numLimbs, u32 numLimbs2,
                  u32 bodySize, 2 remap bytes, 256*3 palette
  section headers (numLimbs * 28 B): char[16] name, u32 limbNumber, u32, u32
  body (bodySize B): per section — int32[sx*sy] spanStart, int32[sx*sy] spanEnd,
                     then span data (per column: skip, count, count*(colour,normal),
                     count); a column offset of -1 is empty
  tailers (numLimbs * 92 B): spanStart/End/Data offsets (into body), f32 scale,
                     12 f32 transform, 3 f32 min, 3 f32 max, u8 sx, sy, sz, normalType
Palette index 0 is transparent.
"""

import struct
from dataclasses import dataclass, field

from .images import load_palette
from .vxl_normals import NORMALS_BY_MODE

# diffuse lighting, matching zzattack's ccmaps-net software rasterizer:
#   colour *= Ambient + Diffuse * max(dot(normal, light), 0)
_AMBIENT = 0.8
_DIFFUSE = 1.3
# game light directions: TS = -X; YR = -X rotated 45° about Z
_LIGHT = {2: (-1.0, 0.0, 0.0),
          4: (-0.7071067811865476, -0.7071067811865476, 0.0)}


class VxlError(Exception):
    pass


@dataclass
class VxlSection:
    name: str
    sx: int
    sy: int
    sz: int
    scale: float
    normal_type: int
    voxels: dict = field(default_factory=dict)   # (x,y,z) -> (colour, normal)


@dataclass
class VxlModel:
    palette: list = field(default_factory=list)   # 256 (r,g,b)
    sections: list = field(default_factory=list)   # [VxlSection]


def parse_vxl(data):
    if len(data) < 802:
        raise VxlError("too small for a VXL")
    _unk, num_limbs, _num2, body_size = struct.unpack_from("<IIII", data, 16)
    if num_limbs == 0 or num_limbs > 512:
        raise VxlError("bad VXL (limb count)")
    palette = load_palette(data[34:34 + 768])
    body_base = 802 + num_limbs * 28
    tail_base = body_base + body_size
    if tail_base + num_limbs * 92 > len(data):
        raise VxlError("VXL truncated (tailer past EOF)")

    model = VxlModel(palette=palette)
    for i in range(num_limbs):
        hb = 802 + i * 28
        name = data[hb:hb + 16].split(b"\x00", 1)[0].decode("latin-1", "replace")
        tb = tail_base + i * 92
        span_start_off, _span_end_off, span_data_off = struct.unpack_from(
            "<III", data, tb)
        scale = struct.unpack_from("<f", data, tb + 12)[0]
        sx, sy, sz = data[tb + 88], data[tb + 89], data[tb + 90]
        ntype = data[tb + 91]
        sec = VxlSection(name=name, sx=sx, sy=sy, sz=sz, scale=scale,
                         normal_type=ntype)
        _read_section_voxels(data, body_base, span_start_off, span_data_off, sec)
        model.sections.append(sec)
    return model


def _read_section_voxels(data, body_base, span_start_off, span_data_off, sec):
    starts_base = body_base + span_start_off
    data_base = body_base + span_data_off
    ncols = sec.sx * sec.sy
    for col in range(ncols):
        so = starts_base + col * 4
        if so + 4 > len(data):
            break
        off = struct.unpack_from("<i", data, so)[0]
        if off < 0:
            continue
        x = col % sec.sx
        y = col // sec.sx
        p = data_base + off
        z = 0
        while z < sec.sz and p < len(data):
            skip = data[p]
            p += 1
            z += skip
            if p >= len(data):
                break
            count = data[p]
            p += 1
            for _k in range(count):
                if p + 1 >= len(data) or z >= sec.sz:
                    break
                colour = data[p]
                normal = data[p + 1]
                p += 2
                sec.voxels[(x, y, z)] = (colour, normal)
                z += 1
            p += 1          # trailing count byte


def render_vxl(model, max_px=200, transparent_index=0, palette=None,
               lighting=True, vpl=None, direction=32):
    """Fixed isometric still of every section's voxels → (w, h, rgba). Nearest
    voxel wins (z-buffer). RA2 VXLs embed an empty palette, so pass an external
    unit palette (`palette`); else the embedded one is used (TS voxels).

    Lighting, best → fallback:
      1. `vpl` (a parsed voxels.vpl): game-accurate — each voxel's normal picks a
         lighting page and the colour index is remapped through it before the
         palette lookup (`direction` is the unit facing 0..255).
      2. `lighting`: diffuse from the stored normal (Ambient + Diffuse·max(dot,0)).
      3. mild height shading when a section has no normal table.
    Raises VxlError if there are no voxels."""
    pal = palette if palette else model.palette
    pts = []                       # (px, py, depth, r, g, b)
    maxz = max((s.sz for s in model.sections), default=1) or 1
    for sec in model.sections:
        normals = NORMALS_BY_MODE.get(sec.normal_type) if lighting else None
        light = _LIGHT.get(sec.normal_type, _LIGHT[4])
        pages = None
        if vpl is not None and normals:
            from .vpl import vpl_pages
            pages = vpl_pages(normals, ra2=(sec.normal_type == 4),
                              direction=direction)
        for (x, y, z), (colour, nidx) in sec.voxels.items():
            if colour == transparent_index:
                continue
            if pages is not None:
                page = pages[nidx] if nidx < len(pages) else 0
                rr, gg, bb = pal[vpl.remap(page, colour)]
            else:
                r, g, b = pal[colour]
                if normals:
                    nx, ny, nz = (normals[nidx] if nidx < len(normals)
                                  else normals[-1])
                    d = nx * light[0] + ny * light[1] + nz * light[2]
                    mult = _AMBIENT + _DIFFUSE * (d if d > 0 else 0.0)
                else:
                    mult = 0.55 + 0.45 * (z / maxz)
                rr = min(255, int(r * mult))
                gg = min(255, int(g * mult))
                bb = min(255, int(b * mult))
            # 2:1 isometric, z up; depth: nearer = larger x+y+z
            pts.append(((x - y), (x + y) * 0.5 - z, x + y + z, rr, gg, bb))
    if not pts:
        raise VxlError("no voxels to render")

    min_px = min(p[0] for p in pts)
    max_px_ = max(p[0] for p in pts)
    min_py = min(p[1] for p in pts)
    max_py_ = max(p[1] for p in pts)
    span_x = (max_px_ - min_px) or 1
    span_y = (max_py_ - min_py) or 1
    scale = max(1, int(max_px / max(span_x, span_y)))
    W = int(span_x * scale) + scale + 1
    H = int(span_y * scale) + scale + 1

    buf = bytearray(W * H * 4)
    zbuf = [-10 ** 9] * (W * H)
    for (px, py, depth, rr, gg, bb) in pts:
        sx = int((px - min_px) * scale)
        sy = int((py - min_py) * scale)
        for dy in range(scale):
            yy = sy + dy
            if yy < 0 or yy >= H:
                continue
            for dx in range(scale):
                xx = sx + dx
                if xx < 0 or xx >= W:
                    continue
                o = yy * W + xx
                if depth > zbuf[o]:
                    zbuf[o] = depth
                    q = o * 4
                    buf[q] = rr
                    buf[q + 1] = gg
                    buf[q + 2] = bb
                    buf[q + 3] = 255
    return W, H, bytes(buf)


def voxel_count(model):
    return sum(len(s.voxels) for s in model.sections)
