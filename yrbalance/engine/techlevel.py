"""
Custom TechLevel system: derive a TechLevel for every TechnoType from its actual
build order rather than the vanilla value.

Buildings get a depth from a topological sort through the tech tree, resolving the
game's generic prerequisite tokens (POWER, BARRACKS, PROC, RADAR, TECH, FACTORY,
read from [General] PrerequisiteXxx=). Generic tokens resolve partially — as soon
as any member is known. Non-building types get a weight by summing/maxing the
weights of their prerequisites through the same machinery.

Ported from the monolith. The interactive prompts (`_prompt_*`) are intentionally
left behind for the UI layer; what lives here is the pure computation plus two
driver functions (`compute_techlevels` / `write_techlevels`) split out of the
original `run_custom_techlevel` orchestrator. `print()` diagnostics became an
optional `progress` callback.
"""

from collections import Counter

from .ini_model import get_val, modify_line


# TechLevel results for the current session, keyed by section name:
#   _tl_store[name] = {"old": int, "new": int, "write_new": bool, "use_new": bool}
# Populated by compute_techlevels(); read by get_effective_tl() and cost scaling.
_tl_store = {}


def get_tl_store():
    """Return the current session's TechLevel store (mutable module state)."""
    return _tl_store


def reset_tl_store():
    """Clear the TechLevel store. Call before a fresh custom-TechLevel run."""
    _tl_store.clear()


# ── Small helpers ────────────────────────────────────────────────────────────

def valid_tl(val_str):
    """Return int TechLevel if 1-10, else None."""
    try:
        v = int(str(val_str).split(";")[0].strip())
        return v if 1 <= v <= 10 else None
    except (ValueError, TypeError):
        return None


def building_name_hint(section_map, b_upper):
    """Return a '[SECTION] (Name=...)' display hint for a building."""
    name_tag = get_val(section_map, b_upper, "Name", "")
    if name_tag:
        return f"[{b_upper}] ({name_tag})"
    return f"[{b_upper}]"


def aggregate(values, mode):
    """Aggregate a list of floats by mode: highest/lowest/average/mode_val."""
    if not values:
        return 0.0
    if mode == "highest":
        return max(values)
    if mode == "lowest":
        return min(values)
    if mode == "average":
        return sum(values) / len(values)
    if mode == "mode_val":
        return float(Counter(values).most_common(1)[0][0])
    return max(values)


def weight_to_techlevel(w):
    """Convert a float weight to a clamped int TechLevel (1-10)."""
    return max(1, min(10, round(w)))


# ── Generic prerequisite tokens ──────────────────────────────────────────────

def parse_generic_prereqs(section_map):
    """
    Read [General] for all PrerequisiteXxx= tags. Returns
    {GENERIC_TOKEN_UPPER: [building_name_upper, ...]}, e.g.
    {"BARRACKS": ["NAHAND","GAPILE","YABRCK"], "POWER": [...], ...}.
    """
    generics = {}
    general = section_map.get("general", {})
    for k_lower, (_, kr, vr) in general.get("keys", {}).items():
        if not k_lower.startswith("prerequisite"):
            continue
        suffix = k_lower[len("prerequisite"):]
        if not suffix:
            continue  # skip plain "Prerequisite="
        token = suffix.upper()
        members = [b.split(";")[0].strip().upper()
                   for b in vr.split(",") if b.split(";")[0].strip()]
        if members:
            generics[token] = members
    return generics


def collect_prereq_buildings(section_map, type_lists, generics):
    """
    Scan all types for Prerequisite= / Prerequisite.ListX= / PrerequisiteOverride=
    buildings. Returns (sorted real-buildings list, set of generic tokens found).
    """
    real_buildings = set()
    generic_tokens_found = set()
    for type_list in type_lists:
        for name in type_list:
            if valid_tl(get_val(section_map, name, "TechLevel")) is None:
                continue

            def scan_csv(csv_str):
                for b in csv_str.split(","):
                    b = b.strip().upper()
                    if not b:
                        continue
                    if b in generics:
                        generic_tokens_found.add(b)
                    else:
                        # Real building, or unknown (treated as real).
                        real_buildings.add(b)

            scan_csv(get_val(section_map, name, "Prerequisite", ""))
            try:
                n_lists = int(get_val(section_map, name, "Prerequisite.Lists", "0"))
            except ValueError:
                n_lists = 0
            for i in range(1, n_lists + 1):
                scan_csv(get_val(section_map, name, f"Prerequisite.List{i}", ""))
            scan_csv(get_val(section_map, name, "PrerequisiteOverride", ""))
    return sorted(real_buildings), generic_tokens_found


def expand_prereq_csv(csv_str, generics, exclude_if_generic_present=True):
    """
    Expand a comma-separated prerequisite string into upper-case tokens. Generic
    tokens are kept as-is (caller resolves them); real buildings kept. If a generic
    token is present, real buildings that are members of any present generic are
    dropped (the generic subsumes them).
    """
    parts = [b.strip().upper() for b in csv_str.split(",") if b.strip()]
    if not parts:
        return []
    present_generics = {p for p in parts if p in generics}
    if not present_generics or not exclude_if_generic_present:
        return parts
    subsumed = set()
    for tok in present_generics:
        subsumed.update(generics[tok])
    return [p for p in parts if p in present_generics or p not in subsumed]


# ── Prerequisite weight (non-building types) ─────────────────────────────────

def compute_prereq_weight(section_map, name, get_weight, combo_mode,
                          list_mode, override_mode, generics):
    """
    Compute the total prerequisite weight for a single TechnoType. Generic tokens
    are resolved via get_weight; real buildings subsumed by a present generic are
    dropped. combo_mode: 'sum'|'highest'. list_mode: 'highest'|'lowest'.
    override_mode: 'prereq'|'override'|'highest'|'lowest'.
    """
    def list_weight(csv_str):
        tokens = expand_prereq_csv(csv_str, generics)
        if not tokens:
            return 0.0
        ws = [get_weight(t) for t in tokens]
        return sum(ws) if combo_mode == "sum" else max(ws)

    prereq_w = list_weight(get_val(section_map, name, "Prerequisite", ""))

    try:
        n_lists = int(get_val(section_map, name, "Prerequisite.Lists", "0"))
    except ValueError:
        n_lists = 0

    lists_w = None
    if n_lists > 0:
        lws = [list_weight(get_val(section_map, name, f"Prerequisite.List{i}", ""))
               for i in range(1, n_lists + 1)]
        if lws:
            lists_w = max(lws) if list_mode == "highest" else min(lws)

    if lists_w is not None:
        base_w = max(prereq_w, lists_w) if list_mode == "highest" else min(prereq_w, lists_w)
    else:
        base_w = prereq_w

    po_str = get_val(section_map, name, "PrerequisiteOverride", "")
    if po_str and valid_tl(get_val(section_map, name, "TechLevel", "")):
        po_w = list_weight(po_str)
        if override_mode == "prereq":
            final_w = base_w
        elif override_mode == "override":
            final_w = po_w
        elif override_mode == "highest":
            final_w = max(base_w, po_w)
        else:
            final_w = min(base_w, po_w)
    else:
        final_w = base_w
    return final_w


# ── Building depth (topological sort) ────────────────────────────────────────

def compute_building_depths(section_map, building_list, generics=None,
                            generic_agg="lowest", token_agg_overrides=None,
                            progress=None):
    """
    Compute tech-tree depth for each building via topological sort, resolving
    generic prerequisite tokens iteratively (partial resolution: a token resolves
    as soon as any member is known). Returns {name_lower: depth}, plus a
    "_generic_depths" entry mapping resolved token depths.
    """
    if generics is None:
        generics = {}
    if token_agg_overrides is None:
        token_agg_overrides = {}

    def get_prereq_tokens(name):
        tokens = []
        tokens.extend(expand_prereq_csv(get_val(section_map, name, "Prerequisite", ""), generics))
        try:
            n_lists = int(get_val(section_map, name, "Prerequisite.Lists", "0"))
        except ValueError:
            n_lists = 0
        list_token_sets = []
        for i in range(1, n_lists + 1):
            pl = get_val(section_map, name, f"Prerequisite.List{i}", "")
            list_token_sets.append(expand_prereq_csv(pl, generics))
        if list_token_sets:
            tokens = list_token_sets[0]
        return list(set(tokens))

    depths = {}
    generic_depths = {}

    # A building is generic-dependent if it or any building in its prereq chain
    # (transitively) references a generic token — those wait for phase 2.
    def has_generic_dep_transitive(name, visited=None):
        if visited is None:
            visited = set()
        if name in visited:
            return False
        visited.add(name)
        for t in get_prereq_tokens(name):
            if t in generics:
                return True
            if section_map.get(t.lower()) and has_generic_dep_transitive(t, visited):
                return True
        return False

    pure_buildings = []
    generic_dependent = []
    for name in building_list:
        if has_generic_dep_transitive(name):
            generic_dependent.append(name)
        else:
            pure_buildings.append(name)

    generic_dep_lower = {n.lower() for n in generic_dependent}
    visiting = set()

    # Phase 1: resolve pure buildings recursively.
    def get_depth_pure(name_lower):
        if name_lower in depths:
            return depths[name_lower]
        if name_lower in visiting:
            return 0  # cycle
        if name_lower in generic_dep_lower:
            return None  # resolve in phase 2
        visiting.add(name_lower)
        tokens = get_prereq_tokens(name_lower)
        if not tokens:
            depths[name_lower] = 0
        else:
            prereq_depths = []
            for t in tokens:
                if t in generics:
                    continue
                if not section_map.get(t.lower()):
                    prereq_depths.append(0)  # unknown = anchor
                else:
                    d = get_depth_pure(t.lower())
                    if d is not None:
                        prereq_depths.append(d)
            depths[name_lower] = max(prereq_depths) + 1 if prereq_depths else 0
        visiting.discard(name_lower)
        return depths[name_lower]

    for name in pure_buildings:
        get_depth_pure(name.lower())
    for name in building_list:
        if name.lower() not in depths and name.lower() not in generic_dep_lower:
            get_depth_pure(name.lower())

    # Phase 2: iteratively resolve generics and dependent buildings.
    max_iterations = len(generics) + len(generic_dependent) + 10
    for _ in range(max_iterations):
        progressed = False

        for tok, members in generics.items():
            if tok in generic_depths:
                continue
            member_depths = [float(depths[m.lower()]) for m in members
                             if depths.get(m.lower()) is not None]
            if member_depths:  # resolve as soon as ANY member is known
                agg = token_agg_overrides.get(tok, generic_agg)
                generic_depths[tok] = int(round(aggregate(member_depths, agg)))
                progressed = True

        for name in generic_dependent:
            if name.lower() in depths:
                continue
            prereq_depths = []
            all_resolved = True
            for t in get_prereq_tokens(name):
                if t in generics:
                    if t not in generic_depths:
                        all_resolved = False
                        break
                    prereq_depths.append(float(generic_depths[t]))
                else:
                    d = depths.get(t.lower())
                    if d is None:
                        if not section_map.get(t.lower()):
                            prereq_depths.append(0.0)  # unknown anchor
                        else:
                            all_resolved = False
                            break
                    else:
                        prereq_depths.append(float(d))
            if all_resolved:
                depths[name.lower()] = (max(int(d) for d in prereq_depths) + 1
                                        if prereq_depths else 0)
                progressed = True

        if not progressed:
            break

    unresolved = [n for n in generic_dependent if n.lower() not in depths]
    if unresolved:
        if progress:
            progress(f"  [!] Could not resolve depth for: {unresolved}")
            progress(f"      Assigning depth 1 as fallback.")
        for n in unresolved:
            depths[n.lower()] = 1

    depths["_generic_depths"] = generic_depths
    return depths


# ── Compute + write (extracted from run_custom_techlevel) ────────────────────

def compute_techlevels(section_map, type_map, modes, *, use_depth,
                       generics=None, combo_mode="sum", list_mode="highest",
                       override_mode="highest", get_weight=None,
                       generic_depth_agg="lowest", generic_depth_ov=None):
    """
    Compute a new TechLevel for every type in type_map and populate the module
    TechLevel store. Returns the store.

    type_map:  {category: [type_name, ...]}; building categories are 'bld'/'def'.
    modes:     {category: {"write_new": bool, "use_new": bool}}.
    use_depth: True to use the building depth tree for 'bld'/'def' categories.
    get_weight: callable(building_upper) -> float, for the prereq-weight path.
    """
    reset_tl_store()
    if get_weight is None:
        get_weight = lambda b: 0.0

    bld_depths = None
    if use_depth:
        all_blds = type_map.get("bld", []) + type_map.get("def", [])
        bld_depths = compute_building_depths(
            section_map, all_blds, generics, generic_depth_agg, generic_depth_ov)
        bld_depths.pop("_generic_depths", None)

    for cat, tlist in type_map.items():
        is_bld = cat in ("bld", "def")
        for name in tlist:
            old_tl = valid_tl(get_val(section_map, name, "TechLevel", ""))
            if old_tl is None:
                continue
            if is_bld and bld_depths is not None:
                new_tl = weight_to_techlevel(float(bld_depths.get(name.lower(), 0)))
            else:
                w = compute_prereq_weight(section_map, name, get_weight,
                                          combo_mode, list_mode, override_mode,
                                          generics or {})
                new_tl = weight_to_techlevel(w)
            mode = modes.get(cat, {"write_new": False, "use_new": False})
            _tl_store[name] = {"old": old_tl, "new": new_tl,
                               "write_new": mode["write_new"],
                               "use_new": mode["use_new"]}
    return _tl_store


def write_techlevels(lines, section_map, store=None):
    """Write TechLevel= lines for store entries flagged write_new. Returns count."""
    if store is None:
        store = _tl_store
    written = 0
    for name, entry in store.items():
        if not entry["write_new"]:
            continue
        s = section_map.get(name.lower())
        if not s:
            continue
        tl_e = s["keys"].get("techlevel")
        if not tl_e:
            continue
        li, kr, _ = tl_e
        modify_line(lines, li, kr,
                    f"{entry['new']}; last_value={entry['old']}"
                    f" ; method=depth ; vanilla={entry['old']}")
        written += 1
    return written


def get_effective_tl(section_map, name, store=None):
    """
    Return the TechLevel to use for tool calculations this run:
      1. store entry (custom TechLevel ran this session),
      2. else the current written value on the TechLevel line,
      3. else '1'.
    Returns the CURRENT value, not vanilla.
    """
    if store is None:
        store = _tl_store
    entry = store.get(name)
    if entry:
        return str(entry["new"] if entry["use_new"] else entry["old"])
    return get_val(section_map, name, "TechLevel", "1")
