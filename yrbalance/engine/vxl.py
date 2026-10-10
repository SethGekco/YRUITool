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
    hva_multiplier: float                         # scales the HVA translation
    normal_type: int
    min_bounds: tuple = (0.0, 0.0, 0.0)
    max_bounds: tuple = (1.0, 1.0, 1.0)
    voxels: dict = field(default_factory=dict)   # (x,y,z) -> (colour, normal)

    def scale(self):
        """Per-axis voxel size: (MaxBounds - MinBounds) / Size."""
        sx = (self.max_bounds[0] - self.min_bounds[0]) / self.sx if self.sx else 1.0
        sy = (self.max_bounds[1] - self.min_bounds[1]) / self.sy if self.sy else 1.0
        sz = (self.max_bounds[2] - self.min_bounds[2]) / self.sz if self.sz else 1.0
        return (sx or 1.0, sy or 1.0, sz or 1.0)


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
        hva_mult = struct.unpack_from("<f", data, tb + 12)[0]
        # tb+16..64 = 12-float section transform (unused — HVA overrides it)
        min_b = struct.unpack_from("<3f", data, tb + 64)
        max_b = struct.unpack_from("<3f", data, tb + 76)
        sx, sy, sz = data[tb + 88], data[tb + 89], data[tb + 90]
        ntype = data[tb + 91]
        sec = VxlSection(name=name, sx=sx, sy=sy, sz=sz, hva_multiplier=hva_mult,
                         normal_type=ntype, min_bounds=min_b, max_bounds=max_b)
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


def _shade(colour, nidx, z, maxz, pal, normals, light, pages, vpl):
    if pages is not None:
        page = pages[nidx] if nidx < len(pages) else 0
        return pal[vpl.remap(page, colour)]
    r, g, b = pal[colour]
    if normals:
        nx, ny, nz = normals[nidx] if nidx < len(normals) else normals[-1]
        d = nx * light[0] + ny * light[1] + nz * light[2]
        mult = _AMBIENT + _DIFFUSE * (d if d > 0 else 0.0)
    else:
        mult = 0.55 + 0.45 * (z / maxz)
    return (min(255, int(r * mult)), min(255, int(g * mult)),
            min(255, int(b * mult)))


def _collect_points(model, hva, pal, vpl, direction, lighting, transparent_index):
    """Project one (model, hva) into shared world space → list of
    (ix, iy, depth, r, g, b, cell_hint). HVA frame 0 places each section (a turret
    on its body, a barrel on the turret). Without an HVA, sections sit at their
    MinBounds (identity rotation)."""
    pts = []
    maxz = max((s.sz for s in model.sections), default=1) or 1
    for idx, sec in enumerate(model.sections):
        normals = NORMALS_BY_MODE.get(sec.normal_type) if lighting else None
        light = _LIGHT.get(sec.normal_type, _LIGHT[4])
        pages = None
        if vpl is not None and normals:
            from .vpl import vpl_pages
            pages = vpl_pages(normals, ra2=(sec.normal_type == 4),
                              direction=direction)
        scx, scy, scz = sec.scale()
        m = hva.matrix(idx) if hva is not None else \
            (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0)
        hm = sec.hva_multiplier
        tx = m[3] * hm * scx
        ty = m[7] * hm * scy
        tz = m[11] * hm * scz
        mnx, mny, mnz = sec.min_bounds
        cell_hint = max(scx, scy, scz)
        for (x, y, z), (colour, nidx) in sec.voxels.items():
            if colour == transparent_index:
                continue
            qx = x * scx + mnx
            qy = y * scy + mny
            qz = z * scz + mnz
            wx = m[0] * qx + m[1] * qy + m[2] * qz + tx
            wy = m[4] * qx + m[5] * qy + m[6] * qz + ty
            wz = m[8] * qx + m[9] * qy + m[10] * qz + tz
            rr, gg, bb = _shade(colour, nidx, z, maxz, pal, normals, light,
                                pages, vpl)
            # 2:1 isometric, z up; depth: nearer = larger wx+wy+wz
            pts.append((wx - wy, (wx + wy) * 0.5 - wz, wx + wy + wz,
                        rr, gg, bb, cell_hint))
    return pts


def render_parts(parts, max_px=200, palette=None, lighting=True, vpl=None,
                 direction=32, transparent_index=0):
    """Composite several voxel parts (a unit's body + turret + barrel) into one
    isometric still. `parts` is [(VxlModel, Hva|None)]; all are placed in shared
    world space via their HVAs and z-buffered together. Returns (w, h, rgba)."""
    pts = []
    for model, hva in parts:
        pal = palette if palette else model.palette
        pts += _collect_points(model, hva, pal, vpl, direction, lighting,
                               transparent_index)
    if not pts:
        raise VxlError("no voxels to render")

    min_px = min(p[0] for p in pts)
    max_px_ = max(p[0] for p in pts)
    min_py = min(p[1] for p in pts)
    max_py_ = max(p[1] for p in pts)
    span_x = (max_px_ - min_px) or 1
    span_y = (max_py_ - min_py) or 1
    fit = max_px / max(span_x, span_y)
    cell_max = max((p[6] for p in pts), default=1.0) or 1.0
    pad = max(1, int(round(fit * cell_max)))
    W = int(span_x * fit) + pad + 1
    H = int(span_y * fit) + pad + 1

    buf = bytearray(W * H * 4)
    zbuf = [-10 ** 18] * (W * H)
    for (px, py, depth, rr, gg, bb, cell_hint) in pts:
        sx = int((px - min_px) * fit)
        sy = int((py - min_py) * fit)
        cell = max(1, int(round(fit * cell_hint)))
        for dy in range(cell):
            yy = sy + dy
            if yy < 0 or yy >= H:
                continue
            row = yy * W
            for dx in range(cell):
                xx = sx + dx
                if xx < 0 or xx >= W:
                    continue
                o = row + xx
                if depth > zbuf[o]:
                    zbuf[o] = depth
                    q = o * 4
                    buf[q] = rr
                    buf[q + 1] = gg
                    buf[q + 2] = bb
                    buf[q + 3] = 255
    return W, H, bytes(buf)


def render_vxl(model, max_px=200, transparent_index=0, palette=None,
               lighting=True, vpl=None, direction=32, hva=None):
    """Isometric still of a single VXL (see render_parts). Lighting, best →
    fallback: `vpl` (game-accurate page remap) → `lighting` (diffuse from the
    stored normal) → height shading. `hva` (frame 0) places the sections."""
    return render_parts([(model, hva)], max_px=max_px,
                         palette=palette if palette else model.palette,
                         lighting=lighting, vpl=vpl, direction=direction,
                         transparent_index=transparent_index)


def voxel_count(model):
    return sum(len(s.voxels) for s in model.sections)
