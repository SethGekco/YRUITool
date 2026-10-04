# YRBalanceTool — TODO / Roadmap

Deferred ideas, with enough context to pick up cold.

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

## Sound effects & music support
Let Eva add sound effects and music to a mod for the user — or walk them through
doing it themselves. This spans `sound(md).ini` / `eva(md).ini` / `theme(md).ini`
and the asset side (registering entries in the right list sections, pointing at
the audio files, `.bag`/`.idx` or loose-file handling). Reference walkthrough:
https://modenc.renegadeprojects.com/Adding_A_Song (the canonical "add a song"
guide — covers theme registration and file placement). Two modes, mirroring the
Eva design: (a) guided walkthrough, and (b) actually writing the INI entries.
Pairs with the "universal helper" item above (sound/theme INIs were deferred
there as cosmetic — this is the concrete use case to pull them in).

## Eva autonomy (manual + agent blend)
Today: Eva writes a validated DSL script; the user clicks **Apply to rules**
(in-place, auto-backup), **Preview** (dry-run), or **Send to Custom Script**
(hand-edit). The click is the safety gate — both manual control and agent drive
coexist. Possible future: an opt-in "let Eva apply directly" mode (still backed up),
and/or Eva calling tool actions as function-calls rather than emitting text scripts.
Keep the manual path as the default/guardrail.
