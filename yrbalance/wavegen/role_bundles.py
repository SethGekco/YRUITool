"""
Turn a user-curated unit role list (see engine.unitroles) into a ready-to-insert
AI bundle — a TaskForce + ScriptType + TeamType + AITriggerTypes entry — and
suggest which units fit a role.

The bundle is built as an engine.LibEntry with placeholder ids; the caller feeds it
to engine.build_pull_plan / apply_pull (the same proven path the AI library uses),
which mints fresh ids, repoints references, dedups against the target and backs it
up. So this module only has to assemble *valid* block content — which it does by
reusing the wavegen generators' own vanilla-confirmed constants:

  • script action sequences  — from script_generator's template library,
  • TeamType flag defaults    — team_generator.FIXED_FLAGS / ROLE_FLAGS,
  • trigger weights + layout  — trigger_generator.ROLE_WEIGHTS + to_ini_line order.
"""

from ..engine import LibEntry, LibBlock

from .trigger_generator import (
    ROLE_WEIGHTS, DEFAULT_WEIGHTS, ALWAYS_TRUE, OWN_AT_LEAST_1,
    COND_ENEMY_CREDITS, COND_ENEMY_OWNS, COND_AI_OWNS,
)
from .team_generator import FIXED_FLAGS, ROLE_FLAGS

# Display role → canonical generator key + role-detector tags for auto-suggest.
ROLE_MAP = {
    "Rush":           {"key": "RUSH",
                       "tags": ["AG-Anti_Infantry", "GENERALIST", "AG-Multi"]},
    "General Attack": {"key": "GENERAL_ATTACK",
                       "tags": ["GENERALIST", "AG-Multi", "HIGH_DPS"]},
    "Anti-Infantry":  {"key": "ANTI_INF", "tags": ["AG-Anti_Infantry"]},
    "Anti-Vehicle":   {"key": "ANTI_VEHICLE",
                       "tags": ["AG-Anti_Vehicle", "Miner_Killer"]},
    "Anti-Building":  {"key": "ANTI_BUILDING",
                       "tags": ["AG-Anti_Building", "Factory_Killer", "CY_Killer"]},
    "Anti-Defenses":  {"key": "ANTI_DEFENSES",
                       "tags": ["AG-Anti_Building", "Factory_Killer"]},
    "Miner Killer":   {"key": "MINER_KILLER", "tags": ["Miner_Killer"]},
    "Anti-Air":       {"key": "AA", "tags": ["AA", "AA_Secondary"]},
}

# Canonical attack scripts, copied verbatim from script_generator's vanilla-confirmed
# template library. Action pairs are (action_id, argument). TGT: 1=base 2=structures
# 3=harvesters 4=infantry 5=vehicles 6=factories 7=defenses. 53=gather-enemy,
# 54=regroup-friendly, 49=repeat-until-ok, 6=jump-to-line.
SCRIPT_BY_ROLE = {
    "RUSH":           [(53, 0), (0, 1), (49, 0), (6, 1)],
    "GENERAL_ATTACK": [(54, 0), (53, 0), (0, 1), (49, 0), (6, 1)],
    "ANTI_INF":       [(54, 0), (53, 0), (0, 4), (49, 0), (0, 1), (49, 0), (6, 1)],
    "ANTI_VEHICLE":   [(54, 0), (53, 0), (0, 5), (49, 0), (0, 1), (49, 0), (6, 1)],
    "ANTI_BUILDING":  [(54, 0), (53, 0), (0, 2), (49, 0), (0, 1), (49, 0), (6, 1)],
    "ANTI_DEFENSES":  [(54, 0), (53, 0), (0, 7), (49, 0), (0, 6), (49, 0),
                       (0, 2), (49, 0), (0, 1), (49, 0), (6, 1)],
    "MINER_KILLER":   [(54, 0), (53, 0), (0, 3), (49, 0), (0, 1), (49, 0), (6, 1)],
    "AA":             [(54, 0), (53, 0), (0, 1), (49, 0), (6, 1)],
}



# ── trigger conditions ──────────────────────────────────────────────────────
# Vanilla spreads AITrigger conditions across credits (53 of 165), "enemy owns"
# (50) and "AI owns" (49) — roughly 60% are conditional on battlefield state.
# Emitting credits>=0 for everything, as this module used to, makes every
# trigger permanently eligible, so the AI picks from a flat weighted lottery
# instead of reacting to what is actually on the field.
#
# Vanilla cannot express "enemy owns a unit of CLASS x", only a specific
# ID, so a role cannot be turned into a condition automatically with full
# fidelity. Per Rex: generate the plausible enemy-side and owner-side variants
# and let the player cycle through and choose.
COND_ALWAYS_LABEL = "Always (enemy credits >= 0)"


def role_trigger_variants(display_role, units=None, enemy_objects=None):
    """Candidate conditions for a role's trigger.

    Returns [{"label", "cond_type", "cond_object", "comparator"}]. The first is
    always the unconditional one, so existing behaviour stays the default.

    `units` are the role's own units — used for the owner-side variant ("only
    form this team once the AI actually owns one"). `enemy_objects` are IDs
    worth watching on the enemy side (harvesters, defences, factories…); the
    caller supplies them because only it has the parsed rules.
    """
    out = [{"label": COND_ALWAYS_LABEL,
            "cond_type": COND_ENEMY_CREDITS,
            "cond_object": "<none>",
            "comparator": ALWAYS_TRUE}]
    for uid in (units or [])[:8]:
        out.append({"label": f"AI owns >= 1 {uid}",
                    "cond_type": COND_AI_OWNS,
                    "cond_object": uid,
                    "comparator": OWN_AT_LEAST_1})
    for oid in (enemy_objects or [])[:12]:
        out.append({"label": f"Enemy owns >= 1 {oid}",
                    "cond_type": COND_ENEMY_OWNS,
                    "cond_object": oid,
                    "comparator": OWN_AT_LEAST_1})
    return out


def available_roles():
    return list(ROLE_MAP.keys())


def _resolve(display_role):
    spec = ROLE_MAP.get(display_role)
    if spec is None:
        # unknown custom role → treat as a general attack
        return "GENERAL_ATTACK", []
    return spec["key"], spec["tags"]


def build_role_entry(display_role, units, label=None, side=1, condition=None):
    """Assemble a LibEntry (TaskForce + Script + TeamType + trigger) for `units` in
    `display_role`. Placeholder ids (RTF/RSC/RTM/RTRIG) are remapped by the pull.

    `side` is the AITrigger Side field (1=Allied, 2=Soviet, 3=Yuri). All 165
    vanilla triggers set 1, 2 or 3 — none uses 0 — so there is no "any side"
    value to fall back on and the caller must say which.

    `condition` is one entry from role_trigger_variants(); omitted means the
    unconditional credits>=0 one.
    """
    key, _tags = _resolve(display_role)
    units = [u for u in units if u]
    label = (label or f"{display_role} ({len(units)})")[:47].replace(",", " ")

    actions = SCRIPT_BY_ROLE.get(key, SCRIPT_BY_ROLE["GENERAL_ATTACK"])
    start_w, min_w, max_w = ROLE_WEIGHTS.get(key, DEFAULT_WEIGHTS)
    flags = dict(FIXED_FLAGS)
    flags.update(ROLE_FLAGS.get(key, ROLE_FLAGS["GENERAL_ATTACK"]))

    tf_keys = [["Name", f"{label} TF"], ["Group", "-1"]]
    tf_keys += [[str(i), f"1,{u}"] for i, u in enumerate(units)]

    sc_keys = [["Name", f"{label} script"]]
    sc_keys += [[str(i), f"{a},{arg}"] for i, (a, arg) in enumerate(actions)]

    tm_keys = [["Name", label], ["TaskForce", "RTF"], ["Script", "RSC"]]
    tm_keys += [[k, v] for k, v in flags.items()]

    cond = condition or role_trigger_variants(display_role)[0]
    try:
        side_n = int(side)
    except (TypeError, ValueError):
        side_n = 1
    if side_n not in (1, 2, 3):
        side_n = 1

    # 18 comma fields, in trigger_generator.to_ini_line order. Three of these
    # were previously emitted as values that appear in NONE of the 165 vanilla
    # triggers: TechLevel=0 (vanilla floor is 1), Side=0 (vanilla is always
    # 1/2/3) and Team2="0" (vanilla is "<none>" or a real TeamType id).
    trig = ",".join([
        label, "RTM", "<all>", "1",
        str(cond["cond_type"]), cond["cond_object"], cond["comparator"],
        f"{start_w:.6f}", f"{min_w:.6f}", f"{max_w:.6f}",
        "1", "0", str(side_n), "0", "<none>", "1", "1", "1",
    ])

    return LibEntry(
        name=label, kind="bundle", source=f"role:{display_role}",
        blocks=[LibBlock("RTF", "taskforce", tf_keys),
                LibBlock("RSC", "script", sc_keys),
                LibBlock("RTM", "teamtype", tm_keys)],
        trigger_id="RTRIG", trigger_def=trig, units=list(units))


def suggest_units_for_role(display_role, rules_path):
    """Units whose detected role tags match `display_role`, using the wavegen role
    detector on `rules_path`. Best-effort: returns [] on any parse failure so the
    caller falls back to pure manual selection."""
    _key, tags = _resolve(display_role)
    if not tags or not rules_path:
        return []
    want = {t.lower() for t in tags}
    try:
        from .rulesmd_parser import RulesMdParser
        parser = RulesMdParser(rules_path)
        parser.parse()
    except Exception:
        return []
    out = []
    for uid, rec in getattr(parser, "units", {}).items():
        rr = {str(r).lower() for r in (getattr(rec, "roles", None) or [])}
        if rr & want:
            out.append(uid)
    return out
