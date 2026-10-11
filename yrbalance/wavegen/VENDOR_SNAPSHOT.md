Vendored snapshot of the AITriggerTypeExt / AI Wave Generator (v39) from:
/mnt/wwn-0x50014ee6b383afb8-part1/(3) Games Data/RA2 & Yuri's Revenge/Tools/0 Claude Vibe Coded Tools/General AI Tool
Snapshot taken 2026-10-11T04:23Z. Code + docs only (no input/output/caches/
duplicate vanilla INIs). To be library-ized (remove input()/file-move) and integrated
per WAVEGEN_MERGE_PLAN.md — this is Phase 0 (version control).

## Phase 0 triage (characterization)
- **27,666 lines, 22 modules** — all import cleanly in the venv (pure stdlib:
  only `secrets`/`statistics` beyond common). Good merge signal.
- **Analytical core runs headlessly.** `rulesmd_parser.RulesMdParser(rules).parse()`
  on vanilla YR rules → 559 units + the full model (SidesRegistry, AliasResolver,
  DepthResolver, FactoryRegistry, WeaponAnalyzer, armor tables, buildable/naval
  sets). This is the salvageable layer — no `input()` in the analysis path.
- **Blockers are in the generation pipeline only:** 50 `input()` calls (cli 20,
  taskforce_generator 12, ai_unit_scanner 7, entry_manager 5, dummy_prereq 4,
  script_generator 1, meta_prereq 1) + the `shutil.move` of the input file.
- `probe_unused_units.py` remains the only standalone diagnostic.

## Next (per WAVEGEN_MERGE_PLAN §4)
- Phase 1: characterization/golden tests on a known rules → aimd output.
- Phase 2: headless-ify — thread the 50 `input()`s into a config/decision object,
  drop the file-move, take explicit in/out paths. The key enabler for the GUI.
- Phase 3: a `Tools → Generate AI…` dialog + auto `lint_ai` on the output.

## Milestone 1 — analysis layer (SHIPPED)
- Moved into `yrbalance/wavegen/` as a proper package; intra-module imports
  rewritten to package-relative so it bundles in the frozen build (no sys.path
  shim). All 23 submodules import.
- `analysis.py` — headless, read-only API: `parse_rules`, `overview_report_text`
  (the generator's MOD OVERVIEW: units, roles, GLOBAL/ROLE grades, factions,
  aerial/role buckets), `mod_summary`. No input().
- UI: **Tools → AI coverage report…** runs it on the loaded rules (0.04s on
  vanilla → 215-line report), with Save. Tested (`test_wavegen_analysis`, 40/40);
  frozen build bundles the analysis chain (rulesmd_parser/overview_report + deps);
  generation-only modules stay unbundled until referenced.
- Next: library-ize the generators (headless, drop input()/file-move) one at a
  time behind a Tools → Generate AI… dialog, with golden tests.

## Milestone 2 — TaskForce generation (SHIPPED)
`generate.py` — headless API over the vendored composer: `taskforce_vocab`,
`mod_factions`, `generate_taskforces(rules, aimd, specs, seed)` (drives
TaskforceGenerator.generate_batch — the role-based composer: ANTI_INF / ANTI_VEHICLE
/ ARTILLERY / AA / RUSH / … , difficulty auto-distributed, collision-free ids from
the existing aimd), `entry_composition`, `entry_section_text`. No input().
UI: AI (aimd) tab → **Generate TaskForces…** — multi-select factions × phases ×
roles (+ count / min-grade / seed / suicide), Generate → preview table (name,
faction/phase/role/diff, composition) + section text, tick what to keep, **Insert
into aimd.ini** (backup kept, reloads the tab). Tested (`test_wavegen_taskforce` +
UI). The composer's role-grading is the known-imperfect part to polish next; team /
script / trigger / wave generators follow the same headless-API + preview pattern.

## Milestone 3 — full wave chain (SHIPPED)
`generate.py`: `wave_vocab`, `generate_waves(rules, aimd, cfg, seed)` (drives
WaveGenerator — builds the registries + side_map + Phobos AIExt scan, mutes the
progress prints, produces the complete chain: TaskForces + ScriptTypes + TeamTypes
+ AITriggerTypes), `waves_preview_text` (generated chain alone, via write_all to an
empty base — write_all isn't safe to call twice on one generator, so insert
regenerates), `insert_waves`, `wave_counts`.
UI: AI (aimd) tab → **Generate Waves…** — wave counts / difficulties / scale /
options (diversify, gunner IFVs, debug AIExt) / seed, Generate → counts summary +
chain preview, Insert into aimd.ini (backup kept, reloads). Unlike TaskForces alone,
this writes a chain that actually RUNS in-game (every team's TaskForce/Script and
every trigger's team resolve — lint adds 0 new unresolved refs). Tested
(`test_wavegen_wave` + UI). The role-grading/script-formation quality is the polish
track from here.

## Milestone 4 — role-grading + script-formation polish
- **Role grading**: `role_unit_filter` fell through to `return True` for ANTI_INF /
  ANTI_VEHICLE / ANTI_BUILDING, so those roles didn't differentiate — ANTI_INF and
  ANTI_VEHICLE produced identical compositions, and snipers/tanks landed in the
  wrong waves. Now each requires the matching WeaponAnalyzer tag (AG-Anti_Infantry
  / AG-Anti_Vehicle / AG-Anti_Building), so specialists route to their role while
  genuine multi-role units stay eligible for both. Verified: sniper → anti-inf
  only, Grizzly → anti-vehicle not anti-inf, and the eligible pools now differ.
- **Script formation**: the dynamic transport-script path validated but never
  repaired. `_get_or_create_script` now runs `script_validator.repair` (fixes the
  assembly-before-unload class → MoveToEnemyBldg so units unload at the target) and
  logs it, then surfaces any remaining ERRORs. The main wave scripts come from a
  curated, already-valid template library and are left untouched.
- Full-chain generation stays consistent after both changes (0 new unresolved refs
  on a 252-trigger batch). Tested (`test_wavegen_roles`).

## Milestone 5 — specialty wave types (SHIPPED)
`generate.py`: `specialty_wave_types()` (20 thematic compositions) +
`generate_specialty_waves` / `specialty_preview_text` / `insert_specialty_waves`.
Drives the vendored TransportCompositionGenerator's per-type methods (attack-dog
harass, Terror Drone / parasite, BFRT & naval transports, mind control, engineer
rush, deploy-to-fire, hero combos, magnetron, electric assault, demo truck, IFV/
open-topped combos, squid naval, …), muting progress prints; a type whose units
aren't in the mod is skipped. Shares the id registries so ids stay collision-free
vs the on-disk aimd. UI: AI (aimd) tab → **Specialty Waves…** — tick the types +
difficulties/scale/seed, Generate → preview + counts, Insert (backup kept, reloads).
Validated on vanilla (0 new unresolved refs). Tested (`test_wavegen_specialty` + UI).
