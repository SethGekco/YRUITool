"""
Mod configuration: every value that used to be hardcoded to Dying Earth lives
here as a setting, so the tool works on any YR/RA2 mod.

`ModConfig` carries the ID prefixes for generated index entries, the "unbuildable"
prerequisite, platform/host naming, side-name labels, and name-length caps. Two
factory profiles are provided:

  * ModConfig.default()      — neutral generic values (recommended for new mods).
  * ModConfig.dying_earth()  — the exact tokens the original tool used, so its
                               output can be reproduced byte-for-byte.

The category model (which index section each category reads, and which section-name
keyword targets it) also lives here and is consumed by `selectors`.
"""

from dataclasses import dataclass, field


# ── Category model ───────────────────────────────────────────────────────────
# Each category has: a key, the index section it is parsed from, a display label,
# and the $KEYWORD used to target it in scope strings. Vehicle/Building are parsed
# from one index section then split (naval / defensive) — see selectors.py.

@dataclass(frozen=True)
class Category:
    key: str            # 'inf', 'veh', 'nav', 'air', 'bld', 'def'
    index_section: str  # e.g. 'InfantryTypes'
    label: str          # e.g. 'Infantry'
    keyword: str        # e.g. '$INFANTRY'
    is_building: bool = False


DEFAULT_CATEGORIES = [
    Category("inf", "InfantryTypes", "Infantry",  "$INFANTRY"),
    Category("veh", "VehicleTypes",  "Vehicle",   "$VEHICLE"),
    Category("nav", "VehicleTypes",  "Naval",     "$NAVAL"),
    Category("air", "AircraftTypes", "Aircraft",  "$AIRCRAFT"),
    Category("bld", "BuildingTypes", "Building",  "$BUILDING",  is_building=True),
    Category("def", "BuildingTypes", "Defensive", "$DEFENSE",   is_building=True),
]

# Composite keywords expand to several category keys.
DEFAULT_COMPOSITE_KEYWORDS = {
    "$ALL":       ["inf", "veh", "nav", "air", "bld", "def"],
    "$UNIT":      ["inf", "veh", "nav", "air"],
    "$STRUCTURE": ["bld", "def"],
    "$GROUND":    ["inf", "veh"],
}


@dataclass
class ModConfig:
    """All mod-specific settings. Use the factory classmethods for presets."""

    tool_name: str = "YRBalanceTool"

    # Index-list key prefixes for generated entries, e.g. "CLONE0003=NAME".
    # The game only cares that these keys are unique within their list section.
    infantry_clone_prefix: str = "GENI"    # -> [InfantryTypes]
    vehicle_clone_prefix: str = "GENV"     # -> [VehicleTypes]
    attachment_id_prefix: str = "GENA"     # -> [AttachmentTypes]

    # A structure the human player cannot build; assigning it as a Prerequisite
    # makes a unit AI-only / unbuildable. Mod-specific — must exist in the rules.
    unbuildable_prereq: str = "CAPARK01"

    # Generated-section naming.
    platform_prefix: str = "ATPLAT_"
    host_suffix: str = "_HOST"
    ai_host_suffix: str = "_AI_HOST"
    max_name_len: int = 24

    # Side internal-name -> friendly label (used for platform naming and reports).
    # Vanilla YR uses GDI/Nod/ThirdSide internally for Allied/Soviet/Yuri.
    side_names: dict = field(
        default_factory=lambda: {"GDI": "Allied", "Nod": "Soviet", "ThirdSide": "Yuri"})

    # Category model (see above). Copied so instances can customize freely.
    categories: list = field(default_factory=lambda: list(DEFAULT_CATEGORIES))
    composite_keywords: dict = field(
        default_factory=lambda: dict(DEFAULT_COMPOSITE_KEYWORDS))

    # ── Lookups ──────────────────────────────────────────────────────────────
    def category_by_key(self, key):
        for c in self.categories:
            if c.key == key:
                return c
        return None

    def category_by_keyword(self, keyword):
        kw = keyword.strip().upper()
        for c in self.categories:
            if c.keyword.upper() == kw:
                return c
        return None

    # ── Factory profiles ─────────────────────────────────────────────────────
    @classmethod
    def default(cls):
        """Neutral generic profile — recommended starting point for a new mod."""
        return cls()

    @classmethod
    def dying_earth(cls):
        """Reproduce the original Dying Earth tool's exact tokens."""
        return cls(
            tool_name="Dying Earth Balance Tool",
            infantry_clone_prefix="DEGB",
            vehicle_clone_prefix="DEAT",
            attachment_id_prefix="DE",
            unbuildable_prereq="CAPARK01",
            platform_prefix="ATPLAT_",
            host_suffix="_HOST",
            ai_host_suffix="_AI_HOST",
        )
