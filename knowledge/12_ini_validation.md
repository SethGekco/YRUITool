# INI validity checklist (what is / isn't acceptable)

Distilled from **AICHECK** (the long-standing Tcl sanity-checker for RA2/YR
`rules`/`art`/`ai` INIs). These are the structural mistakes that genuinely break
a mod. Use them to judge whether an edit is safe — but read the CAVEAT first:
AICHECK is RA2-era and over-strict, so not everything it would flag is actually
wrong.

## CAVEAT — do not over-report (read this first)
AICHECK predates Ares/Phobos/KratosPP and only knows vanilla RA2 (with partial YR)
tags and objects. It therefore **false-positives**:
- It calls an **unknown tag or object "invalid / undeclared / not valid"** even
  when it's a legitimate **Ares / Phobos / KratosPP** flag or a custom object. A
  tag missing from vanilla is NOT automatically wrong — check `07_yr_supported_flags.md`,
  `08_ra2_supported_flags.md`, `09_ares_flags.md`, `phobos_*.txt`, `kratos_*.md`
  before calling anything invalid.
- "Doesn't exist in the tool's eyes" ≠ wrong. The balance tool models a subset of
  INI; a tag it doesn't edit can still be valid game INI.
So: treat the rules below as **real only for vanilla content**, and when a symbol
is unknown, say "unknown — may be an extension flag" rather than "invalid".

## Cross-reference rules (these are almost always real bugs)
Every name a section points at must resolve to a declared section:
- A unit/building/infantry/aircraft listed in a `[TaskForce]` must be a declared
  TechnoType. Undefined → the AI entry is dead.
- A `[TeamType]` must name a `TaskForce=` and a `Script=` that both exist and have
  data; a `[ScriptType]` / `[TaskForce]` that nothing references is unused (dead
  weight, usually harmless but a smell).
- An `[AITriggerType]` must name primary (and optional backup) TeamTypes that
  exist; `Owner`/side/house must be consistent between the trigger and its teams.
- `Warhead=`, `Weapon=`, `Primary`/`Secondary`, `Image=`, `Anim=`/animation lists,
  `VeteranUnits`/`VeteranInfantry`/`VeteranAircraft`, `Prerequisite=` — each named
  object must be declared. A dangling reference does nothing or crashes.

## AI (aimd.ini) consistency — AICHECK's core strength
- **TaskForce transport capacity:** total `Passengers` of a taskforce must not
  exceed the transport unit's capacity (and a big under-fill is flagged too).
- **Houses/Sides:** a trigger's `House`/side must match its teams' `Owner`,
  `RequiredHouses`, `ForbiddenHouses`; an invalid `SIDE`/`HOUSE` value is a bug.
- **TechLevel:** a team/taskforce/object TechLevel must be in range and must not
  *exceed* the trigger's TechLevel (the AI could never build it).
- **Loadable teams:** if a TeamType is `Loadable`, its script must actually use a
  load command — and vice-versa (`OnTransOnly` without Loadable is wrong).
- **Priority weights / SERIAL data:** min/default/max priority must be valid
  numbers; AITrigger `SERIAL` data has a fixed length.
- **"Move to friendly building" script actions:** the target side must line up with
  a real player `AIBasePlanningSide` (not a neutral tech building).
- Note: `aimd.ini` (YR) is ordered differently from RA2's `ai.ini`; ordering/index
  continuity matters for engine parsing (indices like `0=`,`1=`,… must be
  contiguous and numeric).

## rules.ini structural rules
- Declared objects must be unique and their list indices contiguous & numeric
  (`[InfantryTypes] 0=…,1=…`), no duplicates, no gaps.
- Core `Color`/side color indices must be in range and in order.
- Special infantry roles (`Agent`/`Engineer`/`Assaulter`) shouldn't collide on one
  unit; `[General]` object lists (e.g. `PadAircraft`, particle systems, terrain)
  must reference declared objects; `LightningWarhead` must be a real warhead.

## art.ini rules
- Every `[xArt]` entry needs matching art; infantry/animation need the expected
  **alternate art** (e.g. damaged/`A`/`D` frames) or the game falls back/garbles.
- `Cameo`/`AltCameo` names shouldn't collide with other art objects.
- A declared `Sequence` that nothing uses is dead; a referenced sequence that isn't
  declared is a bug.
- A weapon with a firing offset needs its `FLH`/`AltFLH`; `Translucency`/`Layer`
  values must be from the valid set; `Rotates=yes` on a voxel is wrong; trailer
  image vs `UseLineTrail` conflict.

## How Eva should use this
When asked to add/edit INI, sanity-check the cross-references and AI consistency
above, and warn about real breakage (dangling references, capacity/tech/house
mismatches, non-contiguous indices). But never call an unfamiliar tag or object
"invalid" on its own — first check whether it's a vanilla, Ares, Phobos, or
KratosPP feature. When unsure, say so and point to the relevant flag list / docs.
