"""
Headless analysis API over the vendored AI generator — read-only, no input().

This is the "salvage analysis first" layer: parse a mod's rules with the vendored
RulesMdParser (its Sides/Depth/Factory/Weapon model) and produce the generator's
MOD OVERVIEW report (unit roles, GLOBAL/ROLE grades, factions, aerial/role buckets)
without running any generation. The raw generator modules are library-ized
incrementally; this module is the clean, importable entry.
"""

def parse_rules(rules_path):
    """Parse a mod's rules into the vendored analytical model (RulesMdParser)."""
    from . import rulesmd_parser
    parser = rulesmd_parser.RulesMdParser(rules_path)
    parser.parse()
    return parser


def overview_report_text(rules_path, gunner_hard_infantry=True):
    """The generator's MOD OVERVIEW report as text — the AI-relevant units it
    detects, their roles and grades, grouped by section. Analysis only."""
    from . import overview_report
    parser = parse_rules(rules_path)
    rep = overview_report.build_overview(
        parser, gunner_hard_infantry=gunner_hard_infantry)
    return rep if isinstance(rep, str) else "\n".join(rep)


def mod_summary(rules_path):
    """A quick headline dict: unit count + faction→countries map."""
    parser = parse_rules(rules_path)
    units = getattr(parser, "units", {})
    sides = getattr(parser, "sides", None)
    factions = {}
    if sides is not None:
        fc = getattr(sides, "faction_countries", {}) or {}
        factions = {f: sorted(c) for f, c in fc.items()}
    return {"units": len(units), "factions": factions}
