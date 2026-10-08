# YRBalanceTool — TODO / Roadmap

Deferred ideas, with enough context to pick up cold.

## Mod-content porter — remaining phases
Engine core shipped (`engine/portmod.py`): extracts source-only objects, closes
over the weapon chain, computes PER-KIND balance factors (infantry/vehicle/
building/aircraft/weapon — mods rebalance categories differently; validated on
real data: infantry ×0.3 cost, vehicles ×4), cross-checks vanilla, cleans
Owner/Prerequisite. Still to build:
- **Multi-file reach** — load the source mod's `artmd.ini` / `soundmd.ini`
  (+ aimd, eva, theme) as extra section maps so art/sound entries port, not just
  get flagged. Resolve `Image=`/`Cameo=`/`Report=` against them.
- **Selectable UI** (*Tools → Port mod content…*) — per-kind/per-object checkboxes;
  review table (before→after stats, confidence, editable ratio fields); apply-with-
  backup / export-blueprint toggle (both). Each item in its own minimizable box
  showing its code (TechnoType + weapons + warheads + art + sound entries) AND its
  associated media files (.vxl/.hva/.shp incl. sidebar cameo; SW cameo from the SW
  section). Top of page: current-mod balance history / saved user balance
  preferences with a "use these balance modifiers" checkbox.
- **Media / asset transfer** — copy the .vxl/.hva/.shp/.wav the imported objects
  reference (from the source mod's loose files or its .mix archives) into the target.

## .mix archive read / extract / repack — v1 SHIPPED (unencrypted), extend
`engine/mixlib.py` reads/extracts/repacks unencrypted MIX (the mod mixes:
ecache*/expand*/ares) and refuses encrypted master mixes. Remaining:
- **Blowfish+RSA decryption** for encrypted master mixes (ra2.mix/ra2md.mix,
  flags & 0x20000) — needed to read vanilla content straight from the game, for
  users who don't keep loose files. XCC has the key-derivation (RSA-wrapped
  Blowfish key) + Blowfish; port it. Lower priority since Rex keeps vanilla loose.
- **Seamless packed-ini editing** — extract a packed ini to a temp folder, let the
  tool edit it as if loose, repack into the same mix on save (backups kept; atomic
  write already done). Wire into Load/Save when the path points inside a mix.
- **MIX browser UI** (Tools → Open MIX…) — list/extract/add/replace files, with
  the corruption-boogey-man backup prompt surfaced.
- **vanilla vs mod .mix detection** — flag which mixes are stock (compare against a
  known vanilla file list; for now diff against `…/000-RA2YR Vanilla/`).

## Porter media transfer (uses mixlib)
Wire mixlib into the porter: for each imported object, find its referenced
`.vxl/.hva/.shp/.wav` (and sidebar cameo SHP) across the source mod's loose files
AND its mixes, and copy them into the target (loose or into a target mix). The
porter already LISTS these filenames per object; this closes the loop so imported
units actually render. Pairs with the "universal helper" + porter UI items.

## Version-aware vanilla baselines
Keep multiple vanilla `rules`/`art`/… sets for different patch versions, and pick
the right baseline when diffing/porting (the port's balance cross-check and the
"is this vanilla?" question both depend on matching the correct vanilla version).

## #include / $include resolution
Mods split rules across included files — classic Ares `[#include]` (numbered list
of filenames) and Phobos' `[$include]` variant. Resolve the include graph into one
effective section map before analysis/port (Rex's turkey-mod rulesmd uses
`[#include]` for SW/country files). Make inclusion opt-in per file in the porter.

## Object buildability / obtainability classifier — v1 SHIPPED, refine
`engine/obtain.py` labels every TechnoType: buildable / crate / other
(FreeUnit/Spawns/Deploy/SW/Capturable) / dummy / civilian / unknown, with reasons;
wired into the porter (building split, blueprint, Port dialog). Remaining:
- **Shrink the `unknown` bucket** — on kitchen-sink mods ~40% land in `unknown`
  (non-buildable, unreferenced, no art). Many are AI-only units built by aimd
  taskforces (not scanned), Ares-specific obtain paths, or deprecated/test
  objects. Scan aimd TaskForces for AI-buildable units; add Ares mechanics.
- **Deeper dummy taxonomy** — Rex wants a discussion on *types* of dummies
  (SW-tied spawns, prereq placeholders, drone holders, etc.); encode each.
- Consider a secondary "AI-only" flag, and surface the class as a porter filter.

## Sound effects & music support
Let Eva add sound effects and music to a mod for the user — or walk them through
doing it themselves. This spans `sound(md).ini` / `eva(md).ini` / `theme(md).ini`
and the asset side (registering entries in the right list sections, pointing at
the audio files, `.bag`/`.idx` or loose-file handling). Reference walkthrough:
https://modenc.renegadeprojects.com/Adding_A_Song (the canonical "add a song"
guide — covers theme registration and file placement). Two modes, mirroring the
Eva design: (a) guided walkthrough, and (b) actually writing the INI entries.
Pairs with the "universal helper" item below.

## Universal helper: go beyond rules-only
Right now the tool (and Eva's baseline reference) is centered on `rules.ini` /
`rulesmd.ini`. The original plan was rules-only, but it would be more useful as a
**universal RA2/YR INI helper**, not just a balance editor.

Scope to consider adding:
- **art(md).ini** — visuals: `Cameo`, `Image`, `Voxel`, `Remapable`, sequences,
  `BuildingTypeArt` docking. Lets Eva answer art questions and lets the tool edit
  cosmetics.
- **ai(md).ini** — already ingested as *vanilla reference*; could also become an
  editable surface (TeamTypes, TaskForces, ScriptTypes, AITriggerTypes) beyond the
  current AI tab.
- **sound(md).ini / eva(md).ini / theme(md).ini** — audio/EVA/music.
- **map/mode INIs** — `battle*.ini`, `mp*.ini`, `mission*.ini`, `*mdmd` theaters
  (`snow`, `urban`, `desert`, `lunar`, `temperat`).

Two separable pieces:
1. **Eva reference breadth** — ingest more vanilla files as baselines (the "Add
   vanilla reference INI" button already generalizes; just allow any INI, tag by
   file). Cheap. Main cost is retrieval noise — gate behind the RAG index.
2. **Editable surfaces** — new tabs / generalized section editing for art, AI, etc.
   Bigger; the engine's section-map model is already game-agnostic, so this is
   mostly UI + presets.

Decision needed: how far to generalize the *editor* vs. just the *reference*.
Reference breadth is low-risk and high-value first step.

## Eva autonomy (manual + agent blend)
Today: Eva writes a validated DSL script; the user clicks **Apply to rules**
(in-place, auto-backup), **Preview** (dry-run), or **Send to Custom Script**
(hand-edit). The click is the safety gate — both manual control and agent drive
coexist. Possible future: an opt-in "let Eva apply directly" mode (still backed up),
and/or Eva calling tool actions as function-calls rather than emitting text scripts.
Keep the manual path as the default/guardrail.
