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


# ── full wave chain: taskforces + scripts + teams + triggers ────────────────
def wave_vocab():
    """{'role_pool', 'phase_pool', 'difficulties'} defaults for wave generation."""
    from .wave_generator import DEFAULT_ROLE_POOL, DEFAULT_PHASE_POOL
    return {
        "role_pool": list(DEFAULT_ROLE_POOL),
        "phase_pool": list(DEFAULT_PHASE_POOL),
        "difficulties": ["Easy", "Medium", "Hard"],
    }


def generate_waves(rules_path, aimd_path, cfg, seed=None):
    """Run the full wave chain. `cfg` is a dict of WaveGeneratorConfig fields
    (general_waves_per_difficulty, country_waves_per_difficulty, difficulties,
    role_pool, phase_pool, taskforce_scale, diversify_mono, gunner_hard_infantry,
    debug_messages, …). Returns the WaveGenerator, which holds .taskforces /
    .scripts / .teams / .triggers and can write_all() / summary()."""
    from pathlib import Path
    from .id_registry import GlobalIDRegistry
    from .taskforce_generator import IDRegistry
    from .script_generator import ScriptRegistry
    from .team_generator import TeamRegistry
    from .trigger_generator import TriggerRegistry, build_side_map
    from .wave_generator import WaveGenerator, WaveGeneratorConfig

    parser = parse_rules(rules_path)
    global_reg = GlobalIDRegistry.from_aimd(aimd_path)
    tf_reg = IDRegistry.from_aimd(aimd_path, global_reg)
    sc_reg = ScriptRegistry.from_aimd(aimd_path, global_reg)
    team_reg = TeamRegistry.from_aimd(aimd_path, global_reg)
    trig_reg = TriggerRegistry.from_aimd(aimd_path, global_reg)
    side_map = build_side_map(rules_path)
    try:
        from .cli import detect_extensions
        _ares, phobos = detect_extensions(Path(rules_path), Path(aimd_path))
    except Exception:
        phobos = True
    # smart AIExt conditions (Phobos) — optional; fall back to plain triggers
    scan_result = aiext_cfg = None
    try:
        from .aiext_generator import scan_mod, AIExtConfig
        scan_result = scan_mod(rules_path, parser=parser,
                               sides_registry=parser.sides)
        aiext_cfg = AIExtConfig()
    except Exception:
        scan_result = aiext_cfg = None

    gen = WaveGenerator(parser, global_reg, tf_reg, sc_reg, team_reg, trig_reg,
                        side_map, phobos=phobos, seed=seed,
                        scan_result=scan_result)
    wcfg = WaveGeneratorConfig(aiext_config=aiext_cfg, **cfg)
    gen.aiext_writer.set_debug_messages(wcfg.debug_messages)
    import io
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):   # mute progress prints
        gen.generate(wcfg)
    return gen


def waves_preview_text(rules_path, aimd_path, cfg, seed=None):
    """(generator, preview_text): preview_text is the generated chain alone
    (write_all against an empty base, so only the new TaskForces/Scripts/Teams/
    Triggers show). Insert should regenerate fresh and write to the real aimd —
    write_all isn't safe to call twice on one generator."""
    import tempfile
    import os
    gen = generate_waves(rules_path, aimd_path, cfg, seed=seed)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False)
    tmp.close()
    missing = tmp.name + ".base"          # a non-existent base → only generated
    try:
        gen.write_all(missing, tmp.name)
        with open(tmp.name, encoding="latin-1", errors="replace") as fh:
            text = fh.read()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return gen, text


def insert_waves(rules_path, aimd_path, cfg, seed=None):
    """Regenerate and write the full chain into aimd_path (caller backs up first).
    Returns the counts dict."""
    gen = generate_waves(rules_path, aimd_path, cfg, seed=seed)
    gen.write_all(aimd_path)
    return wave_counts(gen)


def wave_counts(gen):
    """{'triggers', 'taskforces', 'scripts', 'teams'} for a generated WaveGenerator."""
    return {
        "triggers": len(gen.triggers),
        "taskforces": len(gen.taskforces),
        "scripts": len(gen.scripts),
        "teams": len(gen.teams),
    }
