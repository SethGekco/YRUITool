# Modifier script DSL — cheat sheet

A script is a target header in `[brackets]` followed by `Tag = value` lines.

## Scopes (the header)
- Categories: `$INFANTRY` `$VEHICLE` `$NAVAL` `$AIRCRAFT` `$BUILDING` `$DEFENSE`
- Composites: `$ALL`, `$UNIT` (inf+veh+nav+air), `$STRUCTURE` (bld+def), `$GROUND` (inf+veh)
- Any literal section name: a unit (`E1`), a weapon, a warhead, `[General]`, a
  super-weapon (`NUKE`). Combine with commas: `[$VEHICLE, $NAVAL, GGI]`.

## Values — formula or literal (auto-detected)
- Formula: math over the techno's own tags. `Cost = TechLevel * 100`.
  - Operators: `+ - * / // % **`. Functions: `min max round floor ceil abs clamp`.
  - Grouping: `( )` or `[ ]` both work: `Cost = 20 * [Cost / TechLevel]`.
  - Variables: the techno's numeric tags (`Cost`, `Strength`, `TechLevel`, `Speed`,
    `Sight`, `BuildLimit`, `Points`, ...) plus `Damage`/`Range`/`ROF` of its primary
    weapon, and `EffectiveTechLevel`. A value set earlier in a block is visible later
    (`Soylent = Cost * 0.5` uses the new Cost).
- Literal: anything that isn't a valid formula is written verbatim: `Elite = yes`,
  `Owner = Russians,Africans`. Quote to force a literal.

## Filtering — narrow which technos in the scope get touched
Put `{EXCLUDE} … {/EXCLUDE}` or `{INCLUDE} … {/INCLUDE}` anywhere in the block,
one condition per line:
- `TechLevel = -1` exact; `TechLevel = <1` / `>10` / `<=N` / `>=N` compare
- `BuildLimit = 0` exact; `BuildLimit = !` the tag is defined
- `Prerequisite = dummy` unbuildable (also `impossible`)
- `Prerequisite = NeutralTechBuildings` requires a neutral tech building
- `Primary = M60` exact weapon match
- `[ADOG]` a specific section
EXCLUDE drops a techno matching ANY condition. INCLUDE is a whitelist (keep only
matches). Regions stack.

## Options (top of the script)
`#name:`  `#desc:`  `#param: factor = 100` (then use `{factor}`)  `#raw: true`
(write plain values, no `; vanilla=` annotation).

## Output contract for Eva
Reply with ONE fenced code block containing only the script. Use real section/tag
names from the rules summary. Never invent tags.
