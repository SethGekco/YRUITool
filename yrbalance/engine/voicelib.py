"""
Voice library: lift a unit's whole *voice set* out of a mod into a reusable
sidecar store, and (V3) push it back into any mod.

A voice is three layers of indirection, and a bundle is only transplantable if it
carries all three:

    rulesmd.ini  [GI] VoiceSelect=GIYessir       the tag on the unit
    soundmd.ini  [GIYessir] Sounds=giyes1 giyes2 the sound *entry*
    samples      giyes1 -> audio.bag / loose .wav

So a VoiceSet = the unit's voice tags + the verbatim soundmd sections they name +
the sample payloads those sections list.

Samples are stored as their ORIGINAL bytes, not decoded WAV. A bag-resident
sample is raw Westwood IMA-ADPCM, and we keep it that way along with the three
idx fields needed to put it back (sample_rate, flags, chunk_size): re-injecting
into a mod's audio.bag is then a byte copy plus an idx rebuild, lossless and
needing no ADPCM *encoder* (we only have decoders). Preview decodes on the fly
via audio.bag_sample_to_wav instead.

Storage is a sidecar under ~/.yrbalancetool/ — never the mod's INI or its bag:
  voicelib.json          the catalogue ({sets, collections})
  voices/<set_id>/       one file per sample, original bytes
"""

import json
import os
import re
from dataclasses import dataclass, field, asdict

from .ini_model import get_val
from .portmod import _clean, _ordered_keys

# Voice/sound reference tags on a TechnoType. Deliberately a SUPERSET of
# portmod._SOUND_REF_TAGS: the porter follows 8 of these, but a voice set that
# silently dropped VoiceFeedback or VoiceSpecialAttack would be visibly
# incomplete (main_window.VOICE_RESTORE_SCRIPT already uses both). The porter's
# own tuple is left alone — widening it would change port output.
_VOICE_TAGS = (
    "VoiceSelect", "VoiceMove", "VoiceAttack", "VoiceFeedback",
    "VoiceSpecialAttack", "VoiceCapture", "VoiceEnter", "VoiceDeploy",
    "VoiceHarvest", "VoiceCrashing",
    "DieSound", "Report", "CrushSound", "EnterWater", "LeaveWater",
    "ChronoInSound", "ChronoOutSound", "TurretRotateSound",
    "AuxSound1", "AuxSound2",
)

# A sample name lives in the audio .idx's fixed <16s field, so it must fit 16
# bytes INCLUDING nothing else — names longer than this cannot be written back.
IDX_NAME_MAX = 16


# ── data model ──────────────────────────────────────────────────────────────
@dataclass
class VoiceSample:
    """One audio sample referenced by a soundmd entry.

    `provenance` is "bag" (raw IMA-ADPCM out of audio.bag, carries the idx
    fields) or "loose" (a real file on disk / in a mix, bytes as-is). Only bag
    samples can be re-injected into a bag without an encoder.
    """
    name: str                       # bare sample name, uppercase, no extension
    provenance: str = ""            # bag | loose | ""  ("" = not stored yet)
    sample_rate: int = 0
    flags: int = 0
    chunk_size: int = 0
    size: int = 0                   # byte length of the stored payload
    file: str = ""                  # filename under voices/<set_id>/

    def fits_idx(self):
        """Whether this name can be written into an audio .idx name field."""
        return idx_name_ok(self.name)


@dataclass
class VoiceEntry:
    """A soundmd.ini section, captured verbatim."""
    name: str
    keys: list = field(default_factory=list)       # [[k, v]] in original order


@dataclass
class VoiceSet:
    name: str                       # user label
    set_id: str = ""                # slug — names the voices/<set_id>/ dir
    source: str = ""                # originating mod label
    unit: str = ""                  # originating unit id
    tags: list = field(default_factory=list)       # [[tag, sound_entry_name]]
    entries: list = field(default_factory=list)    # [VoiceEntry]
    samples: list = field(default_factory=list)    # [VoiceSample]
    notes: list = field(default_factory=list)      # extraction-time (auto) notes
    user_notes: str = ""
    credits: str = ""
    bookmarked: bool = False

    def key(self):
        return self.name

    def to_json(self):
        return asdict(self)

    @staticmethod
    def from_json(d):
        return VoiceSet(
            name=d.get("name", ""), set_id=d.get("set_id", ""),
            source=d.get("source", ""), unit=d.get("unit", ""),
            tags=[list(t) for t in d.get("tags", [])],
            entries=[VoiceEntry(**e) for e in d.get("entries", [])],
            samples=[VoiceSample(**s) for s in d.get("samples", [])],
            notes=list(d.get("notes", [])),
            user_notes=d.get("user_notes", ""), credits=d.get("credits", ""),
            bookmarked=bool(d.get("bookmarked", False)))


@dataclass
class VoiceCollection:
    """A named, ordered grouping of voice sets, referenced by name."""
    name: str
    items: list = field(default_factory=list)      # [set_name]


# ── paths ───────────────────────────────────────────────────────────────────
def voicelib_path():
    return os.path.join(os.path.expanduser("~"), ".yrbalancetool",
                        "voicelib.json")


def samples_dir(set_id):
    return os.path.join(os.path.expanduser("~"), ".yrbalancetool", "voices",
                        set_id)


def _slug(label):
    s = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")
    return s or "voices"


def _unique_id(base, taken):
    cand, n = base, 1
    while cand in taken:
        n += 1
        cand = f"{base}-{n}"
    return cand


# ── sample-name helpers ─────────────────────────────────────────────────────
def idx_name_ok(name):
    """Whether `name` fits the audio .idx fixed-width name field."""
    try:
        return 0 < len(name.encode("latin-1")) <= IDX_NAME_MAX
    except (UnicodeEncodeError, AttributeError):
        return False


def parse_sound_tokens(value):
    """Sample names listed by a soundmd `Sounds=` value.

    The value is whitespace-separated (sometimes comma-separated), and a token
    may carry a leading '$' marking a bag sample id. Returns BARE uppercase
    names with no extension and no '$', de-duplicated, in order — the form the
    .idx stores them under. (portmod._resolve_sound returns the same tokens with
    '.WAV' appended, because it feeds a media catalog; the catalog registers bag
    samples under both forms.)
    """
    out, seen = [], set()
    for tok in _clean(value or "").replace(",", " ").split():
        nm = tok.lstrip("$").upper()
        if nm.endswith(".WAV"):
            nm = nm[:-4]
        if nm and nm not in seen:
            seen.add(nm)
            out.append(nm)
    return out


def entry_sample_names(entry):
    """Sample names a VoiceEntry's Sounds= key lists."""
    out, seen = [], set()
    for k, v in entry.keys:
        if k.lower() == "sounds":
            for nm in parse_sound_tokens(v):
                if nm not in seen:
                    seen.add(nm)
                    out.append(nm)
    return out


# ── extraction ──────────────────────────────────────────────────────────────
def voice_tags(unit, src_map):
    """[(tag, sound_entry_name)] for every voice tag set on `unit`."""
    out = []
    for tag in _VOICE_TAGS:
        val = _clean(get_val(src_map, unit, tag))
        if val and val.lower() != "none":
            out.append((tag, val))
    return out


def extract_voice_set(unit, src_map, src_sound_map, source="", name=None,
                      set_id=None):
    """Build a VoiceSet for `unit` from a rules + soundmd section map pair.

    Metadata only — no bytes are read and nothing is written. Call
    store_samples() with a media catalog to attach the payloads.
    """
    src_sound_map = src_sound_map or {}
    label = name or f"{unit} voices"
    vset = VoiceSet(name=label, set_id=set_id or _slug(label),
                    source=source, unit=unit)

    seen_entries, seen_samples = set(), set()
    for tag, ref in voice_tags(unit, src_map):
        vset.tags.append([tag, ref])
        if ref.lower() not in src_sound_map:
            vset.notes.append(
                f"{tag}={ref}: no [{ref}] section in the source soundmd — "
                "tag captured, sound entry missing")
            continue
        if ref.lower() in seen_entries:
            continue                       # two tags naming the same entry
        seen_entries.add(ref.lower())
        entry = VoiceEntry(name=ref,
                           keys=[[k, v] for k, v in
                                 _ordered_keys(src_sound_map, ref)])
        vset.entries.append(entry)
        for nm in entry_sample_names(entry):
            if nm in seen_samples:
                continue
            seen_samples.add(nm)
            vset.samples.append(VoiceSample(name=nm))
            if not idx_name_ok(nm):
                vset.notes.append(
                    f"sample {nm!r} is longer than {IDX_NAME_MAX} bytes — it "
                    "cannot be written into an audio .idx")

    if not vset.tags:
        vset.notes.append(f"{unit} sets no voice tags")
    elif not vset.entries:
        vset.notes.append("no sound entries resolved — is this the right "
                          "soundmd for the mod?")
    return vset


def store_samples(vset, catalog, path=None):
    """Copy each sample's ORIGINAL bytes into voices/<set_id>/ and fill in its
    provenance + idx fields from the media catalog.

    Returns (stored, missing) — counts of samples written and a list of names
    the catalog could not locate. Bag samples keep their raw ADPCM bytes; loose
    files are copied as-is.
    """
    from .mixlib import read_audio_sample, mix_id, MixError, MixFile

    dest = samples_dir(vset.set_id)
    os.makedirs(dest, exist_ok=True)
    stored, missing = 0, []
    for s in vset.samples:
        loc = (catalog.get(s.name) or catalog.get(s.name + ".WAV")
               or catalog.get(mix_id(s.name + ".wav")))
        if loc is None:
            missing.append(s.name)
            continue
        try:
            if loc[0] == "bag":
                raw = read_audio_sample(loc[1], loc[2])
                _off, _size, sr, fl, cs = loc[2]
                s.provenance, s.sample_rate, s.flags, s.chunk_size = (
                    "bag", sr, fl, cs)
            elif loc[0] == "loose":
                with open(loc[1], "rb") as fh:
                    raw = fh.read()
                s.provenance = "loose"
            else:                                   # ("mix", mixpath, id)
                raw = MixFile(loc[1]).read_id(loc[2])   # by id, not by name
                s.provenance = "loose"
        except (OSError, MixError, KeyError) as exc:
            missing.append(s.name)
            vset.notes.append(f"sample {s.name}: {exc}")
            continue
        s.file = s.name + (".adpcm" if s.provenance == "bag" else ".wav")
        s.size = len(raw)
        with open(os.path.join(dest, s.file), "wb") as fh:
            fh.write(raw)
        stored += 1

    if missing:
        vset.notes.append("samples not found in the mod's media: "
                          + ", ".join(missing))
    return stored, missing


def sample_bytes(vset, sample):
    """Stored original bytes for one sample, or None if it was never stored."""
    if not sample.file:
        return None
    p = os.path.join(samples_dir(vset.set_id), sample.file)
    try:
        with open(p, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def sample_wav(vset, sample):
    """Decoded, playable WAV bytes for one stored sample, or None.

    A loose sample is already a real file and is returned untouched; a bag
    sample is raw IMA-ADPCM and gets decoded + RIFF-wrapped here.
    """
    raw = sample_bytes(vset, sample)
    if raw is None:
        return None
    if sample.provenance != "bag":
        return raw
    from .audio import decode_bag_sample, to_wav
    s = decode_bag_sample(raw, sample.sample_rate, sample.flags,
                          sample.chunk_size)
    return to_wav(s.pcm, s.sample_rate, s.channels, s.bits)


# ── persistence ─────────────────────────────────────────────────────────────
def _load_doc(path=None):
    path = path or voicelib_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (FileNotFoundError, ValueError):
        return {"sets": [], "collections": []}
    raw.setdefault("sets", [])
    raw.setdefault("collections", [])
    return raw


def _save_doc(doc, path=None):
    path = path or voicelib_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    os.replace(tmp, path)


def load_sets(path=None):
    return [VoiceSet.from_json(s) for s in _load_doc(path).get("sets", [])]


def save_sets(sets, path=None):
    """Persist the set list, preserving any collections already on disk."""
    doc = _load_doc(path)
    doc["sets"] = [s.to_json() for s in sets]
    _save_doc(doc, path)


def add_set(vset, path=None):
    """Add (or replace by name) a voice set. Assigns a unique set_id first so
    two sets never share a samples dir."""
    sets = load_sets(path)
    taken = {s.set_id for s in sets if s.name != vset.name}
    if not vset.set_id or vset.set_id in taken:
        vset.set_id = _unique_id(_slug(vset.set_id or vset.name), taken)
    sets = [s for s in sets if s.name != vset.name]
    sets.append(vset)
    save_sets(sets, path)
    return sets


def remove_set(name, path=None):
    """Drop a set, its samples dir, and its collection memberships."""
    import shutil
    gone = [s for s in load_sets(path) if s.name == name]
    sets = [s for s in load_sets(path) if s.name != name]
    save_sets(sets, path)
    for s in gone:
        if s.set_id:
            shutil.rmtree(samples_dir(s.set_id), ignore_errors=True)
    cols = load_voice_collections(path)
    for c in cols:
        c.items = [i for i in c.items if i != name]
    save_voice_collections(cols, path)
    return sets


def rename_set(name, new_name, path=None):
    """Rename a set, keeping collection memberships pointing at it."""
    new_name = (new_name or "").strip()
    sets = load_sets(path)
    if not new_name or any(s.name == new_name for s in sets):
        return sets                                 # empty or taken — no-op
    for s in sets:
        if s.name == name:
            s.name = new_name
    save_sets(sets, path)
    cols = load_voice_collections(path)
    for c in cols:
        c.items = [new_name if i == name else i for i in c.items]
    save_voice_collections(cols, path)
    return sets


def set_meta(name, user_notes=None, credits=None, path=None):
    sets = load_sets(path)
    for s in sets:
        if s.name == name:
            if user_notes is not None:
                s.user_notes = user_notes
            if credits is not None:
                s.credits = credits
    save_sets(sets, path)
    return sets


def set_bookmark(name, on, path=None):
    sets = load_sets(path)
    for s in sets:
        if s.name == name:
            s.bookmarked = bool(on)
    save_sets(sets, path)
    return sets


# ── collections ─────────────────────────────────────────────────────────────
def load_voice_collections(path=None):
    return [VoiceCollection(name=c.get("name", ""),
                            items=list(c.get("items", [])))
            for c in _load_doc(path).get("collections", [])]


def save_voice_collections(cols, path=None):
    doc = _load_doc(path)
    doc["collections"] = [asdict(c) for c in cols]
    _save_doc(doc, path)


def create_voice_collection(name, path=None):
    name = (name or "").strip()
    cols = load_voice_collections(path)
    if not name or any(c.name == name for c in cols):
        return cols
    cols.append(VoiceCollection(name=name))
    save_voice_collections(cols, path)
    return cols


def delete_voice_collection(name, path=None):
    cols = [c for c in load_voice_collections(path) if c.name != name]
    save_voice_collections(cols, path)
    return cols


def add_to_voice_collection(name, set_name, path=None):
    cols = load_voice_collections(path)
    for c in cols:
        if c.name == name and set_name not in c.items:
            c.items.append(set_name)
    save_voice_collections(cols, path)
    return cols


def remove_from_voice_collection(name, set_name, path=None):
    cols = load_voice_collections(path)
    for c in cols:
        if c.name == name:
            c.items = [i for i in c.items if i != set_name]
    save_voice_collections(cols, path)
    return cols
