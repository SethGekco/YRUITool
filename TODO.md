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

## .mix archive read / extract / repack — SHIPPED (incl. encryption + media)
`engine/mixlib.py` reads/extracts/repacks MIX (plain + Blowfish-encrypted via
_blowfish.py), with VANILLA_MIX_NAMES/is_vanilla_mix policy, media cataloguing
(build_media_catalog over loose+mixes, by name AND id), resolve_media, and
pack_media_mixes (split by count). Porter media transfer is wired into the Port
dialog (base mix name + max files/mix). Remaining niceties:
- **Seamless packed-ini editing** — extract a packed ini to a temp folder, edit it
  as if loose, repack into the same mix on save (backups kept; atomic write done).
  Wire into Load/Save when the path points inside a mix. (MIX browser can already
  extract/repack manually.)
- MIX browser UI — SHIPPED (Tools → MIX browser…): open/list/filter/extract/add/
  remove/save, backs up on overwrite, warns on stock/encrypted. Could add: preview
  (SHP/VXL thumbnails), in-place save to the same mix, preserve unnamed entries.
- **Checksum on write** — optionally write the SHA1 trailer (flags & 0x10000); the
  game doesn't require it, so low priority.
- **Cameo gaps** — some units' cameos aren't in the mod's mixes; media transfer
  reports them as not-found. Could cross-check vanilla cameos / warn in the UI.

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

## Unit encyclopedia — SHIPPED (v1), extend
Tools → Encyclopedia…: multi-mod load, search (name/kind/mod), per-unit page
(stats + weapon chain + art/sound + asset locations loose/mix + missing-vs-vanilla
+ obtain class), and Export unit (its code + extracted media into a library
folder). Engine `encyclopedia.py` (tested). Validated on the turkey-mod (5879
units, 0.5s). Remaining:
- Audio .bag/.idx support — DONE (XCC "GABA" format): `parse_audio_idx` /
  `read_audio_sample`; `build_media_catalog` indexes loose `.idx`/`.bag` pairs so
  sound samples resolve to "bag: audio.bag" and extract via `resolve_media`. Only
  sounds absent from every source still show the neutral ♪ status. Remaining:
  samples extract as RAW (IMA-ADPCM) bag bytes — add WAV decode/encode for playable
  export; and reach vanilla voices nested inside the game mixes (unnamed sub-mixes).
- Per-unit balance history (ties to the persistent-history item).
- SHP/VXL thumbnail preview in the detail pane.
- "Send selected unit(s) to the porter as a source" one-click bridge.

## Unit encyclopedia / search (original idea notes)
A searchable, unit-centric view of the loaded mod. Pick/search a unit → one page
showing everything about it, assembled from infrastructure that already exists:
- **Code**: its rules section (stats), weapon chain (Primary/Secondary → warheads →
  projectiles), art entry (artmd), sound entries (soundmd) — the porter already
  resolves all of this per object (`build_port_plan` / `_resolve_art/_resolve_sound`).
- **Assets + their locations**: the .vxl/.hva/.shp/.wav (incl. sidebar cameo) each
  with WHERE it lives — loose file or which .mix — via `build_media_catalog`.
- **Missing-asset hunting**: flag referenced assets that aren't present, split into
  "vanilla (fine)" vs "truly missing (warn)" via `vanilla_asset_ids`/`classify_missing`.
- **Obtainability**: buildable / crate / other / dummy / civilian via
  `classify_obtainability`.
- **Per-unit balance history**: this unit's changes over time (see history item).
Searchable by name/role. Directly serves "stalk for missing assets + known
locations" and makes import review far comfier. Recommended first build; it unifies
the porter/mix/obtain/vanilla work into one browsing surface.

## Persistent + live change history
The History tab already does per-change undo with vanilla annotations. Extend:
- **Scan-on-load diff** — snapshot section hashes on load; on reload, show what
  changed since last time (works even for mods not edited with the tool).
- **Live change log** — while running, append every applied section change to a
  persistent log (per mod), so history survives restarts and feeds the per-unit
  history in the encyclopedia.

## Multi-mod workspace: default rules + bookmarked sources
Keep a primary rules file loaded, plus a set of **bookmarked source mods** (paths)
the user can quickly pull from — so storing several mods as "source libraries" for
the porter / encyclopedia is one click. Pairs with the porter (quick source
selection) and the encyclopedia (search across bookmarked mods).

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
