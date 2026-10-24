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
import io
import os
import struct
import zlib
from dataclasses import dataclass

from ._blowfish import Blowfish

MIX_CHECKSUM = 0x00010000
MIX_ENCRYPTED = 0x00020000
_LMD_NAME = "local mix database.dat"
_LMD_MAGIC = b"XCC by Olaf van der Spek\x1a"

# Stock RA2/YR game mixes. By basename, lowercased.
VANILLA_MIX_NAMES = frozenset({
    "ra2.mix", "ra2md.mix", "language.mix", "langmd.mix", "theme.mix",
    "thememd.mix", "multi.mix", "multimd.mix", "movies01.mix", "movies02.mix",
    "movmd03.mix", "maps01.mix", "maps02.mix", "mapsmd03.mix", "ecache97.mix",
    "expand97.mix", "expandmd01.mix",
})
# Mixes shipped by the community launcher/frameworks, not a specific mod:
CNCNET_MIX_NAMES = frozenset({
    "cncnet.mix", "ra2mode.mix", "expandmd99.mix", "expandmd00.mix",
    "expandspawn01.mix", "expandspawn02.mix", "expandspawn09.mix",
})
ARES_MIX_NAMES = frozenset({"ares.mix"})
# The full "not a specific mod's content" set — skip these when cataloguing a mod.
STOCK_MIX_NAMES = VANILLA_MIX_NAMES | CNCNET_MIX_NAMES | ARES_MIX_NAMES

# Loose framework binaries (Phobos / Ares / CnCNet) — never mod content. Phobos
# ships only its DLL/PDB; the rest are launcher/injector files.
KNOWN_FRAMEWORK_FILES = frozenset({
    "phobos.dll", "phobos.pdb", "ares.dll", "ares.pdb", "syringe.exe",
    "blowfish.dll", "ddraw.dll", "binkw32.dll", "patchw32.dll", "drvmgt.dll",
    "qres32.dll", "findmrua.dll", "readme data.dll", "cncnet5.dll",
})


def is_vanilla_mix(path_or_name):
    """True if this is a stock RA2/YR game mix."""
    return os.path.basename(path_or_name).lower() in VANILLA_MIX_NAMES


def is_stock_mix(path_or_name):
    """True if this mix is vanilla OR a community-framework mix (CnCNet/Ares) —
    i.e. not a specific mod's own content, so the porter should leave it alone."""
    return os.path.basename(path_or_name).lower() in STOCK_MIX_NAMES


def stock_mix_names(*reference_dirs):
    """Build the ignore set from real reference installs (a vanilla dir and/or a
    vanilla+cncnet dir): every *.mix they contain, unioned with the built-in
    STOCK_MIX_NAMES."""
    names = set(STOCK_MIX_NAMES)
    for d in reference_dirs:
        try:
            names |= {f.lower() for f in os.listdir(d)
                      if f.lower().endswith(".mix")}
        except (OSError, TypeError):
            pass
    return frozenset(names)


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
    """Read access to a MIX archive, from a file path or an in-memory bytes buffer
    (the latter lets nested mixes inside a master mix be parsed without extracting).
    Index is parsed on open via seeks."""

    def __init__(self, source):
        if isinstance(source, (bytes, bytearray)):
            self._data = bytes(source)
            self.path = "<bytes>"
            self._size = len(self._data)
        else:
            self._data = None
            self.path = source
            self._size = os.path.getsize(source)
        self.entries = []          # [MixEntry]
        self.by_id = {}            # id -> MixEntry
        self.encrypted = False
        self.checksum = False
        self.old_format = False
        self._parse_index()
        self._resolve_names()

    @classmethod
    def from_bytes(cls, data):
        return cls(bytes(data))

    @property
    def is_complete(self):
        """True if the mix's declared header+index+body accounts for ~all of its
        bytes — distinguishes a real (possibly nested) mix from a leaf asset whose
        bytes happened to parse. Small slack for checksum/alignment padding."""
        return bool(self.entries) and 0 <= self._size - self.total <= 24

    def _open(self):
        return io.BytesIO(self._data) if self._data is not None \
            else open(self.path, "rb")

    def _parse_index(self):
        size = self._size
        body = 0
        with self._open() as f:
            first = f.read(4)
            if len(first) < 4:
                raise MixError("file too small to be a MIX")
            c0 = struct.unpack("<H", first[:2])[0]
            if c0 != 0:
                self.old_format = True
                f.seek(0)
                c_files, body = struct.unpack("<Hi", f.read(6))
                base = 6 + c_files * 12
                index = f.read(c_files * 12)
            else:
                flags = struct.unpack("<I", first)[0]
                self.checksum = bool(flags & MIX_CHECKSUM)
                self.encrypted = bool(flags & MIX_ENCRYPTED)
                if self.encrypted:
                    c_files, index, base, body = self._parse_encrypted(f)
                else:
                    f.seek(4)
                    c_files, body = struct.unpack("<Hi", f.read(6))
                    base = 10 + c_files * 12
                    index = f.read(c_files * 12)
        if len(index) < c_files * 12:
            raise MixError("truncated index")
        # Total bytes this mix declares it occupies (for nested-mix validation).
        self.total = base + body + (20 if self.checksum else 0)
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
        src = f.read(80)
        if len(src) < 80:
            raise MixError("truncated encrypted header")
        key = _decrypt_blowfish_key(src)
        bf = Blowfish(key)
        block = f.read(8)
        if len(block) < 8:
            raise MixError("truncated encrypted header")
        first = bf.decrypt(block)                     # header + 2 index bytes
        c_files, body = struct.unpack("<Hi", first[:6])
        if c_files < 0 or c_files > 1_000_000:
            raise MixError("implausible encrypted file count")
        cb_index = c_files * 12
        cb_f = (cb_index + 5) & ~7                     # index padded to 8 bytes
        rest = bf.decrypt(f.read(cb_f))
        index = first[6:8] + rest[:cb_index - 2]
        base = 92 + cb_f                               # 4 flags + 80 key + 8 first
        return c_files, index, base, body

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
        with self._open() as f:
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
                with self._open() as f:
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


# ── audio .idx/.bag (Westwood sound archive, XCC "GABA" format) ──────────────
_AUDIO_IDX_ID = b"GABA"


def parse_audio_idx(idx_path):
    """Parse an audio .idx → {NAME.UPPER: (offset, size, samplerate, flags,
    chunk_size)}. Names are stored without an extension."""
    with open(idx_path, "rb") as f:
        data = f.read()
    if data[:4] != _AUDIO_IDX_ID or len(data) < 12:
        raise MixError("not an audio .idx (missing GABA id)")
    _two, c_sounds = struct.unpack_from("<ii", data, 4)
    if 12 + 36 * c_sounds != len(data):
        raise MixError("audio .idx size mismatch")
    out = {}
    for i in range(c_sounds):
        fname, off, size, sr, fl, cs = struct.unpack_from("<16siiiii", data,
                                                           12 + i * 36)
        name = fname.split(b"\x00", 1)[0].decode("latin-1")
        if name:
            out[name.upper()] = (off, size, sr, fl, cs)
    return out


def read_audio_sample(bag_path, entry):
    """Read a sample's raw bytes from the .bag (offset/size from its idx entry).
    Note: raw (often IMA-ADPCM) — not a decoded .wav."""
    off, size = entry[0], entry[1]
    with open(bag_path, "rb") as f:
        f.seek(off)
        return f.read(size)


AUDIO_NAME_MAX = 16             # the idx's fixed <16s name field


def write_audio_idx(idx_path, entries, two=2):
    """Write an audio .idx from {NAME: (offset, size, samplerate, flags,
    chunk_size)}. `two` is the second header int, preserved from the source idx
    when rewriting one (stock files carry 2)."""
    out = bytearray(_AUDIO_IDX_ID + struct.pack("<ii", two, len(entries)))
    for name, (off, size, sr, fl, cs) in entries.items():
        raw = name.upper().encode("latin-1")
        if len(raw) > AUDIO_NAME_MAX:
            raise MixError(f"sample name {name!r} exceeds {AUDIO_NAME_MAX} bytes")
        out += struct.pack("<16siiiii", raw, off, size, sr, fl, cs)
    with open(idx_path, "wb") as f:
        f.write(bytes(out))


def inject_audio_samples(bag_path, idx_path, samples):
    """Add samples to an audio .bag/.idx pair, creating the pair if absent.

    `samples` is [(name, raw_bytes, sample_rate, flags, chunk_size)] where
    raw_bytes are in the bag's own encoding (Westwood IMA-ADPCM) — this writes
    bytes through untouched and does NOT encode, so callers must pass data that
    came out of a bag in the first place.

    New bytes are APPENDED and only the .idx is rewritten: existing samples
    never move, so every offset already in the idx stays valid. A sample that
    replaces one of the same name is appended too and its idx entry repointed,
    which orphans the old bytes inside the bag — harmless (nothing indexes
    them) and far safer than rewriting a multi-megabyte archive in place.

    Returns (added, replaced).
    """
    for name, _raw, _sr, _fl, _cs in samples:
        if len(name.upper().encode("latin-1")) > AUDIO_NAME_MAX:
            raise MixError(f"sample name {name!r} exceeds {AUDIO_NAME_MAX} bytes")

    entries, two = {}, 2
    if os.path.isfile(idx_path):
        try:
            with open(idx_path, "rb") as f:
                head = f.read(12)
            if len(head) >= 8 and head[:4] == _AUDIO_IDX_ID:
                two = struct.unpack_from("<i", head, 4)[0]
        except OSError:
            pass
        entries = dict(parse_audio_idx(idx_path))

    off = os.path.getsize(bag_path) if os.path.isfile(bag_path) else 0
    added = replaced = 0
    with open(bag_path, "ab" if off else "wb") as f:
        for name, raw, sr, fl, cs in samples:
            key = name.upper()
            if key in entries:
                replaced += 1
            else:
                added += 1
            f.write(raw)
            entries[key] = (off, len(raw), sr, fl, cs)
            off += len(raw)
    write_audio_idx(idx_path, entries, two)
    return added, replaced


# ── media cataloguing + transfer ────────────────────────────────────────────
_MEDIA_EXTS = {".vxl", ".hva", ".shp", ".wav", ".pal", ".pcx", ".aud",
               ".tem", ".sno", ".urb", ".des", ".lun"}


def build_media_catalog(folders, skip_vanilla=True):
    """Index media files available across a mod's loose files and its mixes.

    `folders` is a dir or list of dirs. Returns a dict keyed by BOTH the uppercased
    filename (when known) AND the file id (always), so files inside mixes that lack
    a name database are still findable by hashing the requested name. A location is
    ("loose", path) or ("mix", mixpath, id). Loose files win over mix copies;
    earlier folders win over later. Stock + community-framework mixes (vanilla,
    CnCNet, Ares) are skipped by default — only the mod's own mixes are indexed."""
    if isinstance(folders, str):
        folders = [folders]
    catalog = {}
    mixes = []
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        names = {f.lower(): f for f in os.listdir(folder)}
        for fn in sorted(os.listdir(folder)):
            p = os.path.join(folder, fn)
            if not os.path.isfile(p):
                continue
            low = fn.lower()
            ext = os.path.splitext(fn)[1].lower()
            if ext in _MEDIA_EXTS:
                loc = ("loose", p)
                catalog.setdefault(fn.upper(), loc)
                catalog.setdefault(mix_id(fn), loc)
            elif ext == ".mix" and not (skip_vanilla and is_stock_mix(fn)):
                mixes.append(p)
            elif ext == ".idx" and (low[:-4] + ".bag") in names:
                bag = os.path.join(folder, names[low[:-4] + ".bag"])
                try:
                    samples = parse_audio_idx(p)
                except (OSError, MixError):
                    continue
                for sname, entry in samples.items():
                    loc = ("bag", bag, entry)
                    catalog.setdefault(sname, loc)            # name (no ext)
                    catalog.setdefault(sname + ".WAV", loc)   # and the .WAV form
    for mp in mixes:                    # loose already populated → setdefault skips
        try:
            mf = MixFile(mp)
        except MixError:
            continue
        for e in mf.entries:
            loc = ("mix", mp, e.id)
            catalog.setdefault(e.id, loc)          # by id — works without a name DB
            if e.name:
                catalog.setdefault(e.name.upper(), loc)
    return catalog


def resolve_media(catalog, names):
    """Read the bytes for each requested filename from the catalog. Returns
    (found {name: bytes}, missing [names]). Groups mix reads so each mix opens
    once."""
    found, missing, by_mix = {}, [], {}
    for n in names:
        loc = catalog.get(n.upper()) or catalog.get(mix_id(n))
        if loc is None:
            missing.append(n)
        elif loc[0] == "loose":
            with open(loc[1], "rb") as f:
                found[n] = f.read()
        elif loc[0] == "bag":
            found[n] = read_audio_sample(loc[1], loc[2])
        else:
            by_mix.setdefault(loc[1], []).append((n, loc[2]))
    for mixpath, items in by_mix.items():
        mf = MixFile(mixpath)
        for n, fid in items:
            found[n] = mf.read_id(fid)
    return found, missing


def pack_media_mixes(files, dest_folder, base_name, per_mix_limit=0,
                     start_index=0):
    """Write `files` ({name: bytes}) into one or more mixes in dest_folder.
    per_mix_limit>0 splits into base00.mix, base01.mix, … of at most that many
    files each; otherwise a single base.mix. Returns the written mix paths."""
    items = list(files.items())
    if not items:
        return []
    limit = per_mix_limit if per_mix_limit and per_mix_limit > 0 else len(items)
    chunks = [items[i:i + limit] for i in range(0, len(items), limit)]
    base = base_name[:-4] if base_name.lower().endswith(".mix") else base_name
    written = []
    for i, chunk in enumerate(chunks):
        name = (f"{base}.mix" if len(chunks) == 1 and not per_mix_limit
                else f"{base}{start_index + i:02d}.mix")
        path = os.path.join(dest_folder, name)
        write_mix(path, dict(chunk))
        written.append(path)
    return written


# (folder, filename, mix-signature) -> bytes|None. A deep mix search costs
# 15-20s on a real game folder, and the porter needs the same file on Analyze
# and again on Apply.
_INI_FROM_MIX_CACHE = {}


def _mix_signature(folder):
    sig = []
    try:
        for fn in sorted(os.listdir(folder)):
            if fn.lower().endswith(".mix"):
                st = os.stat(os.path.join(folder, fn))
                sig.append((fn.lower(), st.st_size, int(st.st_mtime)))
    except OSError:
        pass
    return tuple(sig)


def load_ini_text(folder, names, search_mixes=True, max_depth=4):
    """Find an INI for a mod folder: loose first, then inside its mixes.

    Returns (origin, path, text): origin is "loose" (path is the file),
    "mix" (path is None — the text was extracted from one of the folder's
    mixes) or None (not found anywhere; text is None).

    Looking only for a LOOSE file is not enough: a stock game install keeps
    soundmd.ini inside ra2md.mix, so a porter that checked for a loose sibling
    concluded there was no sound file and never attempted sound at all.
    """
    if not folder:
        return None, None, None
    try:
        listing = {f.lower(): f for f in os.listdir(folder)}
    except OSError:
        listing = {}
    for nm in names:
        real = listing.get(nm.lower())
        if real:
            path = os.path.join(folder, real)
            try:
                with open(path, "rb") as fh:
                    return "loose", path, fh.read().decode("latin-1")
            except OSError:
                pass
    if not search_mixes:
        return None, None, None
    sig = _mix_signature(folder)
    for nm in names:
        key = (os.path.realpath(folder), nm.lower(), sig)
        if key not in _INI_FROM_MIX_CACHE:
            try:
                _INI_FROM_MIX_CACHE[key] = find_in_mixes(
                    [folder], nm, max_depth=max_depth)
            except Exception:
                _INI_FROM_MIX_CACHE[key] = None
        data = _INI_FROM_MIX_CACHE[key]
        if data:
            return "mix", None, data.decode("latin-1")
    return None, None, None


def find_in_mixes(folders, filename, max_depth=3,
                  priority=("ra2md.mix", "ra2.mix"),
                  max_sub_size=64 * 1024 * 1024):
    """Deep-search the loose mixes in `folders` (including nested / encrypted
    sub-mixes) for `filename` by its mix id. Returns the file's bytes, or None.

    Only entries that themselves parse as a complete mix (`is_complete`) are
    descended into, so leaf assets aren't misread; entries outside
    [84, max_sub_size] bytes are skipped (avoids reading huge movie blobs). Mixes
    whose basename is in `priority` are searched first — e.g. voxels.vpl lives in
    local.mix inside ra2(md).mix."""
    if isinstance(folders, str):
        folders = [folders]
    target = mix_id(filename)
    prio = [p.lower() for p in priority]
    paths = []
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        for fn in os.listdir(folder):
            p = os.path.join(folder, fn)
            if fn.lower().endswith(".mix") and os.path.isfile(p):
                paths.append(p)
    paths.sort(key=lambda p: (prio.index(os.path.basename(p).lower())
                              if os.path.basename(p).lower() in prio else len(prio),
                              os.path.basename(p).lower()))

    def search(mf, depth):
        if target in mf.by_id:
            try:
                return mf.read_id(target)
            except (MixError, KeyError, OSError):
                return None
        if depth >= max_depth:
            return None
        for e in mf.entries:
            if e.size < 84 or e.size > max_sub_size:
                continue
            try:
                blob = mf.read_id(e.id)
                sub = MixFile.from_bytes(blob)
            except (MixError, KeyError, OSError):
                continue
            if not sub.is_complete:
                continue
            r = search(sub, depth + 1)
            if r is not None:
                return r
        return None

    for mp in paths:
        try:
            mf = MixFile(mp)
        except (MixError, OSError):
            continue
        r = search(mf, 0)
        if r is not None:
            return r
    return None


# ── vanilla asset index (recursive) + missing-file classification ────────────
import json as _json   # noqa: E402  (kept local to this feature)


def _peek_is_nested_candidate(f, entry):
    """Cheap 10-byte pre-filter: could this entry be a nested mix worth fully
    parsing? Plain mixes are size-checked (rejects leaves cheaply); encrypted ones
    can't be size-checked so they're candidates (validated by the full parse)."""
    f.seek(entry.offset)
    head = f.read(10)
    if len(head) < 10:
        return False
    c0 = struct.unpack("<H", head[:2])[0]
    if c0 != 0:                                    # old format
        body = struct.unpack("<i", head[2:6])[0]
        core = 6 + c0 * 12 + body
        return 0 < c0 < 100000 and 0 <= entry.size - core <= 24
    flags = struct.unpack("<I", head[:4])[0]
    if flags & MIX_ENCRYPTED:                      # nested sub-mixes CAN be encrypted
        return 0x20000 <= flags <= 0x30000 and entry.size > 92
    c_files = struct.unpack("<H", head[4:6])[0]
    body = struct.unpack("<i", head[6:10])[0]
    core = 10 + c_files * 12 + body
    return 0 < c_files < 100000 and 0 <= entry.size - core <= 24


def _walk_mix(mf, ids, names, depth):
    with mf._open() as f:
        for e in mf.entries:
            if e.name:
                names.setdefault(e.id, e.name)
            if depth < 6 and _peek_is_nested_candidate(f, e):
                try:
                    sub = MixFile.from_bytes(mf.read_id(e.id))
                    if sub.is_complete:            # a real nested mix, not a leaf
                        _walk_mix(sub, ids, names, depth + 1)
                        continue
                except MixError:
                    pass
            ids.add(e.id)


def collect_asset_ids(source, ids=None, names=None):
    """Walk a mix (path or bytes), collecting leaf file ids (and any known names),
    recursing into nested mixes — master mixes contain sub-mixes, some encrypted.
    A nested entry is only descended into if it fully parses as a complete mix.
    Returns (ids:set[int], names:dict[int,str])."""
    ids = set() if ids is None else ids
    names = {} if names is None else names
    try:
        mf = MixFile(source)
    except MixError:
        return ids, names
    _walk_mix(mf, ids, names, 0)
    return ids, names


def build_vanilla_asset_ids(dirs, progress=None):
    """Collect the ids (and names) of every asset in vanilla install dir(s):
    loose files plus everything inside their mixes (recursively). Returns
    (ids:set, names:dict)."""
    if isinstance(dirs, str):
        dirs = [dirs]
    ids, names = set(), {}
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            p = os.path.join(d, fn)
            if not os.path.isfile(p):
                continue
            low = fn.lower()
            if low.endswith(".mix"):
                if low.startswith(("movies", "movmd")):
                    continue       # video only — no referenced unit art/sound
                if progress:
                    progress(fn)
                collect_asset_ids(p, ids, names)
            else:
                fid = mix_id(fn)
                ids.add(fid)
                names.setdefault(fid, fn)
    return ids, names


def _cache_path():
    base = os.path.join(os.path.expanduser("~"), ".yrbalancetool")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "vanilla_assets.json")


def _dirs_signature(dirs):
    sig = []
    for d in dirs:
        if d and os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.lower().endswith(".mix"):
                    p = os.path.join(d, fn)
                    try:
                        st = os.stat(p)
                        sig.append(f"{fn}:{st.st_size}:{int(st.st_mtime)}")
                    except OSError:
                        pass
    return sig


def vanilla_asset_ids(dirs, progress=None):
    """Cached build_vanilla_asset_ids: reuses a JSON cache keyed by the dirs' mix
    signature, rebuilding only when the vanilla mixes change. Returns a set of ids.
    Returns an empty set if no valid dirs are given."""
    if isinstance(dirs, str):
        dirs = [dirs]
    dirs = [d for d in dirs if d and os.path.isdir(d)]
    if not dirs:
        return set()
    sig = _dirs_signature(dirs)
    path = _cache_path()
    try:
        with open(path, encoding="utf-8") as f:
            cache = _json.load(f)
        if cache.get("sig") == sig and cache.get("dirs") == dirs:
            return set(cache["ids"])
    except (OSError, ValueError, KeyError):
        pass
    ids, _names = build_vanilla_asset_ids(dirs, progress)
    try:
        with open(path, "w", encoding="utf-8") as f:
            _json.dump({"dirs": dirs, "sig": sig, "ids": sorted(ids)}, f)
    except OSError:
        pass
    return ids


def classify_missing(names, vanilla_ids):
    """Split a list of not-found filenames into (satisfied_by_vanilla, truly_missing)
    using a set of vanilla asset ids. A reference the mod doesn't ship but vanilla
    provides is fine (the unit reuses a stock asset); anything else is worth a
    warning."""
    vanilla, truly = [], []
    for n in names:
        (vanilla if mix_id(n) in vanilla_ids else truly).append(n)
    return vanilla, truly
