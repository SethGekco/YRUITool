#!/usr/bin/env python3
"""
VPL (voxels.vpl) parse + page selection + VPL-lit VXL render. The format and page
math are ported from ccmaps-net; validated here on a synthetic VPL and, when a real
voxels.vpl can be extracted from the game mixes, on that too.

    python3 tests/test_vpl.py
"""

import sys
import os
import struct
import glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import parse_vpl, vpl_pages, render_vxl
from yrbalance.engine.vxl_normals import NORMALS_BY_MODE


def _synth_vpl(num_sections=32):
    # page p maps every colour c -> min(255, c + p) so brighter pages shift the ramp
    data = bytearray(struct.pack("<IIII", 0, 255, num_sections, 0))
    data += bytes(768)
    for p in range(num_sections):
        data += bytes((min(255, c + p) for c in range(256)))
    return bytes(data)


def main():
    ok = True

    vpl = parse_vpl(_synth_vpl(32))
    parse_ok = (vpl.num_sections == 32 and len(vpl.sections) == 32
                and vpl.remap(0, 100) == 100 and vpl.remap(5, 100) == 105
                and vpl.remap(99, 100) == vpl.remap(31, 100))   # page clamp
    print(f"parse VPL + remap + page clamp: {'OK' if parse_ok else 'FAIL'}")
    ok = ok and parse_ok

    # page selection: valid byte pages, special normals 253-255 neutral (16)
    pages = vpl_pages(NORMALS_BY_MODE[4], ra2=True, direction=32)
    pages_ok = (len(pages) == 256 and all(0 <= p <= 255 for p in pages)
                and pages[253] == 16 and pages[254] == 16 and pages[255] == 16
                and max(pages[:245]) > 0)
    print(f"vpl_pages (byte pages, neutral specials, some lit): "
          f"{'OK' if pages_ok else 'FAIL'}")
    ok = ok and pages_ok

    # find_in_mixes recursion: a file nested inside a mix-inside-a-mix
    import tempfile
    from yrbalance.engine import write_mix, find_in_mixes
    with tempfile.TemporaryDirectory() as d:
        payload = b"NESTED-VPL-PAYLOAD" * 8
        inner_path = os.path.join(d, "inner.mix")
        write_mix(inner_path, {"voxels.vpl": payload})
        inner_bytes = open(inner_path, "rb").read()
        os.remove(inner_path)                       # don't leave it loose
        mixdir = os.path.join(d, "mixes")
        os.makedirs(mixdir)
        write_mix(os.path.join(mixdir, "outer.mix"), {"inner.mix": inner_bytes})
        got = find_in_mixes([mixdir], "voxels.vpl")
        nested_ok = got == payload
    print(f"find_in_mixes descends nested mixes: {'OK' if nested_ok else 'FAIL'}")
    ok = ok and nested_ok

    # real voxels.vpl if we cached one, else extract from game mixes
    vpl_path = "/tmp/voxels.vpl"
    if not os.path.isfile(vpl_path):
        try:
            from yrbalance.engine.mixlib import MixFile, mix_id
            target = mix_id("voxels.vpl")
            gdir = "/home/rex/snap/cncra2yr/common/.wine/drive_c/Westwood/RA2"

            def search(d, depth=0):
                try:
                    mf = MixFile(d) if isinstance(d, str) else MixFile.from_bytes(d)
                except Exception:
                    return None
                for e in mf.entries:
                    if e.id == target:
                        try:
                            return mf.read_id(e.id)
                        except Exception:
                            return None
                if depth < 3:
                    for e in mf.entries:
                        try:
                            sub = mf.read_id(e.id)
                        except Exception:
                            continue
                        if 84 <= len(sub) < 80_000_000:
                            r = search(sub, depth + 1)
                            if r:
                                return r
                return None
            for mp in sorted(glob.glob(gdir + "/*.mix")):
                r = search(mp)
                if r:
                    open(vpl_path, "wb").write(r)
                    break
        except Exception:
            pass

    vxlf = glob.glob("/home/rex/snap/cncra2yr/common/.wine/drive_c/Westwood/"
                     "RA2/usaguntur.vxl")
    if os.path.isfile(vpl_path) and vxlf:
        from yrbalance.engine import parse_vxl, grayscale_palette
        rvpl = parse_vpl(open(vpl_path, "rb").read())
        m = parse_vxl(open(vxlf[0], "rb").read())
        pal = grayscale_palette()
        _, _, vpl_rgba = render_vxl(m, palette=pal, vpl=rvpl, direction=32)
        _, _, diff_rgba = render_vxl(m, palette=pal, vpl=None, direction=32)
        real_ok = (rvpl.num_sections == 32 and vpl_rgba != diff_rgba
                   and any(vpl_rgba[i] for i in range(3, len(vpl_rgba), 4)))
        print(f"real voxels.vpl ({rvpl.num_sections} pages) VPL render ≠ diffuse: "
              f"{'OK' if real_ok else 'FAIL'}")
        ok = ok and real_ok
    else:
        print("real VPL render: SKIP (no voxels.vpl / vxl on this machine)")

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
