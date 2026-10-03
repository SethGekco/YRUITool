"""
High-level balance pipeline: turn a per-category ratio spec into the actual
Pass-1 stat/weapon edits, TechLevel recomputation, cost scaling, and the Pass-2
sight pass, in the correct order.

This is the orchestration the GUI and a future CLI both call. It is I/O-free
(operates on lines/section_map) and Qt-free, so it is unit-testable on its own.
Custom modifier scripts are applied separately via runner.run_script.

Ordering within run_balance:
  1. optional TechLevel recompute (depth tree for buildings, prereq weight for
     units) + optional write-to-rules — must precede cost-by-TechLevel.
  2. Pass 1 stats + weapons (flat Cost applied here only in 'flat' cost mode).
  3. Cost scaling for categories in 'techlevel'/'depth' cost mode.
  4. Pass 2 sight.
"""

from dataclasses import dataclass, field

from .config import ModConfig
from .ini_model import get_val
from .selectors import build_category_lists
from .operations import (
    reset_session_state, do_sight_pass, apply_soylent_fraction, apply_tag_ratio,
)
from .weapons import build_weapon_snapshots, WeaponRegistry
from .stats import process_unit_stats, process_building_stats
from .techlevel import (
    valid_tl, parse_generic_prereqs, compute_techlevels, write_techlevels,
    compute_building_depths, reset_tl_store,
)
from .costscaling import apply_tl_cost_scaling, apply_depth_cost_scaling


@dataclass
class CategoryRatios:
    """Ratio inputs for one category (1.0 = unchanged). Buildings ignore the
    unit-only fields (speed/soylent) and use debris/adjacent instead."""
    strength: float = 1.0
    cost: float = 1.0
    speed: float = 1.0
    buildtime: float = 1.0
    soylent: float = 1.0
    reg_dmg: float = 1.0
    reg_rng: float = 1.0
    reg_rof: float = 1.0
    eli_dmg: float = 1.0
    eli_rng: float = 1.0
    eli_rof: float = 1.0
    sight: float = 1.0
    sight_equal: bool = False
    # Fallback when sight_equal is on but a unit has no weapon.
    sight_no_weapon_mode: str = "leave"   # 'leave' | 'multiplier' | 'minimum'
    sight_no_weapon_value: float = 1.0
    speed_max: float = None
    debris: float = 1.0
    adjacent: float = 1.0
    # Soylent: 'legacy' = vanilla_cost * cost_ratio * soylent; 'cost_fraction' =
    # (final Cost) * soylent, applied after cost scaling.
    soylent_mode: str = "legacy"
    # Garrison (OccupyWeapon/EliteOccupyWeapon) ratios. gar_same=True means they
    # scale with the unit's normal weapons; otherwise these custom ratios apply.
    gar_same: bool = True
    gar_dmg: float = 1.0
    gar_rng: float = 1.0
    gar_rof: float = 1.0
    # Cost scaling: 'flat' uses the Cost ratio directly; 'techlevel' scales by
    # TechLevel x ratio; 'depth' (buildings) scales by (depth+1) x ratio.
    cost_mode: str = "flat"
    cost_base: str = "vanilla"   # 'vanilla' | 'current' | 'pre_f'
    # Custom TechLevel recompute from build order.
    tl_recompute: bool = False
    tl_write: bool = False       # write the recomputed TechLevel into the rules
    tl_use: bool = False         # use the recomputed TechLevel for cost scaling

    def is_noop(self, is_building):
        if self.cost_mode != "flat" or self.tl_recompute:
            return False
        base = (self.strength == self.cost == self.buildtime == self.reg_dmg ==
                self.reg_rng == self.reg_rof == self.eli_dmg == self.eli_rng ==
                self.eli_rof == 1.0)
        if is_building:
            extra = self.debris == self.adjacent == 1.0
        else:
            gar_noop = self.gar_same or (self.gar_dmg == self.gar_rng
                                         == self.gar_rof == 1.0)
            extra = (self.speed == self.soylent == 1.0 and self.speed_max is None
                     and self.soylent_mode == "legacy" and gar_noop)
        sight_noop = (not self.sight_equal) and self.sight == 1.0
        return base and extra and sight_noop


@dataclass
class BalanceSpec:
    """A full balance request: category key -> CategoryRatios, plus globals."""
    categories: dict = field(default_factory=dict)
    # If set, weapons whose vanilla Range is below this are left unscaled on Range
    # (protects melee / very short-range weapons across every category).
    range_threshold: float = None
    # Scale MaxNumberOccupants on all garrisonable buildings (1.0 = unchanged).
    occupant_ratio: float = 1.0


@dataclass
class RunSummary:
    per_category: dict = field(default_factory=dict)   # key -> techno count processed
    weapon_conflicts: int = 0
    weapon_conflict_text: str = ""
    techlevels_written: int = 0
    costs_scaled: int = 0


def _default_get_weight(section_map):
    """Prereq weight = each building's vanilla TechLevel (the CLI 'v' default)."""
    def gw(b_upper):
        tl = valid_tl(get_val(section_map, b_upper, "TechLevel", ""))
        return float(tl) if tl else 0.0
    return gw


def run_balance(lines, section_map, spec, config=None, progress=None):
    """Apply a BalanceSpec to lines/section_map. Returns a RunSummary."""
    if config is None:
        config = ModConfig.default()

    reset_session_state()
    reset_tl_store()
    cat_lists = build_category_lists(section_map, config)
    snapshots = build_weapon_snapshots(section_map, list(cat_lists.values()))
    registry = WeaponRegistry()
    summary = RunSummary()

    # ── Step 1: TechLevel recompute (before any cost-by-TechLevel) ───────────
    tl_cats = {k: r for k, r in spec.categories.items() if r.tl_recompute}
    if tl_cats:
        generics = parse_generic_prereqs(section_map)
        type_map = {k: cat_lists.get(k, []) for k in tl_cats}
        modes = {k: {"write_new": r.tl_write, "use_new": r.tl_use}
                 for k, r in tl_cats.items()}
        compute_techlevels(
            section_map, type_map, modes, use_depth=True, generics=generics,
            get_weight=_default_get_weight(section_map))
        summary.techlevels_written = write_techlevels(lines, section_map)
        if progress:
            progress(f"  TechLevel: recomputed {len(type_map)} categories, "
                     f"wrote {summary.techlevels_written}.")

    # Precompute building depths once if any building category uses depth cost.
    depths = None
    if any(r.cost_mode == "depth" for r in spec.categories.values()):
        all_blds = cat_lists.get("bld", []) + cat_lists.get("def", [])
        depths = compute_building_depths(
            section_map, all_blds, parse_generic_prereqs(section_map))
        depths.pop("_generic_depths", None)

    # ── Step 2: Pass 1 stats + weapons ───────────────────────────────────────
    for key, r in spec.categories.items():
        cat = config.category_by_key(key)
        if cat is None:
            continue
        type_list = cat_lists.get(key, [])
        if not type_list or r.is_noop(cat.is_building):
            summary.per_category[key] = 0
            continue
        # In a scaling cost mode, cost is applied in step 3, not the flat pass.
        stat_cost = r.cost if r.cost_mode == "flat" else 1.0

        if cat.is_building:
            process_building_stats(
                lines, section_map, type_list, cat.label,
                r.strength, stat_cost, r.buildtime, r.debris, r.adjacent,
                r.reg_dmg, r.reg_rng, r.reg_rof,
                r.eli_dmg, r.eli_rng, r.eli_rof,
                snapshots, registry, progress=progress,
                range_threshold=spec.range_threshold)
        else:
            gd = None if r.gar_same else r.gar_dmg
            gr = None if r.gar_same else r.gar_rng
            gf = None if r.gar_same else r.gar_rof
            process_unit_stats(
                lines, section_map, type_list, cat.label, cat.key,
                r.strength, stat_cost, r.speed, r.buildtime,
                r.reg_dmg, r.reg_rng, r.reg_rof,
                r.eli_dmg, r.eli_rng, r.eli_rof,
                snapshots, registry,
                speed_max=r.speed_max, soylent_ratio=r.soylent, progress=progress,
                range_threshold=spec.range_threshold,
                do_soylent=(r.soylent_mode == "legacy"),
                gar_dmg=gd, gar_rng=gr, gar_rof=gf)
        summary.per_category[key] = len(type_list)

    # ── Step 3: cost scaling (TechLevel / depth modes) ───────────────────────
    for key, r in spec.categories.items():
        cat = config.category_by_key(key)
        if cat is None or r.cost_mode == "flat":
            continue
        type_list = cat_lists.get(key, [])
        if not type_list:
            continue
        if r.cost_mode == "depth" and cat.is_building and depths is not None:
            summary.costs_scaled += apply_depth_cost_scaling(
                lines, section_map, type_list, depths,
                r.cost, r.cost_base, min_depth=1, tiered_rates=None)
        elif r.cost_mode == "techlevel":
            tl_source = "effective" if r.tl_use else "vanilla"
            summary.costs_scaled += apply_tl_cost_scaling(
                lines, section_map, type_list, r.cost, r.cost_base, tl_source)

    # ── Step 3b: cost-fraction soylent (after final costs are written) ───────
    for key, r in spec.categories.items():
        cat = config.category_by_key(key)
        if cat is None or cat.is_building or r.soylent_mode != "cost_fraction":
            continue
        for name in cat_lists.get(key, []):
            apply_soylent_fraction(lines, section_map, name, r.soylent)

    # ── Step 3c: occupant ratio (scale MaxNumberOccupants on buildings) ──────
    if spec.occupant_ratio != 1.0:
        for name in cat_lists.get("bld", []) + cat_lists.get("def", []):
            apply_tag_ratio(lines, section_map, name, "MaxNumberOccupants",
                            spec.occupant_ratio)

    # ── Step 4: sight (after all weapon ranges are finalized) ────────────────
    for key, r in spec.categories.items():
        cat = config.category_by_key(key)
        if cat is None:
            continue
        if r.sight_equal or r.sight != 1.0:
            do_sight_pass(lines, section_map, cat_lists.get(key, []),
                          r.sight_equal, r.sight,
                          no_weapon_mode=r.sight_no_weapon_mode,
                          no_weapon_value=r.sight_no_weapon_value)

    summary.weapon_conflicts = len(registry.conflicts)
    summary.weapon_conflict_text = registry.format_summary()
    return summary
