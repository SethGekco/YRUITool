"""
High-level balance pipeline: turn a per-category ratio spec into the actual
Pass-1 stat/weapon edits plus the Pass-2 sight pass, in the correct order.

This is the orchestration the GUI and a future CLI both call. It is I/O-free
(operates on lines/section_map) and Qt-free, so it is unit-testable on its own.
The UI's job is only to build a BalanceSpec from its widgets and call run_balance;
custom modifier scripts are applied separately via runner.run_script.
"""

from dataclasses import dataclass, field

from .config import ModConfig
from .selectors import build_category_lists
from .operations import reset_session_state, do_sight_pass
from .weapons import build_weapon_snapshots, WeaponRegistry
from .stats import process_unit_stats, process_building_stats


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
    speed_max: float = None
    debris: float = 1.0
    adjacent: float = 1.0

    def is_noop(self, is_building):
        base = (self.strength == self.cost == self.buildtime == self.reg_dmg ==
                self.reg_rng == self.reg_rof == self.eli_dmg == self.eli_rng ==
                self.eli_rof == 1.0)
        if is_building:
            extra = self.debris == self.adjacent == 1.0
        else:
            extra = (self.speed == self.soylent == 1.0 and self.speed_max is None)
        sight_noop = (not self.sight_equal) and self.sight == 1.0
        return base and extra and sight_noop


@dataclass
class BalanceSpec:
    """A full balance request: category key -> CategoryRatios."""
    categories: dict = field(default_factory=dict)


@dataclass
class RunSummary:
    per_category: dict = field(default_factory=dict)   # key -> techno count processed
    weapon_conflicts: int = 0
    weapon_conflict_text: str = ""


def run_balance(lines, section_map, spec, config=None, progress=None):
    """
    Apply a BalanceSpec to lines/section_map. Pass 1 does stats + weapons; Pass 2
    does sight. Returns a RunSummary. Vanilla weapon snapshots are captured before
    any edit so ratios always derive from vanilla.
    """
    if config is None:
        config = ModConfig.default()

    reset_session_state()
    cat_lists = build_category_lists(section_map, config)
    snapshots = build_weapon_snapshots(section_map, list(cat_lists.values()))
    registry = WeaponRegistry()
    summary = RunSummary()

    # ── Pass 1: stats + weapons ──────────────────────────────────────────────
    for key, r in spec.categories.items():
        cat = config.category_by_key(key)
        if cat is None:
            continue
        type_list = cat_lists.get(key, [])
        if not type_list or r.is_noop(cat.is_building):
            summary.per_category[key] = 0
            continue

        if cat.is_building:
            process_building_stats(
                lines, section_map, type_list, cat.label,
                r.strength, r.cost, r.buildtime, r.debris, r.adjacent,
                r.reg_dmg, r.reg_rng, r.reg_rof,
                r.eli_dmg, r.eli_rng, r.eli_rof,
                snapshots, registry, progress=progress)
        else:
            process_unit_stats(
                lines, section_map, type_list, cat.label, cat.key,
                r.strength, r.cost, r.speed, r.buildtime,
                r.reg_dmg, r.reg_rng, r.reg_rof,
                r.eli_dmg, r.eli_rng, r.eli_rof,
                snapshots, registry,
                speed_max=r.speed_max, soylent_ratio=r.soylent, progress=progress)
        summary.per_category[key] = len(type_list)

    # ── Pass 2: sight (after all weapon ranges are finalized) ────────────────
    for key, r in spec.categories.items():
        cat = config.category_by_key(key)
        if cat is None:
            continue
        if r.sight_equal or r.sight != 1.0:
            do_sight_pass(lines, section_map, cat_lists.get(key, []),
                          r.sight_equal, r.sight)

    summary.weapon_conflicts = len(registry.conflicts)
    summary.weapon_conflict_text = registry.format_summary()
    return summary
