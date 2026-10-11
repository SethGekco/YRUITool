#!/usr/bin/env python3
"""
probe_unused_units.py — why isn't the AI fielding unit X?

Two questions, one report:
  1. WHICH buildable, combat-worthy units never appear in ANY generated
     taskforce in the aimd (the "AI never builds these" list), highest grade
     first — these are the units you expect to see and don't.
  2. WHY each is missing: for every role, at which phases is the unit eligible?
     If it's only eligible at Late/EndGame for a tier-locked role (aerial,
     naval), the independent role/phase cycles in generate() rarely align it —
     that's the usual culprit, not a hard exclusion.

Optionally cross-references a debug.log for the team lifecycle health
(Start/Destroyed/Deleted) — a 1:1 Start:Destroyed with zero Deleted means
teams die instead of completing, a separate signal from "never generated".

Usage:
  python3 probe_unused_units.py <rulesmd.ini> <aimd.ini> [debug.log]
"""
import sys
from collections import defaultdict

from rulesmd_parser import RulesMdParser
import taskforce_generator as tg

PHASES = ("Rush", "Early", "Mid", "Late", "EndGame")
# Roles worth probing — the DEFAULT_ROLE_POOL set plus the specialty roles.
ROLES = ("RUSH", "GENERAL_ATTACK", "ANTI_DEFENSES", "MINER_KILLER",
         "FACTORY_KILLER", "CY_KILLER", "AA",
         "AERIAL_RUSH", "AERIAL_WAVE", "AERIAL_ASSAULT", "NAVAL")

# A unit at/above this grade that never appears is "surprising" — the report
# leads with these.
NOTABLE_GRADE = 15


def units_in_aimd(aimd_path):
    """Set of unit IDs referenced by any taskforce line (`<n>=<count>,<ID>`)."""
    used = set()
    import re
    line = re.compile(r'^\d+\s*=\s*\d+\s*,\s*([A-Za-z0-9_]+)\s*$')
    with open(aimd_path, encoding='latin-1') as f:
        for ln in f:
            m = line.match(ln.strip())
            if m:
                used.add(m.group(1).upper())
    return used


def eligibility_map(parser, faction, uid):
    """role -> [phases where uid is eligible] for this faction."""
    out = {}
    for role in ROLES:
        hits = []
        for phase in PHASES:
            try:
                el = tg._eligible_units(parser, faction, phase, role,
                                        "Generic", 0, "Hard")
            except Exception:
                el = []
            if any(r.unit_id == uid for r in el):
                hits.append(phase)
        if hits:
            out[role] = hits
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    rules_path, aimd_path = sys.argv[1], sys.argv[2]
    log_path = sys.argv[3] if len(sys.argv) > 3 else None

    parser = RulesMdParser(rules_path).parse()
    used = units_in_aimd(aimd_path)

    print(f"Taskforce-referenced unit IDs in aimd: {len(used)}")
    print("=" * 72)

    by_faction = defaultdict(list)
    for r in parser.buildable:
        if not r.faction:
            continue
        if getattr(r, "is_no_combat", False) or getattr(r, "is_utility", False):
            continue
        if getattr(r, "is_transport", False) or getattr(r, "is_suicide", False):
            continue
        by_faction[r.faction].append(r)

    for faction in sorted(by_faction):
        recs = sorted(by_faction[faction],
                      key=lambda u: -(getattr(u, "grade", 0) or 0))
        missing = [r for r in recs if r.unit_id.upper() not in used]
        notable = [r for r in missing
                   if (getattr(r, "grade", 0) or 0) >= NOTABLE_GRADE]
        print(f"\n### {faction.upper()} — {len(missing)} buildable combat units "
              f"never fielded ({len(notable)} at grade >= {NOTABLE_GRADE})")
        for r in notable:
            emap = eligibility_map(parser, faction, r.unit_id)
            g = getattr(r, "grade", 0)
            mob = getattr(r, "mobility", "?")
            tier = getattr(r, "tier", "?")
            if not emap:
                why = "NEVER eligible for any probed role (grade/role-score/filter)"
            else:
                parts = [f"{role}@{'+'.join(ph)}" for role, ph in emap.items()]
                # The tell: only eligible at Late/EndGame for tier-locked roles.
                late_only = all(
                    set(ph) <= {"Late", "EndGame"} for ph in emap.values())
                tag = " [Late/EndGame-only → role/phase cycles rarely align]" \
                      if late_only else ""
                why = "eligible: " + ", ".join(parts) + tag
            print(f"  {r.unit_id:10} g{g:<3} {mob:8} {tier:8} {why}")

    if log_path:
        print("\n" + "=" * 72)
        print("TEAM LIFECYCLE (from debug.log)")
        import re
        tally = defaultdict(int)
        ev = re.compile(r'\[AIExt (Start|Destroyed|Deleted|Selected|Reject)\]')
        try:
            with open(log_path, encoding='latin-1', errors='ignore') as f:
                for ln in f:
                    m = ev.search(ln)
                    if m:
                        tally[m.group(1)] += 1
        except OSError as e:
            print(f"  (could not read log: {e})")
            return
        for k in ("Selected", "Start", "Destroyed", "Deleted", "Reject"):
            print(f"  {k:10} {tally.get(k, 0)}")
        s, d, done = tally.get("Start", 0), tally.get("Destroyed", 0), tally.get("Deleted", 0)
        if s and done == 0:
            print("  ⚠ zero Deleted with nonzero Start — every team DIES rather "
                  "than completing its script (suicide/harass-only, or teams "
                  "never reach action 49,0).")
        if s and abs(s - d) <= max(2, s // 20):
            print("  ⚠ Start ≈ Destroyed — teams that spawn are almost all lost; "
                  "check whether waves are too small / undefended for the map.")


if __name__ == "__main__":
    main()
