"""
voxels.vpl — the Voxel Palette Lookup used for game-accurate voxel lighting.

Instead of multiplying RGB, the game maps each voxel's surface normal to a lighting
page (0..numSections-1), then remaps the voxel's colour index THROUGH that page
before the unit-palette lookup: rgb = unit_palette[ vpl[page][colour] ]. The page a
normal maps to is a Blinn-Phong term quantized ×16.

Format + page math ported verbatim from zzattack's ccmaps-net (VplFile.cs and
VxlRenderer.PreCalculateVplLighting):
  header: u32 firstRemap, u32 lastRemap, u32 numSections, u32 unknown
          + 768-byte embedded palette (unused) + numSections * 256-byte pages
"""

import math
import struct
from dataclasses import dataclass, field

# game light directions (match vxl.py): TS = -X; YR = -X rotated 45° about Z
_TS_LIGHT = (-1.0, 0.0, 0.0)
_YR_LIGHT = (-math.cos(math.radians(45)), -math.sin(math.radians(45)), 0.0)
_SPECULAR_STRENGTH = 3.0


class VplError(Exception):
    pass


@dataclass
class Vpl:
    num_sections: int
    sections: list = field(default_factory=list)   # [bytes(256)]

    def remap(self, page, colour):
        if page >= self.num_sections:
            page = self.num_sections - 1
        return self.sections[page][colour]


def parse_vpl(data):
    if len(data) < 16 + 768:
        raise VplError("too small for a voxels.vpl")
    _first, _last, num_sections, _unk = struct.unpack_from("<IIII", data, 0)
    if num_sections == 0 or num_sections > 256:
        raise VplError("bad VPL (section count)")
    base = 16 + 768
    sections = []
    for i in range(num_sections):
        off = base + i * 256
        if off + 256 > len(data):
            break
        sections.append(data[off:off + 256])
    if not sections:
        raise VplError("VPL has no lookup sections")
    return Vpl(num_sections=len(sections), sections=sections)


def _rotz(v, a):
    c, s = math.cos(a), math.sin(a)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c, v[2])


def vpl_pages(normals, ra2=True, direction=0):
    """Per-normal lighting page (list of 256 bytes), replicating ccmaps-net's
    PreCalculateVplLighting: page = clamp((diffuse + specular) * 16). `direction`
    is the unit facing (0..255); special normals 253-255 are neutrally lit."""
    rot = math.tau * direction / 256.0
    base = _YR_LIGHT if ra2 else _TS_LIGHT
    light = _rotz(base, rot - math.radians(45))
    vx, vy, vz = light[0], light[1], light[2] + 1.0        # + viewer (0,0,1)
    vlen = math.sqrt(vx * vx + vy * vy + vz * vz) or 1.0
    half = (vx / vlen, vy / vlen, vz / vlen)

    pages = [0] * 256
    for i, n in enumerate(normals):
        if i >= 256:
            break
        diffuse = max(n[0] * light[0] + n[1] * light[1] + n[2] * light[2], 0.0)
        hd = n[0] * half[0] + n[1] * half[1] + n[2] * half[2]
        specular = hd / (_SPECULAR_STRENGTH - hd * _SPECULAR_STRENGTH + hd) \
            if (_SPECULAR_STRENGTH - hd * _SPECULAR_STRENGTH + hd) != 0 else 0.0
        if specular < 0:
            specular = 0.0
        pages[i] = max(0, min(255, int((diffuse + specular) * 16.0)))
    pages[253] = pages[254] = pages[255] = 16
    return pages
