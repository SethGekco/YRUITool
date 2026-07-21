"""
GlobalTab — settings that span more than one category.

Currently: the short-range-weapon protection (skip Range scaling for weapons whose
vanilla Range is below a threshold), which applies across every category. This is
the natural home for future cross-cutting controls.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QDoubleSpinBox,
    QGroupBox,
)


class GlobalTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)

        box = QGroupBox("Weapon range protection")
        lay = QVBoxLayout(box)
        self.range_protect = QCheckBox(
            "Protect short-range weapons from Range scaling")
        self.range_protect.setChecked(True)
        lay.addWidget(self.range_protect)

        row = QHBoxLayout()
        row.addWidget(QLabel("Skip Range scaling when vanilla Range is below:"))
        self.range_threshold = QDoubleSpinBox()
        self.range_threshold.setRange(0.0, 50.0)
        self.range_threshold.setSingleStep(0.25)
        self.range_threshold.setDecimals(2)
        self.range_threshold.setValue(3.0)
        row.addWidget(self.range_threshold)
        row.addStretch(1)
        lay.addLayout(row)

        hint = QLabel("Protects melee and very short-range weapons (attack dogs at "
                      "Range 2.25, etc.) from being stretched by Range ratios. "
                      "Damage and ROF still scale.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        lay.addWidget(hint)

        self.range_protect.toggled.connect(self.range_threshold.setEnabled)
        outer.addWidget(box)
        outer.addStretch(1)

    def range_threshold_value(self):
        return self.range_threshold.value() if self.range_protect.isChecked() else None

    def get_state(self):
        return {"range_protect": self.range_protect.isChecked(),
                "range_threshold": self.range_threshold.value()}

    def set_state(self, state):
        self.range_protect.setChecked(bool(state.get("range_protect", True)))
        if "range_threshold" in state:
            self.range_threshold.setValue(float(state["range_threshold"]))
