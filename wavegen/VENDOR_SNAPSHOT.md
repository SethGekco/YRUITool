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
