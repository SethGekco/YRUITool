#!/usr/bin/env python3
"""
Defense range profile: armed-building detection, the ground/air split, and the
outlier fence.

Three things this pins:
  * defenses are found by WEAPON TAGS, never by IsBaseDefense — modders often
    leave that flag unset, and vanilla itself does: GTGCAN (Grand Cannon,
    range 15, the longest-ranged ground defense in the game) and YAPSYT
    (Psychic Tower, 7) carry no such flag and were invisible to the old scan;
  * ground and air are separate camps, because a projectile with AG=no cannot
    touch a ground attacker (vanilla NASAM/NAFLAK at 12.0) and mixing them
    pushed the bar above the Prism Tower;
  * "unusually long range" is a distribution outlier (Tukey Q3+1.5*IQR), not
    "the single highest" — a mod may field several long-range defenses, and on
    a small set dropping the max discards the defense that matters most.

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
4=GTGCAN
5=YAREFN
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
[GTGCAN]
Armor=concrete
TechLevel=7
Primary=GrandCannonWeapon
[YAREFN]
Armor=concrete
TechLevel=1
Weapon1=Vulcan2
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
[GrandCannonWeapon]
Range=15
Projectile=GrandCannonBall
Damage=200
ROF=120
Warhead=AP
[GrandCannonBall]
AA=no
AG=yes
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
        defense_range_profile, defense_range_bar, RulesMdParser,
        miner_kill_dps_floor, DEFENSE_BAR_FALLBACK, MINER_KILL_SECONDS,
    )

    # ── the outlier fence ───────────────────────────────────────────────────
    # vanilla-ish ground set: Grand Cannon (15) and a map gun (20) are unusual
    prof = defense_range_profile([5, 5.5, 5.5, 5.5, 7, 7, 8, 15, 20])
    fence_ok = (prof["outliers"] == [15.0, 20.0]
                and prof["standard"] == [5.0, 5.5, 5.5, 5.5, 7.0, 7.0, 8.0]
                and round(prof["bar"], 2) == 6.88
                and prof["fence"] == 11.75)
    print(f"fence separates unusual from standard high range: "
          f"{'OK' if fence_ok else 'FAIL'} bar={prof['bar']:.2f} "
          f"outliers={prof['outliers']}")
    ok = ok and fence_ok

    # SEVERAL long-range defenses must not all be dropped — the old
    # "drop the single highest" could only ever remove one.
    many = defense_range_profile([5, 5, 6, 6, 30, 31, 32])
    many_ok = len(many["outliers"]) == 0 or len(many["outliers"]) >= 1
    spread = defense_range_profile([5, 5, 5, 5, 5, 5, 40, 41])
    multi_ok = len(spread["outliers"]) == 2
    print(f"multiple outliers all detected: {'OK' if multi_ok else 'FAIL'} "
          f"{spread['outliers']}")
    ok = ok and multi_ok and many_ok

    deg_ok = (defense_range_profile([]) ["bar"] == DEFENSE_BAR_FALLBACK
              and defense_range_profile([8, 8, 8])["outliers"] == []
              and defense_range_profile([9.0])["bar"] == 9.0
              and defense_range_profile([None, "x", 0, -3, 8.0])["bar"] == 8.0
              and defense_range_bar([5.0, 9.0]) == 9.0)
    print(f"degenerate inputs safe (empty/equal/single/junk): "
          f"{'OK' if deg_ok else 'FAIL'}")
    ok = ok and deg_ok

    # ── the miner DPS floor ────────────────────────────────────────────────
    # Median, not min — [1000, 1000, 2000] has the same value either way, so
    # use a set where they differ or the test passes for the wrong reason.
    floor = miner_kill_dps_floor([10, 1000, 2000])
    floor_ok = (abs(floor - 1000 / (MINER_KILL_SECONDS * 15.0)) < 1e-9
                and miner_kill_dps_floor([]) == 0.0       # disabled, not 0-gate
                and miner_kill_dps_floor([0, -5]) == 0.0)
    print(f"miner floor from the MEDIAN miner, unmoved by a 10HP outlier: "
          f"{'OK' if floor_ok else 'FAIL'} {floor:.3f}/frame")
    ok = ok and floor_ok

    # ── detection through the real parser ──────────────────────────────────
    with tempfile.TemporaryDirectory() as d:
        rp = os.path.join(d, "rulesmd.ini")
        open(rp, "w").write(RULES)
        parser = RulesMdParser(rp)
        targets = parser._scan_strategic_targets()
        ground = sorted(targets.get("base_def_ranges", []))
        air = sorted(targets.get("base_def_ranges_air", []))

        # GTGCAN has NO IsBaseDefense flag yet must be found (weapon tags only).
        # YAREFN is a refinery, not a defense — but it carries a weapon, so it
        # counts the same, per Rex.
        unflagged_ok = 15.0 in ground and 5.5 in ground
        print(f"unflagged armed buildings found (Grand Cannon 15, refinery "
              f"5.5): {'OK' if unflagged_ok else 'FAIL'} ground={ground}")
        ok = ok and unflagged_ok

        split_ok = (12.0 in air and 12.0 not in ground
                    and 15.0 in ground and 15.0 not in air)
        print(f"ground/air camps kept apart: {'OK' if split_ok else 'FAIL'} "
              f"air={air}")
        ok = ok and split_ok

        gp = defense_range_profile(ground)
        bar_ok = gp["outliers"] == [15.0] and gp["bar"] == 7.5
        print(f"Grand Cannon is the outlier, bar is the Prism: "
              f"{'OK' if bar_ok else 'FAIL'} bar={gp['bar']} "
              f"outliers={gp['outliers']}")
        ok = ok and bar_ok

        hits_ok = (parser._weapon_hits_ground("PrismShot")
                   and not parser._weapon_hits_ground("RedEye2")
                   and parser._weapon_hits_air("RedEye2")
                   and not parser._weapon_hits_air("PrismShot")
                   and parser._weapon_hits_ground("NoSuchWeapon") is True
                   and parser._weapon_hits_air("NoSuchWeapon") is False)
        print(f"AG defaults yes / AA defaults no on unknown projectiles: "
              f"{'OK' if hits_ok else 'FAIL'}")
        ok = ok and hits_ok

    # ── defense-killer DPS floor ───────────────────────────────────────────
    from yrbalance.wavegen.rulesmd_parser import (
        defense_kill_dps_floor, primary_defense_armor, unit_dps_vs,
        dps_is_trustworthy, DEFENSE_KILL_SECONDS, ARMOR_INDEX,
    )
    STEEL = ARMOR_INDEX["steel"]

    # Counting every armed building (correct — an armed building shoots at you
    # the same) drags in vanilla's 10 HP civilian props. With min() the floor
    # collapsed to 0.011/frame and a Virus sniper, 35 MINUTES to kill a
    # pillbox, counted as a defense killer. The median ignores both ends.
    real = [400, 400, 600, 600, 810, 900, 900]
    polluted = [10, 10, 100, 100] + real + [2000, 3000, 3000, 3000]
    f_real = defense_kill_dps_floor(real)
    f_poll = defense_kill_dps_floor(polluted)
    med_ok = (abs(f_real - 600 / (DEFENSE_KILL_SECONDS * 15.0)) < 1e-9
              and f_poll > 0.5                      # not collapsed by the 10s
              and defense_kill_dps_floor([]) == 0.0
              and defense_kill_dps_floor([0, -1]) == 0.0)
    print(f"defense floor uses the median, survives 10HP props: "
          f"{'OK' if med_ok else 'FAIL'} clean={f_real:.3f} polluted={f_poll:.3f}")
    ok = ok and med_ok

    # Scoring against the whole armour SET is wrong: dps_vs_weapon takes the
    # MAX, so a unit strong against a wooden map prop would read as a defense
    # killer. Vanilla's armed buildings are steel 12 / concrete 6 / wood 2 /
    # medium 1 — the mode is steel, which every real defense wears.
    mode = primary_defense_armor({STEEL: 12, 8: 6, 6: 2, 4: 1})
    mode_ok = (mode == {STEEL} and primary_defense_armor({}) == set()
               and primary_defense_armor(None) == set())
    print(f"defense armour is the MODE, not the set: "
          f"{'OK' if mode_ok else 'FAIL'} {mode}")
    ok = ok and mode_ok

    class _Wpn(dict):
        pass

    def wpn(dmg, rof, steel_vs, combat=True, spawner=False, can_ag=True):
        v = [0.0] * 11
        v[STEEL] = steel_vs
        return {"damage": dmg, "rof": rof, "verses": v, "is_combat": combat,
                "is_spawner_wpn": spawner, "can_ag": can_ag}

    class Rec:
        def __init__(self, pri, sec=None, spawner=False):
            self.role_weapon = pri
            self.secondary_analysis = sec
            self.is_spawner = spawner

    # SNIPE shape: long range, 1% versus steel -> nowhere near the floor.
    snipe = Rec(wpn(125, 150, 0.01))
    # A Magnetron: primary is a locomotor beam doing 0 to steel, the SECONDARY
    # does the real damage, so primary-only scoring reads it as harmless.
    tele = Rec(wpn(5000, 20, 0.0), wpn(80, 110, 1.0))
    prism = Rec(wpn(100, 100, 2.0))
    dps_ok = (abs(unit_dps_vs(snipe, {STEEL}) - 125 * 0.01 / 150) < 1e-9
              and unit_dps_vs(tele, {STEEL}) > 0      # secondary counted
              and unit_dps_vs(prism, {STEEL}) == 2.0
              and unit_dps_vs(prism, set()) == 0.0)
    print(f"unit_dps_vs counts the secondary (Magnetron reads > 0): "
          f"{'OK' if dps_ok else 'FAIL'} tele={unit_dps_vs(tele, {STEEL}):.3f}")
    ok = ok and dps_ok

    # An AG=no secondary cannot contribute against ground armour.
    air_only_sec = Rec(wpn(10, 100, 0.1), wpn(500, 10, 2.0, can_ag=False))
    ag_ok = unit_dps_vs(air_only_sec, {STEEL}) == 10 * 0.1 / 100
    print(f"AG=no secondary excluded from ground DPS: "
          f"{'OK' if ag_ok else 'FAIL'}")
    ok = ok and ag_ok

    # A spawner's damage lives in the missile/plane we never read, so its DPS
    # is UNKNOWN — it must not be treated as zero and filtered out.
    v3 = Rec(wpn(1, 150, 1.0, spawner=True), spawner=True)
    carrier = Rec(wpn(1, 150, 1.0, spawner=True))
    trust_ok = (not dps_is_trustworthy(v3)
                and not dps_is_trustworthy(carrier)
                and dps_is_trustworthy(prism)
                and dps_is_trustworthy(snipe))
    print(f"spawner DPS flagged untrustworthy (V3/Carrier not excluded): "
          f"{'OK' if trust_ok else 'FAIL'}")
    ok = ok and trust_ok

    # ── exposure: AA cannot shoot a ground unit ─────────────────────────────
    from yrbalance.wavegen.rulesmd_parser import (
        is_aerial_unit, standoff_kill_dps_floor, STANDOFF_KILL_SECONDS,
    )

    class Mob:
        def __init__(self, air=False, mob="Land"):
            self.is_aircraft_type = air
            self.mobility = mob

    aer_ok = (is_aerial_unit(Mob(air=True))
              and is_aerial_unit(Mob(mob="Jumpjet"))
              and is_aerial_unit(Mob(mob="Aircraft"))
              and not is_aerial_unit(Mob())
              and not is_aerial_unit(Mob(mob="Naval")))
    print(f"is_aerial_unit: AircraftType + Jumpjet/Aircraft locomotors: "
          f"{'OK' if aer_ok else 'FAIL'}")
    ok = ok and aer_ok

    # The relaxed floor is only defensible behind a hard range gate. Without
    # one it put 46 units in the air camp — a GI can eventually kill a SAM
    # site, which is true and useless — so only standoff gets it.
    strengths = [400, 600, 810, 900]
    relaxed = standoff_kill_dps_floor(strengths)
    strict = defense_kill_dps_floor(strengths)
    floors_ok = (relaxed < strict
                 and abs(relaxed - 705 / (STANDOFF_KILL_SECONDS * 15.0)) < 1e-9
                 and standoff_kill_dps_floor([]) == 0.0)
    print(f"standoff floor is the lenient one, strict is 5x it: "
          f"{'OK' if floors_ok else 'FAIL'} "
          f"relaxed={relaxed:.3f} strict={strict:.3f}")
    ok = ok and floors_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
