"""
CategoryTab — the per-TechnoType tab: a grid of ratio fields (1.0 = unchanged),
plus a Cost-scaling + Custom-TechLevel group.

The Naval tab is linkable: every ratio field gets a "same as Vehicles" checkbox
that, when ticked, disables the field and inherits the Vehicle tab's value at apply
time. The Cost/TechLevel group has one group-level "same as Vehicles" link on Naval.
Building/Defensive tabs use a building-specific field set (no speed/soylent; adds
debris/adjacent) and enable the depth cost mode.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QGridLayout, QVBoxLayout, QHBoxLayout, QLabel, QDoubleSpinBox,
    QCheckBox, QGroupBox, QComboBox,
)

from ..engine import CategoryRatios

UNIT_FIELDS = [
    ("strength", "Strength"), ("cost", "Cost"), ("speed", "Speed"),
    ("buildtime", "Build time"), ("soylent", "Soylent"),
    ("reg_dmg", "Damage (reg)"), ("eli_dmg", "Damage (elite)"),
    ("reg_rng", "Range (reg)"), ("eli_rng", "Range (elite)"),
    ("reg_rof", "ROF (reg)"), ("eli_rof", "ROF (elite)"),
    ("sight", "Sight"),
]
BUILDING_FIELDS = [
    ("strength", "Strength"), ("cost", "Cost"), ("buildtime", "Build time"),
    ("debris", "Debris"), ("adjacent", "Adjacent"),
    ("reg_dmg", "Damage (reg)"), ("eli_dmg", "Damage (elite)"),
    ("reg_rng", "Range (reg)"), ("eli_rng", "Range (elite)"),
    ("reg_rof", "ROF (reg)"), ("eli_rof", "ROF (elite)"),
    ("sight", "Sight"),
]


def _make_spin():
    s = QDoubleSpinBox()
    s.setRange(0.0, 1000.0)
    s.setSingleStep(0.1)
    s.setDecimals(2)
    s.setValue(1.0)
    s.setToolTip("Ratio applied to the vanilla value (1.0 = unchanged).")
    return s


class CategoryTab(QWidget):
    def __init__(self, cat_key, is_building=False, linkable=False,
                 link_label="Vehicles", parent=None):
        super().__init__(parent)
        self.cat_key = cat_key
        self.is_building = is_building
        self.linkable = linkable
        self.spins = {}
        self.links = {}

        outer = QVBoxLayout(self)
        box = QGroupBox("Ratios  (1.0 = unchanged, 2.0 = double, 0.5 = half)")
        grid = QGridLayout(box)
        grid.setColumnStretch(0, 1)

        fields = BUILDING_FIELDS if is_building else UNIT_FIELDS
        for row, (attr, label) in enumerate(fields):
            grid.addWidget(QLabel(label), row, 0)
            spin = _make_spin()
            grid.addWidget(spin, row, 1)
            self.spins[attr] = spin
            if linkable:
                cb = QCheckBox(f"same as {link_label}")
                cb.setChecked(True)
                spin.setEnabled(False)
                cb.toggled.connect(lambda checked, s=spin: s.setEnabled(not checked))
                grid.addWidget(cb, row, 2)
                self.links[attr] = cb
        outer.addWidget(box)

        # Sight = weapon range (a mode, not a ratio).
        sight_row = QGridLayout()
        self.sight_equal = QCheckBox("Set Sight equal to weapon range")
        sight_row.addWidget(self.sight_equal, 0, 0)
        if linkable:
            self.sight_equal_link = QCheckBox(f"same as {link_label}")
            self.sight_equal_link.setChecked(True)
            self.sight_equal.setEnabled(False)
            self.sight_equal_link.toggled.connect(
                lambda checked: self.sight_equal.setEnabled(not checked))
            sight_row.addWidget(self.sight_equal_link, 0, 2)
        else:
            self.sight_equal_link = None
        sight_row.setColumnStretch(1, 1)
        outer.addLayout(sight_row)

        # Sight fallback when a unit has no weapon (sight-equal mode only).
        nw_row = QHBoxLayout()
        nw_row.addWidget(QLabel("If no weapon:"))
        self.sight_nw_mode = QComboBox()
        self.sight_nw_mode.addItem("leave Sight as-is", "leave")
        self.sight_nw_mode.addItem("multiply Sight by", "multiplier")
        self.sight_nw_mode.addItem("minimum Sight of", "minimum")
        nw_row.addWidget(self.sight_nw_mode)
        self.sight_nw_value = QDoubleSpinBox()
        self.sight_nw_value.setRange(0.0, 100.0)
        self.sight_nw_value.setSingleStep(0.5)
        self.sight_nw_value.setDecimals(2)
        self.sight_nw_value.setValue(1.0)
        self.sight_nw_value.setEnabled(False)  # default mode is "leave"
        self.sight_nw_mode.currentIndexChanged.connect(
            lambda: self.sight_nw_value.setEnabled(
                self.sight_nw_mode.currentData() != "leave"))
        nw_row.addWidget(self.sight_nw_value)
        nw_row.addStretch(1)
        outer.addLayout(nw_row)

        # Soylent-as-fraction-of-final-cost (units only).
        if not is_building:
            self.soylent_cost_frac = QCheckBox(
                "Soylent = the Soylent value above × final Cost "
                "(instead of the vanilla-cost formula)")
            outer.addWidget(self.soylent_cost_frac)
        else:
            self.soylent_cost_frac = None

        outer.addWidget(self._build_advanced(link_label))
        outer.addStretch(1)

    def _build_advanced(self, link_label):
        box = QGroupBox("Cost scaling and TechLevel")
        lay = QVBoxLayout(box)

        # Group-level "same as Vehicles" link (Naval only).
        self.adv_link = None
        if self.linkable:
            self.adv_link = QCheckBox(f"same as {link_label}")
            self.adv_link.setChecked(True)
            self.adv_link.toggled.connect(self._set_adv_enabled)
            lay.addWidget(self.adv_link)

        cost_row = QHBoxLayout()
        cost_row.addWidget(QLabel("Cost mode:"))
        self.cost_mode = QComboBox()
        self.cost_mode.addItem("Flat (use Cost ratio)", "flat")
        self.cost_mode.addItem("× TechLevel", "techlevel")
        if self.is_building:
            self.cost_mode.addItem("× Depth", "depth")
        cost_row.addWidget(self.cost_mode, 1)
        cost_row.addWidget(QLabel("from:"))
        self.cost_base = QComboBox()
        self.cost_base.addItem("Vanilla", "vanilla")
        self.cost_base.addItem("Current", "current")
        cost_row.addWidget(self.cost_base)
        lay.addLayout(cost_row)

        self.tl_recompute = QCheckBox("Recalculate TechLevel from build order")
        self.tl_write = QCheckBox("Write recalculated TechLevel to rules")
        self.tl_use = QCheckBox("Use recalculated TechLevel for cost")
        self.tl_write.setEnabled(False)
        self.tl_use.setEnabled(False)
        self.tl_recompute.toggled.connect(self.tl_write.setEnabled)
        self.tl_recompute.toggled.connect(self.tl_use.setEnabled)
        for w in (self.tl_recompute, self.tl_write, self.tl_use):
            lay.addWidget(w)

        self._adv_widgets = [self.cost_mode, self.cost_base, self.tl_recompute,
                             self.tl_write, self.tl_use]
        if self.adv_link is not None:
            self._set_adv_enabled(True)
        return box

    def _set_adv_enabled(self, linked):
        for w in self._adv_widgets:
            w.setEnabled(not linked)
        if not linked:
            # Restore the recompute-gated dependency when unlinking.
            self.tl_write.setEnabled(self.tl_recompute.isChecked())
            self.tl_use.setEnabled(self.tl_recompute.isChecked())

    def _adv_linked(self):
        return self.adv_link is not None and self.adv_link.isChecked()

    def ratios(self, link_source=None):
        """Build a CategoryRatios. Linked fields (Naval) inherit from link_source."""
        r = CategoryRatios()
        for attr, spin in self.spins.items():
            if (self.linkable and link_source is not None
                    and self.links[attr].isChecked()):
                setattr(r, attr, getattr(link_source, attr))
            else:
                setattr(r, attr, spin.value())

        sight_linked = (self.linkable and link_source is not None
                        and self.sight_equal_link is not None
                        and self.sight_equal_link.isChecked())
        if sight_linked:
            r.sight_equal = link_source.sight_equal
            r.sight_no_weapon_mode = link_source.sight_no_weapon_mode
            r.sight_no_weapon_value = link_source.sight_no_weapon_value
        else:
            r.sight_equal = self.sight_equal.isChecked()
            r.sight_no_weapon_mode = self.sight_nw_mode.currentData()
            r.sight_no_weapon_value = self.sight_nw_value.value()

        if self.soylent_cost_frac is not None and self.soylent_cost_frac.isChecked():
            r.soylent_mode = "cost_fraction"

        # Cost scaling + TechLevel group.
        if link_source is not None and self._adv_linked():
            r.cost_mode = link_source.cost_mode
            r.cost_base = link_source.cost_base
            r.tl_recompute = link_source.tl_recompute
            r.tl_write = link_source.tl_write
            r.tl_use = link_source.tl_use
        else:
            r.cost_mode = self.cost_mode.currentData()
            r.cost_base = self.cost_base.currentData()
            r.tl_recompute = self.tl_recompute.isChecked()
            r.tl_write = self.tl_recompute.isChecked() and self.tl_write.isChecked()
            r.tl_use = self.tl_recompute.isChecked() and self.tl_use.isChecked()
        # Depth mode only valid for buildings; fall back to flat elsewhere.
        if r.cost_mode == "depth" and not self.is_building:
            r.cost_mode = "flat"
        return r

    # ── Exact widget state (for blueprints / persistence) ────────────────────
    def get_state(self):
        return {
            "fields": {attr: spin.value() for attr, spin in self.spins.items()},
            "links": {attr: cb.isChecked() for attr, cb in self.links.items()},
            "sight_equal": self.sight_equal.isChecked(),
            "sight_equal_link": (self.sight_equal_link.isChecked()
                                 if self.sight_equal_link is not None else None),
            "sight_nw_mode": self.sight_nw_mode.currentData(),
            "sight_nw_value": self.sight_nw_value.value(),
            "soylent_cost_frac": (self.soylent_cost_frac.isChecked()
                                  if self.soylent_cost_frac is not None else None),
            "cost_mode": self.cost_mode.currentData(),
            "cost_base": self.cost_base.currentData(),
            "tl_recompute": self.tl_recompute.isChecked(),
            "tl_write": self.tl_write.isChecked(),
            "tl_use": self.tl_use.isChecked(),
            "adv_link": (self.adv_link.isChecked()
                         if self.adv_link is not None else None),
        }

    def set_state(self, state):
        for attr, value in state.get("fields", {}).items():
            if attr in self.spins:
                self.spins[attr].setValue(float(value))
        for attr, linked in state.get("links", {}).items():
            if attr in self.links:
                self.links[attr].setChecked(bool(linked))
        self.sight_equal.setChecked(bool(state.get("sight_equal", False)))
        if self.sight_equal_link is not None and state.get("sight_equal_link") is not None:
            self.sight_equal_link.setChecked(bool(state["sight_equal_link"]))
        if "sight_nw_mode" in state:
            self._set_combo(self.sight_nw_mode, state["sight_nw_mode"])
        if "sight_nw_value" in state:
            self.sight_nw_value.setValue(float(state["sight_nw_value"]))
        if self.soylent_cost_frac is not None and state.get("soylent_cost_frac") is not None:
            self.soylent_cost_frac.setChecked(bool(state["soylent_cost_frac"]))
        self._set_combo(self.cost_mode, state.get("cost_mode", "flat"))
        self._set_combo(self.cost_base, state.get("cost_base", "vanilla"))
        self.tl_recompute.setChecked(bool(state.get("tl_recompute", False)))
        self.tl_write.setChecked(bool(state.get("tl_write", False)))
        self.tl_use.setChecked(bool(state.get("tl_use", False)))
        if self.adv_link is not None and state.get("adv_link") is not None:
            self.adv_link.setChecked(bool(state["adv_link"]))

    @staticmethod
    def _set_combo(combo, data):
        idx = combo.findData(data)
        if idx >= 0:
            combo.setCurrentIndex(idx)
