"""
GlobalTab — settings that span more than one category.

  * Global ratios (Strength / Cost / Speed) that COMPOUND with each TechnoType
    tab's own ratios, applied only to the categories whose checkbox is ticked.
  * Weapon range protection (skip Range scaling for short-range weapons).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QCheckBox,
    QDoubleSpinBox, QGroupBox,
)

# Category key -> tab label used for the "apply global to" checkboxes.
GLOBAL_CATEGORIES = [
    ("inf", "Infantry"), ("veh", "Vehicles"), ("nav", "Naval"),
    ("air", "Aircraft"), ("bld", "Structures"), ("def", "Defenses"),
]


def _ratio_spin():
    s = QDoubleSpinBox()
    s.setRange(0.0, 1000.0)
    s.setSingleStep(0.1)
    s.setDecimals(2)
    s.setValue(1.0)
    return s


class GlobalTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)

        # Global ratios (compound with each tab).
        gbox = QGroupBox("Global ratios — multiply on top of each tab's own value")
        grid = QGridLayout(gbox)
        grid.setColumnStretch(0, 1)
        self.g_spins = {}
        for row, (attr, label) in enumerate([("strength", "Strength"),
                                             ("cost", "Cost"), ("speed", "Speed")]):
            grid.addWidget(QLabel(label), row, 0)
            spin = _ratio_spin()
            grid.addWidget(spin, row, 1)
            self.g_spins[attr] = spin
        outer.addWidget(gbox)

        # Which categories the global ratios apply to.
        abox = QGroupBox("Apply global ratios to")
        arow = QHBoxLayout(abox)
        self.cat_checks = {}
        for key, label in GLOBAL_CATEGORIES:
            cb = QCheckBox(label)
            cb.setChecked(True)
            self.cat_checks[key] = cb
            arow.addWidget(cb)
        arow.addStretch(1)
        outer.addWidget(abox)

        # Weapon range protection.
        rbox = QGroupBox("Weapon range protection")
        rlay = QVBoxLayout(rbox)
        self.range_protect = QCheckBox(
            "Protect short-range weapons from Range scaling")
        self.range_protect.setChecked(True)
        rlay.addWidget(self.range_protect)
        rrow = QHBoxLayout()
        rrow.addWidget(QLabel("Skip Range scaling when vanilla Range is below:"))
        self.range_threshold = QDoubleSpinBox()
        self.range_threshold.setRange(0.0, 50.0)
        self.range_threshold.setSingleStep(0.25)
        self.range_threshold.setDecimals(2)
        self.range_threshold.setValue(3.0)
        rrow.addWidget(self.range_threshold)
        rrow.addStretch(1)
        rlay.addLayout(rrow)
        hint = QLabel("Protects melee / very short-range weapons (attack dogs, etc.). "
                      "Damage and ROF still scale.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        rlay.addWidget(hint)
        self.range_protect.toggled.connect(self.range_threshold.setEnabled)
        outer.addWidget(rbox)
        outer.addStretch(1)

    # ── Values ───────────────────────────────────────────────────────────────
    def global_ratios(self):
        return {attr: spin.value() for attr, spin in self.g_spins.items()}

    def applies_to(self, cat_key):
        cb = self.cat_checks.get(cat_key)
        return cb.isChecked() if cb else False

    def range_threshold_value(self):
        return self.range_threshold.value() if self.range_protect.isChecked() else None

    # ── State (blueprints) ───────────────────────────────────────────────────
    def get_state(self):
        return {
            "ratios": {k: s.value() for k, s in self.g_spins.items()},
            "apply_to": {k: cb.isChecked() for k, cb in self.cat_checks.items()},
            "range_protect": self.range_protect.isChecked(),
            "range_threshold": self.range_threshold.value(),
        }

    def set_state(self, state):
        for k, v in state.get("ratios", {}).items():
            if k in self.g_spins:
                self.g_spins[k].setValue(float(v))
        for k, v in state.get("apply_to", {}).items():
            if k in self.cat_checks:
                self.cat_checks[k].setChecked(bool(v))
        self.range_protect.setChecked(bool(state.get("range_protect", True)))
        if "range_threshold" in state:
            self.range_threshold.setValue(float(state["range_threshold"]))
