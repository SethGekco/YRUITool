# Modifier Scripts

A **modifier script** is a small, INI-flavored text file that says *which* technos to
touch and *what* to change. It's the same thing whether you type it into the tool's
text box or save it as a file — saved scripts show up in the dropdown by their name.

The format deliberately mirrors `rulesmd.ini` so it reads like the game files you
already know.

## Anatomy

```ini
#name: Cost by Tech Tier
#desc: Scales cost with tech level; soylent tracks it
#param: factor = 100      ; a fill-in-the-blank value, editable in the UI

[$INFANTRY, $VEHICLE]      ; TARGET: what to apply the tags to
Cost = TechLevel * {factor}   ; a FORMULA (contains variables/math)
Soylent = Cost * 0.5          ; formulas can reference tags set earlier
Elite = yes                   ; a LITERAL (set verbatim)
```

### Metadata (optional, `#` lines at the top)
- `#name:` — shown in the dropdown.
- `#desc:` — tooltip / description.
- `#param: NAME = DEFAULT` — a fill-in value. Each becomes an editable field in the
  UI, referenced in formulas as `{NAME}`.

### Target header `[ ... ]`
A comma-separated **scope** resolved by the selector system:
- category keywords: `$INFANTRY $VEHICLE $NAVAL $AIRCRAFT $BUILDING $DEFENSE`
- composites: `$ALL $UNIT $STRUCTURE $GROUND`
- literal section names: `E1`, `GAWEAP` (applied only if present)

A script can have several target blocks; each applies its tags to its own scope.

### Tag lines `Tag = value`
The right-hand side is **auto-detected**:
- If it's a valid arithmetic expression over known **variables**, params, and
  numbers → it's a **formula**, evaluated once per matched techno.
- Otherwise it's a **literal** string, written verbatim (`Owner = Russians,Africans`,
  `Elite = yes`, `VoiceSelect = GenSovVehicleSelect`).
- Wrap in quotes to force a literal (`Cost = "TechLevel"` writes the text
  `TechLevel`).

### Variables available in formulas
Read from each matched techno at apply time:
`Cost`, `Strength`, `TechLevel`, `Speed`, `Sight`, `BuildLimit`, `Points`, plus
`NAME` (the section name), `EffectiveTechLevel`, and `Depth` (buildings). Weapon
stats via `Damage`, `Range`, `ROF` (primary weapon). Operators: `+ - * / // % **`,
functions: `min max round floor ceil abs clamp`.

Formulas are evaluated by a sandboxed parser — never Python `eval`. Unknown names
are an error (so a typo doesn't silently become a string).

## Reversibility
By default every tag a script writes gets the same `; vanilla=` / `; last_ratio=`
annotation as the built-in passes, so scripts are re-runnable and restorable. Add
`#raw: true` to write plain values without annotation.

---

## Generators (clone features) — the extension

The host squad, faction platforms, and mind-control features are just scripts that
**emit new sections** and register them. A generator block uses `[+ ...]`:

```ini
#name: Host Squad
#param: hosttech = 5

[+ {NAME}{HOST_SUFFIX} from $INFANTRY]  ; for each infantry, make <NAME>_HOST
@clone source                    ; start the new section as a copy of the infantry
TechLevel = {hosttech}           ; then set/override tags on it (formulas ok)
GroupAs = {NAME}
@register InfantryTypes           ; add the clone to an index list
@patch source                     ; switch to modifying the ORIGINAL techno:
Prerequisite = {UNBUILDABLE}      ; config keyword -> ModConfig.unbuildable_prereq
GroupAs = {NAME}
```

Header:
- `[+ NAMEPATTERN from SCOPE]` — for each techno in SCOPE, run the body with
  `{NAME}` = that techno and `{THIS}` = the generated section's name.
- `[+ NAMEPATTERN]` (no `from`) — a single "once" section (e.g. a shared
  AttachmentType). `{NAME}` is unavailable; `{THIS}` = the section name.

Directives:
- `@clone SRC` — fill the current generated section from `SRC`'s body. `SRC` may be
  a pattern; the alias `source` means the current `{NAME}` techno.
- `@emit NAME` — start another fresh generated section as the current target (for
  multi-section features like AttachEffect blocks).
- `@register IDX [as PREFIX]` — add the current section to index list `IDX`
  (keys `PREFIX####`; default prefix comes from `ModConfig`, e.g. `DEGB`/`DEAT`).
- `@patch NAME` — switch to modifying an existing section (writes are annotated
  with `; vanilla=`). `source` = the current `{NAME}` techno.

Placeholders resolve from: `{param}`, `{NAME}`, `{THIS}`, and `{CONFIG}` keywords
(`{UNBUILDABLE}`, `{PLATFORM_PREFIX}`, `{HOST_SUFFIX}`, …) — so a generator isn't
hardcoded to one mod. Tags on a **generated** section are written plain; tags under
`@patch` are annotated like the built-in passes.

An iteration whose primary section already exists is skipped, so re-runs are
idempotent. The bundled Dying-Earth versions of host squad / faction platforms /
mind control ship as example generator scripts under `presets/examples/`.

## Where scripts live
- `presets/examples/` — bundled, read-only examples (shipped with the tool).
- a user presets directory (per-OS config dir) — your saved scripts.

Both are scanned at startup; each script's `#name:` populates the dropdown.
