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
#param: nums = 3

[+ {NAME}_HOST from $INFANTRY]   ; for each infantry, make a clone named <NAME>_HOST
Image = {NAME}                   ; tags for the generated section (formulas ok)
AttachEffectTypes = AE_{NAME}
TechLevel = 5
@register InfantryTypes          ; add the clone to an index list
@patch source                    ; then modify the ORIGINAL techno:
  Prerequisite = $UNBUILDABLE    ; config keyword -> ModConfig.unbuildable_prereq
  GroupAs = {NAME}
```

- `[+ NAMEPATTERN from SCOPE]` — for each techno in SCOPE, create a section whose
  name comes from NAMEPATTERN (using `{NAME}` etc.), with the tags below it.
- `@register <IndexSection>` — add the generated section to a list (keys use the
  `ModConfig` prefixes, e.g. `DEGB####`).
- `@patch source` — switch to editing the original techno; tags below apply to it.
- `$UNBUILDABLE`, `$PLATFORM_PREFIX`, etc. — config keywords resolve to the values
  in the active `ModConfig`, so a generator isn't hardcoded to one mod.

The bundled Dying-Earth versions of host squad / faction platforms / mind control
will ship as example generator scripts under `presets/examples/`.

## Where scripts live
- `presets/examples/` — bundled, read-only examples (shipped with the tool).
- a user presets directory (per-OS config dir) — your saved scripts.

Both are scanned at startup; each script's `#name:` populates the dropdown.
