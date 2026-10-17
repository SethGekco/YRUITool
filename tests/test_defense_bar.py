#!/usr/bin/env python3
"""
Base-defense range bar: the statistic itself, and the AA-only exclusion.

A ground assault unit "out-ranges the base defenses" only relative to defenses
that can actually shoot it. Vanilla YR's two LONGEST-range defenses (NASAM,
NAFLAK at 12.0) have AG=no — they cannot hit ground at all — and counting them
pushed the bar from 7.5 to 9.0, i.e. above the Prism Tower, so genuinely
long-range siege units stopped qualifying.

    python3 tests/test_defense_bar.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mirrors vanilla: four ground defenses (5.5/5.5/7/8) plus two AA-only ones at
# 12.0, which must not count.
RULES = """\
[BuildingTypes]
0=GAPILL
1=TESLA
2=ATESLA
3=NASAM
[GAPILL]
Armor=concrete
IsBaseDefense=yes
Primary=Vulcan2
[TESLA]
Armor=concrete
IsBaseDefense=yes
Primary=CoilBolt
[ATESLA]
Armor=concrete
IsBaseDefense=yes
Primary=PrismShot
[NASAM]
Armor=concrete
IsBaseDefense=yes
Primary=RedEye2
[Vulcan2]
Range=5.5
Projectile=InvisibleLow
Damage=20
ROF=20
Warhead=AP
[CoilBolt]
Range=7
Projectile=Invisible
Damage=60
ROF=50
Warhead=AP
[PrismShot]
Range=8
Projectile=InvisibleHigh
Damage=90
ROF=60
Warhead=AP
[RedEye2]
Range=12
Projectile=AAHeatSeeker
Damage=50
ROF=40
Warhead=AP
[InvisibleLow]
AA=no
AG=yes
[Invisible]
AA=no
AG=yes
[InvisibleHigh]
AA=no
AG=yes
[AAHeatSeeker]
AA=yes
AG=no
[AP]
Verses=100%,100%,100%,100%,100%,100%,100%,100%,100%,100%,100%
"""


def main():
    ok = True
    from yrbalance.wavegen.rulesmd_parser import (
        defense_range_bar, RulesMdParser, DEFENSE_BAR_MIN_TRIM_N,
        DEFENSE_BAR_FALLBACK,
    )

    # ── the statistic ───────────────────────────────────────────────────────
    small = defense_range_bar([5.5, 5.5, 7.0, 8.0])
    stat_ok = (
        small == 7.5                                  # no trim below min N
        and defense_range_bar([]) == DEFENSE_BAR_FALLBACK
        and defense_range_bar([9.0]) == 9.0
        # big set: the 40 is dropped, upper half of the rest averaged
        and defense_range_bar([4, 5, 5, 6, 7, 8, 9, 40]) == 7.5
        # junk is ignored, not crashed on
        and defense_range_bar([None, "x", 0, -3, 8.0]) == 8.0)
    print(f"statistic (small-set no-trim, outlier trimmed, junk ignored): "
          f"{'OK' if stat_ok else 'FAIL'} small={small}")
    ok = ok and stat_ok

    # The trim must NOT fire below the guard, or it discards the one defense a
    # siege unit most needs to out-range (vanilla's Prism).
    four = [5.5, 5.5, 7.0, 8.0]
    guard_ok = (len(four) < DEFENSE_BAR_MIN_TRIM_N
                and defense_range_bar(four) > max(four[:-1]))
    print(f"small-set guard keeps the top defense in play: "
          f"{'OK' if guard_ok else 'FAIL'}")
    ok = ok and guard_ok

    # ── the AA-only exclusion, through the real parser ──────────────────────
    with tempfile.TemporaryDirectory() as d:
        rp = os.path.join(d, "rulesmd.ini")
        open(rp, "w").write(RULES)
        parser = RulesMdParser(rp)
        targets = parser._scan_strategic_targets()
        ranges = sorted(targets.get("base_def_ranges", []))
        bar = defense_range_bar(ranges)
        aa_ok = (12.0 not in ranges            # NASAM excluded
                 and ranges == [5.5, 7.0, 8.0]
                 and bar == 7.5)
        print(f"AA-only defense excluded from the bar: "
              f"{'OK' if aa_ok else 'FAIL'} ranges={ranges} bar={bar}")
        if not aa_ok:
            print("   (AG=no projectile cannot hit ground — its range is "
                  "irrelevant to a ground siege unit)")
        ok = ok and aa_ok

        hits_ok = (parser._weapon_hits_ground("PrismShot")
                   and not parser._weapon_hits_ground("RedEye2")
                   and parser._weapon_hits_ground("NoSuchWeapon") is True
                   and not parser._weapon_hits_ground(""))
        print(f"_weapon_hits_ground: AG=no false, unknown defaults true: "
              f"{'OK' if hits_ok else 'FAIL'}")
        ok = ok and hits_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
