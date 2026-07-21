"""
Per-type stat processing (Pass 1): applies strength/cost/speed/buildtime/soylent
scaling plus regular and elite weapon edits across a list of units or buildings.

The original tool printed progress inline; these take an optional `progress`
callback (`progress(str)`) so the GUI (or a CLI) decides how to surface it.
"""

from .ini_model import get_val
from .operations import (
    apply_tag_ratio, apply_soylent, apply_speed_capped, apply_buildtime,
    apply_debris, apply_adjacent,
)
from .weapons import (
    REGULAR_WEAPON_TAGS, ELITE_WEAPON_TAGS,
    apply_weapon_fields, inject_elite_slots_for_building,
)


def process_unit_stats(lines, section_map, type_list, label, cat_label,
                       strength_ratio, cost_ratio, speed_ratio, buildtime_ratio,
                       reg_dmg, reg_rng, reg_rof,
                       eli_dmg, eli_rng, eli_rof,
                       snapshots, registry, speed_max=None, soylent_ratio=1.0,
                       progress=None, range_threshold=None, do_soylent=True):
    if progress:
        progress(f"  [Pass 1] {label} ({len(type_list)} units)...")
    for t in type_list:
        if strength_ratio != 1.0:
            apply_tag_ratio(lines, section_map, t, "Strength", strength_ratio)
        if cost_ratio != 1.0:
            apply_tag_ratio(lines, section_map, t, "Cost", cost_ratio)
        # Legacy soylent derives from vanilla cost * cost_ratio * soylent_ratio.
        # (The cost-fraction mode is handled by the pipeline after cost scaling.)
        vanilla_cost = get_val(section_map, t, "Cost")
        if do_soylent and vanilla_cost and (cost_ratio != 1.0 or soylent_ratio != 1.0):
            apply_soylent(lines, section_map, t, vanilla_cost, cost_ratio, soylent_ratio)
        if speed_ratio != 1.0 or speed_max is not None:
            apply_speed_capped(lines, section_map, t, speed_ratio, speed_max)
        if buildtime_ratio != 1.0:
            apply_buildtime(lines, section_map, t, buildtime_ratio)

        for tag in REGULAR_WEAPON_TAGS:
            wname = get_val(section_map, t, tag)
            if wname and wname.lower() not in ("none", ""):
                apply_weapon_fields(
                    lines, section_map, wname,
                    reg_dmg, reg_rng, reg_rof,
                    registry, snapshots, "reg", unit_name=t, tag=tag,
                    range_threshold=range_threshold)

        for tag in ELITE_WEAPON_TAGS:
            wname = get_val(section_map, t, tag)
            if wname and wname.lower() not in ("none", ""):
                apply_weapon_fields(
                    lines, section_map, wname,
                    eli_dmg, eli_rng, eli_rof,
                    registry, snapshots, "eli", unit_name=t, tag=tag,
                    range_threshold=range_threshold)


def process_building_stats(lines, section_map, building_list, label,
                           strength_ratio, cost_ratio, buildtime_ratio,
                           debris_ratio, adjacent_ratio,
                           reg_dmg, reg_rng, reg_rof,
                           eli_dmg, eli_rng, eli_rof,
                           snapshots, registry, progress=None,
                           range_threshold=None):
    if progress:
        progress(f"  [Pass 1] {label} ({len(building_list)} buildings)...")
    for t in building_list:
        if strength_ratio != 1.0:
            apply_tag_ratio(lines, section_map, t, "Strength", strength_ratio)
        if cost_ratio != 1.0:
            apply_tag_ratio(lines, section_map, t, "Cost", cost_ratio)
        if buildtime_ratio != 1.0:
            apply_buildtime(lines, section_map, t, buildtime_ratio)
        if debris_ratio != 1.0:
            apply_debris(lines, section_map, t, debris_ratio)
        if adjacent_ratio != 1.0:
            apply_adjacent(lines, section_map, t, adjacent_ratio)

        for tag in REGULAR_WEAPON_TAGS:
            wname = get_val(section_map, t, tag)
            if wname and wname.lower() not in ("none", ""):
                apply_weapon_fields(
                    lines, section_map, wname,
                    reg_dmg, reg_rng, reg_rof,
                    registry, snapshots, "reg", unit_name=t, tag=tag,
                    range_threshold=range_threshold)

        if eli_dmg != 1.0 or eli_rng != 1.0 or eli_rof != 1.0:
            inject_elite_slots_for_building(
                lines, section_map, t, snapshots, registry,
                eli_dmg, eli_rng, eli_rof, range_threshold=range_threshold)
