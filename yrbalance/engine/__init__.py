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
    clean_value, num_value, numbered_entries,
)
from .fsutil import sibling_file
from .includes import (
    resolve_includes,
    load_with_includes,
    include_graph,
    flatten_graph,
    has_includes,
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
    classify_obtainability, Obtainability, ai_unit_ids, ALL_OBTAIN_CLASSES,
    OB_NORMAL, OB_SPECIAL, OB_CRATE, OB_AI_ONLY, OB_CIVILIAN, OB_DUMMY,
    OB_UNKNOWN,
)
from .mixlib import (
    mix_id, MixFile, write_mix, MixError, MixEncrypted, MixEntry,
    is_vanilla_mix, is_stock_mix, stock_mix_names,
    VANILLA_MIX_NAMES, STOCK_MIX_NAMES, KNOWN_FRAMEWORK_FILES,
    build_media_catalog, resolve_media, pack_media_mixes, find_in_mixes,
    collect_asset_ids, build_vanilla_asset_ids, vanilla_asset_ids, classify_missing,
    parse_audio_idx, read_audio_sample,
)
from .encyclopedia import (
    build_mod_index, search_indexes, unit_report, export_unit, effective_tags,
    ModIndex, UnitEntry, AssetRef,
)
from .tags import (
    load_tags, set_unit_tags, add_unit_tag, remove_unit_tag,
    bulk_add_tag, bulk_remove_tag, all_tags,
)
from .bookmarks import (
    load_bookmarks, add_bookmark, remove_bookmark, is_bookmarked,
)
from .modhistory import (
    section_fingerprints, diff_fingerprints, load_history,
    record_load, record_changes, update_snapshot, unit_history,
    format_timeline, clear_history,
)
from .portmod import (
    build_port_plan, classify_new_objects, compute_factors, plan_to_blueprint,
    plan_to_files, apply_port_plan, clean_owner, clean_prereq, target_countries,
    PortPlan, PortObject, StatFactor, StatChange,
)
from .portai import (
    build_ai_import_plan, apply_ai_import, AIImportPlan, AIEntry,
)
from .ailib import (
    load_library, save_library, add_entry, remove_entry, set_entry_meta,
    rename_entry, set_bookmark, reorder_entries,
    load_collections as ai_load_collections,
    save_collections as ai_save_collections,
    create_collection as ai_create_collection,
    delete_collection as ai_delete_collection,
    rename_collection as ai_rename_collection,
    add_to_collection as ai_add_to_collection,
    remove_from_collection as ai_remove_from_collection,
    library_path, extract_block, extract_bundle, build_pull_plan, apply_pull,
    LibEntry, LibBlock, AiCollection, PullPlan, PullItem,
)
from .aiwork import (
    working_path, working_exists, resolve_vanilla_aimd, knowledge_vanilla_aimd,
    new_blank, new_from_vanilla, new_from_file, load_working, export_working,
)
from .images import (
    parse_shp, load_palette, grayscale_palette, frame_to_rgba, best_frame_index,
    house_remap_palette, HOUSE_COLORS, parse_pcx, ShpImage, ShpFrame, ImageError,
)
from .audio import (
    decode_ima_adpcm, decode_ima_adpcm_blocks, to_wav, decode_bag_sample,
    bag_sample_to_wav, AudioSample,
)
from .vxl import (
    parse_vxl, render_vxl, render_parts, voxel_count,
    VxlModel, VxlSection, VxlError,
)
from .vpl import parse_vpl, vpl_pages, Vpl, VplError
from .hva import parse_hva, Hva, HvaError
from .aimd import (
    sibling_aimd, load_aimd, ai_registry, decode_trigger, trigger_fields,
    entry_text, references_to, replace_trigger, replace_section,
)
from .library import (
    load_catalog, save_catalog, library_catalog_path,
    add_source, remove_source, set_source_summary,
    create_collection, delete_collection, rename_collection, rename_source,
    add_to_collection,
    remove_from_collection, collection_keys, scan_items, scan_source,
    balance_culture, describe_culture, conversion_factors, convert_unit_stats,
    convert_weapon_stats, culture_statfactors,
    LibrarySource, Collection, LibraryItem, SourceScan,
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
    "clean_value", "num_value", "numbered_entries", "sibling_file",
    # includes
    "resolve_includes", "load_with_includes", "include_graph", "flatten_graph",
    "has_includes",
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
    "plan_to_blueprint", "plan_to_files", "apply_port_plan", "clean_owner",
    "clean_prereq",
    "target_countries", "PortPlan", "PortObject", "StatFactor", "StatChange",
    # portai
    "build_ai_import_plan", "apply_ai_import", "AIImportPlan", "AIEntry",
    # ailib
    "load_library", "save_library", "add_entry", "remove_entry", "set_entry_meta",
    "rename_entry", "set_bookmark", "reorder_entries",
    "ai_load_collections", "ai_save_collections", "ai_create_collection",
    "ai_delete_collection", "ai_rename_collection", "ai_add_to_collection",
    "ai_remove_from_collection",
    "library_path", "extract_block", "extract_bundle", "build_pull_plan",
    "apply_pull", "LibEntry", "LibBlock", "AiCollection", "PullPlan", "PullItem",
    # aiwork (AI Builder working file)
    "working_path", "working_exists", "resolve_vanilla_aimd",
    "knowledge_vanilla_aimd", "new_blank", "new_from_vanilla", "new_from_file",
    "load_working", "export_working",
    # images (SHP / palette)
    "parse_shp", "load_palette", "grayscale_palette", "frame_to_rgba",
    "best_frame_index", "house_remap_palette", "HOUSE_COLORS", "parse_pcx",
    "ShpImage", "ShpFrame", "ImageError",
    # audio (bag IMA-ADPCM → WAV)
    "decode_ima_adpcm", "decode_ima_adpcm_blocks", "to_wav", "decode_bag_sample",
    "bag_sample_to_wav",
    "AudioSample",
    # vxl (voxel parse + isometric render)
    "parse_vxl", "render_vxl", "render_parts", "voxel_count",
    "VxlModel", "VxlSection", "VxlError",
    # vpl (voxel palette lookup — game-accurate lighting)
    "parse_vpl", "vpl_pages", "Vpl", "VplError",
    # hva (voxel animation transforms)
    "parse_hva", "Hva", "HvaError",
    # aimd (AI registry browsing)
    "sibling_aimd", "load_aimd", "ai_registry", "decode_trigger",
    "trigger_fields", "entry_text", "references_to", "replace_trigger",
    "replace_section",
    # library (asset/AI catalog)
    "load_catalog", "save_catalog", "library_catalog_path",
    "add_source", "remove_source", "set_source_summary",
    "create_collection", "delete_collection", "rename_collection",
    "rename_source", "add_to_collection",
    "remove_from_collection", "collection_keys", "scan_items", "scan_source",
    "balance_culture", "describe_culture", "conversion_factors",
    "convert_unit_stats", "convert_weapon_stats", "culture_statfactors",
    "LibrarySource", "Collection", "LibraryItem", "SourceScan",
    # obtain
    "classify_obtainability", "Obtainability", "ai_unit_ids",
    "ALL_OBTAIN_CLASSES",
    "OB_NORMAL", "OB_SPECIAL", "OB_CRATE", "OB_AI_ONLY", "OB_CIVILIAN",
    "OB_DUMMY", "OB_UNKNOWN",
    # mixlib
    "mix_id", "MixFile", "write_mix", "MixError", "MixEncrypted", "MixEntry",
    "is_vanilla_mix", "is_stock_mix", "stock_mix_names",
    "VANILLA_MIX_NAMES", "STOCK_MIX_NAMES", "KNOWN_FRAMEWORK_FILES",
    "build_media_catalog", "resolve_media", "pack_media_mixes", "find_in_mixes",
    "collect_asset_ids", "build_vanilla_asset_ids", "vanilla_asset_ids",
    "classify_missing", "parse_audio_idx", "read_audio_sample",
    # encyclopedia
    "build_mod_index", "search_indexes", "unit_report", "export_unit",
    "effective_tags", "ModIndex", "UnitEntry", "AssetRef",
    # tags
    "load_tags", "set_unit_tags", "add_unit_tag", "remove_unit_tag",
    "bulk_add_tag", "bulk_remove_tag", "all_tags",
    # bookmarks
    "load_bookmarks", "add_bookmark", "remove_bookmark", "is_bookmarked",
    # modhistory
    "section_fingerprints", "diff_fingerprints", "load_history",
    "record_load", "record_changes", "update_snapshot", "unit_history",
    "format_timeline", "clear_history",
]
