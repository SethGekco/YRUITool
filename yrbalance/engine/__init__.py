"""I/O-free core: INI parsing, section mapping, and the balance edit engine."""

from .config import (
    ModConfig,
    Category,
    DEFAULT_CATEGORIES,
    DEFAULT_COMPOSITE_KEYWORDS,
)
from .selectors import (
    build_category_lists,
    resolve_scope,
    available_keywords,
)
from .sections import (
    make_safe_clone_name,
    get_section_raw_lines,
    append_raw_section,
    set_key_in_appended_section,
    remove_key_from_section,
    new_registration_state,
    register_in_list,
)
from .ini_model import (
    parse_ini_lines,
    build_section_map,
    get_val,
    line_ending,
    modify_line,
    append_line_after_section_header,
    scale_int,
    scale_from_vanilla,
    format_edited_value,
    parse_vanilla_from_line,
    parse_last_value_from_line,
    parse_last_ratio_from_line,
    get_current_field_value_from_line,
)
from .report import (
    SUMMARY_SENTINEL,
    SUMMARY_END,
    detect_rerun,
    write_summary_block,
    ObservationReport,
    smart_apply_tag,
)
from .ticket import (
    TICKET_VERSION,
    ticket_default_path,
    file_hash,
    next_session_id,
    write_ticket,
    read_ticket,
)
from .operations import (
    set_rerun_context,
    set_category,
    reset_session_state,
    apply_tag_ratio,
    apply_speed_capped,
    apply_buildtime,
    apply_debris,
    apply_adjacent,
    apply_soylent,
    apply_soylent_fraction,
    get_current_range_from_lines,
    apply_sight_equal_to_range,
    do_sight_pass,
)
from .typelists import (
    parse_type_list,
    split_vehicles_naval,
    split_buildings_defensive,
)
from .weapons import (
    REGULAR_WEAPON_TAGS,
    ELITE_WEAPON_TAGS,
    ALL_WEAPON_TAGS,
    REG_TO_ELITE_TAG,
    WeaponRegistry,
    snapshot_weapon,
    build_weapon_snapshots,
    make_safe_weapon_name,
    clone_weapon_section,
    redirect_unit_weapon_tag,
    apply_weapon_fields,
    inject_elite_slots_for_building,
)
from .stats import (
    process_unit_stats,
    process_building_stats,
)
from .techlevel import (
    valid_tl,
    building_name_hint,
    aggregate,
    weight_to_techlevel,
    parse_generic_prereqs,
    collect_prereq_buildings,
    expand_prereq_csv,
    compute_prereq_weight,
    compute_building_depths,
    compute_techlevels,
    write_techlevels,
    get_effective_tl,
    get_tl_store,
    reset_tl_store,
)
from .costscaling import (
    get_three_costs,
    write_cost,
    apply_flat_cost_scaling,
    apply_tl_cost_scaling,
    apply_depth_cost_scaling,
)
from .formula import (
    evaluate,
    looks_like_formula,
    referenced_names,
    strip_quotes,
    FormulaError,
)
from .script import (
    parse_script,
    load_script_file,
    ModifierScript,
    TargetBlock,
    TagOp,
    ScriptError,
)
from .filters import (
    Condition,
    Filter,
    FilterContext,
    parse_condition,
    evaluate as evaluate_condition,
    filter_technos,
)
from .runner import (
    run_script,
    RunResult,
    RunError,
    config_keywords,
)
from .pipeline import (
    CategoryRatios,
    BalanceSpec,
    RunSummary,
    run_balance,
)
from .restore import restore_vanilla
from .history import scan_changes, revert_change
from .backup import make_backup, list_backups, default_backup_dir
from .lint import lint_ai, has_ai_sections, format_findings, LintFinding
from .obtain import (
    classify_obtainability, Obtainability,
    OB_BUILDABLE, OB_CRATE, OB_OTHER, OB_DUMMY, OB_CIVILIAN, OB_UNKNOWN,
)
from .portmod import (
    build_port_plan, classify_new_objects, compute_factors, plan_to_blueprint,
    apply_port_plan, clean_owner, clean_prereq, target_countries,
    PortPlan, PortObject, StatFactor, StatChange,
)

__all__ = [
    # config
    "ModConfig", "Category", "DEFAULT_CATEGORIES", "DEFAULT_COMPOSITE_KEYWORDS",
    # selectors
    "build_category_lists", "resolve_scope", "available_keywords",
    # sections
    "make_safe_clone_name", "get_section_raw_lines", "append_raw_section",
    "set_key_in_appended_section", "remove_key_from_section",
    "new_registration_state", "register_in_list",
    # ini_model
    "parse_ini_lines", "build_section_map", "get_val", "line_ending",
    "modify_line", "append_line_after_section_header", "scale_int",
    "scale_from_vanilla", "format_edited_value", "parse_vanilla_from_line",
    "parse_last_value_from_line", "parse_last_ratio_from_line",
    "get_current_field_value_from_line",
    # report
    "SUMMARY_SENTINEL", "SUMMARY_END", "detect_rerun", "write_summary_block",
    "ObservationReport", "smart_apply_tag",
    # ticket
    "TICKET_VERSION", "ticket_default_path", "file_hash", "next_session_id",
    "write_ticket", "read_ticket",
    # operations
    "set_rerun_context", "set_category", "reset_session_state",
    "apply_tag_ratio", "apply_speed_capped", "apply_buildtime", "apply_debris",
    "apply_adjacent", "apply_soylent", "apply_soylent_fraction",
    "get_current_range_from_lines", "apply_sight_equal_to_range", "do_sight_pass",
    # typelists
    "parse_type_list", "split_vehicles_naval", "split_buildings_defensive",
    # weapons
    "REGULAR_WEAPON_TAGS", "ELITE_WEAPON_TAGS", "ALL_WEAPON_TAGS",
    "REG_TO_ELITE_TAG", "WeaponRegistry", "snapshot_weapon",
    "build_weapon_snapshots", "make_safe_weapon_name", "clone_weapon_section",
    "redirect_unit_weapon_tag", "apply_weapon_fields",
    "inject_elite_slots_for_building",
    # stats
    "process_unit_stats", "process_building_stats",
    # techlevel
    "valid_tl", "building_name_hint", "aggregate", "weight_to_techlevel",
    "parse_generic_prereqs", "collect_prereq_buildings", "expand_prereq_csv",
    "compute_prereq_weight", "compute_building_depths", "compute_techlevels",
    "write_techlevels", "get_effective_tl", "get_tl_store", "reset_tl_store",
    # costscaling
    "get_three_costs", "write_cost", "apply_flat_cost_scaling",
    "apply_tl_cost_scaling", "apply_depth_cost_scaling",
    # formula
    "evaluate", "looks_like_formula", "referenced_names", "strip_quotes",
    "FormulaError",
    # script
    "parse_script", "load_script_file", "ModifierScript", "TargetBlock",
    "TagOp", "ScriptError",
    # filters
    "Condition", "Filter", "FilterContext", "parse_condition",
    "evaluate_condition", "filter_technos",
    # runner
    "run_script", "RunResult", "RunError", "config_keywords",
    # pipeline
    "CategoryRatios", "BalanceSpec", "RunSummary", "run_balance",
    # restore / history / backup
    "restore_vanilla", "scan_changes", "revert_change",
    "make_backup", "list_backups", "default_backup_dir",
    # lint
    "lint_ai", "has_ai_sections", "format_findings", "LintFinding",
    # portmod
    "build_port_plan", "classify_new_objects", "compute_factors",
    "plan_to_blueprint", "apply_port_plan", "clean_owner", "clean_prereq",
    "target_countries", "PortPlan", "PortObject", "StatFactor", "StatChange",
]
