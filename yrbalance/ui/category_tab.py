"""
CategoryTab — the per-TechnoType tab: a grid of ratio fields (1.0 = unchanged).

The Naval tab is linkable: every field gets a "same as Vehicles" checkbox that,
when ticked, disables the field and inherits the Vehicle tab's value at apply time.
Building/Defensive tabs use a building-specific field set (no speed/soylent; adds
debris/adjacent).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QGridLayout, QVBoxLayout, QLabel, QDoubleSpinBox, QCheckBox,
    QGroupBox,
)

from ..engine import CategoryRatios

# (attr, label) — attr matches CategoryRatios fields.
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
        outer.addStretch(1)

    def ratios(self, link_source=None):
        """
        Build a CategoryRatios. For linked fields (Naval), pull the value from
        link_source (a CategoryRatios) when its checkbox is ticked.
        """
        r = CategoryRatios()
        for attr, spin in self.spins.items():
            if (self.linkable and link_source is not None
                    and self.links[attr].isChecked()):
                setattr(r, attr, getattr(link_source, attr))
            else:
                setattr(r, attr, spin.value())
        # Buildings don't carry speed/soylent; leave them at default.
        if (self.linkable and link_source is not None
                and self.sight_equal_link is not None
                and self.sight_equal_link.isChecked()):
            r.sight_equal = link_source.sight_equal
        else:
            r.sight_equal = self.sight_equal.isChecked()
        return r
