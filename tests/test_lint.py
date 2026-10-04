#!/usr/bin/env python3
"""
AI linter: catches genuine dangling references / bad structure, stays quiet on
unknown (possibly-extension) content.

    python3 tests/test_lint.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import parse_ini_lines, build_section_map, lint_ai

# Rules: declare two units. GGI is intentionally NOT declared.
RULES = """\
[E1]
Name=GI
[ADOG]
Name=Attack Dog
"""

# AI with one clean team and a set of deliberate problems.
AI = """\
[TaskForces]
0=TF_OK
1=TF_BAD

[ScriptTypes]
0=SCR_OK

[TeamTypes]
0=TEAM_OK
1=TEAM_BADREF

[AITriggerTypes]
TRIG_OK=my trigger,TEAM_OK,<all>,3,0,E1,0100,70,10,70,1,0,1,0,0,0,1,1
TRIG_BAD=bad trigger,NOPE_TEAM,<all>,x,0,E1,0100,70,10,70,1,0,1,0,TEAM_OK,0,1,1

[TF_OK]
Name=Good
0=2,E1
1=1,ADOG

[TF_BAD]
Name=Bad
0=2,GGI
1=notanumber,E1

[SCR_OK]
0=0,0

[TEAM_OK]
Name=ok team
TaskForce=TF_OK
Script=SCR_OK

[TEAM_BADREF]
Name=bad refs
TaskForce=TF_MISSING
Script=SCR_OK
"""


def _msgs(findings, sev=None):
    return [f.message for f in findings if sev is None or f.severity == sev]


def main():
    ok = True
    with tempfile.TemporaryDirectory() as d:
        rp = os.path.join(d, "rulesmd.ini")
        ap = os.path.join(d, "aimd.ini")
        open(rp, "w").write(RULES)
        open(ap, "w").write(AI)
        rules_map, _ = build_section_map(parse_ini_lines(rp)), None
        rules_map = build_section_map(parse_ini_lines(rp))[0]
        ai_map = build_section_map(parse_ini_lines(ap))[0]
        findings = lint_ai(ai_map, rules_map)
        errs = _msgs(findings, "error")
        allmsg = "\n".join(_msgs(findings))

        def want(substr, sev="error"):
            nonlocal ok
            hit = any(substr in m for m in _msgs(findings, sev))
            print(f"  [{sev}] detects {substr!r}: {hit}  {'OK' if hit else 'FAIL'}")
            ok = ok and hit

        def want_absent(substr):
            nonlocal ok
            clean = not any(substr in m for m in _msgs(findings))
            print(f"  no false-positive on {substr!r}: {clean}  "
                  f"{'OK' if clean else 'FAIL'}")
            ok = ok and clean

        want("undefined unit [GGI]")                 # dangling TaskForce unit
        want("non-numeric unit count")               # 'notanumber,E1'
        want("TaskForce=[TF_MISSING] is undefined")  # dangling TeamType ref
        want("primary team [NOPE_TEAM] is undefined") # dangling trigger team
        want("non-numeric TechLevel", "warning")     # trigger TechLevel 'x'
        # E1/ADOG are declared, TEAM_OK/TF_OK/SCR_OK all resolve -> no false error
        want_absent("[E1]")
        want_absent("undefined unit [ADOG]")

        print(f"\n(total: {len(errs)} errors)")

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
