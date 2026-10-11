# Strategic (condition-reactive) AI triggers

Most AI attack waves fire on a weight/timer: the engine rolls the `[AITriggerTypes]`
list and launches whichever trigger wins. **Strategic triggers** are different — they
only fire when a *battlefield condition* is true, so the AI reacts to what is actually
happening (enemy left its base undefended, the AI lost its MCV, nobody owns the sea)
instead of attacking on a schedule. This tool generates them from the **Strategic
Triggers…** button on the AI (aimd) tab.

## The AITriggerTypes line

Each trigger is one CSV line under `[AITriggerTypes]` with 18 fields:

```
ID=Name,PrimaryTeam,Owner,TechLevel,ConditionType,ConditionObject,Comparator,
   StartWeight,MinWeight,MaxWeight,Skirmish,(unused),Side,BaseDefense,
   SecondaryTeam,Easy,Medium,Hard
```

The condition is three fields working together:

- **ConditionType** — whose base is checked:
  `0` = *enemy owns*, `1` = *the AI (self) owns*, `4` = *enemy credits*.
- **ConditionObject** — the TechnoType the count is about (a building ID, usually).
- **Comparator** — a packed `quantity`+`operator` blob. Operators: `0` `<`, `1` `<=`,
  `2` `==`, `3` `>=`, `4` `>`, `5` `!=`. `>=0` is "always true"; `>=1` is "owns at
  least one".

So vanilla conditions can only ask *"does house X own ≥N of building B?"*. That is
enough to say "enemy has a War Factory" but **cannot** directly express "enemy has
**no** base defenses" or "nobody owns a shipyard" — there is no "absence" or
"category" condition.

## The trick: dummy prerequisite buildings

To express those richer conditions on vanilla/Ares, the tool generates invisible
**dummy buildings** whose mere presence encodes a base-state fact, then writes a
normal "owns ≥1 of dummy" trigger. A dummy is granted/removed by paired
superweapons (a *spawner* that gives it, a *killer* that takes it away when the
tracked real building appears), so:

- `DMPR###` — a **presence** dummy: the house owns it while it owns building B.
- `DMPRX###` — an **absence** dummy: the house owns it while building B is *missing*.
- Mixed dummies combine both ("has War Factory **and** has no Construction Yard").

Defense tracking uses a dedicated hierarchy (batch → per-faction master → master of
masters) so one dummy can mean "no defenses of **any** faction":

- `DMPRNODEF` — present while the house has **no base defenses at all**.
- `DMPRNODEFAA` — present while the house has **no anti-air** defenses.

Reading it back: a trigger with `ConditionType=0, ConditionObject=DMPRNODEF, >=1`
means **"an enemy currently has zero base defenses"** → launch the rush team.

## Scenarios generated

Enemy-based (`ConditionType=0`):
- **Enemy has no base defenses** → direct-attack rush, no gather.
- **Enemy has no anti-air** → aerial / air-heavy waves.
- **Enemy fields heavy anti-air** → switch to massed aerial pressure.
- **Aerial transport drop** → paradrop / transport infantry behind the line.
- **Enemy has no Construction Yard (but has a War Factory)** → immediate factory kill.
- **Enemy has no power** → strike while Powered= defenses are blind.

Self-based (`ConditionType=1`):
- **Own base undefended / lacks AA** → small emergency guard teams
  (`IsBaseDefense=no`) that patrol between friendly buildings.
- **MCV lost** (no CY, but has a Service Depot + War Factory) → build-MCV team,
  highest weight (StartW=5000) so recovery pre-empts everything.

Naval:
- **No enemy shipyard**, **fight for naval dominance**, **dominance pressure** —
  contest or exploit control of the water.

Each scenario emits the full chain it needs: a TaskForce, a ScriptType (patrols use
`54,0` regroup → `58,<cell>` move → `5,<ticks>` guard, looped with `6,1`), a
TeamType, and the AITrigger. Weights are tuned so reactive triggers out-prioritise
routine waves (enemy-undefended StartW≈500, self-defense emergencies ≈1000, MCV
rebuild 5000).

## Generating them in the tool

**Strategic Triggers…** on the AI (aimd) tab → pick factions / difficulties → Generate
(preview) → Insert. By default this is **aimd-only** — nothing touches rules.ini, and
the written chain adds zero new unresolved references.

The dummy buildings the conditions depend on must exist for the triggers to fire.
Tick **"Supporting base-state dummies (modifies rules.ini)"** to also generate them.
This is bounded on purpose:

- Only the four strategic categories (Construction Yards, War Factories, Shipyards,
  Power Plants) plus buildings that power `PoweredUnit=` units are targeted — not every
  building. Combo size defaults to 1 (singles); `mixed_scope='auto'` generates only
  the specific combos the triggers actually request. Result is a few dozen dummies,
  not a combinatorial blow-up.
- Adding the dummies roughly **quadruples** the triggers produced, because the
  naval / power / CY / defense scenarios can finally fire (≈18 → ≈72 on vanilla YR,
  Hard, all factions).
- In a Phobos mod the dummy sections are written to separate include files
  (`ai_prereq_present.ini`, `ai_prereq_absent.ini`, `ai_prereq_mixed.ini`,
  `ai_defense_dummies.ini`) alongside rules.ini; rules.ini itself only gains
  `[BuildingTypes]` / `[SuperWeaponTypes]` list entries and a small `[#include]`
  (about +150 lines on vanilla). Non-Phobos mods fall back to writing the sections
  inline.

The tool backs up **both** aimd.ini and (when dummies are enabled) rules.ini before
writing. The "Phobos script actions" toggle controls both the transport-drop action
(`10103,0`) and whether dummies use the `[#include]` split.

## Notes & caveats

- Strategic triggers and the dummy system are **engine-level AI content**, not a DLL
  hook — they work with vanilla logic (and better with Ares/Antares for superweapon
  limbo delivery). This is separate from the YR-Hook-Encyclopedia, which tracks
  Syringe/address hooks.
- The dummy hierarchy relies on superweapon `LimboDelivery`-style granting; verify
  in-game that dummies actually appear/disappear as real buildings change — that is
  the one thing the tool's tests can't exercise.
- Regenerating overwrites the `ai_prereq_*` / `ai_defense_dummies` include files; keep
  the rules.ini backup if you want to revert the list/`[#include]` edits too.
