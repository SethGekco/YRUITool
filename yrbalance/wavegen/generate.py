"""
Headless TaskForce generation API over the vendored generator — the powerful
role-based composer (ANTI_INF / ANTI_VEHICLE / ARTILLERY / AA / RUSH / … ) driven
by a config, not input(). The UI builds specs, previews the generated taskforces,
then inserts a chosen subset into aimd.ini.
"""

from .analysis import parse_rules


def taskforce_vocab():
    """{'roles', 'phases', 'difficulties'} the generator understands."""
    from . import taskforce_generator as tfg
    return {
        "roles": ["EMERGENT"] + list(tfg.ALL_ROLES),
        "phases": list(tfg.PHASE_SIZES.keys()),
        "difficulties": ["Easy", "Medium", "Hard"],
    }


def mod_factions(rules_path):
    """Factions detected in the mod (e.g. ['gdi', 'nod', 'thirdside'])."""
    parser = parse_rules(rules_path)
    sides = getattr(parser, "sides", None)
    fc = getattr(sides, "faction_countries", {}) if sides else {}
    return sorted(fc.keys())


def generate_taskforces(rules_path, aimd_path, specs, seed=None):
    """Run the composer. `specs` is a list of dicts with TaskforceSpec fields
    (faction, phase, role, count, difficulty, min_grade, fodder_ratio,
    allow_suicide_*). Returns (generator, parser, entries) — the generator can
    write a chosen subset via `generator.write_to_ini(entries, aimd_path)`."""
    from . import taskforce_generator as tfg
    parser = parse_rules(rules_path)
    registry = tfg.IDRegistry.from_aimd(aimd_path)
    gen = tfg.TaskforceGenerator(parser, registry, seed=seed)
    tf_specs = [tfg.TaskforceSpec(**s) for s in specs]
    config = tfg.GeneratorConfig(tf_specs)
    entries = gen.generate_batch(config)
    return gen, parser, entries


def entry_composition(entry, parser=None):
    """'2× APOC, 8× CONS' — the taskforce's unit composition."""
    out = []
    for count, uid in entry.units:
        out.append(f"{count}× {uid}")
    return ", ".join(out)


def entry_section_text(entry):
    """The aimd section text the generator would write for this taskforce."""
    lines = [entry.label_comment, f"[{entry.tf_id}]", f"Name={entry.name}"]
    for idx, (count, uid) in enumerate(entry.units):
        lines.append(f"{idx}={count},{uid}")
    lines.append("Group=-1")
    return "\n".join(lines)
