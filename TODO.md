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

## AI-import (transplant an old mod's aimd.ini AI) — SHIPPED
`engine/portai.py`: for a set of ported units, closes over the AI graph
(unit→TaskForce→TeamType+Script→AITrigger via the PRIMARY team), re-mints every
id collision-free (`F0000000-G…`), rewrites all id-string references, cleans
`House=`/trigger `Owner` against the target's countries, neutralizes non-imported
backup teams (Team2→0 with a note), and flags dangling units + index-ref script
actions (18 Join Team) for manual review. `apply_ai_import` appends sections +
registers them in the index lists + appends triggers to `[AITriggerTypes]`.
Wired into the Port dialog as the opt-in **"Also import AI (aimd)"** checkbox
(backs up the target aimd, lints the result). Validated on the turkey-mod →
cncnet-edition target: 10 units → 29 TF / 14 script / 32 team / 36 trigger, **0
new ref-resolution errors** (the 9 that remain are the target mod's own
pre-existing dangling triggers). Tested (`tests/test_portai.py`). Remaining:
proper script *formation* and role/feasibility grading are the generator's job,
not import's — see the AI library item next.

## AI library — store AI blocks out of aimd.ini — SHIPPED
`engine/ailib.py` + `ui/ai_library_dialog.py` (Tools → AI library…). Stores
AITrigger *bundles* (trigger + primary/backup teams + their TaskForces & Scripts)
or single blocks (TaskForce/Script/TeamType) in a sidecar
(`~/.yrbalancetool/ailibrary.json`, never the mod). Pull back into any aimd reuses
portai's re-mint + ref-rewrite + Owner/House cleanup, PLUS:
- **dedup/merge** — a block byte-identical (ignoring Name) to one already in the
  target is *reused* (refs repoint, nothing minted); near-identical blocks
  (Jaccard ≥ threshold, default 95%, adjustable in the pull dialog) are added as
  new WITH a warning;
- **unit deps** — referenced units are listed; per unit the user can leave-and-warn,
  remap to an existing unit, or bring a bundled stub TechnoType section;
- every entry carries a free-form **Private notes** box and a separate **Credits**
  box (round-trip in the sidecar; editable in the dialog). Exported encyclopedia
  units get the same via a `<unit>.notes.md` sidecar (template on first export,
  preserved on re-export).
Validated: unit test + offscreen UI test; real-data self-pull reuses all 6 blocks,
cross-mod pull adds 0 new ref errors. Remaining: library-wide search/tags; a
"grade this AI / find bugs" pass (long-term, per Rex).

## Library tab — cross-mod asset/AI catalog — SHIPPED (v1)
`engine/library.py` + `ui/library_tab.py` (main-window "Library" tab). Adds mods
as *sources* by REFERENCE (read live) or SNAPSHOT (rules/aimd/art/sound copied
into ~/.yrbalancetool/library/<id>/). Fixed KIND sub-tabs (Vehicles/Buildings/
Infantry/Aircraft/AI content/Other) aggregate that kind across every source; a
live search box hides non-matching rows as you type. Left panel = collections:
"All sources", one auto 📁 folder per mod, plus user ★ collections (create/delete,
add selected item, filter). Each source carries an estimated **balance culture**
— per-kind factors vs vanilla via `compute_factors` (factor = mod/vanilla), with
a deterministic `describe_culture` summary and an **Ask Eva to summarize** button
(`eva.summarize_balance`). `conversion_factors(A, B)` gives per-stat multipliers
to move content between two mods' scales. Source scan is lazy (first tab show) so
big mods never slow startup. Sidecar `~/.yrbalancetool/library.json`. Tested
(engine + offscreen UI). Clicking an item opens a **detail pane** (rules preview via `unit_report` + a
Files table with each asset's location/status; AI items show their trigger +
closure via `extract_bundle`). Source scans are cached (`scan_source` → index +
aimd) so detail is instant. Remaining:
- **SHP preview — SHIPPED.** `engine/images.py` decodes SHP(TS/RA2) (raw +
  RLE-Zero) and VGA `.pal` (6-bit→8-bit), renders a frame to RGBA (index 0
  transparent). The Library detail pane shows the best frame as a QPixmap,
  resolving the SHP via `resolve_media` (loose OR mix) and picking a palette
  (loose unittem/temperat/… → mix → grayscale fallback). Validated on real game
  SHPs (`button14`, `ygplast_bd`, etc.) + synthetic round-trip test
  (`test_images`). Palette selection is approximate (not yet theater-aware) and
  SHP is static (no animation) — refine later.
- **Audio playback — SHIPPED.** `engine/audio.py` decodes RA2/YR `.bag` samples
  (Westwood IMA-ADPCM, state reset every `chunk_size` bytes per the `.idx`) to
  16-bit PCM and wraps RIFF/WAVE. The detail pane lists a unit's resolvable bag
  samples with **▶ Play** (best-effort OS player — paplay/aplay/afplay/winsound,
  since QtMultimedia isn't bundled) and **Export WAV…**. Format was pinned on real
  data (continuous decode railed ~22% of samples; per-chunk reset → 0%); output
  verified valid by stdlib `wave`. Tested (`test_audio` + UI). Remaining audio:
  playback for samples inside a mix (currently loose-bag catalog entries), and
  bundling QtMultimedia for in-app playback without a system player.
- **VXL preview — SHIPPED (v1).** `engine/vxl.py` parses VXL (header/sections/
  span bodies/tailers) and renders a fixed ~2:1 isometric still with a z-buffer +
  mild height shading. Validated across 17 real files (8 rendered, 9 genuinely
  empty body-sections, 0 errors) and visually (usaguntur reads as the USA cannon).
  RA2 VXLs embed an EMPTY palette, so colour comes from an external unit palette
  (same resolver as SHP: loose unit*.pal → mod mix → grayscale fallback) — grayscale
  is recognizable, a real `unittem.pal` gives full colour. Tested (`test_vxl` +
  UI). Remaining: **normal/VPL lighting** (needs TS/RA2 normal tables — refs:
  Banshee's OS Voxel Viewer Delphi/OpenGL at svn.ppmsite.com, XCC), **HVA +
  per-section/turret placement** (currently all sections share voxel coords),
  selectable view angle, and a **bundled/standard `unittem.pal`** so colour is the
  default without the user supplying one. **PCX** cameos and SHP animation also
  remain.
- **VXL normal lighting — SHIPPED.** `engine/vxl_normals.py` carries the TS (36)
  and RA2 (245) surface-normal tables extracted verbatim from zzattack's
  ccmaps-net (`VxlFile.cs`; all unit vectors). `render_vxl` now diffuse-lights each
  voxel from its stored normal — colour × (Ambient 0.8 + Diffuse 1.3 · max(dot(N,
  light), 0)), light = TS −X / YR −X rotated 45° about Z — matching ccmaps-net's
  software rasterizer; falls back to height shading when a section has no normal
  table. Verified visually (usaguntur shows proper dome curvature + barrel
  depth). Still approximate vs the game: no VPL page remap (uses direct RGB
  diffuse), base light direction only (no per-section HVA transform), fixed view.
- **VPL page lighting — SHIPPED.** `engine/vpl.py` parses `voxels.vpl` (header +
  768 palette + numSections×256 pages) and computes the per-normal lighting page
  (Blinn-Phong `clamp((diffuse+specular)*16)`, specularStrength 3.0, neutral
  253-255) — ported verbatim from ccmaps-net. `render_vxl(..., vpl=, direction=32)`
  remaps each voxel's colour index through its page before the palette lookup
  (`rgb = unit_palette[vpl[page][colour]]`) — the game's real voxel lighting.
  Verified against a real 32-page `voxels.vpl` extracted from ra2.mix (looks
  game-accurate; house-remap voxels show as magenta as expected without a house
  colour). The Library uses a `voxels.vpl` the mod ships loose, else a shared copy
  **auto-imported once from the game mixes** (`mixlib.find_in_mixes` deep-searches
  nested/encrypted mixes; ~20s in a background thread, cached to
  `~/.yrbalancetool/voxels.vpl` and reused forever), else diffuse. Tested
  (`test_vpl` incl. a synthetic nested-mix `find_in_mixes`; import flow verified
  end-to-end on real mixes).
- **HVA / per-section turret placement — SHIPPED.** `engine/hva.py` parses the
  animation transforms; `vxl.py` now places each section by its HVA frame-0 matrix
  (translation scaled by HVAMultiplier·Scale, atop MinBounds, using real per-axis
  Scale = (Max−Min)/Size — the tailer `tb+12` float was HVAMultiplier, not scale)
  in a shared world space. `render_parts([(model, hva), …])` composites a unit's
  **body + turret + barrel** (the game draws turrets as separate `*tur.vxl` /
  `*barl.vxl` files); the Library auto-resolves those siblings + their `.hva`s and
  renders them together. Matrix/placement math ported from ccmaps-net; verified
  (synthetic HVA translation shifts a section by exactly HVAMultiplier·offset;
  two parts composite distinctly; usaguntur unchanged). Note: the loose sample
  units all have empty bodies + identity HVAs, so the visible payoff shows on real
  turreted vehicles (inside the game mixes).
- **art.ini TurretOffset — SHIPPED.** `render_parts` parts may carry a per-part
  world offset; the Library reads `TurretOffset` (ReadFloat) from the unit's art
  entry and shifts the turret + barrel by `0.18 · TurretOffset` along world X —
  ported from ccmaps-net (`UnitDrawable` sets `TurretVoxelOffset`, `VxlRenderer`
  applies `translate(0.18·offset,0,0)` to turret/barrel only). Verified: a +X
  offset of 10 shifts a part's projected ix by exactly 10; visually the turret
  copy repositions forward. Remaining voxel work: selectable view angle, applying
  a house colour to the remap range.
- "Convert selected into THIS mod's culture" action wired to the porter using
  `conversion_factors`; cross-mod tags on library items; jump-to-encyclopedia/
  porter from a row.
- Background/incremental scan + caching keyed on rules mtime for very large mods.

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

## Unit tags — SHIPPED (v1), extend
engine/tags.py: user tags stored in a sidecar (~/.yrbalancetool/tags.json, never
in the mod), auto-tags (source:/kind:/obtain:) merged at display, add/remove/bulk,
tag filter + "Tag all shown" in the Encyclopedia for fast bulk tagging. Remaining:
- Eva-driven tagging — DONE: eva.plan_tags turns "tag all crate-only vehicles as
  needs-review" into a TagPlan {tag, action, kind, obtain, name_contains, has_tag};
  the Encyclopedia's "Ask Eva to tag" field previews the matched units and applies
  via bulk_add/remove. Verified on qwen2.5:14b across add/remove + name/obtain/kind
  filters.
- Provenance-on-export — stamp exported units with a source tag so a re-indexed
  library keeps "from:<mod>".
- Tag presets / colors, and cross-mod (library-wide) tags.

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
- Per-unit history — DONE (modhistory.unit_history in the detail pane).
- "Open in Porter as source" bridge — DONE.
- SHP/VXL thumbnail preview in the detail pane (remaining).
- Export whole mod / multi-unit export to a library (currently one unit at a time).

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

## Persistent + live change history — SHIPPED
engine/modhistory.py: scan-on-load diff (status bar) + per-apply edit log +
per-unit history (encyclopedia) + a full timeline UI (History tab "Timeline…"
button / Tools → Change timeline…, with Clear). Done.

## Workspace: default rules + bookmarked sources — SHIPPED
engine/bookmarks.py + pickers in Encyclopedia and Porter; File → "Set current as
default workspace" pins the primary rules to reopen on startup (falls back to last
file). Remaining: search across *all* bookmarked mods without loading each
(lazy/background index), and a session-restore of open encyclopedia mods.

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
