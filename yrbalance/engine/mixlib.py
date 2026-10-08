"""
MIX archive reader/writer for RA2/YR.

Supports the two unencrypted layouts the game and mods use:
  * "new" format (TS/RA2/YR): 4-byte flags, then a 6-byte header
    (uint16 file_count, int32 body_size), then count×12-byte index entries
    (uint32 id, int32 offset, int32 size), then the body. Offsets are relative
    to the body start.
  * "old" format (TD/RA1): the 6-byte header at offset 0, index at 6.

Encrypted mixes (flags & 0x20000) use Blowfish with an RSA-wrapped key; those are
decrypted transparently (XCC fast-key, via _blowfish). Reading works for both
plain and encrypted; writing always produces a plain mix. Stock game mixes are
listed in VANILLA_MIX_NAMES / is_vanilla_mix() so callers can leave them alone —
that is a policy choice (don't modify vanilla), independent of encryption.

Filename → id hashing follows XCC (OlafvdSpek/xcc, GPL): uppercase, '/'→'\\',
pad the final partial 4-byte block, then CRC-32. I/O-free except for the file it
opens; reading uses seeks so huge mixes aren't loaded whole. Writing is atomic
(temp file + replace) so a failed pack can't corrupt the target.
"""

import base64
import os
import struct
import zlib
from dataclasses import dataclass

from ._blowfish import Blowfish

MIX_CHECKSUM = 0x00010000
MIX_ENCRYPTED = 0x00020000
_LMD_NAME = "local mix database.dat"
_LMD_MAGIC = b"XCC by Olaf van der Spek\x1a"

# Stock game mixes to leave alone (never port/modify into/out of). By basename,
# lowercased; from a vanilla RA2+YR install. Extend via vanilla_mix_names().
VANILLA_MIX_NAMES = frozenset({
    "ra2.mix", "ra2md.mix", "language.mix", "langmd.mix", "theme.mix",
    "thememd.mix", "multi.mix", "multimd.mix", "movies01.mix", "movies02.mix",
    "movmd03.mix", "maps01.mix", "maps02.mix", "mapsmd03.mix", "ecache97.mix",
    "expand97.mix", "expandmd01.mix",
})


def is_vanilla_mix(path_or_name):
    """True if this is a stock game mix that should not be touched."""
    return os.path.basename(path_or_name).lower() in VANILLA_MIX_NAMES


def vanilla_mix_names(vanilla_dir):
    """Build the ignore set from a real vanilla install dir (its *.mix files)."""
    try:
        return frozenset(f.lower() for f in os.listdir(vanilla_dir)
                         if f.lower().endswith(".mix"))
    except OSError:
        return VANILLA_MIX_NAMES


# ── Westwood RSA-wrapped Blowfish key (XCC fast-key) ─────────────────────────
_WW_PUBKEY = "AihRvNoIbTn85FZRYNZRcT+i6KpU+maCsEqr3Q5q+LDB5tH7Tz2qQ38V"


def _ww_modulus():
    raw = base64.b64decode(_WW_PUBKEY)
    j = 1                                   # skip the leading tag byte (0x02)
    if raw[j] & 0x80:
        nlen = raw[j] & 0x7f
        klen = int.from_bytes(raw[j + 1:j + 1 + nlen], "big")
        j += 1 + nlen
    else:
        klen = raw[j]
        j += 1
    return int.from_bytes(raw[j:j + klen], "big")


_WW_MOD = _ww_modulus()
_WW_EXP = 0x10001


def _decrypt_blowfish_key(src):
    """Recover the 56-byte Blowfish key from the 80-byte RSA-wrapped source."""
    a = (_WW_MOD.bit_length() - 2) // 8
    pre_len = (55 // a + 1) * (a + 1)
    out = bytearray()
    off = 0
    while a + 1 <= pre_len:
        c = int.from_bytes(src[off:off + a + 1], "little")
        m = pow(c, _WW_EXP, _WW_MOD)
        out += m.to_bytes(a + 8, "little")[:a]
        pre_len -= a + 1
        off += a + 1
    return bytes(out[:56])


class MixError(Exception):
    pass


class MixEncrypted(MixError):
    pass


def mix_id(name):
    """RA2/YR filename hash (XCC algorithm): CRC-32 of the uppercased, '\\'-ified,
    block-padded name. Returns an unsigned 32-bit int."""
    name = name.upper().replace("/", "\\")
    n = len(name)
    a = n & ~3
    if n & 3:
        name = name + chr(n - a)
        name = name + name[a] * (3 - (n & 3))
    return zlib.crc32(name.encode("latin-1")) & 0xFFFFFFFF


@dataclass
class MixEntry:
    id: int
    offset: int       # absolute byte offset in the file
    size: int
    name: str = ""    # resolved if a local mix database is present


def _sniff_ext(data):
    """Best-effort file type from the first bytes (for naming extracted blobs)."""
    if data[:4] == b"RIFF":
        return "wav"
    if data[:15] == b"Voxel Animation":
        return "vxl"
    if data[:1] in (b"[", b";") or (data[:1].isalpha() and b"=" in data[:200]):
        return "ini"
    return "bin"


class MixFile:
    """Read access to a MIX archive. Index is parsed on open via seeks."""

    def __init__(self, path):
        self.path = path
        self.entries = []          # [MixEntry]
        self.by_id = {}            # id -> MixEntry
        self.encrypted = False
        self.checksum = False
        self.old_format = False
        self._parse_index()
        self._resolve_names()

    def _parse_index(self):
        size = os.path.getsize(self.path)
        with open(self.path, "rb") as f:
            first = f.read(4)
            if len(first) < 4:
                raise MixError("file too small to be a MIX")
            c0 = struct.unpack("<H", first[:2])[0]
            if c0 != 0:
                self.old_format = True
                f.seek(0)
                c_files, _body = struct.unpack("<Hi", f.read(6))
                base = 6 + c_files * 12
                index = f.read(c_files * 12)
            else:
                flags = struct.unpack("<I", first)[0]
                self.checksum = bool(flags & MIX_CHECKSUM)
                self.encrypted = bool(flags & MIX_ENCRYPTED)
                if self.encrypted:
                    c_files, index, base = self._parse_encrypted(f)
                else:
                    f.seek(4)
                    c_files, _body = struct.unpack("<Hi", f.read(6))
                    base = 10 + c_files * 12
                    index = f.read(c_files * 12)
        if len(index) < c_files * 12:
            raise MixError("truncated index")
        for i in range(c_files):
            fid, off, sz = struct.unpack_from("<Iii", index, i * 12)
            off += base
            if off < 0 or off + sz > size:
                raise MixError(f"entry {fid:#010x} out of bounds "
                               "(corrupt or wrong format)")
            e = MixEntry(fid, off, sz)
            self.entries.append(e)
            self.by_id[fid] = e

    def _parse_encrypted(self, f):
        """Decrypt a Blowfish-encrypted header. f is positioned after the 4-byte
        flags. Returns (c_files, index_bytes, base_offset)."""
        key = _decrypt_blowfish_key(f.read(80))
        bf = Blowfish(key)
        first = bf.decrypt(f.read(8))                 # header + 2 index bytes
        c_files, _body = struct.unpack("<Hi", first[:6])
        cb_index = c_files * 12
        cb_f = (cb_index + 5) & ~7                     # index padded to 8 bytes
        rest = bf.decrypt(f.read(cb_f))
        index = first[6:8] + rest[:cb_index - 2]
        base = 92 + cb_f                               # 4 flags + 80 key + 8 first
        return c_files, index, base

    def _resolve_names(self):
        self.names = {}
        lmd = self.by_id.get(mix_id(_LMD_NAME))
        if lmd:
            try:
                for nm in _parse_lmd(self.read_id(lmd.id)):
                    self.names[mix_id(nm)] = nm
            except Exception:
                pass
        for e in self.entries:
            e.name = self.names.get(e.id, "")

    # ── reading ─────────────────────────────────────────────────────────────
    def read_id(self, fid):
        e = self.by_id.get(fid)
        if e is None:
            raise KeyError(f"id {fid:#010x} not in mix")
        with open(self.path, "rb") as f:
            f.seek(e.offset)
            return f.read(e.size)

    def read(self, name):
        """Read a file by name (hashed to its id)."""
        return self.read_id(mix_id(name))

    def has(self, name):
        return mix_id(name) in self.by_id

    def listing(self):
        """[(id, size, name_or_'', type_guess)] — type sniffed when unnamed."""
        out = []
        for e in self.entries:
            t = os.path.splitext(e.name)[1].lstrip(".") if e.name else ""
            if not t:
                with open(self.path, "rb") as f:
                    f.seek(e.offset)
                    t = _sniff_ext(f.read(min(e.size, 256)))
            out.append((e.id, e.size, e.name, t))
        return out

    def extract(self, name, dest_path):
        with open(dest_path, "wb") as f:
            f.write(self.read(name))

    def extract_all(self, dest_dir):
        """Extract every entry. Named entries keep their name; unnamed are written
        as <id>.<sniffed-ext>. Returns the list of written filenames."""
        os.makedirs(dest_dir, exist_ok=True)
        written = []
        for fid, size, name, typ in self.listing():
            fn = name or f"{fid:08X}.{typ}"
            with open(os.path.join(dest_dir, fn), "wb") as f:
                f.write(self.read_id(fid))
            written.append(fn)
        return written


def _parse_lmd(data):
    """Best-effort: pull filenames (null-terminated ASCII) out of an XCC local
    mix database.dat. Tolerant of header variations."""
    body = data
    if data[:len(_LMD_MAGIC)] == _LMD_MAGIC:
        body = data[len(_LMD_MAGIC) + 16:]   # skip magic + a small header
    names = []
    for tok in body.split(b"\x00"):
        try:
            s = tok.decode("ascii")
        except UnicodeDecodeError:
            continue
        if 2 <= len(s) <= 255 and s.isprintable() and "." in s:
            names.append(s)
    return names


def _build_lmd(names):
    out = bytearray(_LMD_MAGIC)
    out += struct.pack("<iiii", 0, 0, 0, 0)   # size/type/version/game (game ignores)
    for n in names:
        out += n.encode("latin-1") + b"\x00"
    return bytes(out)


def write_mix(path, files, with_lmd=True):
    """Write an unencrypted new-format MIX from {name: bytes}. Atomic (temp +
    replace). Includes a local mix database so names round-trip. Returns the
    number of files written."""
    items = dict(files)
    if with_lmd:
        items[_LMD_NAME] = _build_lmd([n for n in files])
    recs = sorted(((mix_id(n), bytes(d)) for n, d in items.items()),
                  key=lambda r: r[0])
    body = bytearray()
    index = bytearray()
    for fid, data in recs:
        index += struct.pack("<Iii", fid, len(body), len(data))
        body += data
    header = struct.pack("<I", 0) + struct.pack("<Hi", len(recs), len(body))
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(header)
        f.write(index)
        f.write(body)
    os.replace(tmp, path)
    return len(recs)
