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
import zipfile
from dataclasses import dataclass, field, asdict

from .ini_model import get_val
from .portmod import _clean, _ordered_keys
from .sections import (append_raw_section, set_key_in_appended_section,
                       make_safe_clone_name)

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


# ── applying a set back into a mod ──────────────────────────────────────────
@dataclass
class VoiceItem:
    """One planned change. `kind` is entry | sample | tag.

    entry  : add | reuse | rename      (reuse = keep the target's section)
    sample : add | skip | rename | loose | absent
    tag    : set | skip
    """
    kind: str
    name: str
    action: str
    value: str = ""                 # tag: the entry name it will point at
    new_name: str = ""              # rename: the minted name actually written
    conflict: bool = False          # the target already had this, and differs
    detail: str = ""


@dataclass
class VoicePlan:
    unit: str = ""
    items: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    entry_remap: dict = field(default_factory=dict)   # src_lower -> final name
    sample_remap: dict = field(default_factory=dict)  # SRC -> FINAL

    def of(self, kind):
        return [i for i in self.items if i.kind == kind]

    def conflicts(self):
        return [i for i in self.items if i.conflict]


def _keys_equal(a, b):
    """Whether two [(k, v)] bodies are the same ignoring key case + whitespace."""
    def norm(kv):
        return sorted((k.strip().lower(), _clean(v).strip()) for k, v in kv)
    return norm(a) == norm(b)


def _mint_sample(base, taken):
    """A free sample name near `base` that still fits the idx name field."""
    if base not in taken and idx_name_ok(base):
        return base
    n = 2
    while True:
        tail = str(n)
        stem = base[:IDX_NAME_MAX - len(tail)]
        cand = stem + tail
        if cand not in taken:
            return cand
        n += 1


def _remap_sounds(value, sample_remap):
    """Rewrite a `Sounds=` value through a sample rename map, keeping each
    token's '$' marker. Returns the value unchanged when nothing was renamed."""
    toks = _clean(value or "").replace(",", " ").split()
    if not any(t.lstrip("$").upper().removesuffix(".WAV") in sample_remap
               for t in toks):
        return value                            # untouched — keep formatting
    out = []
    for t in toks:
        dollar = t.startswith("$")
        bare = t.lstrip("$").upper().removesuffix(".WAV")
        out.append(("$" if dollar else "") + sample_remap.get(bare, bare))
    return ",".join(out)


def build_voice_plan(vset, unit, tgt_rules_map, tgt_sound_map,
                     tgt_catalog=None, choices=None):
    """Plan applying `vset` onto `unit` in a target mod. Pure — reads nothing.

    `choices` resolves conflicts, keyed ("entry"|"sample", source_name):
      entry  "reuse"    keep the target's existing section (default)
             "keepboth" append ours under a minted name and point the tag at it
      sample "skip"     keep the target's existing sample (default)
             "keepboth" inject ours under a minted name and rewrite Sounds=
    """
    choices = choices or {}
    tgt_sound_map = tgt_sound_map or {}
    plan = VoicePlan(unit=unit)

    if unit.lower() not in (tgt_rules_map or {}):
        plan.warnings.append(
            f"[{unit}] does not exist in the target rulesmd — its voice tags "
            "would be written into a section that isn't there")

    # ── sound entries ───────────────────────────────────────────────────────
    taken = dict(tgt_sound_map)                 # grows as we mint names
    for e in vset.entries:
        if e.name.lower() not in tgt_sound_map:
            plan.entry_remap[e.name.lower()] = e.name
            plan.items.append(VoiceItem("entry", e.name, "add"))
            taken[e.name.lower()] = True
            continue
        if _keys_equal(_ordered_keys(tgt_sound_map, e.name), e.keys):
            plan.entry_remap[e.name.lower()] = e.name
            plan.items.append(VoiceItem(
                "entry", e.name, "reuse",
                detail="identical section already in the target"))
            continue
        if choices.get(("entry", e.name)) == "keepboth":
            new = make_safe_clone_name(e.name, "V", taken)
            taken[new.lower()] = True
            plan.entry_remap[e.name.lower()] = new
            plan.items.append(VoiceItem("entry", e.name, "rename",
                                        new_name=new, conflict=True,
                                        detail="target had a different section"))
        else:
            plan.entry_remap[e.name.lower()] = e.name
            plan.items.append(VoiceItem(
                "entry", e.name, "reuse", conflict=True,
                detail="target has a DIFFERENT section of this name — its "
                       "version is kept"))

    # ── samples ─────────────────────────────────────────────────────────────
    cat = tgt_catalog or {}
    taken_s = {k for k in cat if isinstance(k, str)}
    for s in vset.samples:
        if not s.file:
            plan.items.append(VoiceItem(
                "sample", s.name, "absent",
                detail="never stored — not found when the set was saved"))
            continue
        if s.provenance != "bag":
            plan.items.append(VoiceItem(
                "sample", s.name, "loose",
                detail="not bag-encoded; written as a loose file (injecting it "
                       "would need an ADPCM encoder)"))
            continue
        present = cat.get(s.name) or cat.get(s.name + ".WAV")
        if present is None:
            plan.items.append(VoiceItem("sample", s.name, "add"))
            continue
        if choices.get(("sample", s.name)) == "keepboth":
            new = _mint_sample(s.name, taken_s)
            taken_s.add(new)
            plan.sample_remap[s.name] = new
            plan.items.append(VoiceItem("sample", s.name, "rename",
                                        new_name=new, conflict=True,
                                        detail="target already has this sample"))
        else:
            plan.items.append(VoiceItem(
                "sample", s.name, "skip", conflict=True,
                detail="target already has a sample of this name — its "
                       "version is kept"))

    # ── tags (last: they point at whatever the entries resolved to) ─────────
    for tag, ref in vset.tags:
        final = plan.entry_remap.get(ref.lower())
        if final is None:
            plan.items.append(VoiceItem(
                "tag", tag, "skip", value=ref,
                detail=f"no [{ref}] section was captured — writing this tag "
                       "would point at nothing"))
            continue
        plan.items.append(VoiceItem("tag", tag, "set", value=final))

    if not any(i.kind == "tag" and i.action == "set" for i in plan.items):
        plan.warnings.append("nothing to apply — no voice tag resolved")
    return plan


def plan_to_text(vset, plan):
    """Human preview of a VoicePlan — what lands in which file."""
    L = [f"Apply voice set “{vset.name}” → [{plan.unit}]", ""]
    for w in plan.warnings:
        L.append(f"! {w}")
    if plan.warnings:
        L.append("")

    L.append("; ===== rulesmd.ini =====")
    L.append(f"[{plan.unit}]")
    for i in plan.of("tag"):
        if i.action == "set":
            L.append(f"{i.name}={i.value}")
        else:
            L.append(f"; {i.name}={i.value}  — SKIPPED: {i.detail}")
    L.append("")

    by_name = {e.name.lower(): e for e in vset.entries}
    L.append("; ===== soundmd.ini =====")
    for i in plan.of("entry"):
        if i.action == "reuse":
            L.append(f"; [{i.name}] — {i.detail}")
            continue
        e = by_name.get(i.name.lower())
        L.append(f"[{i.new_name or i.name}]"
                 + (f"   ; renamed from {i.name} — {i.detail}"
                    if i.action == "rename" else ""))
        for k, v in (e.keys if e else []):
            L.append(f"{k}={_remap_sounds(v, plan.sample_remap) if k.lower() == 'sounds' else v}")
        L.append("")

    L.append("; ===== samples =====")
    for i in plan.of("sample"):
        tail = f"  ; {i.detail}" if i.detail else ""
        L.append(f"; {i.name}"
                 + (f" → {i.new_name}" if i.new_name else "")
                 + f"  [{i.action}]{tail}")
    return "\n".join(L) + "\n"


def apply_voice_plan(lines, tgt_map, vset, plan, sound=None, bag=None,
                     folder=None):
    """Apply a VoicePlan. Mutates `lines`/`tgt_map` (rulesmd) and, when given,
    `sound` = (lines, section_map) for the target soundmd — both IN MEMORY, so
    the caller still owns backup-then-write.

    `bag` = (bag_path, idx_path) receives the injected samples and IS written
    here (a bag is not a text file we can stage). `folder` receives any loose
    samples. Returns a summary dict.
    """
    from .mixlib import inject_audio_samples

    summary = {"tags": 0, "entries": 0, "reused": 0, "samples": 0,
               "replaced": 0, "loose": 0, "skipped": 0, "errors": []}
    by_name = {e.name.lower(): e for e in vset.entries}

    # 1) soundmd sections first, so the tags point at something that exists.
    if sound is not None:
        slines, smap = sound
        ending = "\r\n" if slines and slines[-1].endswith("\r\n") else "\n"
        for i in plan.of("entry"):
            if i.action == "reuse":
                summary["reused"] += 1
                continue
            e = by_name.get(i.name.lower())
            if e is None:
                continue
            raw = [f"[{i.new_name or i.name}]"]
            for k, v in e.keys:
                raw.append(f"{k}="
                           + (_remap_sounds(v, plan.sample_remap)
                              if k.lower() == "sounds" else v))
            append_raw_section(slines, smap, i.new_name or i.name, raw, ending)
            summary["entries"] += 1

    # 2) the unit's voice tags.
    for i in plan.of("tag"):
        if i.action != "set":
            summary["skipped"] += 1
            continue
        set_key_in_appended_section(lines, tgt_map, plan.unit, i.name, i.value)
        summary["tags"] += 1

    # 3) samples — bag injections in one pass, loose files written beside them.
    inject, loose = [], []
    for i in plan.of("sample"):
        s = next((x for x in vset.samples if x.name == i.name), None)
        if s is None or i.action in ("skip", "absent"):
            summary["skipped"] += 1
            continue
        raw = sample_bytes(vset, s)
        if raw is None:
            summary["errors"].append(f"{i.name}: stored bytes missing")
            continue
        if i.action == "loose":
            loose.append((s.file or (i.name + ".wav"), raw))
        else:
            inject.append((i.new_name or i.name, raw, s.sample_rate, s.flags,
                           s.chunk_size))

    if inject and bag:
        try:
            added, replaced = inject_audio_samples(bag[0], bag[1], inject)
            summary["samples"], summary["replaced"] = added, replaced
        except Exception as exc:                 # a bad name, unwritable bag…
            summary["errors"].append(f"bag injection failed: "
                                     f"{type(exc).__name__}: {exc}")
    elif inject:
        summary["errors"].append(
            f"{len(inject)} samples could not be injected — no audio.bag given")

    for name, raw in loose:
        if not folder:
            summary["errors"].append(f"{name}: no target folder for loose files")
            continue
        try:
            with open(os.path.join(folder, name), "wb") as fh:
                fh.write(raw)
            summary["loose"] += 1
        except OSError as exc:
            summary["errors"].append(f"{name}: {exc}")
    return summary


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


# ── export / import a shareable set ─────────────────────────────────────────
VOICE_FILE_EXT = ".yrvoice"
_MANIFEST = "voiceset.json"


def _unique_name(base, taken):
    cand, n = base, 1
    while cand in taken:
        n += 1
        cand = f"{base} ({n})"
    return cand


def export_set(vset, path):
    """Write a self-contained, shareable voice set: a zip holding the manifest
    plus every stored sample's original bytes. Returns (path, exported_count)."""
    n = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(_MANIFEST, json.dumps(vset.to_json(), indent=1))
        for s in vset.samples:
            raw = sample_bytes(vset, s)
            if not s.file or raw is None:
                continue
            z.writestr("samples/" + os.path.basename(s.file), raw)
            n += 1
    return path, n


def import_set(path, name=None, lib_path=None):
    """Read a .yrvoice and add it to the library under a non-colliding name.

    Returns (vset, missing) where `missing` lists samples the archive did not
    actually carry. The file may have come from anyone, so member names are
    never trusted: we only read paths we build ourselves from the manifest, and
    a sample whose stored name isn't a plain basename is rejected rather than
    written (that is the zip path-traversal shape — "../../x").
    """
    with zipfile.ZipFile(path) as z:
        try:
            doc = json.loads(z.read(_MANIFEST).decode("utf-8"))
        except (KeyError, ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"not a voice set file: {exc}") from exc
        if not isinstance(doc, dict) or not doc.get("name"):
            raise ValueError("not a voice set file: manifest has no name")
        vset = VoiceSet.from_json(doc)
        vset.name = _unique_name(name or vset.name,
                                 {s.name for s in load_sets(lib_path)})
        vset.set_id = _unique_id(_slug(vset.name),
                                 {s.set_id for s in load_sets(lib_path)})

        members = set(z.namelist())
        dest = samples_dir(vset.set_id)
        os.makedirs(dest, exist_ok=True)
        missing = []
        for s in vset.samples:
            safe = os.path.basename(s.file or "")
            member = "samples/" + safe
            if not safe or safe != s.file or member not in members:
                missing.append(s.name)
                s.file = ""                     # nothing stored for this one
                continue
            raw = z.read(member)
            with open(os.path.join(dest, safe), "wb") as fh:
                fh.write(raw)
            s.size = len(raw)

    if missing:
        vset.notes.append("imported without samples: " + ", ".join(missing))
    add_set(vset, lib_path)
    return vset, missing


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
