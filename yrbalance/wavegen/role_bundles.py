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
    # Rush is gated on PREREQUISITE TIER, not TechLevel. Per Rex: TechLevel
    # correlates with tech tier but is not the proper instrument; prerequisites
    # are. rec.tier already encodes exactly that — tier_from_prereq_chain walks
    # the unit's full prerequisite chain and asks whether it contains a radar
    # (Mid) or a lab (Late). Vanilla: GI/Conscript/Dog/Guardian GI/Flak
    # Trooper/Brute/Grizzly are Early, while the Chrono Legionnaire is Late
    # (GAPILE,TECH) — which is the unit that prompted this.
    #
    # Raw prerequisite DEPTH does not work: vanilla's tree is shallow, so CLEG
    # and GI both come out at depth 2. Chain membership is the signal.
    "Rush":           {"key": "RUSH",
                       "tags": ["AG-Anti_Infantry", "GENERALIST", "AG-Multi"],
                       # HOW MANY BUILDINGS the player must erect first (Rex).
                       # Calibrated against vanilla: a war factory costs 4, an
                       # airfield 3, and a radar-gated unit 5 — "5 is not
                       # rushing, 5 is mid game". So the ceiling is 4.
                       #
                       # This replaced both a TechLevel gate (wrong instrument)
                       # and a prereq-membership test. Membership could not
                       # work: an alias is an OR, and taking the union of its
                       # options dragged a battle lab into the Rhino Tank's
                       # chain through NANRCT, an OPTIONAL power plant it would
                       # never choose. Counting takes the cheapest option per
                       # alias instead.
                       "max_build_count": 4,
                       # A rush exists to wreck the enemy's base, so it needs
                       # real building DPS — enough to kill the median
                       # structure inside RUSH_STRUCTURE_KILL_SECONDS. This is
                       # what drops the Attack Dog, which does 0.000 to
                       # buildings.
                       "building_dps_seconds": 60.0,
                       # Requiring BOTH CY_Killer and Factory_Killer was
                       # considered and rejected: on vanilla it drops the GI
                       # and the Initiate, i.e. the archetypal infantry
                       # rushers, leaving only a Rhino and a Destroyer.
                       # "any of" is a no-op on vanilla but guards a mod whose
                       # early units cannot touch production at all.
                       "any_tags": ("CY_Killer", "Factory_Killer"),
                       # tier_from_prereq_chain returns "Early" when the chain
                       # contains no radar and no lab — which is also true of a
                       # chain that is EMPTY. So campaign and civilian units
                       # (ALL, JOSH, MUMY, POLARB, the dinosaurs) read as Early
                       # and flooded the list. Rush therefore also requires the
                       # unit to be in the parser's buildable pool: 42 -> the
                       # units a player could actually open with.
                       "buildable_only": True},
    "General Attack": {"key": "GENERAL_ATTACK",
                       "tags": ["GENERALIST", "AG-Multi", "HIGH_DPS"]},
    "Anti-Infantry":  {"key": "ANTI_INF", "tags": ["AG-Anti_Infantry"]},
    "Anti-Vehicle":   {"key": "ANTI_VEHICLE",
                       "tags": ["AG-Anti_Vehicle", "Miner_Killer"]},
    "Anti-Building":  {"key": "ANTI_BUILDING",
                       "tags": ["AG-Anti_Building", "Factory_Killer", "CY_Killer"]},
    # Two camps, split by the DOMAIN OF THE DEFENSES BEING ATTACKED, not by
    # the attacker's domain — a V3 sniping a SAM site is an air-defense killer
    # just as much as a fighter is. Each camp is scored against its own range
    # bar: ground-capable building weapons for one, air-capable (AA) for the
    # other. Mixing them put the bar above the Prism Tower.
    # The ground case splits again, because the two ways to kill a defense have
    # requirements that differ in KIND, not degree:
    #   standoff — out-ranges the defense, so it is never shot at and slow
    #              damage is fine ("fine if damage is slow so long as range is
    #              high", Rex). Needs reach; barely needs toughness.
    #   breacher — does NOT out-range it, so it has to drive into the envelope
    #              and tank the hits. Needs real DPS and real survivability.
    # One sliding DPS-per-range formula cannot express "also needs armour", and
    # the AI fields them differently anyway (ROLE_FLAGS already separates
    # hold-position-and-fire from push-to-target).
    "Anti-Defenses (Standoff)": {"key": "ANTI_DEFENSES",
                                 "tags": ["AG-Anti_Building",
                                          "Factory_Killer"]},
    "Anti-Defenses (Breacher)": {"key": "ANTI_DEFENSES_BREACH",
                                 "tags": ["AG-Anti_Building",
                                          "Factory_Killer"]},
    "Anti-Defenses (Air)":    {"key": "ANTI_DEFENSES_AIR",
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
    # Same action list for both camps: vanilla's "attack defenses" target (7)
    # cannot distinguish an AA tower from a gun tower, so the camps differ in
    # WHICH UNITS get recruited, not in what the script tells them to shoot.
    "ANTI_DEFENSES_AIR": [(54, 0), (53, 0), (0, 7), (49, 0), (0, 6), (49, 0),
                          (0, 2), (49, 0), (0, 1), (49, 0), (6, 1)],
    "ANTI_DEFENSES_BREACH": [(54, 0), (53, 0), (0, 7), (49, 0), (0, 6),
                             (49, 0), (0, 2), (49, 0), (0, 1), (49, 0),
                             (6, 1)],
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


# Role lists are saved by DISPLAY NAME in unit_roles.json, so the old name has
# to keep resolving or a user's saved picks silently detach from their role.
LEGACY_ROLE_NAMES = {
    "Anti-Defenses":          "Anti-Defenses (Standoff)",
    "Anti-Defenses (Ground)": "Anti-Defenses (Standoff)",
}


def available_roles():
    return list(ROLE_MAP.keys())


# UnitRecord.tier defaults to the literal string "Rush" (see rulesmd_parser),
# which is NOT a tier — it is "never computed", and it sits on 418 of vanilla's
# 559 units, nearly all unbuildable props. A tier gate must therefore name the
# tiers it wants rather than exclude the ones it does not.
UNCOMPUTED_TIER = "Rush"


def _spec_of(display_role):
    spec = ROLE_MAP.get(display_role)
    if spec is None and display_role in LEGACY_ROLE_NAMES:
        spec = ROLE_MAP.get(LEGACY_ROLE_NAMES[display_role])
    return spec or {}


def role_tiers(display_role):
    """Prerequisite tiers a role accepts, or () for no tier restriction."""
    return tuple(_spec_of(display_role).get("tiers") or ())


def role_buildable_only(display_role):
    """Whether the role rejects units the player cannot actually build."""
    return bool(_spec_of(display_role).get("buildable_only"))


def role_any_tags(display_role):
    """Tags of which the unit must carry at least one, or () for no such test."""
    return tuple(_spec_of(display_role).get("any_tags") or ())


def _resolve(display_role):
    spec = ROLE_MAP.get(display_role)
    if spec is None and display_role in LEGACY_ROLE_NAMES:
        spec = ROLE_MAP.get(LEGACY_ROLE_NAMES[display_role])
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


AERIAL_CAMP_KEY  = "ANTI_DEFENSES_AIR"
STANDOFF_CAMP_KEY = "ANTI_DEFENSES"
BREACH_CAMP_KEY  = "ANTI_DEFENSES_BREACH"
DEFENSE_CAMP_KEYS = (STANDOFF_CAMP_KEY, BREACH_CAMP_KEY,
                     AERIAL_CAMP_KEY)
# kept for callers that predate the standoff/breacher split
GROUND_CAMP_KEY = STANDOFF_CAMP_KEY


def defense_camp_profile(rules_path, aerial=False):
    """Range profile of the defenses a camp attacks, or None if unreadable.

    aerial=False -> building weapons that can hit GROUND (what threatens a
    ground army). aerial=True -> weapons that can hit AIR (the AA network).
    """
    if not rules_path:
        return None
    try:
        from .rulesmd_parser import RulesMdParser, defense_range_profile
        parser = RulesMdParser(rules_path)
        parser.parse()
        targets = parser._scan_strategic_targets()
    except Exception:
        return None
    key = "base_def_ranges_air" if aerial else "base_def_ranges"
    return defense_range_profile(targets.get(key) or [])


def _median_unit_strength(parser):
    """Median Strength across the mod's units, cached on the parser."""
    cached = getattr(parser, "_rb_median_strength", None)
    if cached is not None:
        return cached
    vals = sorted(float(getattr(r, "strength", 0) or 0)
                  for r in getattr(parser, "units", {}).values())
    vals = [v for v in vals if v > 0]
    if not vals:
        med = 0.0
    else:
        n = len(vals)
        med = (vals[n // 2] if n % 2
               else (vals[n // 2 - 1] + vals[n // 2]) / 2.0)
    try:
        parser._rb_median_strength = med
    except Exception:
        pass
    return med


def _is_tough(rec, parser):
    """At or above the mod's median unit Strength."""
    med = _median_unit_strength(parser)
    if med <= 0:
        return True                 # unreadable -> do not exclude
    try:
        return float(getattr(rec, "strength", 0) or 0) >= med
    except (TypeError, ValueError):
        return False


def suggest_units_for_role(display_role, rules_path):
    """Units whose detected role tags match `display_role`, using the wavegen role
    detector on `rules_path`. Best-effort: returns [] on any parse failure so the
    caller falls back to pure manual selection.

    The two Anti-Defenses camps additionally require the unit to OUT-RANGE its
    camp's defenses AND to carry enough DPS against defense armour to actually
    kill one (warhead verses x damage / ROF against the median defense's
    health). Tags alone cannot separate them — both camps want
    building-damaging units — so without the range test the ground and air
    lists would be identical.

    NOT A BUG: no AIRCRAFT qualifies for either camp on vanilla. The six
    building-damaging vanilla aircraft (ASW, BEAG, BPLN, HORNET, ORCA,
    PDPLANE) have range 3-6 against bars of 7.71 and 12.0, because aircraft
    close in and bomb rather than out-range anything. Confirmed expected by Rex
    2026-10-16 — do not "fix" it by exempting aircraft from the range test.
    These camps mean "can engage the defenses from outside their reach", which
    is a property aircraft simply do not have.
    """
    key, tags = _resolve(display_role)
    if not tags or not rules_path:
        return []
    want = {t.lower() for t in tags}
    try:
        from .rulesmd_parser import (
            RulesMdParser, defense_range_profile, defense_kill_dps_floor,
            standoff_kill_dps_floor, primary_defense_armor,
            best_weapon_dps_vs, dps_is_trustworthy, is_aerial_unit,
            kill_dps_floor, _median,
        )
        parser = RulesMdParser(rules_path)
        parser.parse()
    except Exception:
        return []

    camp = None
    if key in DEFENSE_CAMP_KEYS:
        try:
            targets = parser._scan_strategic_targets()
            rkey = ("base_def_ranges_air" if key == AERIAL_CAMP_KEY
                    else "base_def_ranges")
            strengths = targets.get("base_def_strengths")
            camp = {
                "bar": defense_range_profile(targets.get(rkey) or [])["bar"],
                "armor": primary_defense_armor(
                    targets.get("base_def_armor_counts")),
                # A standoff unit is out of reach, so it only has to be able to
                # hurt the thing at all; a breacher is being shot at and has to
                # kill it promptly.
                # The relaxed floor is only defensible when paired with a
                # hard range gate: "it can take as long as it likes" is true
                # of a unit sitting outside every defense, and of nothing
                # else. The air camp has no range gate for ground units (AA
                # cannot shoot them), so relaxing it there put 46 units in —
                # a GI can eventually kill a SAM site, which is true and
                # useless. Air and breacher therefore both use the strict
                # floor; only standoff gets the relaxation.
                "floor": (standoff_kill_dps_floor(strengths)
                          if key == STANDOFF_CAMP_KEY
                          else defense_kill_dps_floor(strengths)),
            }
        except Exception:
            camp = None                    # fall back to tags only

    tiers = role_tiers(display_role)
    spec = _spec_of(display_role)
    any_tags = {x.lower() for x in role_any_tags(display_role)}
    max_build = spec.get("max_build_count")
    bld_floor = 0.0
    if max_build or spec.get("building_dps_seconds"):
        try:
            from .rulesmd_parser import build_prereq_count, ARMOR_BUILDING
            secs = spec.get("building_dps_seconds")
            if secs:
                _t = parser._scan_strategic_targets()
                bld_floor = kill_dps_floor(
                    _median(_t.get("base_def_strengths") or []), secs)
        except Exception:
            max_build, bld_floor = None, 0.0
    buildable = None
    if role_buildable_only(display_role):
        try:
            buildable = {r.unit_id for r in getattr(parser, "buildable", ())}
        except Exception:
            buildable = None
    cands = []
    for uid, rec in getattr(parser, "units", {}).items():
        rr = {str(r).lower() for r in (getattr(rec, "roles", None) or [])}
        if not (rr & want):
            continue
        if tiers and str(getattr(rec, "tier", UNCOMPUTED_TIER)) not in tiers:
            continue
        if buildable is not None and uid not in buildable:
            continue
        if any_tags and not (rr & any_tags):
            continue
        if max_build:
            try:
                if build_prereq_count(uid, parser.ini, parser.aliases,
                                      getattr(rec, "faction", None),
                                      getattr(parser, "sides", None)) > max_build:
                    continue
            except Exception:
                continue
        if bld_floor > 0 and dps_is_trustworthy(rec):
            if best_weapon_dps_vs(rec, ARMOR_BUILDING) < bld_floor:
                continue
        if camp is None:
            cands.append((uid, rec))
            continue
        try:
            rng = float(getattr(rec, "weapon_range", 0) or 0)
        except (TypeError, ValueError):
            continue
        outranges = rng > camp["bar"]
        if key == AERIAL_CAMP_KEY:
            # An AA defense has AG=no — it physically cannot shoot a ground
            # unit. So a tank walking up to a SAM site takes NOTHING from it,
            # and demanding that it out-range the AA was meaningless. Only an
            # AERIAL attacker is exposed, and it then needs the same thing any
            # unit under fire needs: reach, or the toughness to absorb it.
            if is_aerial_unit(rec):
                if not (outranges or _is_tough(rec, parser)):
                    continue
        elif key == BREACH_CAMP_KEY:
            # A breacher is DEFINED by not out-ranging its target, so
            # requiring range here would leave the camp permanently empty.
            if outranges:
                continue
        elif not outranges:
            continue
        # Enough damage to actually kill a defense. Skipped when the damage
        # comes from a spawned missile or plane we cannot read (V3, Carrier):
        # unknown DPS must not be treated as zero.
        if (camp["floor"] > 0 and camp["armor"]
                and dps_is_trustworthy(rec)
                and best_weapon_dps_vs(rec, camp["armor"]) < camp["floor"]):
            continue
        cands.append((uid, rec))

    if camp is None or key != BREACH_CAMP_KEY:
        return [uid for uid, _rec in cands]

    # A breacher drives into the defense's envelope, so it also has to survive
    # the trip: require at-or-above-median toughness among the candidates.
    tough = [float(getattr(r, "strength", 0) or 0) for _u, r in cands]
    tough = [x for x in tough if x > 0]
    if not tough:
        return [uid for uid, _rec in cands]
    tough.sort()
    mid = tough[len(tough) // 2] if len(tough) % 2 else (
        tough[len(tough) // 2 - 1] + tough[len(tough) // 2]) / 2.0
    return [uid for uid, rec in cands
            if float(getattr(rec, "strength", 0) or 0) >= mid]
    return out
