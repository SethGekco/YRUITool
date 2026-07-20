"""
Ticket: session-layered edit history written alongside the rules file.

A ticket is an INI file (`<rules>_ticket.ini`) with a [Meta] block (tool version +
a hash of the original vanilla file) and one [Session_N] block per tool run. Each
session records the features used, per-category ratios (non-1.0 only), per-unit
changes (old/new/method), feature params, and host/clone processing. This is the
basis for eventual point-in-time restoration.

Ported from the monolith; branding generalized (new tickets say "YRBalanceTool"),
while `read_ticket` stays neutral so old Dying-Earth tickets still parse.
"""

import os
import re
import hashlib
import datetime as _dt

TICKET_VERSION = "2"

# Category and stat-field label maps shared by write/read.
_CAT_KEYS = [
    ("Infantry", "inf"), ("Vehicle", "veh"), ("Naval", "nav"),
    ("Aircraft", "air"), ("Building", "bld"), ("Defensive", "def"),
]
_STAT_FIELDS = [
    ("Strength", "str"), ("Cost", "cost"), ("Speed", "spd"),
    ("SpeedCap", "spd_cap"), ("BuildTimeMultiplier", "btm"),
    ("Soylent", "soylent"), ("Debris", "debris"), ("Adjacent", "adjacent"),
    ("Damage_Regular", "fp_r"), ("Damage_Elite", "fp_e"),
    ("Range_Regular", "rng_r"), ("Range_Elite", "rng_e"),
    ("ROF_Regular", "rof_r"), ("ROF_Elite", "rof_e"),
    ("Sight", "sight"), ("SightEqualRange", "sight_eq"),
]


def ticket_default_path(ini_path):
    """Return the default ticket path for a rules file: <base>_ticket.ini."""
    base, _ = os.path.splitext(ini_path)
    return base + "_ticket.ini"


def file_hash(path):
    """SHA-256 hash of a file, truncated to 16 hex chars ('unknown' on error)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return "unknown"


def next_session_id(ticket_path):
    """Return the next [Session_N] id by scanning the existing ticket."""
    if not os.path.isfile(ticket_path):
        return 1
    max_id = 0
    try:
        with open(ticket_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"^\[Session_(\d+)\]", line.strip())
                if m:
                    max_id = max(max_id, int(m.group(1)))
    except OSError:
        pass
    return max_id + 1


def write_ticket(ticket_path, ratios, gb_config, want_ai_clone,
                 rerun_mode, uncommented_mode, buildable_inf,
                 ini_path=None, unit_changes=None, feature_params=None,
                 tool_name="YRBalanceTool"):
    """
    Append a new session layer to the ticket, creating it with a [Meta] header on
    first run. Only non-1.0 ratios and real (old != new) unit changes are stored.

    unit_changes:   {cat_label: {unit_name: {tag: {old, new, ratio|method}}}}
    feature_params: {feature_name: {param: value}}
    """
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    session_id = next_session_id(ticket_path)
    is_new = not os.path.isfile(ticket_path)

    lines_out = []

    if is_new:
        fh = file_hash(ini_path) if ini_path else "unknown"
        lines_out += [
            f"; {tool_name} — Session History\n",
            f"; Format version: {TICKET_VERSION}\n",
            "; Each [Session_N] block records one tool run.\n",
            ";\n",
            "[Meta]\n",
            f"ToolVersion={TICKET_VERSION}\n",
            f"VanillaHash={fh}\n",
            f"VanillaFile={os.path.basename(ini_path) if ini_path else 'unknown'}\n",
            f"FirstSession={now}\n",
            "\n",
        ]

    features_used = []
    if ratios:
        features_used += [k.split("_")[0] for k in ratios
                          if str(ratios.get(k, "1.0")) not in ("1.0", "None", "False")]
    if feature_params:
        features_used += list(feature_params.keys())
    features_str = ",".join(sorted(set(features_used))) or "none"

    lines_out += [
        f"[Session_{session_id}]\n",
        f"Date={now}\n",
        f"Features={features_str}\n",
        f"RerunMode={rerun_mode}\n",
        "\n",
    ]

    for cat_label, cat_key in _CAT_KEYS:
        section_lines = []
        for field_label, field_key in _STAT_FIELDS:
            val = ratios.get(f"{cat_key}_{field_key}") if ratios else None
            if val is None or str(val) in ("1.0", "1", "None", "False"):
                continue
            section_lines.append(f"{field_label}={val}\n")
        if section_lines:
            lines_out.append(f"[Session_{session_id}_{cat_label}]\n")
            lines_out += section_lines
            lines_out.append("\n")

    if feature_params:
        for feature_name, params in feature_params.items():
            lines_out.append(f"[Session_{session_id}_{feature_name}_Params]\n")
            for k, v in params.items():
                lines_out.append(f"{k}={v}\n")
            lines_out.append("\n")

    if unit_changes:
        for cat_label, changes in unit_changes.items():
            if not changes:
                continue
            lines_out.append(f"[Session_{session_id}_{cat_label}_Units]\n")
            for unit_name, tags in changes.items():
                for tag, info in tags.items():
                    old_v = info.get("old", "?")
                    new_v = info.get("new", "?")
                    if str(old_v) == str(new_v):
                        continue
                    method = info.get("method")
                    ratio = info.get("ratio")
                    if method:
                        lines_out.append(
                            f"{unit_name}_{tag}=old:{old_v},new:{new_v},method:{method}\n")
                    elif ratio is not None:
                        lines_out.append(
                            f"{unit_name}_{tag}=old:{old_v},new:{new_v},ratio:{ratio}\n")
                    else:
                        lines_out.append(
                            f"{unit_name}_{tag}=old:{old_v},new:{new_v}\n")
            lines_out.append("\n")

    if gb_config:
        lines_out += [
            f"[Session_{session_id}_Host]\n",
            f"Mode={gb_config['mode']}\n",
            f"Value={gb_config['value']}\n",
        ]
        for name in (buildable_inf or []):
            lines_out.append(f"Processed_{name}=yes\n")
        lines_out.append("\n")

    with open(ticket_path, "w" if is_new else "a", encoding="utf-8") as f:
        f.writelines(lines_out)


def read_ticket(ticket_path):
    """
    Parse a ticket file. Returns a dict with meta, a sessions list, and
    backward-compat 'ratios'/'giftbox' keys (latest session), or None if absent.
    """
    if not os.path.isfile(ticket_path):
        return None
    try:
        raw_lines = open(ticket_path, encoding="utf-8", errors="replace").readlines()
    except OSError:
        return None

    meta = {}
    sessions = []
    current_section = None
    current_session = None
    current_session_id = None

    cat_map = {"infantry": "inf", "vehicle": "veh", "naval": "nav",
               "aircraft": "air", "building": "bld", "defensive": "def"}
    field_label_map = {fl: fk for fl, fk in _STAT_FIELDS}

    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        m = re.match(r"^\[([^\]]+)\]", stripped)
        if m:
            current_section = m.group(1).strip()
            sm = re.match(r"^Session_(\d+)$", current_section)
            if sm:
                current_session_id = int(sm.group(1))
                current_session = {
                    "id": current_session_id, "date": "",
                    "features": [], "ratios": {},
                    "unit_changes": {}, "params": {},
                    "giftbox": {"enabled": False, "processed": set()},
                }
                sessions.append(current_session)
            continue
        m2 = re.match(r"^([^=]+)=(.*)$", stripped)
        if not m2:
            continue
        k, v = m2.group(1).strip(), m2.group(2).strip()

        cs = current_section or ""
        if cs.lower() == "meta":
            meta[k.lower()] = v
        elif re.match(r"^Session_\d+$", cs) and current_session:
            if k.lower() == "date":
                current_session["date"] = v
            elif k.lower() == "features":
                current_session["features"] = [f.strip() for f in v.split(",")]
            elif k.lower() == "rerunmode":
                current_session["rerunmode"] = v
        elif current_session:
            csm = re.match(r"^Session_(\d+)_(\w+)$", cs)
            if csm and int(csm.group(1)) == current_session_id:
                sub = csm.group(2).lower()
                if sub in cat_map:
                    fk = field_label_map.get(k)
                    if fk:
                        current_session["ratios"][f"{cat_map[sub]}_{fk}"] = v
                elif sub == "host":
                    if k.lower().startswith("processed_"):
                        current_session["giftbox"]["processed"].add(k[10:])
                    elif k.lower() in ("mode", "value"):
                        current_session["giftbox"][k.lower()] = v
                elif sub.endswith("_units"):
                    cat_label = sub[:-6]
                    uc = current_session["unit_changes"].setdefault(cat_label, {})
                    uvm = re.match(r"^(.+)_([^_]+)$", k)
                    if uvm:
                        uname, tag = uvm.group(1), uvm.group(2)
                        parts = dict(p.split(":", 1) for p in v.split(",") if ":" in p)
                        uc.setdefault(uname, {})[tag] = parts
                elif sub.endswith("_params"):
                    current_session["params"][k] = v

    latest_ratios = sessions[-1]["ratios"] if sessions else {}
    latest_gb = {"enabled": False, "mode": "flat", "value": 1,
                 "ai_clones": False, "processed": set()}
    if sessions and sessions[-1]["giftbox"].get("processed"):
        latest_gb["enabled"] = True
        latest_gb["processed"] = sessions[-1]["giftbox"]["processed"]

    return {"meta": meta, "sessions": sessions,
            "ratios": latest_ratios, "giftbox": latest_gb}
