"""
Scope resolution: turn a category key or a scope string of section-name keywords
into concrete lists of TechnoType section names.

A scope string is a comma-separated mix of:
  * category keywords  — $INFANTRY, $VEHICLE, $NAVAL, $AIRCRAFT, $BUILDING, $DEFENSE
  * composite keywords — $ALL, $UNIT, $STRUCTURE, $GROUND
  * literal section names — e.g. E1, GAWEAP (included as-is if present in the file)

This is what the GUI's category dropdown and a preset's "scope" field resolve
through, so a custom modifier can target "$VEHICLE, $NAVAL" or a specific unit.
"""

from .config import ModConfig
from .typelists import (
    parse_type_list, split_vehicles_naval, split_buildings_defensive,
)


def build_category_lists(section_map, config=None):
    """
    Return {cat_key: [type_name, ...]} for every category in the config, applying
    the naval and defensive splits. Vehicle/Naval come from VehicleTypes; Building/
    Defensive from BuildingTypes.
    """
    if config is None:
        config = ModConfig.default()

    lists = {}
    # Parse each distinct index section once.
    index_cache = {}

    def index_list(section):
        if section not in index_cache:
            index_cache[section] = parse_type_list(section_map, section)
        return index_cache[section]

    # First pass: raw per-index lists.
    veh_index = None
    bld_index = None
    for cat in config.categories:
        if cat.key in ("veh", "nav"):
            veh_index = cat.index_section
        elif cat.key in ("bld", "def"):
            bld_index = cat.index_section

    veh_all = index_list(veh_index) if veh_index else []
    bld_all = index_list(bld_index) if bld_index else []
    veh_land, naval = split_vehicles_naval(section_map, veh_all)
    bld_regular, bld_defensive = split_buildings_defensive(section_map, bld_all)

    for cat in config.categories:
        if cat.key == "veh":
            lists["veh"] = veh_land
        elif cat.key == "nav":
            lists["nav"] = naval
        elif cat.key == "bld":
            lists["bld"] = bld_regular
        elif cat.key == "def":
            lists["def"] = bld_defensive
        else:
            lists[cat.key] = index_list(cat.index_section)
    return lists


def resolve_scope(section_map, scope, config=None):
    """
    Resolve a scope (category key, single keyword, or comma-separated scope string)
    into a de-duplicated, order-preserving list of type-section names.

    Accepts:
      * a bare category key ('inf') or keyword ('$INFANTRY'),
      * composite keywords ('$ALL', '$UNIT', ...),
      * literal section names (kept only if present in section_map),
      * any comma-separated combination of the above.
    """
    if config is None:
        config = ModConfig.default()

    cat_lists = build_category_lists(section_map, config)
    result = []
    seen = set()

    def add(name):
        low = name.lower()
        if low not in seen:
            seen.add(low)
            result.append(name)

    def add_cat(cat_key):
        for name in cat_lists.get(cat_key, []):
            add(name)

    for raw in str(scope).split(","):
        token = raw.strip()
        if not token:
            continue
        upper = token.upper()
        # Accept keywords written without the leading '$' (a common slip):
        # 'VEHICLE' / 'ALL' resolve like '$VEHICLE' / '$ALL'. The literal-section
        # fallback below still handles any real section of that name.
        kw = upper if upper.startswith("$") else "$" + upper

        # Composite keyword?
        if kw in config.composite_keywords:
            for cat_key in config.composite_keywords[kw]:
                add_cat(cat_key)
            continue
        # Category keyword?
        cat = config.category_by_keyword(kw)
        if cat is not None:
            add_cat(cat.key)
            continue
        # Bare category key?
        if config.category_by_key(token.lower()) is not None:
            add_cat(token.lower())
            continue
        # Literal section name — include only if it exists.
        if section_map.get(token.lower()):
            add(token)

    return result


def available_keywords(config=None):
    """Return all keyword strings a scope may use (categories + composites)."""
    if config is None:
        config = ModConfig.default()
    return [c.keyword for c in config.categories] + list(config.composite_keywords.keys())
