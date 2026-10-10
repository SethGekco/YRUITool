"""
HVA (voxel animation) parsing. An .hva holds, per animation frame and per voxel
section, a 3×4 affine matrix that positions/orients that section in the model's
shared world space. For a still preview we use frame 0 — the resting pose — which
is what places a turret on its body and a barrel on the turret.

Format: char[16] name, int32 numFrames, int32 numSections, numSections×char[16]
section names, then numFrames × numSections × 12 float32 (row-major 3×4: the 4th
column of each row is translation).
"""

import struct
from dataclasses import dataclass, field

_IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)


class HvaError(Exception):
    pass


@dataclass
class Hva:
    num_frames: int
    section_names: list = field(default_factory=list)
    matrices: list = field(default_factory=list)   # matrices[section][frame] -> 12 floats

    def matrix(self, section, frame=0):
        """Frame matrix for a section (identity if out of range)."""
        if 0 <= section < len(self.matrices) and self.matrices[section]:
            fr = min(frame, len(self.matrices[section]) - 1)
            return self.matrices[section][fr]
        return _IDENTITY


def parse_hva(data):
    if len(data) < 24:
        raise HvaError("too small for an HVA")
    num_frames, num_sections = struct.unpack_from("<ii", data, 16)
    if num_frames <= 0 or num_sections <= 0 or num_sections > 512:
        raise HvaError("bad HVA (frame/section count)")
    off = 24
    names = []
    for _i in range(num_sections):
        if off + 16 > len(data):
            break
        names.append(data[off:off + 16].split(b"\x00", 1)[0]
                     .decode("latin-1", "replace"))
        off += 16
    mats = [[] for _ in range(num_sections)]
    for _frame in range(num_frames):
        for s in range(num_sections):
            if off + 48 > len(data):
                break
            mats[s].append(struct.unpack_from("<12f", data, off))
            off += 48
    return Hva(num_frames=num_frames, section_names=names, matrices=mats)
