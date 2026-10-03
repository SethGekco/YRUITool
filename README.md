# YRBalanceTool

A general-purpose balance editor for **Red Alert 2: Yuri's Revenge** (`rulesmd.ini`
/ `aimd.ini`). Successor to the Dying-Earth-specific `dying_earth_balance_tool.py`
CLI — same battle-tested editing engine, but generalized for any YR/RA2 mod and
driven by a desktop GUI instead of terminal prompts.

## Status

Early development. Building in reviewable slices.

## Architecture

- **`yrbalance/engine/`** — I/O-free core. No prompts, no UI. Everything sits on
  top of this. Modules:
  - `ini_model.py` — parser, section map, mutate-in-place line model, value/comment helpers.
  - `report.py` — re-run detection, `ObservationReport`, `smart_apply_tag`.
  - `operations.py` — scalar ops (tag ratio, speed+cap, buildtime, debris, adjacent, soylent, sight) + re-run context.
  - `weapons.py` — vanilla snapshots, `WeaponRegistry`, conflict-clone, elite-slot injection.
  - `typelists.py` — index-section parsing, land/naval + regular/defensive splits.
  - `stats.py` — Pass-1 orchestrators (`process_unit_stats` / `process_building_stats`).
  - `techlevel.py` — custom TechLevel depth tree (topological sort through generic
    prereq tokens), prereq-weight computation, `compute_techlevels`/`write_techlevels`.
  - `costscaling.py` — flat / TechLevel-tier / building-depth cost scaling.
  - `ticket.py` — session-layered edit history (`write_ticket`/`read_ticket`) with a
    [Meta] vanilla-file hash; basis for point-in-time restoration.
  - `config.py` — `ModConfig`: every mod-specific token as a setting (ID prefixes,
    unbuildable prereq, platform/host naming, side names) + the category model.
    Factory profiles: `ModConfig.default()` and `ModConfig.dying_earth()`.
  - `selectors.py` — resolve `$INFANTRY`/`$VEHICLE`/`$DEFENSE`/`$ALL`… scope strings
    into concrete type lists (`resolve_scope`, `build_category_lists`).
  - `sections.py` — shared clone/section plumbing: `append_raw_section`,
    `set_key_in_appended_section` (**end-insertion corruption fix**),
    `register_in_list`, drift-safe index fixups.
  - `formula.py` — sandboxed arithmetic evaluator (AST whitelist, no `eval`) +
    literal/formula auto-detection.
  - `script.py` — parse INI-flavored modifier scripts (see `docs/SCRIPTS.md`).
  - `runner.py` — apply a modifier script: resolve scope, substitute params,
    evaluate formulas per-techno (chained), write with vanilla annotation;
    generator blocks (`@clone`/`@emit`/`@register`/`@patch`).
  - `pipeline.py` — `run_balance`: turn a per-category `BalanceSpec` into the
    Pass-1 stat/weapon edits + Pass-2 sight pass.
- **`yrbalance/ui/`** — PySide6 desktop app.
  - `controller.py` — Qt-free glue (load / preview / apply-and-save / diff).
  - `category_tab.py` — per-TechnoType ratio fields + cost-scaling/TechLevel group;
    Naval tab has a per-field "same as Vehicles" link; exact get/set state.
  - `section_form_tab.py` — General/Gameplay/Super Weapons section-tag editor.
  - `script_panel.py` — custom-script editor with import/export + preset dropdown.
  - `blueprint.py` — save/load the whole tool state as hand-editable text.
  - `main_window.py`, `app.py` — window assembly and entry point.
- **`tests/`** — smoke + end-to-end tests (run against a real `rulesmd.ini`).

The key design rule inherited from the original tool: all edits mutate a raw
`lines` list in place, tracked by a `section_map`, with vanilla values embedded as
inline `; vanilla=X` comments so every change is reversible and re-runnable.

## Roadmap

1. **Engine extraction** *(in progress)* — pull the I/O-free core out of the
   monolith; every balance operation takes explicit parameters instead of calling
   `input()`, and `print()` progress is replaced by an optional callback.
   **Done — the clean core is fully extracted:** scalar ops, weapon system,
   type-list helpers, re-run/observation system, Pass-1 orchestrators, the
   custom-TechLevel depth tree, cost scaling (flat / TechLevel-tier / depth), and
   the ticket session-history — all verified end-to-end (`tests/`; the depth tree
   reproduces the classic GAPOWR=1…GACSPH=5 anchors, ticket sessions round-trip).
   **Deferred to Phase 2:** the clone-generating subsystems (host squad, faction
   platforms, mind control) are soaked in Dying-Earth specifics
   (`DEGB####`/`DEAT`/`CAPARK01`/`ATPLAT_*`) and will be extracted-and-generalized
   in one pass rather than ported twice.
2. **Generalize** *(in progress)* — Dying-Earth assumptions moved into `ModConfig`;
   `$KEYWORD` scope resolution done; shared clone plumbing extracted with the
   corruption fix. Remaining: the clone *features* (host squad, faction platforms,
   mind control) — see note below, likely re-expressed as templates in Phase 3.
3. **Formula engine + modifier scripts** *(core done)* — sandboxed expression
   evaluator and the INI-flavored modifier-script system (`docs/SCRIPTS.md`):
   user-typed `Cost = TechLevel * BuildLimit * 100`-style tags applied across a
   `$KEYWORD` scope, with fill-in `#param`s and vanilla annotation. Remaining:
   **generator blocks** (`[+ ... from ...]`) that emit + register new sections —
   this is how the host-squad / faction-platform / mind-control features will ship
   (as bundled example scripts rather than hardcoded Python).
4. **GUI** *(done)* — PySide6 desktop app:
   - A tab per TechnoType (Infantry/Vehicle/Naval/Aircraft/Building/Defensive)
     with ratio fields; the Naval tab links each field to Vehicles via a
     "same as Vehicles" checkbox.
   - Per-tab "Cost scaling and TechLevel": cost mode (flat / ×TechLevel /
     ×depth) + base, and Custom-TechLevel recompute/write/use.
   - General / Gameplay / Super Weapons tabs (structured section-tag editors),
     plus a Global placeholder for cross-cutting features.
   - A bottom custom-script panel (import/export/preview + saved-script dropdown).
   - "Preview changes" (unified diff before writing) and one "Apply and save".
   - **Blueprints**: save/load the whole tool state as a hand-editable text file
     (`Blueprint` menu). **Restore to vanilla** undoes a file's edits
     (`File` menu). Window geometry / profile / last file persist across sessions.

## Running the GUI

Install it (gives a `yrbalancetool` command):

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
yrbalancetool
```

Or without installing:

```sh
pip install -r requirements.txt
python -m yrbalance.ui
```

### Standalone executable (no Python needed)

Build a single self-contained binary with PyInstaller:

```sh
python3 -m venv .venv && . .venv/bin/activate && pip install -e .
./build_exe.sh              # or: ./build_exe.sh --windowed  (no console window)
./dist/yrbalancetool        # ~57 MB, bundles Python + Qt + the example scripts
```

Share `dist/yrbalancetool` with anyone on the same OS/arch — they just run it.
Drop extra `.yrs` scripts into `~/.yrbalancetool/presets/` to have them show up in
the dropdown.

### Workflow
Load a `rulesmd.ini`, set ratios per tab, **Preview changes**, then **Apply**.

By default Apply **edits the loaded file in place and backs it up first** (to a
`yrbt_backups` folder next to the rules). Change the backup folder, how many
backups to keep, or switch to writing a separate `_edited` file in
**File → Settings**. In-place applies restore-to-vanilla first, so re-applying
never compounds. Save/load the whole setup as a **blueprint**, see every change in
**History** (with per-change undo), and **File → Restore a file to vanilla** rolls
a file all the way back.

## Eva — the local AI assistant (optional)

The **Eva** tab turns plain-English requests ("halve the cost of all tier-1
infantry except the attack dog") into modifier scripts you can preview and apply —
she writes scripts, the engine does the editing, so it stays safe and reversible.

Eva edits your rules directly via her **Apply to rules** button (backed up first,
like any apply), or you can **Preview** / **Send to Custom Script** to tweak first.

Eva runs a **local** model via [Ollama](https://ollama.com) — free, offline, no
account, and your data never leaves the machine. Set up:

```sh
# install Ollama (see ollama.com), then pull a model:
ollama pull llama3.1        # or qwen2.5, mistral, etc.
```

Then in the app: open the **Eva** tab → **Refresh models** → pick one → ask away.

**Teaching Eva:** drop reference docs (.txt/.md) into
`~/.yrbalancetool/knowledge/inbox/` and hit **Scan inbox**, paste a tutorial/wiki
URL into **Learn from URL**, or hit **Remember this chat** — each becomes part of
her knowledge (moved to `…/knowledge/read/`).

**Scaling memory (RAG):** for big libraries, pull an embedding model
(`ollama pull nomic-embed-text`) and hit **Rebuild memory index**. Eva then
*retrieves* only the most relevant chunks per question, so the knowledge base can
grow without limit. Tune **Top-K** (how many chunks) and **Context** (the model's
window — bigger = more working memory per reply, slower, more RAM). Eva needs no
extra Python packages (it talks to Ollama over HTTP).

## Running the tests

```sh
./tests/run_all.sh "/path/to/rulesmd.ini"
```

The engine tests are pure standard library; the UI test needs the venv (PySide6)
and runs headless via `QT_QPA_PLATFORM=offscreen`.

