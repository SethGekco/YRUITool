# Merge plan: AI Wave Generator → YRBalanceTool

Assessment + staged integration plan. **No code changes proposed yet** — this is
the decision document to agree on before any surgery.

## 0. Priority (per Rex, 2026-10)
The **full wave-generator merge is an ambitious *later* project** — and the
generator is known-imperfect right now (it doesn't form scripts correctly or grade
units' roles/feasibility well), so we do **not** lean on AI *generation* yet.

The **near-term, worth-doing-now** goal is narrower and reliable: **let the porter
import an old mod's existing `aimd.ini` AI** for the units being ported — the AI the
modder already wrote and tuned — to complement the rules/art/sound/media import the
porter already does. This needs nothing from the generator; it reuses YRBalanceTool's
own AI understanding (`lint_ai` already parses TaskForces/ScriptTypes/TeamTypes/
AITriggerTypes) and the exact extract→dependency-closure→clean-references pattern the
porter uses for units. See §7. The generator phases (§4) remain the later track.

Source tool: `…/Tools/0 Claude Vibe Coded Tools/General AI Tool/`, `TOOL_VERSION="v39"`,
21 Python modules (~1.3 MB), **not under git**, plain files edited by multiple
sessions in parallel. Verified against `cli.py`'s *actual* imports (lazy,
in-function) on 2026-10-xx, not against any prose summary.

## 1. What it actually is (verified)
A build-time generator: reads a mod's `rulesmd.ini`, writes `aimd.ini`
(TaskForces / ScriptTypes / TeamTypes / AITriggerTypes) + a modified `rulesmd.ini`.
The inverse of YRBalanceTool's AI side (which *lints* `aimd` via `lint_ai` and
holds the aiedit action/condition knowledge). **Complementary, not overlapping.**

- **Live pipeline** (`cli.py:main()`, ~20 steps): parse rules → detect Ares/Phobos
  → categorize buildings → user menus → init registries from existing aimd →
  optional strip old entries → dummy prereqs → defense dummies → strategic
  triggers → general + country wave triggers → write rules → write aimd → summary.
- **Live modules** (imported, directly or transitively, by the pipeline): ALL
  except `probe_unused_units.py` (0 importers — standalone diagnostic, confirmed).
  The handoff under-counted; these are also live: `building_categorizer`,
  `dummy_prereq_generator`, `meta_prereq_generator`, `id_registry`,
  `script_generator`, `team_generator`, `trigger_generator`, `coverage_tracker`,
  `overview_report`, `sw_chain_walker`. `script_validator` ← `wave_generator`;
  `sw_chain_walker` ← `ai_unit_scanner`.
- **Dependencies**: pure stdlib (only `secrets`, `statistics` beyond common) —
  matches YRBalanceTool's I/O-free-stdlib engine philosophy. Good sign for merging.
- **Domain model** (`rulesmd_parser.py`, 177 KB): `IniParser`, `SidesRegistry`
  (faction/prereq-chain inference), `AliasResolver` (GenericPrerequisites),
  `DepthResolver` (tech-tree depth), `FactoryRegistry`, `WeaponAnalyzer`
  (role grading from damage/warhead/Verses), `UnitRecord`, `RulesMdParser`. Plus
  `BuildingInfo`/`BuildingCategorizer` and `ScannedUnit`. This is a *high-level
  analytical* model well beyond YRBalanceTool's low-level section-map.
- **Config surface**: `WaveGeneratorConfig` (~25 fields: wave counts per
  difficulty, difficulties, role/phase pools, scaling, type caps, debug) and
  `aiext_generator`'s 10+ config dataclasses. These are the natural GUI form.

## 2. The real obstacles to a merge
1. **Not under git, parallel-edited.** A hard code-merge snapshots a moving target
   and risks clobbering/diverging from other sessions' ongoing edits. Must be
   version-controlled first.
2. **Interactive surface is spread, not isolated.** ~47 `input()` calls across 7
   modules: `cli` 17, `taskforce_generator` 12, `ai_unit_scanner` 7,
   `entry_manager` 5, `dummy_prereq_generator` 4, `meta_prereq_generator` 1,
   `script_generator` 1. You cannot just wrap `main()`; decisions are made deep in
   the generators. Running headless (GUI/Eva-driven) requires threading those
   choices into config objects / a decision callback.
3. **The file-move gotcha** (`cli.py:1730 shutil.move`): the tool MOVES the input
   file into `input/`. Incompatible with a GUI that edits a loaded file; must be
   replaced with explicit in-path/out-dir + backups.
4. **No tests at all** (v0.1 is just an older copy). YRBalanceTool runs 24 suites;
   bringing this in without a characterization harness is risky given its size.
5. **Parser overlap ≠ duplication.** The low-level INI read overlaps with
   `build_section_map`, but the analytical layer (Sides/Weapon/Depth/Factory) is
   unique and must be preserved. Reconciling is optional polish, not a blocker.
6. **I/O**: latin-1 + CRLF, writes to `output/`. Matches YRBalanceTool's newline
   handling; fine.

## 3. Integration options (depth)
- **A. Thin wrapper** — `Tools → Generate AI…` shells out / imports and runs the
  existing pipeline with a GUI form replacing menus. Fastest, keeps two parsers.
  But blocked by the spread `input()` surface and the file-move; brittle.
- **B. Library-ize, then vendor** (recommended) — refactor the tool into an
  importable, headless library: no `input()`, no file-move, everything config- and
  callback-driven; a `generate(config, in_path, out_dir) -> result` entry. Then
  vendor it into YRBalanceTool as `yrbalance/wavegen/`, reusing YRBalanceTool's INI
  read where clean. Eva can drive it; `lint_ai` validates its output.
- **C. Deep merge** — unify data models/parsers into one engine. High effort/risk;
  not now. The analytical model is worth keeping intact.

## 4. Recommended staged plan
Each phase is independently valuable and leaves the tool working.

- **Phase 0 — version control (do first).** Snapshot the tool into git: its own
  repo, or a YRBalanceTool subdir / submodule. Coordinate with the parallel
  editors so we freeze a known state. Nothing else is safe until edits are tracked.
- **Phase 1 — characterization tests.** Capture current behavior: run v39 on a
  known `rulesmd` (e.g. vanilla + Rex's turkey-mod), save the generated `aimd`/
  `rules` as golden outputs, and add a test that re-runs and diffs. This freezes
  behavior so the refactor can't silently regress. (The tool has none today.)
- **Phase 2 — headless-ify (the key enabler).** Extract the ~47 `input()` prompts
  into `WaveGeneratorConfig`/other config objects + a decision interface
  (callbacks or a pre-answered choices dict), so `main()` can run end-to-end with
  no TTY. Remove the `shutil.move`; take explicit `in_path`/`out_dir`; use
  YRBalanceTool's `make_backup`. Golden tests from Phase 1 guard this. Valuable
  even if the merge stops here (scriptable + CI-able).
- **Phase 3 — vendor + UI.** Bring the headless library into YRBalanceTool
  (`yrbalance/wavegen/`), add a `Tools → Generate AI…` dialog driven by the config
  dataclass, and run `lint_ai` on the output automatically. Optionally expose to
  Eva ("generate skirmish AI for this mod"). Reuse the porter's mix/vanilla infra
  for any asset needs.
- **Phase 4 — optional parser reconciliation.** Share the low-level INI read with
  YRBalanceTool's engine, keeping the wave-gen analytical layer on top. Pure
  cleanup; do only if it buys real simplicity.

## 5. Independent of the merge
The 9 `ROUTING → TOOL` backlog items (Miner_Killer threshold, Ares/Phobos custom
`ArmorTypes`, DPS-based wave role, `[CASLAB]` capture target, skirmish script
library, overview-report enhancements, …) can be done in the standalone tool at
any time — they don't need the merge and would carry over.

## 7. Near-term: AI import in the porter (the piece to do now)
Bring an old mod's existing `aimd.ini` AI for the ported units into the target —
no generation, just transplant + clean, exactly like units/art/sound.

**Where the AI lives / structure** (already modelled in `engine/lint.py`):
`[TaskForces]`/`[ScriptTypes]`/`[TeamTypes]` are index lists → per-id sections;
`[AITriggerTypes]` is inline (`ID=Name,Team1,Owner,TechLevel,…,Team2,…`). Source AI
is the source mod's sibling `aimd.ini`.

**Dependency closure (the key graph):** a ported unit is used by TaskForces →
TaskForces are used by TeamTypes (`TaskForce=`) which also name a `Script=`
(ScriptType) → TeamTypes are referenced by AITriggerTypes (primary + backup team).
So: for each ported unit, pull the source TaskForces that list it; then the
TeamTypes that use those TaskForces (+ their ScriptTypes); then the AITriggers that
use those TeamTypes. Transitively closed, like the weapon→warhead→projectile chain.

**Clean / reconcile (mirrors unit Owner/Prereq cleanup):**
- `Owner`/`House`/side on teams & triggers → map to the target's `[Countries]`; drop
  unknowns and flag (the Turkey case again). `RequiredHouses` left for the user.
- **ID collisions** — source ids (e.g. `0CAD0DCC-G`) must not clash with target aimd
  ids; re-mint unique ids for every brought section/trigger and rewrite all internal
  references. (The generator's `id_registry` concept is the reference; a small local
  unique-id minter is enough — no need to import the generator.)
- Register brought entries in the target's `[TaskForces]`/`[ScriptTypes]`/
  `[TeamTypes]` index lists and append `[AITriggerTypes]` lines.
- Skip/merge entries already present in the target by content fingerprint.

**Deliverable shape:** an engine module (e.g. `engine/portai.py`, I/O-free, tested)
that takes (source aimd map, target aimd map, set of ported unit names,
target countries) → a plan of AI entries to add with cleaned refs + re-minted ids +
warnings; plus an apply step writing the target `aimd.ini` (backup first). Wire into
the Port dialog as an opt-in "also import AI (aimd)" checkbox, and run `lint_ai` on
the result to confirm no dangling references. Validate on Rex's turkey-mod → dest.

This is self-contained, reliable (transplants hand-tuned AI rather than generating),
and reuses existing infrastructure (`lint_ai`, the porter's cleanup patterns,
`make_backup`). It is the natural completion of "import an old mod" and is
independent of the generator's quality.

## 6. Recommendation
**Now:** build §7 — AI import in the porter. Self-contained, reliable (transplants
hand-tuned AI, no generation), reuses `lint_ai` + the porter's cleanup patterns,
and completes "import an old mod." An engine `portai.py` (tested) + a Port-dialog
opt-in + a post-import `lint_ai` pass.

**Later (ambitious):** the full generator merge via §4 — **Option B, staged**:
Phase 0 (git) → Phase 1 (golden tests) → Phase 2 (headless-ify, the key enabler) →
Phase 3 (vendor + UI). The generator is known-imperfect (scripts, role/feasibility
grading), so generation is not relied on until it's hardened — and its own backlog
(§5) can be worked independently meanwhile. Do NOT hard-merge the code now
(untracked, moving target, no tests); do NOT discard the analytical parser.
