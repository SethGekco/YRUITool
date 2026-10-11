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


# ── specialty wave types (thematic, unit-specific compositions) ─────────────
# (key, friendly label, TransportCompositionGenerator method)
SPECIALTY_WAVES = [
    ("dog", "Attack-dog harass", "generate_dog_waves"),
    ("suicide_rush", "Suicide rush", "generate_suicide_rush_waves"),
    ("parasite", "Terror Drone / parasite", "generate_parasite_waves"),
    ("bfrt", "BFRT open-topped (IFV + infantry)", "generate_bfrt_waves"),
    ("htk_terror", "HTK + Terror Drone transport", "generate_htk_terror_waves"),
    ("mind_control", "Mind control (Yuri / Mastermind)",
     "generate_mind_control_waves"),
    ("chaos", "Chaos Drone", "generate_chaos_waves"),
    ("engineer_rush", "Engineer rush", "generate_engineer_rush_waves"),
    ("deployfire", "Deploy-to-fire units", "generate_deployfire_waves"),
    ("dtruck", "Demo Truck rush", "generate_dtruck_waves"),
    ("ifv_passenger", "IFV passenger combos", "generate_ifv_passenger_waves"),
    ("opentopped", "Open-topped transport combos", "generate_opentopped_waves"),
    ("hero_companion", "Hero + companions", "generate_hero_companion_waves"),
    ("country_hero", "Country hero", "generate_country_hero_waves"),
    ("defensive_sniper", "Defensive snipers", "generate_defensive_sniper_waves"),
    ("magnetron_patrol", "Magnetron patrol", "generate_magnetron_patrol_waves"),
    ("electric_assault", "Tesla / electric assault",
     "generate_electric_assault_waves"),
    ("naval_transport", "Naval transport + suicide",
     "generate_naval_transport_waves"),
    ("naval_general", "Naval general attack", "generate_naval_general_waves"),
    ("sqd_naval", "Squid naval", "generate_sqd_naval_waves"),
]


def specialty_wave_types():
    """[(key, label)] of the specialty wave compositions available."""
    return [(k, lbl) for k, lbl, _m in SPECIALTY_WAVES]


def generate_specialty_waves(rules_path, aimd_path, keys, difficulties,
                             seed=None, taskforce_scale=1.0):
    """Run the selected specialty wave generators (dog / terror-drone / naval /
    mind-control / …). Returns the TransportCompositionGenerator, which holds
    .taskforces/.scripts/.teams/.triggers and write_all()/summary(). A type that
    can't apply to this mod (missing units) is skipped."""
    import io
    import contextlib
    from .id_registry import GlobalIDRegistry
    from .taskforce_generator import IDRegistry
    from .script_generator import ScriptRegistry
    from .team_generator import TeamRegistry
    from .trigger_generator import TriggerRegistry, build_side_map
    from .wave_generator import TransportCompositionGenerator

    parser = parse_rules(rules_path)
    gr = GlobalIDRegistry.from_aimd(aimd_path)
    try:
        from .cli import detect_extensions
        from pathlib import Path
        _ares, phobos = detect_extensions(Path(rules_path), Path(aimd_path))
    except Exception:
        phobos = True
    gen = TransportCompositionGenerator(
        parser, IDRegistry.from_aimd(aimd_path, gr),
        ScriptRegistry.from_aimd(aimd_path, gr),
        TeamRegistry.from_aimd(aimd_path, gr),
        TriggerRegistry.from_aimd(aimd_path, gr),
        build_side_map(rules_path), phobos=phobos, seed=seed,
        taskforce_scale=taskforce_scale)
    method_of = {k: m for k, _lbl, m in SPECIALTY_WAVES}
    with contextlib.redirect_stdout(io.StringIO()):
        for key in keys:
            m = getattr(gen, method_of.get(key, ""), None)
            if m is None:
                continue
            try:
                m(list(difficulties))
            except Exception:
                pass          # specialty type not applicable to this mod — skip
    return gen


def specialty_preview_text(rules_path, aimd_path, keys, difficulties, seed=None,
                           taskforce_scale=1.0):
    """(generator, preview_text) — the generated specialty chain alone."""
    import tempfile
    import os
    gen = generate_specialty_waves(rules_path, aimd_path, keys, difficulties,
                                   seed=seed, taskforce_scale=taskforce_scale)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False)
    tmp.close()
    try:
        gen.write_all(tmp.name + ".base", tmp.name)
        with open(tmp.name, encoding="latin-1", errors="replace") as fh:
            text = fh.read()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return gen, text


def insert_specialty_waves(rules_path, aimd_path, keys, difficulties, seed=None,
                           taskforce_scale=1.0):
    """Regenerate and write the selected specialty waves into aimd_path (caller
    backs up first). Returns the counts dict."""
    gen = generate_specialty_waves(rules_path, aimd_path, keys, difficulties,
                                   seed=seed, taskforce_scale=taskforce_scale)
    gen.write_all(aimd_path)
    return wave_counts(gen)


# ── "generate everything": the full CLI pipeline (aimd-only) ────────────────
def _wavegen_registries(rules_path, aimd_path):
    """Shared registries + side_map + phobos for a combined generation pass, so
    every generator mints ids collision-free against the same base aimd."""
    from pathlib import Path
    from .id_registry import GlobalIDRegistry
    from .taskforce_generator import IDRegistry
    from .script_generator import ScriptRegistry
    from .team_generator import TeamRegistry
    from .trigger_generator import TriggerRegistry, build_side_map
    gr = GlobalIDRegistry.from_aimd(aimd_path)
    try:
        from .cli import detect_extensions
        _ares, phobos = detect_extensions(Path(rules_path), Path(aimd_path))
    except Exception:
        phobos = True
    return {
        "global": gr,
        "tf": IDRegistry.from_aimd(aimd_path, gr),
        "sc": ScriptRegistry.from_aimd(aimd_path, gr),
        "team": TeamRegistry.from_aimd(aimd_path, gr),
        "trig": TriggerRegistry.from_aimd(aimd_path, gr),
        "side_map": build_side_map(rules_path),
        "phobos": phobos,
    }


_DEFAULT_EVERYTHING_OPTS = {
    "pool_teams": True, "patrols": True, "capture": True,
    "destroy_tech": True, "coverage": True,
}


def generate_everything(rules_path, aimd_path, wave_cfg, specialty_keys,
                        options=None, seed=None):
    """Run the whole aimd pipeline with SHARED registries: regular waves + pool
    teams + procedural extras (patrols/capture/destroy-tech) + coverage pass +
    the selected specialty waves. Returns (wave_gen, transport_gen); both are
    written (wave_gen first, then transport_gen) by the preview/insert helpers."""
    import io
    import contextlib
    from .wave_generator import (WaveGenerator, WaveGeneratorConfig,
                                 TransportCompositionGenerator)
    opts = dict(_DEFAULT_EVERYTHING_OPTS)
    opts.update(options or {})
    parser = parse_rules(rules_path)
    reg = _wavegen_registries(rules_path, aimd_path)
    scale = wave_cfg.get("taskforce_scale", 1.0)

    scan_result = aiext_cfg = None
    try:
        from .aiext_generator import scan_mod, AIExtConfig
        scan_result = scan_mod(rules_path, parser=parser,
                               sides_registry=parser.sides)
        aiext_cfg = AIExtConfig()
    except Exception:
        scan_result = aiext_cfg = None

    with contextlib.redirect_stdout(io.StringIO()):
        wg = WaveGenerator(parser, reg["global"], reg["tf"], reg["sc"],
                           reg["team"], reg["trig"], reg["side_map"],
                           phobos=reg["phobos"], seed=seed,
                           scan_result=scan_result)
        wcfg = WaveGeneratorConfig(aiext_config=aiext_cfg, **wave_cfg)
        wg.aiext_writer.set_debug_messages(wcfg.debug_messages)
        wg.generate(wcfg)

        if opts["pool_teams"]:
            try:
                facs = sorted(parser.sides.faction_countries.keys())
                pool = wg.team_gen.generate_pool_teams(
                    facs, reg["tf"], reg["sc"], aimd_path, taskforce_scale=scale)
                if pool:
                    wg.teams.extend(pool)
                    wg.triggers.extend(wg.trig_gen.generate_for_teams(pool))
            except Exception:
                pass
        if opts["patrols"] or opts["capture"] or opts["destroy_tech"]:
            try:
                from .procedural_scripts import generate_procedural_extras
                generate_procedural_extras(
                    wg, parser, patrols=opts["patrols"],
                    capture=opts["capture"], destroy_tech=opts["destroy_tech"])
            except Exception:
                pass
        if opts["coverage"]:
            try:
                from .coverage_tracker import apply_coverage
                apply_coverage(wg, parser)
            except Exception:
                pass

        trg = None
        if specialty_keys:
            trg = TransportCompositionGenerator(
                parser, reg["tf"], reg["sc"], reg["team"], reg["trig"],
                reg["side_map"], phobos=reg["phobos"], seed=seed,
                taskforce_scale=scale)
            method_of = {k: m for k, _lbl, m in SPECIALTY_WAVES}
            for key in specialty_keys:
                m = getattr(trg, method_of.get(key, ""), None)
                if m is None:
                    continue
                try:
                    m(list(wave_cfg.get("difficulties", ["Hard"])))
                except Exception:
                    pass
    return wg, trg


def everything_counts(wg, trg):
    c = wave_counts(wg)
    if trg is not None:
        tc = wave_counts(trg)
        for k in c:
            c[k] += tc[k]
    return c


def everything_preview_text(rules_path, aimd_path, wave_cfg, specialty_keys,
                            options=None, seed=None):
    """(wg, trg, preview_text): the full generated chain alone (both generators
    written against an empty base, wave first then specialty)."""
    import tempfile
    import os
    wg, trg = generate_everything(rules_path, aimd_path, wave_cfg,
                                  specialty_keys, options=options, seed=seed)
    t1 = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False)
    t1.close()
    t2 = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False)
    t2.close()
    try:
        wg.write_all(t1.name + ".base", t1.name)
        if trg is not None:
            trg.write_all(t1.name, t2.name)
            src = t2.name
        else:
            src = t1.name
        with open(src, encoding="latin-1", errors="replace") as fh:
            text = fh.read()
    finally:
        for t in (t1.name, t2.name):
            try:
                os.unlink(t)
            except OSError:
                pass
    return wg, trg, text


def insert_everything(rules_path, aimd_path, wave_cfg, specialty_keys,
                      options=None, seed=None):
    """Regenerate the full pipeline and write it into aimd_path (caller backs up
    first): wave_gen first, then the specialty generator. Returns the counts."""
    wg, trg = generate_everything(rules_path, aimd_path, wave_cfg,
                                  specialty_keys, options=options, seed=seed)
    wg.write_all(aimd_path)
    if trg is not None:
        trg.write_all(aimd_path)
    return everything_counts(wg, trg)


# ── strategic triggers (tactical, condition-reactive AI behaviour) ──────────
# The scenarios StrategicTriggerGenerator.generate() emits, with friendly text.
# These are all-or-nothing inside generate(); listed here for the dialog blurb.
STRATEGIC_SCENARIOS = [
    ("Enemy has no base defenses", "rush the undefended enemy"),
    ("Enemy has no anti-air", "send aerial / air-heavy attacks"),
    ("Enemy fields heavy anti-air", "switch to massed aerial pressure"),
    ("Aerial transport drop", "paradrop / transport infantry behind the line"),
    ("Own base undefended", "pull forces back to defend"),
    ("Own base lacks anti-air", "build / retask anti-air defense"),
    ("MCV lost", "rebuild from a captured or spare MCV"),
    ("Enemy has no Construction Yard", "press the kill on a crippled enemy"),
    ("Enemy has no power", "strike while the enemy is shut down"),
    ("Naval: no enemy shipyard", "contest / dominate the water"),
    ("Naval: fight for dominance", "commit the fleet for sea control"),
    ("Naval: dominance pressure", "exploit naval superiority"),
]


def strategic_scenarios():
    """[(label, effect)] of the tactical strategic-trigger scenarios emitted."""
    return list(STRATEGIC_SCENARIOS)


def generate_strategic_triggers(rules_path, aimd_path, factions=None,
                                difficulties=None, phobos=None, seed=None):
    """Build condition-reactive 'strategic' triggers (aimd-only) with shared id
    registries: the AI reacts to enemy/own base state (no defenses, no AA, MCV
    lost, naval dominance, …) by launching the matching attack chain. Returns the
    StrategicTriggerGenerator (written by the preview/insert helpers)."""
    import io
    import contextlib
    from .strategic_trigger_generator import StrategicTriggerGenerator
    parser = parse_rules(rules_path)
    reg = _wavegen_registries(rules_path, aimd_path)
    if factions is None:
        factions = sorted(parser.sides.faction_countries.keys())
    if difficulties is None:
        difficulties = ["Easy", "Medium", "Hard"]
    if phobos is None:
        phobos = reg["phobos"]
    with contextlib.redirect_stdout(io.StringIO()):
        sg = StrategicTriggerGenerator(
            parser, reg["global"], reg["tf"], reg["sc"], reg["team"],
            reg["trig"], reg["side_map"], dummy_gen=None, defense_gen=None,
            seed=seed, ares=False)
        sg.generate(factions=list(factions),
                    difficulties=list(difficulties), phobos=phobos)
    return sg


def strategic_preview_text(rules_path, aimd_path, factions=None,
                           difficulties=None, phobos=None, seed=None):
    """(gen, preview_text): the generated strategic chain alone (written against
    an empty base)."""
    import tempfile
    import os
    sg = generate_strategic_triggers(rules_path, aimd_path, factions,
                                     difficulties, phobos, seed)
    t = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False)
    t.close()
    try:
        sg.write_all(t.name + ".base", t.name)
        with open(t.name, encoding="latin-1", errors="replace") as fh:
            text = fh.read()
    finally:
        try:
            os.unlink(t.name)
        except OSError:
            pass
    return sg, text


def insert_strategic_triggers(rules_path, aimd_path, factions=None,
                              difficulties=None, phobos=None, seed=None):
    """Regenerate the strategic chain and write it into aimd_path (caller backs
    up first). Returns the counts."""
    sg = generate_strategic_triggers(rules_path, aimd_path, factions,
                                     difficulties, phobos, seed)
    sg.write_all(aimd_path)
    return wave_counts(sg)


# ── strategic triggers WITH supporting base-state dummies ───────────────────
# The full tactical system the cli builds: a bounded, request-driven dummy
# prereq set (Construction Yards / War Factories / Shipyards / Power Plants +
# powered-unit buildings) plus defense-tracking dummies. These let the strategic
# triggers condition on real base state (enemy has no shipyard / no power / no
# CY / no defenses / no AA), which roughly quadruples the triggers generated.
#
# The dummy sections land in SEPARATE ai_prereq_*.ini / ai_defense_dummies.ini
# include files alongside rules.ini; rules.ini itself only gains [BuildingTypes]
# / [SuperWeaponTypes] list entries + a small [#include]. Writing touches BOTH
# rules.ini and aimd.ini, so callers must back up BOTH first.
DEFENSE_INCLUDE_NAME = "ai_defense_dummies.ini"


def _build_strat_dummies(rules_path, parser, phobos, max_combo,
                         with_defense, require_isbasedefense):
    """Build the bounded strategic dummy generator (presence + absence singles,
    category-level absence/mixed requests registered) and, optionally, the
    defense-tracking dummy generator. Mixed combos are NOT generated yet — the
    strategic trigger generator registers its mixed requests during generate(),
    after which strat_dummy._generate_mixed_all() must run. Returns
    (strat_dummy, def_gen)."""
    from .dummy_prereq_generator import (FullDummyPrereqGenerator,
                                         DummyPrereqGenerator)
    from .building_categorizer import BuildingCategorizer

    blds = BuildingCategorizer(str(rules_path)).scan()
    cats = {"Construction Yards", "War Factories", "Shipyards", "Power Plants"}
    strat_blds = [(b.idx, b.bid) for b in blds if b.category in cats]

    # Buildings that power PoweredUnit= units also need presence dummies.
    powered = set()
    for _uid, bld_ids in getattr(parser, "powered_unit_map", {}).items():
        powered.update(bld_ids)
    existing = {bid for _, bid in strat_blds}
    nxt = max((i for i, _ in strat_blds), default=-1) + 1
    for bid in sorted(powered - existing):
        strat_blds.append((nxt, bid))
        nxt += 1

    sd = None
    if strat_blds:
        sd = FullDummyPrereqGenerator(
            str(rules_path), max_combo_size=max_combo, mode=3,
            use_ares_include=phobos, mixed_scope="auto")
        sd.target_blds = sorted(strat_blds)
        cy = [b.bid for b in blds if b.category == "Construction Yards"]
        sy = [b.bid for b in blds if b.category == "Shipyards"]
        pp = [b.bid for b in blds if b.category == "Power Plants"]
        wf = [b.bid for b in blds if b.category == "War Factories"]
        if len(cy) > 1:
            sd.request_absence_combo(cy)
        if len(sy) > 1:
            sd.request_absence_combo(sy)
        if len(pp) > 1:
            sd.request_absence_combo(pp)
        for w in wf:
            sd.request_mixed_combo(present_ids=[w], absent_ids=cy)
        DummyPrereqGenerator.generate_all(sd)   # presence singles
        sd._generate_absence_all()              # absence singles + requested

    dg = None
    if with_defense:
        from .defense_dummy_generator import DefenseDummyGenerator
        dg = DefenseDummyGenerator(
            str(rules_path), require_isbasedefense=require_isbasedefense)
        dg.scan()
        dg.generate()
    return sd, dg


def generate_strategic_full(rules_path, aimd_path, factions=None,
                            difficulties=None, phobos=None, seed=None,
                            dummy_max_combo=1, with_defense=True,
                            require_isbasedefense=False):
    """Strategic triggers + their supporting base-state dummies, wired exactly
    as the cli does. Returns a bundle dict: {strat_gen, strat_dummy, def_gen,
    phobos}. Nothing is written — use the preview / insert helpers."""
    import io
    import contextlib
    from .strategic_trigger_generator import StrategicTriggerGenerator
    parser = parse_rules(rules_path)
    reg = _wavegen_registries(rules_path, aimd_path)
    if factions is None:
        factions = sorted(parser.sides.faction_countries.keys())
    if difficulties is None:
        difficulties = ["Easy", "Medium", "Hard"]
    if phobos is None:
        phobos = reg["phobos"]
    with contextlib.redirect_stdout(io.StringIO()):
        sd, dg = _build_strat_dummies(
            rules_path, parser, phobos, dummy_max_combo,
            with_defense, require_isbasedefense)
        sg = StrategicTriggerGenerator(
            parser, reg["global"], reg["tf"], reg["sc"], reg["team"],
            reg["trig"], reg["side_map"], dummy_gen=sd, defense_gen=dg,
            seed=seed, ares=False)
        sg.generate(factions=list(factions),
                    difficulties=list(difficulties), phobos=phobos)
        if sd is not None:
            sd._generate_mixed_all()            # second pass: requested mixed
    return {"strat_gen": sg, "strat_dummy": sd, "def_gen": dg,
            "phobos": phobos}


def strategic_full_counts(bundle):
    """Counts for both halves: the aimd chain + the rules.ini dummy additions."""
    c = wave_counts(bundle["strat_gen"])
    sd = bundle.get("strat_dummy")
    dg = bundle.get("def_gen")
    c["presence_dummies"] = len(sd.dummy_blds) if sd else 0
    c["absence_dummies"] = len(sd.absence_dummies) if sd else 0
    c["mixed_dummies"] = len(getattr(sd, "_mixed_dummies", [])) if sd else 0
    c["defense_dummies"] = len(dg.buildings) if dg else 0
    return c


def strategic_full_preview(rules_path, aimd_path, factions=None,
                           difficulties=None, phobos=None, seed=None,
                           dummy_max_combo=1, with_defense=True,
                           require_isbasedefense=False):
    """(bundle, aimd_text, rules_note): the strategic chain alone (written to a
    temp aimd) plus a human-readable note of what the dummy system adds to
    rules.ini and which include files it writes."""
    import tempfile
    import os
    bundle = generate_strategic_full(
        rules_path, aimd_path, factions, difficulties, phobos, seed,
        dummy_max_combo, with_defense, require_isbasedefense)
    t = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False)
    t.close()
    try:
        bundle["strat_gen"].write_all(t.name + ".base", t.name)
        with open(t.name, encoding="latin-1", errors="replace") as fh:
            aimd_text = fh.read()
    finally:
        try:
            os.unlink(t.name)
        except OSError:
            pass
    c = strategic_full_counts(bundle)
    files = []
    sd = bundle.get("strat_dummy")
    if sd and bundle["phobos"]:
        if sd.mode in (1, 3, 4):
            files.append("ai_prereq_present.ini")
        if sd.mode in (2, 3, 4):
            files.append("ai_prereq_absent.ini")
        if getattr(sd, "_mixed_dummies", None):
            files.append("ai_prereq_mixed.ini")
    if bundle.get("def_gen"):
        files.append(DEFENSE_INCLUDE_NAME)
    note = (
        f"Base-state dummies → rules.ini (backup kept): "
        f"{c['presence_dummies']} presence, {c['absence_dummies']} absence, "
        f"{c['mixed_dummies']} mixed, {c['defense_dummies']} defense-tracking.\n"
        f"Bulk written to separate include file(s): "
        f"{', '.join(files) if files else '(inline — non-Phobos mod)'}.\n"
        f"rules.ini itself gains only [BuildingTypes]/[SuperWeaponTypes] list "
        f"entries"
        + (" + a [#include]." if bundle['phobos'] else " + dummy sections."))
    return bundle, aimd_text, note


def insert_strategic_full(rules_path, aimd_path, factions=None,
                          difficulties=None, phobos=None, seed=None,
                          dummy_max_combo=1, with_defense=True,
                          require_isbasedefense=False,
                          rules_out=None, out_dir=None):
    """Regenerate and write the full strategic system. The dummy generators write
    their sections to include files in out_dir and update [BuildingTypes] /
    [SuperWeaponTypes] + [#include] in rules_out; the strategic triggers write to
    aimd_path. The CALLER must back up BOTH rules.ini and aimd.ini first.
    rules_out defaults to rules_path (in place); out_dir to rules_out's folder.
    Returns the counts."""
    import os
    rules_out = rules_out or rules_path
    out_dir = out_dir or os.path.dirname(os.path.abspath(rules_out))
    bundle = generate_strategic_full(
        rules_path, aimd_path, factions, difficulties, phobos, seed,
        dummy_max_combo, with_defense, require_isbasedefense)
    sd = bundle.get("strat_dummy")
    dg = bundle.get("def_gen")
    if sd is not None:
        sd.write_all(rules_path, rules_out, out_dir)
    if dg is not None:
        dg.write_to_file(os.path.join(out_dir, DEFENSE_INCLUDE_NAME), rules_out)
    bundle["strat_gen"].write_all(aimd_path)
    return strategic_full_counts(bundle)
