#!/usr/bin/env python3
"""
Auto-suggest gating for Unit Roles.

A role tag only means "this weapon can hurt that armor class" — `assign_roles`
keys purely off Verses clearing ROLE_THRESHOLD and never looks at damage — so
useless units match roles too. These cover the two gates that keep them out of
the pre-checked list, that rejections are reported rather than silently dropped,
and that the old two-argument call still works.

The real RulesMdParser needs a full rulesmd, so it is substituted here: the
logic under test is the gating, not the parsing.

    python3 tests/test_role_suggest.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yrbalance.wavegen.rulesmd_parser as _rp
from yrbalance.wavegen.role_bundles import suggest_units_for_role


class _Rec:
    def __init__(self, roles, dps):
        self.roles = roles
        self.dps_score = dps


_UNITS = {
    "GOODGUY":    _Rec(["AG-Anti_Infantry"], 100.0),
    "LOWDPSONLY": _Rec(["AG-Anti_Infantry"], 10.0),
    "WEAKTAG":    _Rec(["AG-Anti_Infantry", "LOW_DPS"], 5.0),
    "GARBAGE":    _Rec(["AG-Anti_Infantry", "UNDESIRABLE"], 50.0),
    "WRONGROLE":  _Rec(["AA"], 500.0),
}


class _FakeParser:
    def __init__(self, _path):
        self.units = dict(_UNITS)

    def parse(self):
        pass


def main():
    ok = True
    real = _rp.RulesMdParser
    _rp.RulesMdParser = _FakeParser
    try:
        # Default: tag gates on, no absolute floor.
        picks, rej = suggest_units_for_role("Anti-Infantry", "x",
                                            with_reasons=True)
        why = dict(rej)
        d_ok = (set(picks) == {"GOODGUY", "LOWDPSONLY"}
                and set(why) == {"WEAKTAG", "GARBAGE"}
                and "LOW_DPS" in why["WEAKTAG"]
                and "UNDESIRABLE" in why["GARBAGE"])
        print(f"default gates drop LOW_DPS + UNDESIRABLE: "
              f"{'OK' if d_ok else 'FAIL'} picks={sorted(picks)}")
        ok = ok and d_ok

        # A unit that matched but was gated must be REPORTED, not vanish.
        rep_ok = len(rej) == 2 and all(isinstance(r, tuple) for r in rej)
        print(f"rejections carry a reason: {'OK' if rep_ok else 'FAIL'}")
        ok = ok and rep_ok

        # A non-matching unit is not a rejection — it simply isn't a candidate.
        nm_ok = "WRONGROLE" not in why and "WRONGROLE" not in picks
        print(f"non-matching unit is not reported as rejected: "
              f"{'OK' if nm_ok else 'FAIL'}")
        ok = ok and nm_ok

        # Absolute floor: independent of the mod's own distribution.
        picks2, rej2 = suggest_units_for_role("Anti-Infantry", "x", min_dps=25.0,
                                              with_reasons=True)
        why2 = dict(rej2)
        f_ok = (set(picks2) == {"GOODGUY"}
                and "LOWDPSONLY" in why2
                and "floor 25" in why2["LOWDPSONLY"])
        print(f"min_dps floor drops a matching but weak unit: "
              f"{'OK' if f_ok else 'FAIL'} {why2.get('LOWDPSONLY', '')}")
        ok = ok and f_ok

        # Gates are opt-outable — exclude_tags=() restores the old behaviour.
        picks3 = suggest_units_for_role("Anti-Infantry", "x", exclude_tags=())
        o_ok = set(picks3) == {"GOODGUY", "LOWDPSONLY", "WEAKTAG", "GARBAGE"}
        print(f"exclude_tags=() disables tag gating: "
              f"{'OK' if o_ok else 'FAIL'} {sorted(picks3)}")
        ok = ok and o_ok

        # Old two-arg call still returns a plain list (ai_tab used to call this).
        legacy = suggest_units_for_role("Anti-Infantry", "x")
        l_ok = isinstance(legacy, list) and set(legacy) == {"GOODGUY",
                                                            "LOWDPSONLY"}
        print(f"two-arg call still returns a plain list: "
              f"{'OK' if l_ok else 'FAIL'}")
        ok = ok and l_ok

        # Custom role: no tags to match, and the reasons shape is still honest.
        c_picks, c_rej = suggest_units_for_role("My Custom Role", "x",
                                                with_reasons=True)
        c_ok = c_picks == [] and c_rej == []
        print(f"custom role yields no suggestions, no bogus rejections: "
              f"{'OK' if c_ok else 'FAIL'}")
        ok = ok and c_ok
    finally:
        _rp.RulesMdParser = real

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
