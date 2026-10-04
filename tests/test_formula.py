#!/usr/bin/env python3
"""
Unit tests for the sandboxed formula evaluator and auto-detection. No rules file
needed.

    python3 tests/test_formula.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import evaluate, looks_like_formula, FormulaError


def main():
    ok = True

    def check(label, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        print(f"  {'OK' if good else 'FAIL'}  {label}: got {got!r}, want {want!r}")

    vars = {"TechLevel": 4, "BuildLimit": 2, "Cost": 800, "Strength": 100}

    # Evaluation.
    check("TechLevel * BuildLimit * 100", evaluate("TechLevel * BuildLimit * 100", vars), 800)
    check("Cost * 0.5", evaluate("Cost * 0.5", vars), 400)
    check("brackets as grouping", evaluate("20 * [Cost / TechLevel]", vars), 4000)
    check("parens still work", evaluate("20 * (Cost / TechLevel)", vars), 4000)
    check("min(Cost, 500)", evaluate("min(Cost, 500)", vars), 500)
    check("clamp(TechLevel*3, 1, 10)", evaluate("clamp(TechLevel * 3, 1, 10)", vars), 10)
    check("round(Cost/300)", evaluate("round(Cost / 300)", vars), 3)
    check("whole float -> int", evaluate("Strength * 2.0", vars), 200)

    # Errors: unknown var, disallowed constructs, division by zero.
    for bad in ["Armor + 1", "__import__('os')", "Cost.real", "[1,2,3]",
                "Cost if TechLevel else 0", "Cost / 0", "lambda: 1"]:
        try:
            evaluate(bad, vars)
            print(f"  FAIL  {bad!r} should have raised")
            ok = False
        except FormulaError:
            print(f"  OK    {bad!r} rejected")

    # Auto-detection.
    known = set(vars)
    checks = [
        ("TechLevel * 100", True),      # formula: op + known var
        ("TechLevel", True),            # single known var
        ("Cost * 0.5", True),
        ("yes", False),                 # bare unknown -> literal
        ("Russians,Confederation", False),  # tuple/commas -> literal
        ("GenSovVehicleSelect", False), # unknown name -> literal
        ('"TechLevel"', False),         # quoted -> forced literal
        ("500", False),                 # bare number -> literal
        ("Armor * 2", False),           # unknown var in expr -> literal (not formula)
    ]
    for rhs, want in checks:
        got = looks_like_formula(rhs, known)
        good = got == want
        ok = ok and good
        print(f"  {'OK' if good else 'FAIL'}  detect {rhs!r} -> {got} (want {want})")

    # intended_formula_but_invalid: broken formulas must be flagged (so they are
    # skipped, never written verbatim), while real literals pass through.
    from yrbalance.engine.formula import intended_formula_but_invalid
    inv_checks = [
        ("if(TechLevel == 1, Cost * 0.5, Cost)", True),  # comparison + call -> broken
        ("Cost >= 500", True),                           # comparison on known var
        ("Cost * 0.5", False),                           # valid formula, not 'invalid'
        ("heavy", False), ("yes", False), ("-1", False),
        ("GAPILE,GAWEAP", False), ("100%,100%,100%", False),
        ("Russians,Africans", False), ("pipsnum.shp", False),
    ]
    for rhs, want in inv_checks:
        got = intended_formula_but_invalid(rhs, known)
        good = got == want
        ok = ok and good
        print(f"  {'OK' if good else 'FAIL'}  invalid-formula {rhs!r} -> {got} "
              f"(want {want})")

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
