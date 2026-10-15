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

from .trigger_generator import ROLE_WEIGHTS, DEFAULT_WEIGHTS, ALWAYS_TRUE
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


def available_roles():
    return list(ROLE_MAP.keys())


def _resolve(display_role):
    spec = ROLE_MAP.get(display_role)
    if spec is None:
        # unknown custom role → treat as a general attack
        return "GENERAL_ATTACK", []
    return spec["key"], spec["tags"]


def build_role_entry(display_role, units, label=None):
    """Assemble a LibEntry (TaskForce + Script + TeamType + trigger) for `units` in
    `display_role`. Placeholder ids (RTF/RSC/RTM/RTRIG) are remapped by the pull."""
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

    # 18 comma fields, in trigger_generator.to_ini_line order.
    trig = ",".join([
        label, "RTM", "<all>", "0", "4", "<none>", ALWAYS_TRUE,
        f"{start_w:.6f}", f"{min_w:.6f}", f"{max_w:.6f}",
        "1", "0", "0", "0", "0", "1", "1", "1",
    ])

    return LibEntry(
        name=label, kind="bundle", source=f"role:{display_role}",
        blocks=[LibBlock("RTF", "taskforce", tf_keys),
                LibBlock("RSC", "script", sc_keys),
                LibBlock("RTM", "teamtype", tm_keys)],
        trigger_id="RTRIG", trigger_def=trig, units=list(units))


# Role tags that disqualify a unit from auto-suggestion however well its Verses
# profile matches the role. The role tags themselves (AG-*/AA) say only "this
# weapon can hurt that armor class" — magnitude is not part of that judgement —
# so without these gates a unit whose weapon is useless still gets pre-checked.
#   UNDESIRABLE — the parser's own flat-grade verdict (grade <= -5)
#   LOW_DPS     — its bottom-quartile damage marker
SUGGEST_EXCLUDE_TAGS = ("UNDESIRABLE", "LOW_DPS")


def suggest_units_for_role(display_role, rules_path, min_dps=0.0,
                           exclude_tags=SUGGEST_EXCLUDE_TAGS,
                           with_reasons=False):
    """Units whose detected role tags match `display_role`, using the wavegen role
    detector on `rules_path`.

    A tag match alone is a weak signal: `assign_roles` keys purely off `Verses`
    clearing `ROLE_THRESHOLD`, so a weapon that technically hurts infantry
    qualifies no matter how little damage it does. Two gates narrow it:

      exclude_tags — drop units carrying a disqualifying tag (see above).
      min_dps      — absolute floor on the parser's `dps_score`. Absolute on
                     purpose: the LOW_DPS quartile marker is *relative* to the
                     mod, so in a mod of uniformly weak weapons its top quartile
                     is still weak, and a genuinely strong unit in a strong mod
                     gets marked LOW_DPS. 0 disables the floor.

    With `with_reasons`, returns (picked, [(unit_id, why_rejected)]) so the
    caller can tell the user what was filtered instead of silently shrinking the
    list. Best-effort: returns empty on any parse failure, so the caller falls
    back to pure manual selection.
    """
    _key, tags = _resolve(display_role)
    if not tags or not rules_path:
        return ([], []) if with_reasons else []
    want = {t.lower() for t in tags}
    excl = {t.lower() for t in (exclude_tags or ())}
    try:
        from .rulesmd_parser import RulesMdParser
        parser = RulesMdParser(rules_path)
        parser.parse()
    except Exception:
        return ([], []) if with_reasons else []
    out, rejected = [], []
    for uid, rec in getattr(parser, "units", {}).items():
        rr = {str(r).lower() for r in (getattr(rec, "roles", None) or [])}
        if not (rr & want):
            continue
        bad = sorted(rr & excl)
        if bad:
            rejected.append((uid, "tagged " + ", ".join(t.upper() for t in bad)))
            continue
        dps = float(getattr(rec, "dps_score", 0.0) or 0.0)
        if min_dps > 0 and dps < min_dps:
            rejected.append((uid, f"dps {dps:.0f} < floor {min_dps:.0f}"))
            continue
        out.append(uid)
    return (out, rejected) if with_reasons else out
