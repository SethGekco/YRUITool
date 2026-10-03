"""
CategoryTab — the per-TechnoType tab.

Layout:
  * A "Same as X" dropdown at the top. Pick another category to inherit its
    settings; per-setting link checkboxes (checked by default) then appear, and
    ticked settings are disabled and inherited at apply time. "(independent)"
    turns inheritance off.
  * A Ratios grid (Strength, Speed, Build time, Damage/Range/ROF; buildings swap
    Speed for Debris/Adjacent).
  * Separate boxes for Cost (ratio × cost mode + base), Sight (ratio + sight-equal
    + no-weapon fallback), Soylent (units only), and Custom TechLevel.

Ratios (1.0 = unchanged). Linking sources are resolved by the main window.
"""

from PySide6.QtWidgets import (
    QWidget, QGridLayout, QVBoxLayout, QHBoxLayout, QLabel, QDoubleSpinBox,
    QCheckBox, QGroupBox, QComboBox,
)

from ..engine import CategoryRatios

FIELD_LABEL = {
    "strength": "Strength", "speed": "Speed", "buildtime": "Build time",
    "debris": "Debris", "adjacent": "Adjacent",
    "reg_dmg": "Damage (reg)", "eli_dmg": "Damage (elite)",
    "reg_rng": "Range (reg)", "eli_rng": "Range (elite)",
    "reg_rof": "ROF (reg)", "eli_rof": "ROF (elite)",
    "cost": "Cost", "sight": "Sight", "soylent": "Soylent",
}
UNIT_GRID = ["strength", "speed", "buildtime",
             "reg_dmg", "eli_dmg", "reg_rng", "eli_rng", "reg_rof", "eli_rof"]
BUILDING_GRID = ["strength", "buildtime", "debris", "adjacent",
                 "reg_dmg", "eli_dmg", "reg_rng", "eli_rng", "reg_rof", "eli_rof"]


def _spin():
    s = QDoubleSpinBox()
    s.setRange(0.0, 1000.0)
    s.setSingleStep(0.1)
    s.setDecimals(2)
    s.setValue(1.0)
    s.setToolTip("Ratio applied to the vanilla value (1.0 = unchanged).")
    return s


class CategoryTab(QWidget):
    def __init__(self, cat_key, is_building=False, linkable=False,
                 link_label="", garrison=False, parent=None):
        super().__init__(parent)
        self.cat_key = cat_key
        self.is_building = is_building
        self.garrison = garrison
        self.spins = {}          # attr -> QDoubleSpinBox (all ratio fields)
        self.link_checks = {}    # link-id -> QCheckBox

        outer = QVBoxLayout(self)

        # "Same as X" selector.
        top = QHBoxLayout()
        top.addWidget(QLabel("Same as:"))
        self.link_combo = QComboBox()
        self.link_combo.addItem("(independent)", None)
        self.link_combo.currentIndexChanged.connect(self._refresh_enabled)
        top.addWidget(self.link_combo)
        top.addStretch(1)
        outer.addLayout(top)

        # Ratios grid.
        grid_box = QGroupBox("Ratios  (1.0 = unchanged, 2.0 = double, 0.5 = half)")
        grid = QGridLayout(grid_box)
        grid.setColumnStretch(0, 1)
        fields = BUILDING_GRID if is_building else UNIT_GRID
        for row, attr in enumerate(fields):
            grid.addWidget(QLabel(FIELD_LABEL[attr]), row, 0)
            spin = _spin()
            self.spins[attr] = spin
            grid.addWidget(spin, row, 1)
            grid.addWidget(self._link_cb(attr), row, 2)
        outer.addWidget(grid_box)

        outer.addWidget(self._build_cost_box())
        outer.addWidget(self._build_sight_box())
        if not is_building:
            outer.addWidget(self._build_soylent_box())
        else:
            self.soylent_cost_frac = None
        if garrison:
            outer.addWidget(self._build_garrison_box())
        outer.addWidget(self._build_techlevel_box())
        outer.addStretch(1)

        self._refresh_enabled()

    # ── Box builders ─────────────────────────────────────────────────────────
    def _link_cb(self, link_id):
        cb = QCheckBox("inherit")
        cb.setChecked(True)
        cb.setToolTip("Inherit this setting from the 'Same as' category.")
        cb.toggled.connect(self._refresh_enabled)
        cb.setVisible(False)
        self.link_checks[link_id] = cb
        return cb

    def _box_header(self, title, link_id):
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        head = QHBoxLayout()
        head.addStretch(1)
        head.addWidget(self._link_cb(link_id))
        lay.addLayout(head)
        return box, lay

    def _build_cost_box(self):
        box, lay = self._box_header("Cost", "cost")
        row = QHBoxLayout()
        self.spins["cost"] = _spin()
        row.addWidget(QLabel("Cost ratio"))
        row.addWidget(self.spins["cost"])
        row.addWidget(QLabel("×"))
        self.cost_mode = QComboBox()
        self.cost_mode.addItem("Flat", "flat")
        self.cost_mode.addItem("TechLevel", "techlevel")
        if self.is_building:
            self.cost_mode.addItem("Depth", "depth")
        row.addWidget(self.cost_mode)
        row.addWidget(QLabel("from"))
        self.cost_base = QComboBox()
        self.cost_base.addItem("Vanilla", "vanilla")
        self.cost_base.addItem("Current", "current")
        row.addWidget(self.cost_base)
        row.addStretch(1)
        lay.addLayout(row)
        hint = QLabel("Final cost = base × Cost ratio × (mode). Flat uses just the "
                      "ratio; TechLevel/Depth multiply by the unit's tier.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        lay.addWidget(hint)
        return box

    def _build_sight_box(self):
        box, lay = self._box_header("Sight", "sight")
        row = QHBoxLayout()
        self.spins["sight"] = _spin()
        row.addWidget(QLabel("Sight ratio"))
        row.addWidget(self.spins["sight"])
        row.addStretch(1)
        lay.addLayout(row)
        self.sight_equal = QCheckBox("Set Sight equal to weapon range")
        lay.addWidget(self.sight_equal)
        nw = QHBoxLayout()
        nw.addWidget(QLabel("If no weapon:"))
        self.sight_nw_mode = QComboBox()
        self.sight_nw_mode.addItem("leave Sight as-is", "leave")
        self.sight_nw_mode.addItem("multiply Sight by", "multiplier")
        self.sight_nw_mode.addItem("minimum Sight of", "minimum")
        self.sight_nw_mode.currentIndexChanged.connect(self._refresh_enabled)
        nw.addWidget(self.sight_nw_mode)
        self.sight_nw_value = QDoubleSpinBox()
        self.sight_nw_value.setRange(0.0, 100.0)
        self.sight_nw_value.setSingleStep(0.5)
        self.sight_nw_value.setDecimals(2)
        self.sight_nw_value.setValue(1.0)
        nw.addWidget(self.sight_nw_value)
        nw.addStretch(1)
        lay.addLayout(nw)
        return box

    def _build_soylent_box(self):
        box, lay = self._box_header("Soylent", "soylent")
        row = QHBoxLayout()
        self.spins["soylent"] = _spin()
        row.addWidget(QLabel("Soylent ratio"))
        row.addWidget(self.spins["soylent"])
        row.addStretch(1)
        lay.addLayout(row)
        self.soylent_cost_frac = QCheckBox(
            "Soylent = Soylent ratio × final Cost (instead of the vanilla-cost formula)")
        lay.addWidget(self.soylent_cost_frac)
        return box

    def _build_garrison_box(self):
        box = QGroupBox("Garrison weapons (OccupyWeapon / EliteOccupyWeapon)")
        lay = QVBoxLayout(box)
        self.gar_same = QCheckBox("Same as the weapon ratios above")
        self.gar_same.setChecked(True)
        lay.addWidget(self.gar_same)

        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        self.gar_spins = {}
        for row, (attr, label) in enumerate(
                [("dmg", "Damage"), ("rng", "Range"), ("rof", "ROF")]):
            grid.addWidget(QLabel(label), row, 0)
            sp = _spin()
            self.gar_spins[attr] = sp
            grid.addWidget(sp, row, 1)
        lay.addLayout(grid)

        orow = QHBoxLayout()
        orow.addWidget(QLabel("Occupant count ratio (building MaxNumberOccupants):"))
        self.occupant_spin = _spin()
        orow.addWidget(self.occupant_spin)
        orow.addStretch(1)
        lay.addLayout(orow)

        note = QLabel("Garrison weapons default to the unit's normal weapon ratios "
                      "— uncheck to give them their own. The occupant ratio scales "
                      "how many infantry can garrison a structure.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)

        def _upd():
            for sp in self.gar_spins.values():
                sp.setEnabled(not self.gar_same.isChecked())
        self.gar_same.toggled.connect(lambda _c: _upd())
        _upd()
        return box

    def _build_techlevel_box(self):
        box, lay = self._box_header("Custom TechLevel", "techlevel")
        self.tl_recompute = QCheckBox("Recalculate TechLevel from build order")
        self.tl_write = QCheckBox("Write recalculated TechLevel to rules")
        self.tl_use = QCheckBox("Use recalculated TechLevel for cost")
        self.tl_write.setEnabled(False)
        self.tl_use.setEnabled(False)
        self.tl_recompute.toggled.connect(self._refresh_enabled)
        for w in (self.tl_recompute, self.tl_write, self.tl_use):
            lay.addWidget(w)
        return box

    # ── Link options (set by the main window) ────────────────────────────────
    def set_link_options(self, options):
        """options: list of (cat_key, label) for the OTHER categories."""
        current = self.link_combo.currentData()
        self.link_combo.blockSignals(True)
        self.link_combo.clear()
        self.link_combo.addItem("(independent)", None)
        for key, label in options:
            self.link_combo.addItem(f"Same as {label}", key)
        self.set_link_source(current)
        self.link_combo.blockSignals(False)
        self._refresh_enabled()

    def set_link_source(self, cat_key):
        idx = self.link_combo.findData(cat_key)
        self.link_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def link_source_key(self):
        return self.link_combo.currentData()

    # ── Enable/disable + link visibility ─────────────────────────────────────
    def _widgets_for(self, link_id):
        if link_id in self.spins and link_id not in ("cost", "sight", "soylent"):
            return [self.spins[link_id]]
        if link_id == "cost":
            return [self.spins["cost"], self.cost_mode, self.cost_base]
        if link_id == "sight":
            return [self.spins["sight"], self.sight_equal,
                    self.sight_nw_mode, self.sight_nw_value]
        if link_id == "soylent":
            if self.soylent_cost_frac is None:
                return []
            return [self.spins["soylent"], self.soylent_cost_frac]
        if link_id == "techlevel":
            return [self.tl_recompute, self.tl_write, self.tl_use]
        return []

    def _refresh_enabled(self):
        linked = self.link_source_key() is not None
        for lid, cb in self.link_checks.items():
            cb.setVisible(linked)
            inherit = linked and cb.isChecked()
            for w in self._widgets_for(lid):
                w.setEnabled(not inherit)
        # Sub-dependencies (only when that group is editable).
        sight_editable = not (linked and self.link_checks["sight"].isChecked())
        if sight_editable:
            self.sight_nw_value.setEnabled(self.sight_nw_mode.currentData() != "leave")
        tl_editable = not (linked and self.link_checks["techlevel"].isChecked())
        if tl_editable:
            self.tl_write.setEnabled(self.tl_recompute.isChecked())
            self.tl_use.setEnabled(self.tl_recompute.isChecked())

    def _inherits(self, link_id, link_source):
        cb = self.link_checks.get(link_id)
        return link_source is not None and cb is not None and cb.isChecked()

    # ── Build the CategoryRatios ─────────────────────────────────────────────
    def ratios(self, link_source=None):
        r = CategoryRatios()
        for attr, spin in self.spins.items():
            if self._inherits(attr, link_source):
                setattr(r, attr, getattr(link_source, attr))
            else:
                setattr(r, attr, spin.value())

        if self._inherits("cost", link_source):
            r.cost_mode = link_source.cost_mode
            r.cost_base = link_source.cost_base
        else:
            r.cost_mode = self.cost_mode.currentData()
            r.cost_base = self.cost_base.currentData()
            if r.cost_mode == "depth" and not self.is_building:
                r.cost_mode = "flat"

        if self._inherits("sight", link_source):
            r.sight_equal = link_source.sight_equal
            r.sight_no_weapon_mode = link_source.sight_no_weapon_mode
            r.sight_no_weapon_value = link_source.sight_no_weapon_value
        else:
            r.sight_equal = self.sight_equal.isChecked()
            r.sight_no_weapon_mode = self.sight_nw_mode.currentData()
            r.sight_no_weapon_value = self.sight_nw_value.value()

        if self._inherits("soylent", link_source):
            r.soylent_mode = link_source.soylent_mode
        elif self.soylent_cost_frac is not None and self.soylent_cost_frac.isChecked():
            r.soylent_mode = "cost_fraction"

        if self._inherits("techlevel", link_source):
            r.tl_recompute = link_source.tl_recompute
            r.tl_write = link_source.tl_write
            r.tl_use = link_source.tl_use
        else:
            r.tl_recompute = self.tl_recompute.isChecked()
            r.tl_write = r.tl_recompute and self.tl_write.isChecked()
            r.tl_use = r.tl_recompute and self.tl_use.isChecked()

        if self.garrison:
            r.gar_same = self.gar_same.isChecked()
            r.gar_dmg = self.gar_spins["dmg"].value()
            r.gar_rng = self.gar_spins["rng"].value()
            r.gar_rof = self.gar_spins["rof"].value()
        return r

    def occupant_ratio(self):
        return self.occupant_spin.value() if self.garrison else 1.0

    # ── State (blueprints / persistence) ─────────────────────────────────────
    def get_state(self):
        return {
            "same_as": self.link_source_key(),
            "links": {lid: cb.isChecked() for lid, cb in self.link_checks.items()},
            "fields": {attr: spin.value() for attr, spin in self.spins.items()},
            "sight_equal": self.sight_equal.isChecked(),
            "sight_nw_mode": self.sight_nw_mode.currentData(),
            "sight_nw_value": self.sight_nw_value.value(),
            "soylent_cost_frac": (self.soylent_cost_frac.isChecked()
                                  if self.soylent_cost_frac is not None else None),
            "cost_mode": self.cost_mode.currentData(),
            "cost_base": self.cost_base.currentData(),
            "tl_recompute": self.tl_recompute.isChecked(),
            "tl_write": self.tl_write.isChecked(),
            "tl_use": self.tl_use.isChecked(),
            "garrison": ({
                "same": self.gar_same.isChecked(),
                "dmg": self.gar_spins["dmg"].value(),
                "rng": self.gar_spins["rng"].value(),
                "rof": self.gar_spins["rof"].value(),
                "occupant": self.occupant_spin.value(),
            } if self.garrison else None),
        }

    def set_state(self, state):
        for attr, value in state.get("fields", {}).items():
            if attr in self.spins:
                self.spins[attr].setValue(float(value))
        for lid, checked in state.get("links", {}).items():
            if lid in self.link_checks:
                self.link_checks[lid].setChecked(bool(checked))
        if "same_as" in state:
            self.set_link_source(state["same_as"])
        self.sight_equal.setChecked(bool(state.get("sight_equal", False)))
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
        g = state.get("garrison")
        if self.garrison and g:
            self.gar_same.setChecked(bool(g.get("same", True)))
            self.gar_spins["dmg"].setValue(float(g.get("dmg", 1.0)))
            self.gar_spins["rng"].setValue(float(g.get("rng", 1.0)))
            self.gar_spins["rof"].setValue(float(g.get("rof", 1.0)))
            self.occupant_spin.setValue(float(g.get("occupant", 1.0)))
        self._refresh_enabled()

    @staticmethod
    def _set_combo(combo, data):
        idx = combo.findData(data)
        if idx >= 0:
            combo.setCurrentIndex(idx)
