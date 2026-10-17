"""
Library addons: extra Library tabs the user declares in plain English and Eva
authors for them.

An addon is a DECLARATIVE SPEC, never generated code. Eva fills in a small
fixed vocabulary — which INI sections to enumerate, which tags to read, how to
group them — and this module interprets it against an already-parsed rules
section map. That keeps a model-authored addon in the same safety class as the
modifier-script DSL and the JSON TagPlan Eva already emits: the app does the
doing, the model only describes.

Deliberately NOT exec/eval of model output. The tool writes to the user's mod
files, so running generated Python in-process would put arbitrary code a prompt
away from their data, to buy expressiveness that a tag-reading tab does not
need.

Everything in a spec arrives from a language model, so validate_spec() is the
real security boundary: every field is coerced to its declared type, enums are
clamped, section/tag names are checked against the INI identifier charset, and
every list is length-capped. A spec that cannot be made safe is rejected with
reasons rather than silently narrowed.

Specs live in ~/.yrbalancetool/addons.json — never in the mod.
"""

import json
import os
import re
from dataclasses import dataclass, field, asdict

from .ini_model import get_val, clean_value as _clean
from .typelists import parse_type_list

ADDON_VERSION = 1

# How an addon finds its rows.
SRC_TYPELIST = "typelist"       # enumerate an index section, e.g. [AttachmentTypes]
SRC_TECHNOS = "technos"         # walk the four TechnoType lists
SRC_SECTIONS = "sections"       # every section whose name matches a prefix
ALL_SOURCES = (SRC_TYPELIST, SRC_TECHNOS, SRC_SECTIONS)

_TECHNO_LISTS = ("InfantryTypes", "VehicleTypes", "AircraftTypes",
                 "BuildingTypes")

# An INI section name or key: letters, digits, and the handful of punctuation
# real mods use. No brackets, '=', ';', quotes, newlines or control characters —
# those are how a crafted name would escape into a different section or key.
_IDENT = re.compile(r"^[A-Za-z0-9 _.\-+#&'/()]{1,64}$")

MAX_COLUMNS = 12
MAX_FILTERS = 16
MAX_ROWS = 5000                 # a pathological spec must not hang the UI
MAX_NAME = 48


@dataclass
class AddonColumn:
    label: str
    tag: str


@dataclass
class AddonSpec:
    name: str = ""                                  # the tab's title
    source: str = SRC_TYPELIST
    list_section: str = ""                          # for typelist
    section_prefix: str = ""                        # for sections
    require_tags: list = field(default_factory=list)   # row must set ALL
    any_tags: list = field(default_factory=list)       # row must set >= 1
    columns: list = field(default_factory=list)        # [AddonColumn]
    group_by: str = ""                              # a column label
    notes: str = ""                                 # Eva's why/how, shown in UI
    version: int = ADDON_VERSION

    def to_json(self):
        return asdict(self)

    @staticmethod
    def from_json(d):
        spec, errors = validate_spec(d)
        return spec if not errors else None


@dataclass
class AddonRow:
    name: str
    values: dict = field(default_factory=dict)      # column label -> value
    group: str = ""


# ── validation (the security boundary) ──────────────────────────────────────
def _ident(v, what, errors):
    """Coerce to a safe INI identifier, or record why it isn't one."""
    s = str(v or "").strip()
    if not s:
        return ""
    if not _IDENT.match(s):
        errors.append(f"{what}: {s!r} is not a plain INI name")
        return ""
    return s


def _ident_list(seq, what, errors, cap=MAX_FILTERS):
    if isinstance(seq, str):
        seq = [seq]
    if not isinstance(seq, (list, tuple)):
        if seq:
            errors.append(f"{what}: expected a list")
        return []
    out = []
    for v in seq[:cap]:
        s = _ident(v, what, errors)
        if s and s not in out:
            out.append(s)
    if isinstance(seq, (list, tuple)) and len(seq) > cap:
        errors.append(f"{what}: more than {cap} entries, extra ignored")
    return out


def validate_spec(d):
    """(AddonSpec, errors) from a raw dict — typically straight off a model.

    Never raises: a hostile or confused spec comes back with errors listed.
    """
    errors = []
    if not isinstance(d, dict):
        return AddonSpec(), ["spec is not an object"]

    name = str(d.get("name", "")).strip()[:MAX_NAME]
    # A tab title is shown, not parsed, so only strip what would break the UI.
    name = re.sub(r"[\r\n\t]", " ", name).strip()
    if not name:
        errors.append("name: the tab needs a title")

    source = str(d.get("source", "")).strip().lower()
    if source not in ALL_SOURCES:
        errors.append(f"source: {source!r} is not one of "
                      f"{', '.join(ALL_SOURCES)}")
        source = SRC_TYPELIST

    spec = AddonSpec(
        name=name, source=source,
        list_section=_ident(d.get("list_section"), "list_section", errors),
        section_prefix=_ident(d.get("section_prefix"), "section_prefix",
                              errors),
        require_tags=_ident_list(d.get("require_tags"), "require_tags", errors),
        any_tags=_ident_list(d.get("any_tags"), "any_tags", errors),
        notes=str(d.get("notes", "")).strip()[:2000],
    )

    raw_cols = d.get("columns") or []
    if isinstance(raw_cols, dict):              # {label: tag} is a fair guess
        raw_cols = [{"label": k, "tag": v} for k, v in raw_cols.items()]
    if not isinstance(raw_cols, (list, tuple)):
        errors.append("columns: expected a list")
        raw_cols = []
    for c in raw_cols[:MAX_COLUMNS]:
        if isinstance(c, str):
            c = {"label": c, "tag": c}
        if not isinstance(c, dict):
            errors.append(f"columns: {c!r} is not a column")
            continue
        tag = _ident(c.get("tag"), "column tag", errors)
        label = re.sub(r"[\r\n\t]", " ",
                       str(c.get("label") or tag)).strip()[:MAX_NAME]
        if tag and label:
            spec.columns.append(AddonColumn(label=label, tag=tag))
    if len(raw_cols) > MAX_COLUMNS:
        errors.append(f"columns: more than {MAX_COLUMNS}, extra ignored")

    # Source-specific requirements.
    if spec.source == SRC_TYPELIST and not spec.list_section:
        errors.append("list_section: required when source is 'typelist'")
    if spec.source == SRC_SECTIONS and not spec.section_prefix:
        errors.append("section_prefix: required when source is 'sections'")

    gb = re.sub(r"[\r\n\t]", " ", str(d.get("group_by", "") or "")).strip()
    labels = {c.label for c in spec.columns}
    if gb and gb not in labels:
        errors.append(f"group_by: {gb!r} is not one of this addon's columns")
        gb = ""
    spec.group_by = gb

    return spec, errors


# ── interpretation ──────────────────────────────────────────────────────────
def _candidates(spec, rules_map):
    """Section names this addon considers, before tag filtering."""
    if spec.source == SRC_TYPELIST:
        return parse_type_list(rules_map, spec.list_section)
    if spec.source == SRC_TECHNOS:
        out = []
        for lst in _TECHNO_LISTS:
            out.extend(parse_type_list(rules_map, lst))
        return out
    pref = spec.section_prefix.lower()
    return [s["name_raw"] for key, s in rules_map.items()
            if key.startswith(pref)]


def scan_addon(spec, rules_map):
    """Run an addon against a parsed rules section map → [AddonRow].

    Row order follows the INI. Bounded by MAX_ROWS so a broad spec can't wedge
    the UI; the caller can tell it was clipped by comparing len() to MAX_ROWS.
    """
    rows, seen = [], set()
    for name in _candidates(spec, rules_map):
        if len(rows) >= MAX_ROWS:
            break
        low = name.lower()
        if low in seen or low not in rules_map:
            continue
        seen.add(low)

        vals = {}
        for col in spec.columns:
            vals[col.label] = _clean(get_val(rules_map, name, col.tag) or "")

        if spec.require_tags and not all(
                _clean(get_val(rules_map, name, t) or "")
                for t in spec.require_tags):
            continue
        if spec.any_tags and not any(
                _clean(get_val(rules_map, name, t) or "")
                for t in spec.any_tags):
            continue

        rows.append(AddonRow(name=name, values=vals,
                             group=vals.get(spec.group_by, "")))
    return rows


def spec_summary(spec):
    """One line describing what an addon collects — shown under its tab."""
    if spec.source == SRC_TYPELIST:
        where = f"[{spec.list_section}]"
    elif spec.source == SRC_TECHNOS:
        where = "all TechnoTypes"
    else:
        where = f"sections starting {spec.section_prefix!r}"
    bits = [f"from {where}"]
    if spec.require_tags:
        bits.append("with " + " + ".join(spec.require_tags))
    if spec.any_tags:
        bits.append("with any of " + ", ".join(spec.any_tags))
    bits.append(f"{len(spec.columns)} column(s)")
    return "; ".join(bits)


# ── persistence ─────────────────────────────────────────────────────────────
def addons_path():
    return os.path.join(os.path.expanduser("~"), ".yrbalancetool", "addons.json")


def _load_doc(path=None):
    path = path or addons_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (FileNotFoundError, ValueError):
        return {"addons": []}
    raw.setdefault("addons", [])
    return raw


def _save_doc(doc, path=None):
    path = path or addons_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    os.replace(tmp, path)


def load_addons(path=None):
    """Every stored addon. Specs are re-validated on load, so a file edited by
    hand (or written by an older version) can't smuggle in a bad spec."""
    out = []
    for d in _load_doc(path).get("addons", []):
        spec, errors = validate_spec(d)
        if not errors:
            out.append(spec)
    return out


def save_addons(specs, path=None):
    doc = _load_doc(path)
    doc["addons"] = [s.to_json() for s in specs]
    _save_doc(doc, path)


def add_addon(spec, path=None):
    specs = [s for s in load_addons(path) if s.name != spec.name]
    specs.append(spec)
    save_addons(specs, path)
    return specs


def remove_addon(name, path=None):
    specs = [s for s in load_addons(path) if s.name != name]
    save_addons(specs, path)
    return specs
