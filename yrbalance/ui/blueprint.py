"""
Blueprint files: a human-editable text snapshot of the whole tool state.

A blueprint captures every tab's settings and the custom script so it can be
saved, shared, re-loaded, and re-run. It is plain INI-flavored text — open it in
Notepad and edit by hand. Naval fields set to `link` inherit the Vehicle value.
The `[Script]` section is terminal: everything after its header is raw
modifier-script text.

This module converts between blueprint text and plain state dicts; the main window
reads/writes those dicts from/to its widgets.
"""

import datetime

CATEGORY_ORDER = ["inf", "veh", "nav", "air", "bld", "def"]
CATEGORY_LABELS = {"inf": "Infantry", "veh": "Vehicle", "nav": "Naval",
                   "air": "Aircraft", "bld": "Building", "def": "Defensive"}
LABEL_TO_CAT = {v.lower(): k for k, v in CATEGORY_LABELS.items()}

KEY_LABEL = {
    "strength": "Strength", "cost": "Cost", "speed": "Speed",
    "buildtime": "BuildTime", "soylent": "Soylent",
    "reg_dmg": "Damage.Reg", "eli_dmg": "Damage.Elite",
    "reg_rng": "Range.Reg", "eli_rng": "Range.Elite",
    "reg_rof": "ROF.Reg", "eli_rof": "ROF.Elite",
    "sight": "Sight", "debris": "Debris", "adjacent": "Adjacent",
}
LABEL_KEY = {v.lower(): k for k, v in KEY_LABEL.items()}

FORM_ORDER = ["general", "gameplay", "superweapons"]
FORM_LABELS = {"general": "General", "gameplay": "Gameplay",
               "superweapons": "SuperWeapons"}
FORM_LABEL_TO_KEY = {v.lower(): k for k, v in FORM_LABELS.items()}


def _fmt(v):
    v = float(v)
    return str(int(v)) if v == int(v) else str(v)


def _yesno(b):
    return "yes" if b else "no"


def _is_yes(s):
    return str(s).strip().lower() in ("yes", "true", "1", "on")


def _default_category_state():
    return {"fields": {}, "links": {}, "sight_equal": False,
            "sight_equal_link": None, "sight_nw_mode": "leave",
            "sight_nw_value": 1.0, "soylent_cost_frac": None,
            "cost_mode": "flat", "cost_base": "vanilla",
            "tl_recompute": False, "tl_write": False, "tl_use": False,
            "adv_link": None}


# ── Serialize ────────────────────────────────────────────────────────────────

def serialize(category_states, form_states, script_text, name="Untitled",
              global_state=None):
    out = ["# YRBalanceTool blueprint",
           f"#name: {name}",
           f"#created: {datetime.date.today().isoformat()}",
           "# Hand-editable. Naval fields set to 'link' inherit the Vehicle value.",
           "# The [Script] section is terminal: everything after it is raw script.",
           ""]

    for cat_key in CATEGORY_ORDER:
        state = category_states.get(cat_key)
        if state is None:
            continue
        out.append(f"[{CATEGORY_LABELS[cat_key]}]")
        linkable = bool(state.get("links"))
        for attr, val in state["fields"].items():
            label = KEY_LABEL.get(attr, attr)
            if linkable and state["links"].get(attr):
                out.append(f"{label} = link")
            else:
                out.append(f"{label} = {_fmt(val)}")
        if linkable and state.get("sight_equal_link"):
            out.append("Sight.Equal = link")
        else:
            out.append(f"Sight.Equal = {_yesno(state['sight_equal'])}")
        out.append(f"Sight.NoWeapon = {state.get('sight_nw_mode', 'leave')}")
        out.append(f"Sight.NoWeaponValue = {_fmt(state.get('sight_nw_value', 1.0))}")
        if state.get("soylent_cost_frac") is not None:
            out.append(f"Soylent.CostFraction = {_yesno(state['soylent_cost_frac'])}")
        if linkable:
            out.append(f"AdvGroup = {'link' if state.get('adv_link') else 'own'}")
        out.append(f"CostMode = {state['cost_mode']}")
        out.append(f"CostBase = {state['cost_base']}")
        out.append(f"TL.Recompute = {_yesno(state['tl_recompute'])}")
        out.append(f"TL.Write = {_yesno(state['tl_write'])}")
        out.append(f"TL.Use = {_yesno(state['tl_use'])}")
        out.append("")

    for form_key in FORM_ORDER:
        fstate = form_states.get(form_key)
        if fstate is None:
            continue
        out.append(f"[Form.{FORM_LABELS[form_key]}]")
        out.append(f"Section = {fstate.get('section', '')}")
        for used, tag, value in fstate.get("rows", []):
            if not tag.strip():
                continue
            prefix = "" if used else "#"
            out.append(f"{prefix}{tag} = {value}")
        out.append("")

    if global_state:
        out.append("[Global]")
        out.append(f"RangeProtect = {_yesno(global_state.get('range_protect', True))}")
        out.append(f"RangeThreshold = {_fmt(global_state.get('range_threshold', 3.0))}")
        out.append("")

    out.append("[Script]")
    out.append(script_text.rstrip("\n"))
    return "\n".join(out) + "\n"


# ── Parse ────────────────────────────────────────────────────────────────────

def parse(text):
    """Return (meta, category_states, form_states, global_state, script_text)."""
    meta = {}
    category_states = {}
    form_states = {}
    global_state = {}
    script_lines = []
    mode = "header"  # "header" | ("category",k) | ("form",k) | "global" | "script"

    for raw in text.splitlines():
        if mode == "script":
            script_lines.append(raw)
            continue

        line = raw.strip()
        if not line:
            continue

        if line.startswith("[") and line.endswith("]"):
            inner = line[1:-1].strip()
            low = inner.lower()
            if low == "script":
                mode = "script"
            elif low == "global":
                mode = "global"
            elif low.startswith("form."):
                key = FORM_LABEL_TO_KEY.get(inner[5:].strip().lower(),
                                            inner[5:].strip().lower())
                form_states[key] = {"section": "", "rows": []}
                mode = ("form", key)
            elif low in LABEL_TO_CAT:
                cat = LABEL_TO_CAT[low]
                category_states[cat] = _default_category_state()
                mode = ("category", cat)
            else:
                mode = "unknown"
            continue

        if mode == "header":
            if line.startswith("#") and ":" in line:
                k, _, v = line[1:].strip().partition(":")
                meta[k.strip().lower()] = v.strip()
            continue

        if mode == "global":
            _parse_global_line(global_state, line)
        elif isinstance(mode, tuple) and mode[0] == "category":
            _parse_category_line(category_states[mode[1]], line)
        elif isinstance(mode, tuple) and mode[0] == "form":
            _parse_form_line(form_states[mode[1]], line)

    return (meta, category_states, form_states, global_state,
            "\n".join(script_lines).strip("\n"))


def _parse_global_line(state, line):
    if line.startswith("#"):
        return
    k, sep, v = line.partition("=")
    if not sep:
        return
    kl = k.strip().lower()
    if kl == "rangeprotect":
        state["range_protect"] = _is_yes(v)
    elif kl == "rangethreshold":
        try:
            state["range_threshold"] = float(v.strip())
        except ValueError:
            pass


def _parse_category_line(state, line):
    if line.startswith("#"):
        return  # comment inside a category block
    k, sep, v = line.partition("=")
    if not sep:
        return
    k, v = k.strip(), v.strip()
    kl = k.lower()
    if kl == "sight.equal":
        if v.lower() == "link":
            state["sight_equal_link"] = True
        else:
            state["sight_equal"] = _is_yes(v)
    elif kl == "sight.noweapon":
        state["sight_nw_mode"] = v
    elif kl == "sight.noweaponvalue":
        try:
            state["sight_nw_value"] = float(v)
        except ValueError:
            pass
    elif kl == "soylent.costfraction":
        state["soylent_cost_frac"] = _is_yes(v)
    elif kl == "advgroup":
        state["adv_link"] = (v.lower() == "link")
    elif kl == "costmode":
        state["cost_mode"] = v
    elif kl == "costbase":
        state["cost_base"] = v
    elif kl == "tl.recompute":
        state["tl_recompute"] = _is_yes(v)
    elif kl == "tl.write":
        state["tl_write"] = _is_yes(v)
    elif kl == "tl.use":
        state["tl_use"] = _is_yes(v)
    else:
        attr = LABEL_KEY.get(kl)
        if attr is None:
            return
        if v.lower() == "link":
            state["links"][attr] = True
        else:
            try:
                state["fields"][attr] = float(v)
                state["links"][attr] = False
            except ValueError:
                pass


def _parse_form_line(fstate, line):
    used = True
    if line.startswith("#"):
        used = False
        line = line[1:].strip()
    k, sep, v = line.partition("=")
    if not sep:
        return
    k, v = k.strip(), v.strip()
    if k.lower() == "section":
        fstate["section"] = v
    elif k:
        fstate["rows"].append((used, k, v))
