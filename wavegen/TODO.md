# YR AI Wave Generator — TODO (long-term)

Items collected during Rex's session. Not to work on now; documented so
they don't get lost.

**ROUTING LEGEND (triaged with Rex, 2026-09-18).** Every open item carries a
`ROUTING →` tag. Owners: **TOOL** = this Python wave generator. **ATTExt** =
AITriggerTypeExt DLL (the playbook: per-trigger conditions/weights/debug +
per-TeamType/TaskForce opt-in tags — ScriptSwitch precedent). **DoctrineExt** =
global battlefield reflexes (sense→decide→act, threat fields, kill tracking).
**DossierExt** = knowledge of the PLAYER (cross-game profiles, economy
surveillance, trigger master-control — see ~/Claude/DossierExt/DESIGN.md).
**ScriptTypeExt** = future scripts DLL (new script actions + per-action debug).
**Antares** = extend/PR the framework (base planner, SW targeting, production
MacroHacks). **Country_Extension / PrerequisiteExt** = the sibling DLLs named.
Untagged sections are shipped history or reference notes.

## Content / roster
> **ROUTING → TOOL.**

- **`[CASLAB]` as a neutral tech capture target.**  Vanilla lists it in
  `NeutralTechBuildings=`.  Overview module currently misses it.

- **Skirmish script library — massive collection.**  The tool needs a
  curated library of script templates known to work in skirmish, drawn
  from vanilla aimd.ini and reputable community mods.  Compile from
  vanilla first, expand incrementally.  Each entry should record: the
  action sequence, what role/situation it's for, which mod it came
  from, and whether it's Phobos-only.

## Detection / grading
> **ROUTING → TOOL.**

- **Miner_Killer threshold too lax.**  `spectrum >= 1` means "any
  positive damage against a miner-armor unit" wins the tag.  GHOST
  gets tagged Miner_Killer just because his MP5 hurts medium-armor
  slightly.  Raise to `>= 2` and require the DPS band to actually be
  above average, not merely present.  Also worth thinking about: SLAV
  is a slave/miner, so units with strong anti-infantry AND anti-vehicle
  DPS (dual role) should get priority as slave/miner killers since
  they're good against both the slaves and the miner they orbit.

- **Ares / Phobos ArmorTypes support.**  `ARMOR_NAMES` is a hardcoded
  11-entry list.  If a mod defines custom armor names via Phobos
  `ArmorTypes`, they collapse to index 0 (`none`) and Verses lookups
  break.  Should scan the mod's ini for custom `[ArmorTypes]` entries
  and extend `ARMOR_INDEX` at parse time.

- **DPS-based wave role.**  `effective_dps` exists in the parser but
  isn't used as a wave-selection axis.  Would let the tool build waves
  that specifically prioritize high-DPS damage output regardless of
  target type.

## Overview report
> **ROUTING → TOOL.**

- **List ALL units highest→lowest**, not just top 15 / bottom 10.

- **Trigger count per unit.**  For each unit line, show N = how many
  AITriggerTypes reference a taskforce containing that unit.

- **Spawner sub-category under low/no-AA.**  V3 Rocket Launcher (missile
  spawner), Aircraft Carrier (aircraft spawner) — feasibly serve the
  same "hit stuff we can't reach" role as aerial units.  List them as
  a sub-section of the aerial-low-AA block.

- **Global Grade vs Role Grade split.**  Overall unit grade is one
  number; but for each section (AA, meatgrinder, artillery...) the
  unit deserves a role-specific grade.  HTK is mid-grade overall but
  high-grade as AA.

- **Script/TeamType/TaskForce feature counting.**  For each script
  action code used, count how many scripts use it.  Same for TeamType
  flags and TaskForce unit types.  Helps modders see coverage.

## Behavior / gameplay
> **ROUTING → TOOL.**

- **Infantry → Gunner/OpenTopped auto-loading.**  When a taskforce
  contains infantry and vehicles, and the mod has Gunner=yes or
  OpenTopped=yes vehicles, load the infantry into them.  Choose
  transport size by infantry count (≤16 → smaller transport is fine,
  larger → prefer bigger transport with more space).

- **Phobos `10103,0` Load Onto Transports.**  Constant exists but no
  template uses it.  Should replace the vanilla `14,0` action in a
  Phobos-mode template variant so multi-transport taskforces load
  correctly.

- **Mind-control detection + AIExt gate.**  Detect
  `ImmuneToPsionics=yes` units.  When enemy has mind-control units
  (MIND) or towers (YAPSYT), fire waves composed of MC-immune units
  (robot tanks, aircraft, etc.).  Requires a new AIExt condition on
  the mind-control units/buildings.

- **`[SPY]` and Engineer detection.**  SPY has `Agent=yes, TL=5`, so
  it IS buildable — the overview's spy filter misses it because SPY
  doesn't have "SPY" in `roles` (which the overview checks first) and
  the `Agent=yes` fallback probably fires but SPY isn't in
  `parser.buildable` in the vanilla_rulesmd used for testing.
  ENGINEER / SENGINEER / YENGINEER all have `Engineer=yes, TL=1` and
  should show up in the Engineers section.  Both work with the
  mod's rulesmd, only vanilla's small buildable list makes them
  disappear.

- **AI scanner default `none`.**  Change "Recommended: D" to
  "Recommended: none" and make "none" the actual default.

- **Specialist boost default `1.0`.**  Allow players to enter values
  <1.0 for scale-down effect.  Round up to prevent zero-count lines.


## Item 48 — DPS-based wave role (deferred from INSTRUCTIONS LIST)
> **ROUTING → TOOL.**

`effective_dps()` and `dps_vs()` already exist in the parser. Need to:

- Add a HIGH_DPS role to the role vocabulary
- Threshold: what DPS level qualifies?
- Composition rules for such a wave
- AIExt gate: when should it fire?
- Coordinate with existing HIGH_DPS role tag on some units

Rex: "Consider calculating DPS for units and making DPS waves."


## Item 56 follow-up — MC-immune wave role in the wave generator
> **ROUTING → TOOL.**

Item 56 landed detection (`is_mc_immune` on units, `MCThreatResult` scan,
`_apply_mc_conditions` AIExt gate). Still needed:

- Add `MC_IMMUNE` (or `ANTI_MC`) as a first-class role in
  `taskforce_generator.ROLE_UNIT_FILTERS`
- Filter must require `rec.is_mc_immune` when this role is active
- Optional: fallback role when no MC-immune units exist in a faction's
  buildable pool (probably `GENERAL_ATTACK`)
- Wire the role into `wave_generator._generate_general_waves` role rotation


## Community Strategy Guide Findings — Actionable Patterns
> **ROUTING → TOOL** (content patterns for generated waves).

Read through 12 of the 14 guides (2 blocked or partially fetched). Patterns
distilled below in priority order for future implementation.

### Combat mechanics / grading refinements

- **Cost / 25 = build-time seconds coefficient.** Confirmed across multiple
  guides; useful for phase-scaling triggers by build cost budget.
- **Veterancy = 3x unit's own cost in kills.** Already used implicitly by
  the grading system but could be surfaced in the overview.
- **Elite units gain armor-splitting on beam weapons** (Prism Tank beam
  splits at Elite, Tesla Tank bolt splits at Elite). Detection: `.Elite`
  suffix on Weapon= in section; suggests EliteAbilities= tag scan for
  bonus classification.
- **Miner armor rating is stronger than the tool assumes.** From Wan Zafran:
  "10 Prism tanks lost to 2 War Miners." Miners have ~1000 HP and their
  primary weapon (Machine Gun) is effective vs infantry. Reinforces the
  Miner_Killer role needing to prioritize anti-vehicle *and* anti-infantry
  DPS combined, not just anti-medium-armor.
- **HP thresholds matter:** GI=125, Grizzly=300, Rhino=400, Prism=250,
  Battle Fortress=1000, Apocalypse=800, Kirov=2000. Kirov's 2000 HP is
  the outlier; other units cluster tightly. Suggests grade "endurance
  bracket" bonus for HP >= 1500.

### Composition patterns worth encoding

- **Support tank behind tank.** Prism Tank behind Rhino (soaks damage);
  V3 behind Apocalypse (V3 fires long-range while tank tanks). Suggests
  a `[SUPPORT_FROM_BEHIND]` composition tag for long-range weak units
  paired with heavy tanks.
- **Repair IFV escort.** Vanilla AI does not use Engineer-in-IFV escort
  behavior. Would be a great AITriggerTypeExt DLL feature: Repair IFV
  auto-generated when TF has 3+ tanks. Composition side already thinks
  about this via Gunner detection.
- **Kirov escort:** Kirov + Apocalypse or Kirov + Flak Track chase.
  Slow airship with anti-air ground escort. Detection: any unit with
  HP >= 1500 and mobility=Aircraft gets tagged for `AERIAL_ESCORT`.
- **Battle Fortress with 5x Guardian GI or 5x Yuri Clone or 4x GGI+1x SEAL.**
  Composition-specific IFV loading. Item 52 groundwork.
- **Chrono Legionnaire IFV → Elite fast (Harvestors).** Range extension
  when in IFV. High priority tag when both unit types exist.

### Target-priority order (universally cited)

Standard target order that shows up in nearly every guide:
1. Ore Refinery / Miners (economy strangle)
2. War Factory
3. Construction Yard
4. Battle Lab / Radar Tower
5. Superweapon
6. Barracks (last, since it rebuilds fast)

This maps to AITriggerTypeExt condition targets. Suggests reworking the
target selection so early-phase waves prefer Refinery/Miner targets,
mid-phase prefer War Factory, late-phase prefer Superweapon/Battle Lab.
Currently the tool uses Ares HVT scanning; this ordering could refine it.

### Phase pacing

- **~2 minutes to first tank rush** (GNgo/GeneralZ agree). Rush-phase
  triggers should fire around this game-time band, not on unit count
  alone.
- **~5 minutes to first Harrier/Black Eagle strike.**
- **~10 minutes to first superweapon.**
- Phase-tag heuristic in current tool aligns with these bands roughly;
  worth double-checking `EARLY/MID/LATE/ENDGAME` timing.

### Anti-Yuri patterns (from every guide)

- Robot Tank is *the* answer to Yuri: MC-immune, water-crossing, moderate
  DPS. Every guide flags it. Detection: `ImmuneToPsionics=yes` +
  `Locomotor=RobotTank GUID`. Item 56 groundwork already detects this.
- Rocketeers vs Yuri Clones (Yuri can't MC air units).
- Snipers vs Yuri (long-range, no reveal). British Sniper stays back.
- Chrono Commando vs Yuri (Chrono ability + C4).
- Robot Tanks / MC-immunes should be **preferred composition members**
  when facing Yuri opponents. Currently the tool detects MC-immunity
  but doesn't yet compose MC-immune-only waves. Item 56 follow-up.

### Superweapon / defensive infrastructure

- **Force Shield around Con Yard** (Ares/Phobos supports this via
  ForceShield= tag). AI never uses this. Would be an AITriggerTypeExt
  feature: trigger fires Force Shield on Con Yard when enemy superweapon
  countdown < 15 seconds.
- **Undeploy Con Yard when enemy fires Airstrike** (harriers miss the
  smaller MCV). AI never does this. Also a DLL feature idea.

### Neutral tech buildings — priority order

Item 47 landed CATHOSP detection. Guides consistently rank:
1. **Oil Derrick** — instant $1000 + $20/2sec ongoing (universally #1)
2. **Airport** — 6 free GIs/Conscripts per drop
3. **Machine Shop** — auto-repair for all vehicles
4. **Hospital** — auto-heal for infantry
5. **Outpost** — mobile repair
6. **Power Plant** — +200 power

IMPLEMENTED (v26): `_tech_priority_score(sec)` in procedural_scripts.py
scores each neutral tech building by mechanical tags — Cash tags rank
highest, then vehicle repair, then superweapon grants, then infantry heal,
then power. `find_capturable_tech_buildings` sorts its output by this
score. Downstream callers (AITrigger capture generation picks first 4;
overview displays first 20) benefit automatically.

Caveat: Airport-style free-drops aren't tagged at the rules level in
vanilla YR (map-scripted via triggers), so CAAIRP scores 0. Guides rank
Airport #2 but the tool cannot mechanically distinguish it. If a mod
adds a proper Airport rules tag, the scoring can extend to catch it.

### Parasite drones + expendable transport (drop-bomb) — IMPLEMENTED (v25)

`is_parasite_drone` field on UnitRecord = `is_parasite AND category=='Vehicle'`.
`_maybe_transport_parasites` composition transform adds any Vehicle
transport (Passengers >= 1, SizeLimit >= drone size) when a taskforce
contains parasite drones and doesn't already have a carrier — OpenTopped
NOT required. Strategy is drop-bomb: transport is expendable delivery,
gets destroyed by enemy fire, and its parasite drones spill out to latch
onto nearby enemy vehicles. AI's flexible money makes the sacrifice a
worthwhile trade. Frustratingly effective anti-armor pattern for players
to face. Uses the existing LOAD_INFANTRY script templates (14,0 vanilla
or 10103,0 Phobos). Toggleable via `auto_transport_parasites` config field.

In vanilla YR the pattern is DRON (Terror Drone, Allied vehicle with
parasite warhead) delivered by any Vehicle transport (Flak Track is one
common option). In mods any custom Vehicle+parasite-warhead unit
qualifies as a drone; any Vehicle+Passengers>=1 unit qualifies as a
carrier. Fully pattern-based, no hardcoded unit IDs.

### Miscellaneous features that might not be worth implementing

- Terror Drone → Miner attack pattern (Cheese vs weak miners early —
  this is a very specific opening move, distinct from the general
  Flak Track composition above).
- Battle Fortress crushing walls / Guardian GIs / Characters.
- Aircraft Carrier hornets respawning cost = 50 credits (Osprey too).
- Amphibious Transport can carry 25 units via Flak Track stacking (bug).
- Chronosphere kills infantry (except Chrono infantry) inside its area.
- Iron Curtain kills infantry (except Curtained already) inside its area.

These are player-only tricks or bugs; not useful for AI wave generation.

### Two guides not fully readable

- faqs/14539 — blocked by bot detection. If retried, may need alternate
  fetch path or mirror.
- Second half of faqs/9899 truncated at token limit.


## DLL Priority Roadmap (agreed with Rex)

**Priority 1: Debug Message System.** Ship this first so every subsequent
feature can be verified live rather than through indirect symptoms. Rex's
concern: "a lot of times I do testing I feel it may be placebo what is
and isn't working." Debug output eliminates that.

Every AITriggerType.AIExt condition and every new feature gets three tags:

```ini
DebugMessageDisplay.Start=STT:CSFKey    ; when trigger begins building
DebugMessageDisplay.Cancel=STT:CSFKey   ; when trigger is cancelled
DebugMessageDisplay.Finish=STT:CSFKey   ; when trigger deploys
```

Per-condition debug output appends computed values to the CSF message
using a boolean flag next to a CSF key. Pattern:

```ini
<Feature>.<Index>.Debug.CSF=STT:MessageKey
<Feature>.<Index>.Debug.<Metric>=yes
```

Example:

```ini
DPSCheck.0.Debug.CSF=STT:AI_RUSH_DPSCheck
DPSCheck.0.Debug.DPSCount=yes         ; appends the calculated total
DPSCheck.0.Debug.Threshold=yes        ; appends the Min/Max being compared

CreditsRateCheck.0.Debug.CSF=STT:AI_ENEMY_BOOMING
CreditsRateCheck.0.Debug.Rate=yes     ; appends the credits/sec

TargetHouseMode.Debug.CSF=STT:AI_HOUSE_SELECTION
TargetHouseMode.Debug.House=yes       ; appends the resolved house name

RequiredOwnerBuildings.Debug.CSF=STT:AI_OWNER_BUILDINGS_OK
RequiredOwnerBuildings.Debug.Count=yes  ; appends the current count
```

DLL guard: `[Debug].DisplayAIWaveMessages=yes` at the top level toggles
all debug output off in shipped builds. Should default to no. Rex should
consider a shipping/debug build split so debug tags compile out entirely
in release, keeping perf clean.

**Priority 2: DPS Check core.** Weapon cache, group cache, sum function.
Auto scope only (All), no groups yet, no armor filter, no warhead filter.
Get a hardcoded trigger firing based on real live enemy DPS as smoke test.
Ship WITH debug support (Priority 1) so Rex can verify each check step.

**Priority 3: DPSCheck condition parser + [DPSGroupTypes] reading.**
Groups, scopes (AA/AG plus All), Types field, union semantics between
Lock and Types.

**Priority 4: Filter refinements + Ratio weights + Comparative form.**
ArmorType filter (multi-value, averaged Verses coefficient), Warhead
filter (intersection when combined with ArmorType), .Ratio= on group
entries, CompareTo=Friendly (sum-across-allies) + RatioMin/RatioMax.

**Priority 5: CreditsRateCheck.** Rolling window sample of house credits.
Independent subsystem, cheap. Include debug output (rate + window).

**Priority 6: AISuperWeaponValues.** Per-superweapon value tables with
HPWeighting axis (Direct = damaged structures score higher for
protection, Inverse = damaged structures score lower for attack).
Subsumes vanilla AIIonCannonXValue as a special case. Extends to
Force Shield, Iron Curtain, Chronosphere-offensive, Nuke, etc.

**Priority 7: AN/AS scopes for DPS.** Naval and Subterranean scope
detection. AN needs a projectile-flag heuristic (UseIn=Water or being
mounted on a naval unit). AS is rare enough to defer.

**Priority 8: Cross-check compound conditions.** "Fire when enemy has few
defenses AND enemy has no engineers to rebuild them" style compound
conditions. Depends on Priority 3 working.

**Priority 9 (much later): Cash rate comparative check.** Rex is
indifferent; defer until real use case emerges. Rejected for v1.

**Priority 10 (much later): Specific-friendly targeting.** House-name
field on comparative checks. `sum-across-allies` covers 90% of cases;
this is for the last 10%.


## Locked DPS Schema

Section for modder-declared unit groups:

```ini
[DPSGroupTypes]
GROUP_ONE=HTNK,MTNK
GROUP_ONE.Ratio=2.0,1.5

SlowTowers=SLOWTWR1,SLOWTWR2
SlowTowers.Ratio=10,10   ; each tower counts as 10 DPS worth

GeneralTanks=HTNK,LTNK,MTNK   ; no .Ratio means all 1.0
```

Trigger condition syntax (nested with .Property.0 order per Rex's call —
matches Prerequisite.List# convention):

```ini
; Bare minimum — one bound
DPSCheck.Max.0=50

; Full syntax
DPSCheck.Target.0=Enemy       ; Owner / Enemy / Friendly (default Enemy)
DPSCheck.Max.0=50             ; -1 for uncapped (default -1)
DPSCheck.Min.0=0              ; (default 0)
DPSCheck.Lock.0=AA,AG         ; projectile scope filter (default any)
DPSCheck.Types.0=GROUP_ONE    ; group names — UNION with Lock (default none)
DPSCheck.ArmorType.0=heavy    ; apply Verses vs armors (default raw, no coef)
DPSCheck.Warhead.0=Fire       ; restrict to warhead (default any)

; Comparative form (reinforcement AI)
DPSCheck.Target.1=Owner
DPSCheck.CompareTo.1=Friendly    ; sum-across-allies
DPSCheck.RatioMin.1=2.0
DPSCheck.Lock.1=AG

; Debug support (ship with every check)
DPSCheck.Debug.CSF.0=STT:CSFKey
DPSCheck.Debug.DPSCount.0=yes

; Cash rate
CreditsRateCheck.Target.0=Enemy
CreditsRateCheck.Min.0=100
CreditsRateCheck.Window.0=30
CreditsRateCheck.Debug.CSF.0=STT:CSFKey
CreditsRateCheck.Debug.Rate.0=yes
```

Semantics locked:
- If both Lock and Types are set: UNION (sum matching units by either
  filter; dedupe unit types so nothing double-counted)
- If ArmorType has multiple values: average Verses across those armors
- If both ArmorType and Warhead set: intersection (weapon must match both)
- If CompareTo is set: Min/Max are ignored, RatioMin/RatioMax govern
- If neither Min/Max nor RatioMin/RatioMax set: skip check, log warning
- Raw DPS by default: (damage × burst) / (rof / 10); no armor coefficient
- Missing check indices are fine; DLL scans .0, .1, .2... until empty
- Repair defenses: no special handling needed. Weapon DPS is
  instantaneous; a repaired defense counts full DPS until it dies.


## AISuperWeaponValues Schema
> **ROUTING → Antares** — it already owns SW-targeting AI (Ext/SWType/Hooks.Targeting.cpp); extend/PR there, don't reimplement.

Modder-declared per-superweapon value tables. Subsumes and extends the
vanilla `AIIonCannonXValue=` system:

```ini
[AISuperWeaponValues]
IonCannon.Target=Enemy
IonCannon.Weights=ConYard:100,WarFactory:100,Refinery:80,PowerPlant:60,BaseDefense:35
IonCannon.HPWeighting=Inverse       ; damaged structures score lower
IonCannon.FireThreshold=200

ForceShield.Target=Owner
ForceShield.Weights=ConYard:100,BattleLab:90,Refinery:60
ForceShield.HPWeighting=Direct      ; damaged structures score higher for protection
ForceShield.FireThreshold=150

IronCurtain.Target=Owner
IronCurtain.Weights=ConYard:100,ProdBuilding:80,MCV:200
IronCurtain.HPWeighting=Direct
IronCurtain.FireThreshold=150
```

DLL scans candidate cells (offensive superweapons) or owned structures
(defensive superweapons) and sums `base_value × hp_modifier` per matching
category. Fires when a cell exceeds threshold.

Cross-references vanilla `AIIonCannonXValue=` tags: those become a
built-in default IonCannon row if `[AISuperWeaponValues]` is absent, so
existing mods don't break.


## Dogs Hunting Engineers Pattern (defensive counter)
> **ROUTING → split.** Cheap version = TOOL (this wave, AIExt-gated, shippable now). Smart version = DoctrineExt rule (When=enemy engineers near base → Respond=Dogs).

Companion to the tech-storming pattern Rex mentioned. When the AI detects
enemy engineers on the map, compose a small dog pack with a hunt-quarry-
infantry script. Fires whenever engineers are visible; low priority so
it doesn't spam. AIExt gate:

```ini
RequiredEnemyUnits=ENGINEER,SENGINEER,YENGINEER
RequiredEnemyUnitsMin=1,1,1
```

Composition: 3-4 dogs, hunt script. Faction-generic (any faction with
dogs). Reuses existing hunt actions. Not implemented yet; probably
half-hour of work when we get to it.


## Chrono-Legionnaire Airstrike Bait (parked)
> **ROUTING → DoctrineExt** (reactive per-unit state machine). Stays parked.

Chrono Legionnaire force-fires on own Con Yard, then chronoshifts out at
the frame Harrier missiles impact — missiles hit empty ground. Rex flagged
as feature creep with massive reward. Requires a state machine per Con
Yard per airstrike with corner cases (Chrono Legionnaire dies mid-
sequence, airstrike cancelled, second airstrike overlaps). Reward vs
scope is genuinely uncertain. Park indefinitely.


## Undeploy Con Yard on Airstrike (parked)
> **ROUTING → DoctrineExt** (airstrike-reaction reflex). Stays parked.

Con Yard undeploys when Harriers are inbound — the MCV target is smaller
and harder to hit. Would make the AI feel very human. Also feature creep;
requires airstrike prediction plus MCV timing. Park.


## Force Shield / Iron Curtain expansion (superseded by AISuperWeaponValues)

Vanilla AI already handles nuke defense on Con Yard automatically. The
gap Rex identified is (a) protecting non-Con-Yard structures and
(b) Iron Curtain never being used defensively. Both covered by the
AISuperWeaponValues schema above; specific FSS_ tags aren't needed.


## Debug Messages for Scripts (later)
> **ROUTING → ScriptTypeExt** (per-action debug belongs with the scripts DLL).

Same CSF-based pattern per script action. Phobos has ScriptType action
logging via debug log but not CSF-message display. Genuine gap that
would help mod debugging enormously:

```ini
[MyScript]
Actions=53,0,0,1,49,0
DebugMessage.53.CSF=STT:AI_GatherAtEnemy
DebugMessage.0.CSF=STT:AI_AttackQuarry
DebugMessage.49.CSF=STT:AI_RegisterSuccess
```

Priority: after the AITrigger debug system is stable.



## Notes — Community Strategy Guide Reference

Rex asked for per-guide summaries plus the overlap distillation. Honest
caveat: I fetched each guide but didn't take separate notes at the time —
they overlapped substantially and I extracted patterns rather than
transcribing individual documents. Per-guide summaries below are based on
what stood out during read; overlap distillation is the more reliable
record and lives in "Community Strategy Guide Findings" earlier in this
file.

### Per-URL notes

**https://gamefaqs.gamespot.com/pc/476890-.../faqs/9899**
Stats compendium — unit damage tables, weapon mechanics, verses percentages.
Overlaps heavily with faqs/30732. Useful as a cross-reference for
verifying my parser's DPS computation matches player observation. Second
half was truncated at token limit; the covered portion was primarily
weapon stats tables.

**https://gamefaqs.gamespot.com/pc/476890-.../faqs/51130**
Overview guide with tactical patterns. Less stat-heavy than 9899. Notable
for phase pacing observations that fed into the "~2 min tank rush,
~5 min air strike, ~10 min superweapon" bands.

**https://gamefaqs.gamespot.com/pc/476890-.../faqs/11041**
Rich tactical content. Multiple new patterns extracted here that showed
up in the overlap distillation. Kirov+Apocalypse escort pattern, tank
support (V3 behind Apoc) pattern, target-priority order all came from
this guide's neighborhood.

**https://gamefaqs.gamespot.com/pc/476890-.../faqs/10445**
Build orders and demo-truck-in-transport pattern. Latter isn't
implementable for AI (needs mid-combat micro), noted in TODO as
player-only.

**https://gamefaqs.gamespot.com/pc/476890-.../faqs/9546**
Phase-specific unit ratings — early game, mid game, late game categories.
Fed into HP threshold and Endurance Bracket bonus ideas.

**https://gamefaqs.gamespot.com/pc/476890-.../faqs/9421**
Source of Cost/25 = build-time coefficient observation. Broad tactical
overview covering rush, boom, and turtle strategies.

**https://gamefaqs.gamespot.com/pc/476890-.../faqs/14539**
BLOCKED by GameFAQs bot detection. Never successfully fetched. If Rex
retrieves this manually and shares the content, worth reviewing.

**https://gamefaqs.gamespot.com/pc/476890-.../faqs/31699**
Comprehensive strategy guide. Rich content matching the overlapping
patterns.

**https://gamefaqs.gamespot.com/pc/476890-.../faqs/18220**
Very long strategy guide. Confirmed patterns across all faction pairings.
Reinforced the anti-Yuri patterns (Robot Tank, Rocketeer counter).

**https://gamefaqs.gamespot.com/pc/476890-.../faqs/30732**
Stats-only unit-by-unit guide. Cross-reference for weapon damage and
armor verses. Overlaps with 9899.

**https://gamefaqs.gamespot.com/pc/476890-.../faqs/9752**
Tactical guide with build orders and phase pacing. Confirmed the ~2 min
tank rush timing.

**https://gamefaqs.gamespot.com/pc/476890-.../faqs/9751**
Detailed tactical patterns. Terror Drone strategies (miner cheese,
Flak Track delivery) showed up here — the Flak Track composition pattern
now implemented in v25 was inspired partly by this guide.

**https://gamefaqs.gamespot.com/pc/476890-.../faqs/10486**
GNgo's comprehensive guide. Build orders with exact timings, DPS-driven
tactical advice, phase pacing. One of the most useful individual guides.

**https://forums.cncnz.com/topic/4715-yr-top-three-tactics/**
Forum thread on top YR tactics. Confirmed the target-priority order
(Refinery > War Factory > CY > Battle Lab > SW > Barracks) from a
community-consensus angle.


### Overlap Distillation

The patterns below appeared in three or more guides — this is the more
reliable signal than any single guide's take.

**Phase pacing (universal consensus):**
- ~2 minutes: first tank rush window
- ~5 minutes: first air strike window
- ~10 minutes: first superweapon window
- Cost/25 = build-time in seconds

**Target priority order (universal consensus):**
1. Ore Refinery / Miners (economy strangle)
2. War Factory
3. Construction Yard
4. Battle Lab / Radar Tower
5. Superweapon
6. Barracks (last — rebuilds fast)

**HP endurance clusters:**
- Light: 125-300 (infantry, medium tanks) — most units
- Heavy: 300-800 (heavy tanks, key vehicles) — worth the AI investing in escort
- Superheavy: 1500+ (Kirov, siege units) — Kirov's 2000 HP is outlier

**Composition patterns (multiple guide consensus):**
- Support-behind-heavy: V3+Apoc, Prism+Rhino, artillery-behind-tank universally endorsed
- Kirov+ground-AA escort: slow airship with anti-air ground escort
- Battle Fortress + 5 Guardian GI / Yuri Clone: composition-specific IFV loading
- Anti-Yuri: Robot Tank (MC-immune) as universal answer, Rocketeer/Sniper as air/range counters
- Chrono Legionnaire in IFV: range-extension for anti-Harvester harassment
- Repair IFV escort: guides observe AI never does this (missed opportunity)

**Miner strength observation (from Wan Zafran's guide, 9546):**
"10 Prism tanks lost to 2 War Miners." Miners have 1000 HP + machine
gun. Reinforces Miner_Killer role prioritizing dual anti-inf + anti-veh
DPS, not just anti-medium-armor damage.

**Elite unit bonuses (Prism, Tesla, Grizzly):**
Beam-splitting and burst-multiplier bonuses at Elite veterancy. Grade
should reward EliteAbilities= tag scan.

**Neutral tech ranking (universal):**
1. Oil Derrick (instant $1000 + $20/2sec)
2. Airport (6 free GIs per drop) — NOT rules-detectable
3. Machine Shop (fleet auto-repair)
4. Hospital (infantry auto-heal)
5. Outpost (mobile repair)
6. Power Plant (+200 power)

Implemented in v26 via `_tech_priority_score()`. Airport unrankable.

**Player-only tricks (skipped for AI wave generation):**
Demo-truck-in-transport, Terror-Drone-in-Flak-Track kamikaze (implemented
as scripted pattern, but the manual variant is player-only), Chrono
Legionnaire force-fire-then-chronoshift airstrike bait, Iron Curtain
into water/cliff to force units through, Force Shield during Chronosphere
window abuse, Flak Track passenger stacking bug (25 units via Amphibious
Transport passenger).



## ScriptType Argument Extensions (Rex idea, 2026-07-12)
> **ROUTING → ScriptTypeExt.**

Extend individual ScriptType actions with per-action override tags via a
sidecar section pattern, mirroring what AITriggerTypeExt does for triggers.

Rex's specific ask: control the gather radius for Script 53 (Gather at
Enemy Base). Default radius makes teams cluster too tight or too loose;
per-script override lets modders tune assembly for specific compositions
(Kirov teams want tight gather, Terror Drone rushes want loose gather).

**Proposed syntax:**

```ini
[MyScript]
Actions=53,0,20,300,0,1,49,0

[MyScript.PhobosExt]           ; sidecar section on the script
Action.0.GatherDistance=8      ; override for action index 0 (the 53,0)
Action.2.WaitJitter=60         ; ±60 frame randomization on action 2 wait
```

Alternative shape — per-action-type override applied to every occurrence:
```ini
[MyScript.PhobosExt]
Action.53.GatherDistance=8     ; all 53,X in this script use radius 8
```

**Candidates for similar per-action extensions:**
- Script 6  (Move to waypoint) — completion tolerance radius
- Script 16 (Wait) — jitter/randomization on wait duration
- Script 20 (Do the same for X seconds) — jitter on repeat duration
- Script 53 (Gather at Enemy Base) — gather radius (Rex's primary ask)
- Script 54 (Regroup at Base) — regroup radius

**Implementation approach:**
1. Extend `ScriptTypeClass` with an ExtData following the same Container
   pattern as AITriggerTypeExt (fresh clone of the infrastructure).
2. Read `Action.X.*` per-index and `Action.<type>.*` per-action-type tags
   from the sidecar section.
3. Hook the individual action handler functions in gamemd.exe. Each
   action has its own handler — Ghidra work to find them.

**Scope:** ~1 day per action once its handler is located in Ghidra. The
extension infrastructure is a copy of what AITriggerTypeExt does. Multiple
actions can ship together as one "ScriptType arg override" mini-feature.

**Priority:** LOW. Debug system + DPS Check first. This is a nice-to-have
that improves AI feel but doesn't unblock other work.



## Overview Report Enhancements (Rex ask, 2026-07-12)
> **ROUTING → TOOL.**

The overview report currently lists AIExt tag inventory but not context
around WHY those tags were emitted or what settings the modder needs
to enable in rulesmd.ini.

**Requested additions:**

1. **List all rulesmd.ini entries added or modified by the tool.**
   Current: doesn't touch rulesmd. Future: if we start emitting
   `[Debug].DisplayAIWaveMessages=both` or similar, list what got set.
   For now, a section explaining "no rulesmd changes are made; you must
   manually add [Debug].DisplayAIWaveMessages=both to see debug output".

2. **RelaxedStray stats.**
   Explain how many AI triggers use RelaxedStray, how many required it
   to be larger than default, how many are TOO LARGE for the current
   RelaxedStray value and are skipped. Ties into the trigger-emission
   logic where wave size vs RelaxedStray gets validated.

3. **Debug Toggling: what features required it.**
   List the specific tags emitted that need `[Debug].DisplayAIWaveMessages`
   turned on to have visible effect:
   - DebugMessageDisplay.Start/Cancel/Finish (overlay)
   - DebugLog.Start/Cancel/Finish (log file)
   - All per-condition Debug.MessageDisplay / .LogMessage / .ValueDisplay
     tags
   - Details display fields
   Report format: "Debug tags emitted: 84 triggers × 9 sub-fields = 756
   debug directives. Requires [Debug].DisplayAIWaveMessages=both in
   rulesmd.ini to activate."

Priority: LOW. Ship after DPS Check core (Priority 5 in main roadmap).
Overview text improvements don't unblock DLL work.



## ScriptType Debug Extension (Rex, 2026-07-13)
> **ROUTING → ScriptTypeExt.**

Rex confirmed in-game testing: AITriggerType lifecycle events (Start/Cancel)
fire correctly, but the trigger-level "Finish" event never appears because
the team-spawn hook hasn't been located yet. This uncovered the need for
ScriptType-level observability too, since ScriptTypes are what actually
execute after the team is spawned.

**Full observability chain needed:**

- AITriggerType.Start          — prereqs passed (WORKS)
- AITriggerType.Cancel         — AIExt veto (WORKS)
- AITriggerType.Finish         — team dispatched (needs disassembly)
- ScriptType.Start             — script begins (needs new DLL work)
- ScriptType.ActionStart.N     — action N begins execution
- ScriptType.ActionComplete.N  — action N finishes
- ScriptType.Finish            — script done / team disbanded

**Use case**: watch a wave from prereqs → spawn → gather → move → attack →
success in real time. Answers questions like "did my Kirov wave reach the
target?" and "which action is the AI stuck on?".

**Implementation**: brand-new ScriptTypeExt DLL sidecar OR add ScriptType
handling to the existing AITriggerTypeExt DLL (probably cleaner separation
as its own DLL matching Phobos's pattern).

**Sidecar section format** would mirror AITriggerType:
```ini
[MyScript]
Actions=53,0,20,300,0,1

[MyScript.AIExt]
DebugMessageDisplay.Start=NOSTR:MyScript begins
DebugMessageDisplay.Finish=NOSTR:MyScript ends
DebugMessageDisplay.ActionStart.0=NOSTR:Gathering at enemy base
DebugMessageDisplay.ActionComplete.0=NOSTR:Reached enemy base
```

**Priority**: after AITriggerType debug is fully functional (per-gate value
display + veto reason), before DPS Check. Rex flagged it as necessary for
verifying wave behavior in-game.



## AI Wave Debug Lifecycle — Full 8-Event Vocabulary (2026-07-14)

Rex + Claude conversation established the correct lifecycle terminology
after in-game testing revealed the previous "Start" event was
mis-named: it fires when a trigger PASSES prereqs, which is upstream
of the actual team-spawn moment.

**Full lifecycle vocabulary — locked:**

1. **Consider** — trigger passed AIExt gates, now in AI weight
   competition. Fires ~1x per frame per eligible trigger. This is what
   the DLL currently calls "Start" (mis-named).
2. **Cancel** — AIExt vetoed. (Already working.)
3. **Reject** — trigger considered but lost weight competition, no
   team spawns. Distinct from Cancel because Cancel = AIExt veto,
   Reject = vanilla YR weight-based rejection.
4. **Start** — team spawned. TeamClass exists. Factories about to
   receive build orders. Requires new hook (WhoIsBest → team creation).
5. **FirstUnit** — first unit finished production and added to team.
   Actual "team assembly begins" moment.
6. **Finish** — script begins executing / team fully assembled and
   dispatched. Distinct from Start (team object created) and FirstUnit.
7. **Destroyed** — last unit died. Team object being destroyed.
   Failure path.
8. **Deleted** — team finished its script actions successfully and
   disbanded. Success path. Likely same underlying function as
   Destroyed, distinguished by team state or script completion flag.

**Priority ordering (from this session):**

- Priority 1a — Rename Start→Consider throughout DLL and tool (THIS SESSION)
- Priority 1b — Per-index Cancel detail (previous Priority 1)
- Priority 2 — Find team-spawn hook, wire real Start event (Ghidra)
- Priority 3 — Find team-destroy hook, wire Destroyed AND Deleted (Ghidra)
- Priority 4 — FirstUnit event (Ghidra, small hook)
- Priority 5 — Reject event (weight-loss hook)
- Priority 6 — Per-unit build progress messages (see below)
- Priority 7 — Finish event (may collapse into Start or FirstUnit)

## TeamType-scoped Destroyed messages (Rex, 2026-07-14)
> **ROUTING → ATTExt, SHIPPED 2026-09-18** (commit df44591, CI green run
> 35414212848, DLL deployed byte-verified 260096 bytes) as the "TeamType
> sidecar pair" — `[TeamID.AIExt] DebugMessageDisplay/DebugLog .Destroyed/
> .Deleted`, new hook 0x6E8DE0 (TeamClass destructor entry, fires for every
> team of the type regardless of provenance). See INI_REFERENCE.md "Team-scoped
> Destroyed / Deleted messages". This entry was left stale after shipping —
> the design below is history, not a to-do.

Rex wants BOTH per-AITriggerType AND per-TeamType Destroyed messages:

- **AITriggerType-scoped**: "the team from THIS trigger got destroyed"
  — attached to the `[TriggerID.AIExt]` section. Fires once per team
  instance created by that trigger.
- **TeamType-scoped**: "any team of THIS TeamType got destroyed" —
  attached to a `[TeamTypeID.AIExt]` section. Fires for every team
  instance of that type, regardless of which trigger spawned it.

Both should fire when both are configured — no exclusion.

**Implementation approach**: extend the current AITriggerTypeExt DLL
(don't split into separate DLL — Rex's call for single-DLL
consolidation). Adding TeamType sidecar sections requires a new
Container<TeamTypeExt> or similar, but reuses same infrastructure
patterns. Bigger scope but conceptually straightforward.

**Ghidra target**: TeamClass destructor at ~0x0X (unknown yet).
Same hook fires both events; parameters or team state distinguishes
Destroyed (failure — last unit died) vs Deleted (success — script
completed and disbanded).

Priority: after Priority 2-4 (need at least Start + Destroyed hooks
working for single AITriggerType level before extending to TeamType).


## Per-Unit Build Progress Messages (Rex idea, 2026-07-14)
> **ROUTING → ATTExt** (debug-message family).

Rex asked whether the DLL could emit a message per unit added to a
building team:

  `{AITriggerID}: Taskforce {Taskforce ID} E1 built 5/10`

**Feasibility**: medium scope, valuable for verifying wave assembly
progress in real time.

**Requires**:
- Hook where a unit is added to a team (TeamClass::Add or unit-added
  callback in Ghidra — unknown address)
- Look up the parent AITrigger + TaskForce IDs from the team instance
- Track per-unit-type counts in the taskforce (target quantity vs current)
- Format message with all four pieces of info

**INI-side sub-fields**:
```ini
DebugMessageDisplay.UnitAdded=NOSTR:{trigger} building {tf} unit {n}/{max}
DebugLog.UnitAdded=Trigger {trigger} taskforce {tf} added {unittype} ({n}/{max})
```

Would need substitution tokens (`{trigger}`, `{tf}`, `{n}`, `{max}`,
`{unittype}`) — NEW capability. Currently the tool bakes IDs into
messages at generation time. Runtime substitution is a step up in
complexity but bounded scope.

**Alternative for lower scope**: fire the message with just the
FirstUnit event (see Priority 4), no counter, no per-unit spam. Modder
can enable/disable per-trigger. Less info but simpler.

**Priority**: after core lifecycle events land (Priorities 2-4). File
under Priority 6.


## About NOSTR: substitution (clarification from this session)

`NOSTR:` does NOT support runtime placeholder substitution — that's
purely an Ares "literal text, no CSF lookup" prefix. What Rex has been
seeing (`336056CA-G started`) with trigger IDs in messages is because
the WAVE GENERATOR TOOL bakes each trigger's ID into its own hardcoded
`NOSTR:` string at aimd.ini generation time.

For per-unit progress messages to work with runtime substitution,
we'd need to implement OUR OWN placeholder syntax in the DLL that
scans the message for `{token}` and substitutes runtime values.
Non-trivial but bounded. Documented above under "Per-Unit Build
Progress Messages".



## Taskforce Randomization System (Rex, 2026-07-14)
> **ROUTING → ATTExt** (TaskForce-scoped tag resolved at team-build; MUST roll from the synced ScenarioClass RNG — MP determinism).

New INI feature for injecting randomization into taskforce composition
at team-build time. Modder-declared unit groups + inline random-range
notation on quantity, with a shuffle mechanism to pick between multiple
groups on the same taskforce line.

### Syntax

**RandomRange(X,Y)** — replaces the quantity field on a taskforce line
with a random integer in [X, Y] inclusive, rolled once at team-build
time.

```ini
[TASKFORCE]
0=5,E1
1=RandomRange(0,5),E1
```

Above builds 5-10 E1 total (fixed 5 + random 0-5).

**[RandomTaskforceGroups]** — modder-declared unit groups keyed by
name. When a taskforce line references one of these names as its unit
type, the DLL picks ONE unit from the group at team-build time.

```ini
[RandomTaskforceGroups]
RANDOMMBT=HTNK,MTNK,LTNK

[TASKFORCE]
0=5,MTNK
1=5,RANDOMMBT
```

Above possibilities on line 1: 5 HTNK, 5 MTNK, or 5 LTNK. Combined
with line 0: any of 5 HTNK+5 MTNK / 10 MTNK / 5 LTNK+5 MTNK.

**ShuffleRandom=idx,idx,...** — on a taskforce that references
RandomTaskforceGroups, ShuffleRandom lists which indices should
individually re-roll their group pick per unit rather than as a
single pick for the whole quantity.

```ini
[RandomTaskforceGroups]
RANDOMINF=SHK,FLAKT
RANDOMMBT=HTNK,MTNK,LTNK

[TASKFORCE]
0=5,E2
1=5,RANDOMINF
2=2,RANDOMMBT
ShuffleRandom=2
```

Line 1 picks one from RANDOMINF for all 5 units (either 5 SHK or 5 FLAKT).
Line 2 (in ShuffleRandom) picks per-unit — each of the 2 units
independently rolls RANDOMMBT. So line 2 could be 2 HTNK, 1 HTNK+1 MTNK,
1 HTNK+1 LTNK, 2 MTNK, 1 MTNK+1 LTNK, or 2 LTNK.

Total possibilities in this example: 12 (2 line-1 choices × 6 line-2
combinations). Rex enumerated all 12 explicitly in the request.

### Lookup rule

When a taskforce line references a name as its unit type, resolve in
this order:
1. Does the name exist in [RandomTaskforceGroups]? If yes, use as a
   group, pick per rules above.
2. Otherwise, treat as a literal TechnoType ID (existing behavior).

Names in [RandomTaskforceGroups] shadow same-named TechnoTypes if any
collision. Modder responsibility to avoid collisions or intentionally
use collision to override.

### Interaction with ShuffleRandom on non-random entries

Rex's example:
```ini
[RandomTaskforceGroups]
GHOSTGROUP=GHOST,IVAN

[TASKFORCE]
0=5,E1
1=6,GHOST
ShuffleRandom=1
```

Line 1 references GHOST which is a TechnoType, not a
RandomTaskforceGroups key. ShuffleRandom=1 is a no-op here because
there's no random group on line 1. Result: always 5 E1 + 6 GHOST.

### Implementation notes

Requires a new sidecar Container on TaskForceClass (similar to how
AITriggerTypeExt sidecars TriggerTypes). Or piggyback on TaskForceClass
LoadFromINI via a hook if we want to avoid a new Container.

The RandomRange() syntax is a parser change — the quantity field
becomes a small expression instead of a literal integer. Regex or
manual parser both work.

**Priority**: after debug system core is complete (Priorities 1-5).
Filed under Priority 8. Big value for wave variety without needing
5x as many taskforces defined by hand.


## AI + Tech Secret Lab (Rex, 2026-07-14)
> **ROUTING → ATTExt** (per-house grant tracking + taskforce keyword). LOW.

Vanilla YR's AI never uses the Tech Secret Lab (captured neutral
building that unlocks a random unit from the enemy side's roster).
Modder-side taskforces would benefit from being able to reference
"whatever this house's Secret Lab has provided" as a unit slot.

**Feasibility**: bigger scope than most items here. Requires:
- Tracking which unit was granted by the Secret Lab per-house
- New taskforce syntax to reference it (`SECRETLAB` special keyword,
  or a per-house dynamic group)
- Integration with the RandomTaskforceGroups system above if that
  ships first

**Priority**: LOW — nice to have but not blocking core AI improvements.


## Research references (Rex, 2026-07-14)

Two additional strategy guides Rex wants to mine for AI-modding
insights (predicting human behavior, teaching AI to counter it):

- https://xwis.net/forums/index.php/topic/62084-yr-players-guide/
  YR player's guide — human strategy patterns useful for training AI
  counter-behaviors.
- https://gamebanana.com/tuts/7669
  Second reference tutorial (context unclear from URL — need to check
  content for AI-relevance).

Add to the existing guides section when next fetched. Cross-reference
against overlap distillation.


## Investigate AI Team-Build Startup Delay (Rex, 2026-07-14)
> **ROUTING → research** (engine RE; conclusions may land anywhere).

Rex observation: the AI never rushes because it has no ability to
build any trigger at very early game state. Something in the delay
system prevents team-build attempts in the first ~30 seconds even
when prereqs would be met.

**Rules tags to investigate**:

- `TeamDelays=2000,2500,3500` — interval between AI checks for team
  creation, by difficulty (easy/medium/hard). At 15 frames/sec, 2000
  frames = ~2.2 minutes. That's the interval BETWEEN attempts, not
  the initial delay before FIRST attempt. But if the game starts at
  frame 0 and next attempt is at frame 2000, that IS effectively the
  first-team delay for easy AI.
- `AIHateDelays=30,50,70` — delay before AI picks an enemy. Prevents
  targeting until this fires. AI can't create attack teams without
  an enemy picked. 30 frames = 2 seconds, so not the culprit.
- `AIAlternateProductionCreditCutoff=1000` — spending strategy
  threshold. Not a delay per se but affects team-build willingness.

**Hypothesis**: TeamDelays is the primary culprit. AI's first team-
creation check is offset by the delay value from game start. Reducing
the numbers should enable faster rushes.

**Test approach**:
1. Set `TeamDelays=15,15,15` (one second between attempts).
2. Set up an AITriggerType with rush prereqs (buildings AI has at
   frame 0: usually just ConYard + a barracks after ~30 seconds).
3. Observe whether AI starts building rush units within 60 seconds.

If TeamDelays isn't the culprit, deeper investigation into
AITriggerTypeClass::WhoIsBest or team-creation code path needed.

**Related idea**: expose per-trigger delay override via AIExt tag —
`InitialDelay=` on the trigger itself, letting modders say "this
trigger is eligible after N frames from game start regardless of
TeamDelays". Would need a new hook to check per-trigger. Bigger scope
but bounded.

**Priority**: research first (short — Rex can test TeamDelays alone
in an hour of skirmish testing). Implementation only if research
confirms a real problem TeamDelays alone can't solve.



## Per-Trigger Weight Delta Overrides + Cross-Trigger Cascades (Rex, 2026-07-14)
> **ROUTING → ATTExt, SHIPPED.** The beyond-per-game ambitions — cross-game /
> per-player trigger track record, weight priors at game start, trigger
> families, habit-based enable/disable — were MOVED (2026-09-18) to
> **DossierExt DESIGN.md §6a "Trigger track-record learning"**. Don't grow
> them here.

Vanilla YR has three rulesmd tags governing AI trigger weight adjustment
after a team succeeds or fails:

```
AITriggerSuccessWeightDelta=20        ; weight increase on team success (vanilla: 5)
AITriggerFailureWeightDelta=-50       ; weight decrease on team failure (vanilla: -20)
AITriggerTrackRecordCoefficient=1     ; scaling on cumulative track record
```

These are global — every trigger gets the same delta. Rex's proposal:
per-trigger overrides via AIExt sidecar, plus the ability for one
trigger's outcome to cascade weight changes to OTHER listed triggers.

### Basic override syntax

```ini
[MyAerialTrigger.AIExt]
SuccessWeightDelta=30       ; override global AITriggerSuccessWeightDelta
FailureWeightDelta=-30      ; override global AITriggerFailureWeightDelta
TrackRecordCoefficient=2    ; override global scaling
```

### Cross-trigger cascade — the killer feature

Rex's exact use case: "if an aerial group fails, it goes in AITriggerExt
and looks at how much weight to lower and which other triggers to lower
too". So a single trigger's failure can penalize weights on OTHER
related triggers even if those didn't run.

```ini
[MyKirovRush.AIExt]
FailureWeightDelta=-40
FailureCascadeTargets=OtherAerialRush1,OtherAerialRush2,BombardierWave
FailureCascadeTargets.Delta=-15    ; each listed target loses 15 weight

SuccessCascadeTargets=RelatedGroundPush
SuccessCascadeTargets.Delta=+10    ; success helps allied ground trigger too
```

Semantics:
- Primary trigger's own weight adjusts by FailureWeightDelta/SuccessWeightDelta
- Then each CascadeTarget adjusts by CascadeTargets.Delta
- Cascade deltas can differ per-target if we add
  `FailureCascadeTargets.Deltas=-15,-10,-5` (positional, matches
  Targets list) — future extension

### Use cases

- **Aerial group fails → other aerial groups penalized** (defenses
  proven strong, don't try again soon)
- **Ground push succeeds → other ground triggers weighted UP** (this
  approach is working, keep pressure)
- **Rush failure → penalize ALL early-game triggers** (game state
  moved past rush window)
- **Mid-game shift → success of first mid-game trigger boosts other
  mid-game triggers** (AI committing to phase)

### Implementation

Ties naturally into Destroyed/Deleted hook work (Priority 3 of the
lifecycle system). Same hook that fires the lifecycle event has
access to:
- The trigger that spawned this team
- Success vs failure state
- The current weights of all triggers (via AITriggerTypeClass::Array)

Adding weight modification per Rex's syntax is straightforward once
the hook lands. No additional Ghidra work needed beyond what's
already planned for Destroyed/Deleted.

### Interaction with vanilla weight system

Vanilla YR handles weight adjustment via a code path that reads
AITriggerSuccessWeightDelta/AITriggerFailureWeightDelta. We hook that
path (post-adjustment) and:
1. If the trigger has per-trigger overrides, UNDO vanilla's global
   delta and apply the override delta.
2. Apply cascade deltas to the listed target triggers.

Alternative: hook PRE-adjustment and replace the whole delta value.
Cleaner but requires knowing exactly where vanilla reads the delta.

**Priority**: 6. After Destroyed/Deleted hook lands (Priority 3), this
becomes small extension work — maybe 100 lines of DLL code once the
hook exists.

**Value**: HIGH. Transforms YR's flat weight-adjustment into an
expressive AI-behavior-modeling system. Modders can encode strategic
learning ("if this approach failed, try that other approach") directly
in trigger metadata.



## Diag Hook Scope Investigation (2026-07-14 findings)

In-game testing at TeamDelays=15,15,15 with 84 wave-gen triggers revealed:

- **Diag hook (0x41E720) narrow scope**: 4835 Diag events across only 5
  unique trigger IDs. All 5 are named "INFANTRY_RUSH_EARLY" (NOD,
  British ×2, Arabs ×2 with Early Medium/Hard variants).
- **Team creation log** shows 15+ different team types being created
  (Soviet Dogs, Arabs Hero Escort, Soviet Rush, Soviet Overcharge
  Patrol, etc.) — many NOT among the 5 in Diag.
- Conclusion: Diag hook fires only for a subset of evaluations. Might
  be tied to a specific code path (WhoIsBest with team slot available,
  or specific INI value combination).

**Impact on Priority 1b**: gate hook at 0x41EAC0 is the correct place
for per-index detail — it fires for every trigger that passes vanilla
ConditionMet. Diag hook's narrower coverage doesn't affect Priority
1b's utility.

**Rush behavior observation**: 
- Soviet Early Infantry Rush team spawned 6 times
- Arabs Early Infantry Rush team spawned 2 times
- NOD/British rushes: 0 spawns despite Diag showing them evaluated

Rex's original "AI doesn't rush" observation may be side-specific.
Soviets rush fine, NOD/British don't. Priority 1b will confirm whether
this is AIExt veto or vanilla ConditionMet difference.


## Success Event Debug — Confirmation of Existing Plan (Rex, 2026-07-14)

Rex flagged wanting "successful" debug events for triggers whose teams
COMPLETE their script actions successfully. This maps cleanly to the
already-planned **Deleted** lifecycle event.

**Vocabulary confirmation**:
- **Destroyed** = failure path (last unit died before script complete)
- **Deleted** = SUCCESS path (script completed, team disbanded normally)

Both are Priority 3 in the lifecycle roadmap (Ghidra work to find team
destruction/deletion hook). Both use the same underlying game hook,
distinguished by team state at destruction time — check whether the
script completed all actions or not.

**Emit format per event** (already designed):
```
[AIExt Deleted] 73EB11E9-G: team completed successfully
[AIExt Destroyed] 73EB11E9-G: team destroyed (last unit died)
```

**Value**: "success debug" tells modder which triggers are actually
producing effective teams vs which spawn but never accomplish their
script. Combined with per-index Cancel detail (Ship #3 just landed),
this gives full accountability: why triggers get vetoed, when they
succeed, when they fail.

**Also useful for the weight cascade system** (Priority 6):
- Deleted event → apply SuccessWeightDelta + trigger success cascades
- Destroyed event → apply FailureWeightDelta + trigger failure cascades

Both events fire at the same Ghidra hook. When we do Priority 3, we
get both events AND the foundation for the weight cascade system in
one shot.

**Priority**: Already 3 (unchanged). This entry just confirms Rex's
success-debug request maps to Deleted and doesn't need separate work.



## TeamType Extensions — Threat Avoidance + Guard System (Rex, 2026-07-15)

New TeamType-level features requiring extension of the DLL to cover
TeamTypeClass (mirroring the existing AITriggerTypeExt Container pattern
for AITriggerTypeClass). Big scope but coherent group of features.

### 1. Structure Avoidance Lists
> **ROUTING → DoctrineExt** (threat-field steering engine, not a trigger tag).

Two parallel lists on a TeamType telling its units to avoid specific
structures + how far to stay away.

```ini
[MyTeamType.AIExt]
AvoidStructures=NAOBEL,GAPILL,NABNKR                ; comma-list BuildingType IDs
AvoidRadius=8,6,10                                  ; positional cells to avoid by
```

Team pathfinding routes around these structures at the given cell
radius. If AvoidRadius has fewer entries than AvoidStructures, missing
entries default to some sensible constant (say 5 cells).

**Use case**: Kirov fleet avoids Prism Towers; ground rush avoids
Sentry Guns; naval strike avoids IFVs.

### 2. Blanket Range Avoidance
> **ROUTING → DoctrineExt** (same threat-field engine).

Simpler alternative to the specific list — tell all team units to
avoid ANY unit or building whose weapon range reaches them:

```ini
[MyTeamType.AIExt]
AvoidAllUnitRanges=yes           ; steer clear of enemy unit weapon ranges
AvoidAllBuildingRanges=yes       ; steer clear of enemy building weapon ranges
```

Uses a run-time query of enemy unit/building weapon ranges to build
a dynamic avoidance field. More expensive than static lists, so
default off. Ideal for skirmisher-style compositions (Terror Drones,
harvesters-under-escort, hit-and-run cavalry).

### 3. Guard System — Priority Escort Behavior
> **ROUTING → ATTExt, SHIPPED 2026-09-18** (commit 8c5158e, deployed, not yet in-game tested) as TAGS: GuardMe=yes/.Radius/.Scope on the guardee + Escort=yes on the protector (whole-life duty). Scope=trigger = Team1/Team2 siblings of one AITriggerType. The script-ACTION activation variant stays ScriptTypeExt. Test K.

Two coupled features: mark a team as needing protection + a new
script action that protects such teams.

```ini
[MyKirovTeam.AIExt]
GuardMe=yes                                  ; flag: this team wants escorts
GuardMe.Radius=6                             ; nearby cells where escorts must stay
GuardMe.Scope=trigger                        ; trigger / global (see below)
```

New ScriptType action (Rex to check next available action ID):
```
Actions=X,0     ; where X = new "Protect all Guarded teams in scope"
```

**Scope semantics**:
- `trigger`: only teams from the SAME AITriggerType that spawned this
  team can be guarded (siblings under one trigger)
- `global`: any active team on the same house with `GuardMe=yes` is
  a valid guard target

Escort teams find nearest guarded team (in scope) and pathfind to
maintain radius. Re-target if their guardee dies. Idle-attack posture
inside guard formation.

**Use case**: Kirov + escort squadron. Kirovs have `GuardMe=yes`.
Rocketeer team has the guard script action. Kirovs fly the route,
Rocketeers automatically stay in formation.

### 4. AI Location Vocabulary Expansion
> **ROUTING → ScriptTypeExt** (new 53-variant script actions).

Vanilla YR's script action `53,0` (Gather at Enemy Base) uses a
single hardcoded location. Expand with numbered variants:

```
53,0   Gather at Enemy Base            (vanilla — geographic centroid)
53,1   Gather at Enemy Outer Perimeter (edge of base, any side)
53,2   Gather at Enemy Weakest Side    (side with lowest defense score)
53,3   Gather at Enemy Resource Center (harvesters + refinery area)
53,4   Gather at Enemy Superweapon     (nearest SW building)
53,5   Gather at Enemy ConYard         (specifically the MCV/ConYard)
53,6   Gather at Enemy Chokepoint      (map-narrowest approach)
```

Rex's specific ask: "enemy outer base perimeter with least defenses"
= variant 2 (weakest side). Others added for coverage.

**Implementation**: hook script action 53's handler, dispatch to
new location-picking functions based on the second parameter.
Location calculation lives in the DLL — reads enemy house structures,
scores them, picks target cell.

**Priority**: 8 (before but linked to Priority 6 ScriptType arg
overrides — same hook, both work on script action 53).

### 5. Per-Trigger Weight Delta Overrides
> **ROUTING → ATTExt, SHIPPED** (self-deltas + cascades live).

Already filed above under "Per-Trigger Weight Delta Overrides +
Cross-Trigger Cascades (Rex, 2026-07-14)" — this is the same feature.
Merged. See that entry for full design.

### 6. Standby Script Action
> **ROUTING → ScriptTypeExt.**

New script action pair:

```
Actions=X,0     ; Enter Standby: units park where they are, defend-only
                ; until a sibling team activates Leave Standby.
```

While in Standby:
- Units engage anything that comes into weapon range
- Do NOT pursue targets that flee out of range
- Do NOT move unless directly threatened

Coupled leave-standby action:

```
Actions=Y,0     ; Leave Standby: signal to sibling standby teams under
                ; the same AITriggerType that they should exit standby
                ; and continue their script.
```

**Semantics**: Y triggers a house-wide event that any team spawned
by the SAME AITriggerType as the calling team receives. All such
teams' standby-flag flips false; they resume their next script
action.

**Use case**: 
- Trigger spawns 3 teams: Kirov strike + escort + ground diversion
- All 3 first execute Move to waypoint X
- All 3 execute Standby at waypoint X
- Diversion team's script has additional actions: Attack quarry,
  after some delay Leave Standby
- Diversion team hits enemy, times out, sends Leave Standby signal
- Kirov + escort resume their scripts: Move to target, Attack

This lets modders build coordinated multi-team attacks synchronized
on a "diversion team draws fire, then strike team commits" pattern.

### 7. Pathfinding-Aware Trigger Eligibility
> **ROUTING → split.** The CONDITION half **SHIPPED 2026-09-25** (commit
> b642396+9235ab8, CI green run 36115507467, DLL deployed byte-verified
> 267776 bytes, NOT yet in-game tested) as ATTExt tags — see below. Mid-move
> steering around threats is still open, owned by DoctrineExt.

**Shipped as `RequiresGroundPathToEnemy` / `RequiresNavalPathToEnemy` — no live
pathfind, no new hook needed.** `MapClass::GetMovementZoneType` (0x56D230) is a
real, already-declared YRpp thunk returning the id of the precomputed
connectivity region a cell belongs to for a given `MovementZone` — the same
mechanism the engine itself uses for reachability, recomputed only when the
map/bridges change. Two base centers are reachable by that movement type iff
their zone ids match, so the check is two lookups + an equality test, reusing
`RequiredBaseDistance`'s own `GetBaseCenter()`/`ResolveTargetHouse()` pattern.
Missing base info never vetoes, only an established disconnected pair does.
Docs: INI_REFERENCE.md "Reachability" subsection (flags the naval variant's
shore-cell edge case as needing field verification — a land base a short
bridge from its own dock may read as disconnected), TESTING.md Test M.

AI checks whether the enemy base is REACHABLE by ground before
committing ground teams. If ground-blocked, only aerial/naval teams
fire.

```ini
[MyGroundRush.AIExt]
RequiresGroundPathToEnemy=yes    ; trigger vetoes if ground unreachable
```

```ini
[MyNavalStrike.AIExt]
RequiresNavalPathToEnemy=yes     ; trigger vetoes if no water route
```

```ini
[MyAerialWave.AIExt]
; No path requirement — aerial always eligible
```

**Implementation**: pathfinding query from AI base center to enemy
base center. Query cached per-frame or per-N-frames (expensive). If
unreachable, veto the trigger with a specific detail line:

```
[AIExt Cancel] MyTrigger FAIL RequiresGroundPathToEnemy: no ground path
```

**Advanced**: could also check individual team unit MOVEMENT_ZONE
types. Water-only ships can't cross land, ground can't cross deep
water, hover CAN cross both. If ANY unit in the taskforce has an
unreachable movement zone to enemy base, veto.

**Priority**: 7-9 range. Standby, avoidance, and pathfinding are all
Priority 7-9 range. Rex to prioritize as testing reveals what's most
useful.

## Priority Rebalance After This Batch

Existing lifecycle work (Ghidra hooks for Start/Destroyed/Deleted) is
still Priority 3. Everything filed in this batch sits at Priority
7-9. Rex to sequence based on which unlocks the most immediate value.



## TeamType Avoid Structures System (Rex, 2026-07-14)
> **ROUTING → DoctrineExt** (older twin of Structure Avoidance above).

Per-TeamType tag for AI teams to avoid specific structures within a radius.
Useful for keeping infantry away from enemy super-tesla-coil zones,
kamikaze planes away from bio-reactors, etc.

**Proposed syntax** (TeamType sidecar section):
```ini
[MyKirovsTeam.AIExt]
AvoidStructures=NAPULS,NATBNK,NAOBEL
AvoidStructures.Radius=10,8,8      ; per-entry radius in cells
```

**Semantics**: pathfinding + movement AI checks each entry against enemy
buildings. Any move order that would take a team member within Radius[i]
cells of an instance of AvoidStructures[i] is rejected; the team re-paths
around.

**Also proposed**: global "auto-avoid" tag that applies to all threat
sources without listing them:
```ini
[MyKirovsTeam.AIExt]
AvoidAllUnitThreats=yes       ; avoid range of all enemy AA/AG units
AvoidAllBuildingThreats=yes   ; avoid range of all enemy defense structures
```

**Implementation scope**: medium-to-large. Requires:
- TeamTypeExt sidecar container (same as planned for Destroyed events)
- Hook into team movement AI (TeamClass::MoveTo or similar — Ghidra work)
- Range/threat lookup per candidate destination

**Priority**: 9. After TeamType sidecar infrastructure lands (Priority 4
via team-destroyed hook work).


## TeamType "Guarded" Marker + Protect Script Action (Rex, 2026-07-14)
> **ROUTING → ATTExt** (older twin of the Guard System above); the new protect ACTION itself = ScriptTypeExt.

Per-TeamType tag marking a team as needing protection, plus a new ScriptType
action for other teams to escort/protect it.

**Proposed syntax** (TeamType sidecar):
```ini
[MyKirovsTeam.AIExt]
Guarded=yes             ; this team is a protectee

[MyEscortTeam.AIExt]
Guarded=no
```

**New ScriptType actions**:
- `ProtectGuardedTeamLocal,X` — protect the Guarded team spawned by the
  SAME AITriggerType as this script's owning team (X = protection style:
  hover/patrol/escort formation)
- `ProtectGuardedTeamGlobal,X` — protect ANY currently-active Guarded team
  belonging to the same house

**Semantics for "protection"**: the protector team's movement AI targets
positions near the guarded team, and prioritizes attacking anything that
attacks the guarded team.

**Use case**: Kirov wave (slow, heavy, lethal AA-vulnerable) escorted by
Yak fighters or Terror Drones. Modder marks Kirov team as Guarded and
gives Yak team a `ProtectGuardedTeamLocal` script action.

**Implementation scope**: large. Requires:
- TeamTypeExt sidecar (see above)
- ScriptType extension for new action codes (see ScriptType extension TODO)
- Runtime pairing logic: at team creation time, note which teams share
  a spawning AITrigger; at script action execution, look up sibling teams
- Movement AI hook to route protector near protectee

**Priority**: 10. Depends on TeamType sidecar (Priority 4) AND ScriptType
extension (Priority 6 area). Big feature; ship after core lifecycle work.


## AI Location System — Sub-Variants of Script 53 Gather (Rex, 2026-07-14)
> **ROUTING → ScriptTypeExt** (older twin of AI Location Vocabulary).

New ScriptType action variants for gathering at more specific enemy-base
locations. Vanilla Script 53,0 "Gather at Enemy Base" always picks the
same central point.

**Proposed variants** (still Script code 53, different Arg2 semantics):
- `53,0` — vanilla (center of enemy base)
- `53,1` — outer perimeter, LEAST defended sector (finds weakest edge)
- `53,2` — outer perimeter, most defended sector (for suicidal charges)
- `53,3` — nearest resource extractor (harvester harassment)
- `53,4` — nearest superweapon structure
- `53,5` — nearest production structure

**Semantics**: at script action execution, the team's target location is
computed dynamically based on the current enemy base layout. Requires
enemy-base spatial analysis at runtime.

**Implementation scope**: medium. Requires:
- Hook the Script 53 handler (Ghidra work to find address)
- For each Arg2 value, implement the corresponding search algorithm
  (iterate enemy buildings, find weakest sector, etc.)
- Cache per-frame or per-few-seconds to avoid re-scanning on every step

**Priority**: 11. Standalone feature, no dependencies on other planned
work. Rush-behavior tuning benefits from this: modder can send infantry
rushes at "least defended" sector instead of always crashing into the
main gate.


## ScriptType "Standby" Action (Rex, 2026-07-14)
> **ROUTING → ScriptTypeExt** (older twin of Standby above).

New ScriptType action for teams to hold position and only fight what
comes into range, waiting for a signal from another team's script.

**Proposed actions**:
- `StandbyAction=<code>` — enter standby state (units cease movement,
  fire at anything in range, otherwise wait)
- `LeaveStandbySignal=<code>` — broadcast signal that ends standby for
  teams from the same AITriggerType (local mode) or all houses
  (global mode)

**Wave-choreography use case**:
```
Script "Setup Ambush":
  Action 0: Move to waypoint 5
  Action 1: Standby

Script "Trigger Ambush":
  Action 0: Move to waypoint 3 (attack the enemy that just arrived)
  Action 1: LeaveStandbySignal
  Action 2: Attack quarry
```

Two teams spawned by the same AITriggerType — one hides and waits, one
initiates. When the initiator broadcasts LeaveStandbySignal, the ambusher
switches from Standby to next script action.

**Implementation scope**: medium. Requires:
- ScriptType extension for new action codes (same infrastructure as Script
  53 variants above)
- Standby state on TeamClass (idle-fire behavior modification)
- Signal broadcast mechanism (per-house or per-AITrigger event queue)

**Priority**: 12. Great feature for scripted-wave modders. Depends on
ScriptType extension infrastructure.


## AI Pathfinding-Aware Trigger Eligibility (Rex, 2026-07-14)
> **ROUTING → split** (older twin): condition = ATTExt, steering = DoctrineExt.

AI should detect whether ground/naval routes to the enemy exist and gate
trigger eligibility on that. If ground route is blocked (island map, no
bridges), AI should only fire aerial/naval triggers. Vice versa for naval.

**Current problem**: on island maps, the AI still tries to build ground
rushes that immediately get stuck at map edges. Wasted resources and
production time.

**Proposed AIExt tag**:
```ini
[MyGroundRushTrigger.AIExt]
RequireGroundRouteToEnemy=yes    ; veto if no ground path exists
RequireNavalRouteToEnemy=no
RequireAerialAccess=yes          ; always true unless map has airspace walls
```

**Semantics**: at trigger evaluation time, run a pathfinding check from
the AI's base to the enemy's base restricted to ground/naval movement.
If no path exists, veto the trigger. AIExt gate would emit:
```
[AIExt Cancel] TRIGID FAIL RequireGroundRoute (no ground path to enemy base)
```

**Implementation scope**: medium-to-large. Requires:
- Access to game's pathfinding system (A* or similar)
- Cache pathfinding results (base-to-base) at low frequency (~once per 30
  seconds is enough) — pathfinding is expensive
- Handle the "enemy has multiple bases" case (any-of, all-of, primary-of)

**Alternative approach**: modder-declared per-map hints instead of runtime
pathfinding. Modder adds `[YRAI].MapType=IsIsland` and the AI adjusts
behavior. Much simpler but modder must set it per-map. Less magical.

**Priority**: 13. High-value for map diversity but not blocking anything.
The alternative approach could be a fast interim solution (Priority 8
range) while runtime pathfinding lands later.


## Note: "Customize weight for success/failure on aitriggers" (Rex, 2026-07-14)

This request is **already covered** by the earlier-filed feature:
"Per-Trigger Weight Delta Overrides + Cross-Trigger Cascades"

That TODO entry (see above) documents:
- Per-trigger `SuccessWeightDelta` and `FailureWeightDelta` overrides
- Per-trigger `TrackRecordCoefficient` override
- Bonus: cross-trigger cascades ("if X fails, penalize triggers Y and Z")

Rides on Priority 3 (Destroyed/Deleted hook). No duplicate entry needed —
already Priority 6 in the roadmap.



## Bug Discovery: DeployFire Script Uses Wrong Action Codes (2026-07-15)

Rex observed siege choppers arriving at target then sitting doing nothing.

**Root cause traced**: `wave_generator.py::_deployfire_script()` uses action
codes `4,0` and `5,0` with comments claiming these are Deploy/Undeploy.

In RA2/YR, action 4 is NOT Deploy — likely "Guard for X minutes" or "Move
to cell". Action 5 is Guard Area, not Undeploy.

**Result**: units reach target (action 53,0 works), then execute a Guard
that's essentially no-op, try to attack while still in flight mode
(action 0,X), then trap in Guard Area (action 5,0) forever. Never reach
Register Success (action 49,0).

**Fix required**:
1. Look up correct Deploy action code (likely 39 or 41 in YR)
2. Look up correct Undeploy code
3. Update `_deployfire_script()` comments AND action codes

**Additional consideration — aircraft vs ground deploy**:
Siege Choppers (SCHP) are aircraft that deploy differently from ground
artillery (Desolators, Prism Tanks). Different Locomotor types
(`Aircraft` vs `StaticDeployed`) may respond to different action codes.

Tool needs to either:
- Detect aircraft vs ground deployfire units and use appropriate scripts
- Or exclude aircraft from deployfire generation, use standard scripts

**Confirmed by log analysis**: TaskForce `[04D3CC18-G]` "3 Soviet Siege
Chopper" (0=3,SCHP) uses Script `FF3F222B-G` "GI Deploy Artillery" with
actions `53,0 / 4,0 / 0,2 / 0,1 / 5,0 / 49,0` — pattern matches the tool's
_deployfire_script output verbatim.

**Priority**: HIGH. Directly impacts wave effectiveness. Blocks debugging
of ANY deployfire unit until fixed.


## Case Study: This Bug Would Have Been Instantly Visible with ScriptType Debug (2026-07-15)

Rex spent minutes tracking down "siege choppers sit doing nothing"
through manual aimd.ini inspection + tool code review. The 6-hour
diagnosis chain was:

1. Notice broken behavior in-game
2. Search aimd.ini for SCHP taskforces
3. Trace taskforce ID to team definition
4. Trace team's Script reference
5. Read script actions
6. Grep tool code for deployfire script generation
7. Discover incorrect action code comments

**Alternative with ScriptType Debug (TODO #6 area)**: log output would say:

```
[AIExt Script AB2BA4DA-G] Team spawned: 3 x SCHP
[AIExt Script AB2BA4DA-G] Action 0 (Gather at Enemy Base): STARTED
[AIExt Script AB2BA4DA-G] Action 0: COMPLETED (25 seconds)
[AIExt Script AB2BA4DA-G] Action 1 (Guard for X minutes): STARTED (arg=0)
[AIExt Script AB2BA4DA-G] Action 1: COMPLETED (0 seconds — arg=0)
[AIExt Script AB2BA4DA-G] Action 2 (Attack Quarry): STARTED (quarry=2)
[AIExt Script AB2BA4DA-G] Action 2: STUCK (no valid targets reached
                          in 30 seconds — units are in AIR mode)
```

Immediate diagnosis: "Action 1 wasn't Deploy" + "Action 2 stuck because
units in air mode" would have surfaced the same bug in <1 minute.

**This raises the priority of ScriptType debug system**. Not a
nice-to-have — a critical debugging tool. Previously filed under Priority
6-8 range; consider promoting to Priority 4 (immediately after Ghidra
session for Start/Destroyed/Deleted hooks).



## Fix: Exclude Siege Choppers from DeployFire Wave Generation (2026-07-15)

Root cause of the "siege choppers sit doing nothing" bug: **vanilla YR AI
cannot deploy Siege Choppers.** They're aircraft that require player
manual deploy — the AI script engine doesn't handle their deploy state
transition properly.

**Confirmed via research**:
- ppmforums.com/topic-30289/how-to-make-the-al-harder/
- forums.cncnz.com/topic/11149-resources-and-ai-editing/

Both threads confirm the AI's inability to deploy aircraft-based deploy
units.

**Immediate tool fix**: filter Siege Choppers (and any other
aircraft-based deployfire units) OUT of `generate_deployfire_waves()`.

Detection: exclude units where `Locomotor` corresponds to an aircraft
type (aircraft `Locomotor={4A582741-9839-11d1-B709-00A024DDAFD1}`) OR
where `MovementZone=Fly`.

This means the previously-generated Deployfire Arty triggers for SCHP
are useless — they'll still spawn but never deploy. Once tool is fixed,
regenerate aimd.ini and these problematic triggers go away.

**Related — also fix comment error in `_deployfire_script()`**:
The comments claim actions 4/5 are Deploy/Undeploy but they're actually
Guard actions. For GROUND deployfire units (Desolator, Prism Tank), the
script may still work due to how their deploy state auto-triggers — but
the comments are misleading and should reflect reality.

**Priority**: HIGH. Simple filter change. Ships as tool v35.

## Future Consideration: Phobos PR #565 for AI-Deployable Aircraft (2026-07-15)

Phobos Pull Request #565: https://github.com/Phobos-developers/Phobos/pull/565

Rex flagged that Phobos may add a new deploy system that could enable AI
to properly deploy siege choppers and other aircraft-based deployfire
units.

**Action item**: verify PR #565 status and functionality once it's merged
into Phobos. If it lands and includes AI-deployable aircraft support:

1. Update Phobos submodule in AITriggerTypeExt repo to include the fix
2. Remove the siege chopper exclusion from tool's DeployFire generator
3. Re-enable AITriggerTypeExt siege chopper wave generation
4. Test in-game to confirm choppers deploy and attack correctly

**Priority**: MONITOR. Not blocking anything. When Rex or someone else
notices PR #565 has merged, revisit this exclusion.


## Priority 3 Complete — Start/Destroyed/Deleted Hooks Confirmed via Ghidra (Rex, 2026-07-16)

The Ghidra session referenced throughout this doc as blocking Priority 3
(Destroyed/Deleted) and enabling Priority 6 (weight cascades) is done.
Four real addresses, confirmed by decompile — not header-guessing.

**HouseClass::FindEligibleAITeams — 0x6F0AB0** (this is WhoIsBest).
Rolls team-ratio chance, evicts over-cap teams via vtable+0x20, loops
AITriggerTypeClass::Array calling ConditionMet, builds a weighted
distribution, weighted-random-draws a winner. Winner's Team1/Team2
(AITriggerTypeClass+0xDC/+0xE0) become spawn candidates. Matches
DCoder's dc.strategy-x.com reconstruction almost exactly.

This is where **Priority 5 (Reject event)** lives — every trigger that
passes ConditionMet but loses the weighted draw is an implicit reject.
Not hooked yet; would need to hook inside the distribution loop and diff
against the eventual winner. Still open.

**TeamTypeClass::CreateTeam — 0x6F09C0**, actual **Start** hook at
**0x4F8AB2** (steal 9 bytes, fires right after CreateTeam returns —
EAX = new TeamClass*). Two independent callers of CreateTeam exist (AI
path and the TAction "Create Team" script action) — hooking the return
site here only catches the AI path, which is what we want for AITrigger
lifecycle messages.

**AITriggerTypeClass::RegisterSuccess — 0x41FD60** (steal 13) /
**RegisterFailure — 0x41FE20** (steal 8). Non-virtual, ECX = the
AITriggerTypeClass* to notify directly — no array-walking needed. Real
Deleted/Destroyed dispatch, called from inside TeamClass's destructor
based on a flag at TeamClass+0x84. This is what "Deleted" and "Destroyed"
actually map to at the AITriggerType scope.

**TeamClass destructor — 0x6E8DE0** (steal 13). Found via xrefs INTO
RegisterSuccess/Failure, not direct callers — it's invoked via vtable
slot +0x20, not a plain CALL. Not needed for AITriggerType-scoped
messages, but this is the hook for the future **TeamType-scoped**
"any team of this type died" sidecar feature (separate from Priority 3).

**Status**: all four hook skeletons written using existing DEFINE_HOOK /
GET / return-0-to-fall-through convention (matches Hooks.cpp style
exactly — confirmed against the real repo). None compiled or tested
in-game yet.

**Also checked**: Phobos PR #2119 ("Additional AITriggerType conditions
— Tech Buildings / Bridge Repair Huts") adds ConditionType values 8/9
inside ConditionMet's dispatch (0x41E720). No overlap with the above —
logged here because the existing 0x41EAC0 gate hook sits downstream of
that same function. Worth a byte-diff check if this PR merges into
whatever Phobos version the repo builds against.

**Unblocks**: Priority 6 (Per-Trigger Weight Delta Overrides +
Cross-Trigger Cascades) can now proceed once Start/Destroyed/Deleted are
compiled and tested in-game.

**Priority**: 3 — mark complete pending in-game test confirmation.


## BUG: Engineer-rush transports unload OUTSIDE the enemy base (2026-07-18)

Rex observed: the wave generator's engineer-rush script has the (Flak
Track) transports unload their engineers OUTSIDE the enemy base rather
than inside, so the engineers have to walk in under fire instead of
capturing adjacent buildings.

**Root cause** — `wave_generator.py`, `generate_engineer_rush_waves`,
the `sc_acts` list (~line 4408):

```python
sc_acts = [
    (load_act, 0),  # 10103,0 (Phobos) or 14,0 (vanilla) — load engineers
    (53, 0),        # move to enemy base  ← gathers at the base GATHER CELL
    (8,  0),        # unload              ← so they drop at the perimeter
    (0,  2),        # attack structures
    (49, 0),
]
```

Script action `53,0` ("Gather at Enemy Base") moves the team to the
enemy-base gather cell (the perimeter point logged in debug.log as
"chosen for its GatherAtEnemy cell", e.g. (58,77)), NOT into the base.
`8,0` then unloads there → engineers dumped outside. The comment
"engineers auto-capture on contact" assumes contact that doesn't happen
because they land too far out.

**Vanilla method (reference)** — the vanilla engineer-capture script
`0A88524C-G` ("Soviet Transport vs Allied Con") instead leads with:

```
0=58,131086    ; action 58 = Move to a SPECIFIC enemy building.
               ; 131086 = 131072 (0x20000 building-index flag) + 14
1=14,0         ; load
2=43,0         ; wait until loaded
3=47,2
4=8,0          ; unload — now adjacent to the targeted building
5=49,0
```

Action `58,<131072+idx>` drives the transport TO a specific structure,
so the unload happens next to a capturable building (inside). The catch:
`131072+14` is a HARDCODED building index for one matchup. The tool
can't emit a fixed index mod-agnostically, and vanilla YR has no
"move to nearest capturable enemy building" action that takes no index.

**Action items (revisit later — do NOT fix blindly, needs in-game test):**
1. Investigate a DLL "AI Location Vocabulary" 53-variant (already on the
   AITriggerTypeExt roadmap): e.g. `53,5` = gather at enemy ConYard /
   `53,2` = weakest side — sends the transport to an interior target so
   it unloads inside. This is the proper fix.
2. **Verify whether Ares and/or Phobos already add a script action** for
   "move to / unload at a specific enemy building (by type or category)"
   that solves this mod-agnostically — both frameworks add new AI script
   actions, so one may already cover it before we build our own.
3. Only after (1)/(2): change `sc_acts` accordingly and re-test in-game
   (watch the actual unload cell vs the base interior).

**Priority**: MEDIUM. Real gameplay impact on engineer rushes, but the
fix is uncertain and needs the DLL feature or an Ares/Phobos action —
not a blind Python edit.

### UPDATE (2026-07-18): Phobos ALREADY has the action — verified in docs

phobos.readthedocs.io "Move Team to Techno Location Actions" = script
actions **10050-10061**: approach a target specified by the 2nd param
(a built-in "Target Type#" OR a 0-based index into a modder-declared
`[AITargetTypes]` section of TechnoType lists), enemy/friendly,
closer/farther, threat-weighted. Random-pick variants exist (10052 etc.).

Built-in Target Type# values include (37 total, see docs):
`1=Anything, 2=Structures, 7=Base Defenses, 25=Super Weapon,
26=Construction Yard, 33=Capturable`.

**Likely engineer-rush fix**: replace `53,0` (gather at perimeter) with
`10050,33` (approach nearest ENEMY Capturable building, closer) or
`10050,26` (approach enemy ConYard) BEFORE the `8,0` unload, so the
transport drives to a capturable structure and drops engineers next to
it. Docs don't explicitly confirm move+unload adjacency → verify
in-game. If it works, this ALSO covers most of the DLL "AI Location
53-variant" idea — no custom DLL action needed for base-location targeting.

`[AITargetTypes]` format:
```
[AITargetTypes]   ; each row = a list of TechnoTypes to evaluate
0=SOMETECH,SOMEOTHERTECH
1=ANOTHERTECH
```
The tool could emit AITargetTypes lists + 10050-series moves for smarter
target selection (ties into the target-priority-order TODO:
Refinery > War Factory > CY > Battle Lab > SW).

**Still a genuine DLL gap (NOT in Phobos)**: weapon-range / DPS scanning
— scan enemy TechnoType primary/secondary/weaponX ranges, custom
cell-range conditions. The DLL DPSCheck/range system remains a real,
non-redundant feature to build.

## Per-Country [AI] Tag Overrides (Rex, 2026-07-21) — NOT URGENT
> **ROUTING → Country_Extension** (as already decided in the section).

Rex wants a lot of the vanilla global `[AI]` tags (modenc.renegadeprojects.com/AI:
BaseSizeAdd, AIHateDelays, TeamDelays, AIAlternateProductionCreditCutoff,
AITriggerSuccess/FailureWeightDelta, AITriggerTrackRecordCoefficient, etc.)
made overridable on a per-CountriesTypes basis.

**Decision: implement in Country_Extension.dll, NOT AITriggerTypeExt.**
Rationale:
- The feature is fundamentally country-scoped; Country_Extension already owns
  the CountryType/HouseType extension container + ext-data infrastructure.
- Most `[AI]` tags are house/country-level engine AI behaviors (base sizing,
  production pacing, hate/team timing) that AITriggerTypeExt doesn't touch —
  AITriggerTypeExt is specifically the AITriggerType object (conditions,
  lifecycle, DPS/threat, weights).
- Mechanism: hook each engine read of a global `[AI]` tag and substitute the
  acting house's country override (fall back to global default when unset).
  Many hook points (one per tag) — big feature, scope incrementally.

**Coordination note:** the AITrigger weight-delta tags
(AITriggerSuccess/FailureWeightDelta, TrackRecordCoefficient) overlap
AITriggerTypeExt's weight system (which already reads
RulesClass::Instance->AITrigger*WeightDelta). To avoid two DLLs hooking the same
reads: Country_Extension owns the per-country override; AITriggerTypeExt's
weight code should consult that override rather than reading the global directly.


## ═══ NEW SCOPE BATCH (Rex, 2026-07-31) ═══

### Toolchain switch: Ares → Antares

Abandon Ares, adopt **Antares** (open-source Ares reimplementation, full
feature parity, Rex-tested). Every "Ares has X" item below targets Antares
now — and because Antares is open source we can actually read its base-building
/ TeamRetaliate / SW-value code instead of black-boxing it.

### Reference-mod findings — "2018 06 25 Build" (Dropbox), an AI-improvement mod

Verified against the raw files (the auto-summary swapped 53/54 and hallucinated
action codes; corrected here from a direct grep).

RulesMD.ini — AI knobs it actually changes vs vanilla:
- `TeamDelays=500,900,1250`  (vanilla 2000-ish) — **much faster team creation**;
  directly relevant to our "AI never rushes early" investigation. This mod's
  answer to the rush problem is simply slashing TeamDelays.
- `MultiplayerAICM=450,150,50`  (vanilla 400,300,200) — hard AI gets far MORE
  money, easy AI far less. Steeper difficulty economy curve.
- `AISlaveMinerNumber=4,3,2`  (vanilla 3,2,1) — more slave miners per difficulty.
- `AlliedBaseDefenseCounts=26,20,12`, `SovietBaseDefenseCounts=22,18,12`,
  `ThirdBaseDefenseCounts=25,22,12` — explicit per-side defense counts replacing
  the old arcane formula. **These are exactly the "defense counts" Rex wants
  per-COUNTRY** — they exist per-SIDE globally today.
- `AISafeDistance=30` — comment confirms: "cell distance the AI will consider in
  Gathering outside enemy base (from center of enemy base)". **This is the value
  the `53,0` script uses** — confirms the 10053,X design below is sound.
- `AIBaseSpacing=1`, `AIBuildsWalls=no`, `DissolveUnfilledTeamDelay=5000`,
  `MaximumAIDefensiveTeams=9,3,2`, `MinimumAIDefensiveTeams=1,1,1`,
  `TotalAITeamCap=40,40,40`, `AISuperDefenseProbability=99,80,50`.

aimd.ini — structure (raw grep, 222 taskforces):
- Has real [AITriggerTypes]/[TeamTypes]/[ScriptTypes]/[TaskForces].
- **NO extended (>100) script actions** — pure vanilla-engine scripted AI, no
  Phobos/Antares actions. (Summary's "131000/196608" codes were hallucinated.)
- Script action frequency: 0(206), 46(108), 5-wait(89), 49-attack(82),
  58-guard-area(79), 54-regroup-at-base(61), 14(45), 53-gather-at-enemy(37),
  47(32), 6-move(29), 11-area-guard(19), 43(12), 9(12).
- Takeaway: gather+attack (53/54 → wait → 49) is the backbone. This mod leans
  HARD on 53/54, which is exactly why Rex's customizable-gather-distance idea
  (10053,X / 10054,X) has high payoff.
- TeamType flags: Whiner widespread; Suicide on transport/IFV-rush/anti-structure;
  Recruiter on spy/Tanya/Kirov; Reinforce on base-guard infantry.
- Big taskforces: 20 Conscripts; 18 Rhinos; 20 Initiates+2 Yuri Clones.

### New feature items (triaged by subsystem)
> **ROUTING** — inline per item: #1 Country_Extension, #2/#3 research (repro), #4/#5 ScriptTypeExt, #6 ATTExt SHIPPED, #7 ATTExt, #8 Antares base planner, #9 sensing=DoctrineExt + placement=Antares, #10 PrerequisiteExt, #11 TOOL.

1. **Per-country [AI] overrides** — defense counts, defensive team min/max, etc.
   overridable per-Country from `[AI]`/CountryTypes. → **Country_Extension.dll**
   (AI-related but belongs there per earlier call). The global keys to shadow:
   AlliedBaseDefenseCounts/SovietBaseDefenseCounts/ThirdBaseDefenseCounts,
   Min/MaximumAIDefensiveTeams. ref: https://modenc.renegadeprojects.com/AI

2. **Spy transport rush bug** — spies unloaded from transport just stand there,
   ignore the script. → behavior bug. Investigate spy post-unload state / whether
   the disguise/agent state clears their mission. Likely a ScriptType or
   unload-handler hook. Needs Ghidra + in-game repro.

3. **Only one spy per wave disguises** — engine only lets one spy in a group take
   a disguise. → investigate disguise-on-spawn path; make per-spy. Bug/limitation.

4. **Script `10053,X` — Gather at Enemy w/ custom distance.** X = cells outside
   enemy base center (overrides AISafeDistance=30 for THIS action only). Enables
   staged approach: meet at 40 → let stragglers catch up → close by 10 → repeat.
   Use 10053 to avoid colliding with vanilla 53. → **ScriptTypeExt** (new script
   action; slots into the "ScriptType Argument Extensions" plan already in this
   file). Antares/Phobos-style extended action number.

5. **Script `10054,X` — Regroup at Base w/ custom distance.** Same philosophy for
   the 54 (gather at own base) action. → ScriptTypeExt.

6. **AITrigger base-distance condition** — `RequiredBaseDistanceMin/Max` (cells,
   owner base center ↔ resolved-enemy base center). "100 cells apart → big waves
   fine; 20 cells apart → favor rapid guerilla." → **AITriggerTypeExt DLL
   condition.** MOST TRACTABLE — natural sibling of RequiredEnemyMaxRange /
   RequiredTeamRangeRatio. **BUILDING THIS NOW.**

7. **Per-team TeamRetaliate** — Antares TeamRetaliate is global; make it a
   per-TeamType override. → **TeamTypeExt sidecar** (`[TeamID.AIExt]`
   TeamRetaliate=). ref: Antares docs new/teamretaliate.

8. **Base-building layer preference (4-layer)** — Dead center / near center /
   near outer / outer perimeter, generalizing Antares AIInnerBase (which only
   has "center"). → base-building AI; read Antares base-planner source (now
   possible since open). Medium-large RE.

9. **Directional threat-aware defense placement** — base sub-priority by the
   direction threats come from (heat-map of enemy ground movement / where damage
   is taken), with a customizable **blind-spot minimum threshold** so a human
   can't feint one side to strip another for a steamroll. Stretch: pathfind
   likely-weak approaches and defend them. Also: AI currently won't build off
   captured Tech Buildings — check if `AIBaseNormal=` fixes it; if not, that's a
   separate enhancement. → biggest research item; base-building AI. Break into
   (a) heat-map sensing, (b) directional defense weighting, (c) blind-spot floor,
   (d) tech-building base extension.

10. **Prerequisite.AllowAI / Prerequisite.AllowPlayer** — additive buildability
    filter restricting a TechnoType to AI-only or player-only. Does NOT override
    TechLevel/BuildLimit — purely additional AND-terms (TechLevel=-1 still blocks
    the player even with AllowPlayer=yes; a taskforce unit with AllowAI=no simply
    makes the aitriggertype not activate, same as a TechLevel=11 unit would). →
    **PrerequisiteExt project** (this is squarely its Requirement-primitive
    territory — house-scope polarity already in its design).

11. **Reinvestigate player guides** for AI-behavior + aimd-editor inspiration.
    → research; extend the existing "Community Strategy Guide" section. Pending
    guides: xwis 62084, gamebanana 7669, gamefaqs 14539 (bot-blocked).

### Priority call for the "can't test for a while" stretch

Build features that CI-compile-verify even without in-game testing, preferring
DLL conditions that extend the existing AIExt framework (self-contained, low
regression risk). Order:
- (a) #6 base-distance condition — DONE/in progress this session.
- (b) #10 Prerequisite.AllowAI/AllowPlayer — but route to PrerequisiteExt.
- (c) #7 per-team TeamRetaliate — needs TeamTypeExt container (already planned
  for TeamType-scoped Destroyed msgs; shares that infra).
- Defer #2/#3 (need repro), #4/#5 (need Ghidra action handlers), #8/#9 (base
  planner RE) until Rex can test again.


## ═══ SCOPE BATCH 2 (Rex, 2026-08-03) ═══

### The unifying insight: CONDITION ENGINE as a reusable primitive

Rex is independently arriving at a system where the `Required*` condition set
(owner/enemy/allies/neutral buildings, units, credits, power, DPS, range, base
distance, elapsed time, superweapon readiness) — already built for
`[TriggerID.AIExt]` — is the reusable CORE, and different ACTIONS consume it:
- spawn a team  (existing AITriggerType selection)
- change a live team's script  (new — "ScriptSwitch")
- keep rebuilding a team to flood  (new — "Steamroll")
- fire a superweapon / build at a location  (new — the hard track)

**Architectural decision to make:** factor the condition set out of
`AITriggerTypeExt::ExtData` into a shared `ConditionSet` component that both
`AITriggerType.AIExt` and a new `TeamType.AIExt` (and later SW rules) hold and
evaluate the same way. This is the AITriggerTypeExt-local version of the TraitExt
unification. Do this BEFORE the second consumer (ScriptSwitch) so we don't fork
the parser/evaluator.

### Idea A — Dynamic ScriptSwitch on TeamTypes (Rex's main design question)

Live team swaps its ScriptType when battlefield conditions flip (e.g. attacking
team whose base loses power → switch to a defend-base script).

**Recommendation: put it on the TeamType (`[TeamTypeID.AIExt]`), not the
AITriggerType.** Rationale: it is runtime, per-team-INSTANCE behaviour (how a
living team reacts), whereas AITriggerType conditions fire at SELECTION time
before the team exists. A TeamType can be spawned by many triggers; the reactive
behaviour should travel with the team. (Rex was open to either — this is the
cleaner split: AITrigger = "should I spawn", TeamType = "how the team behaves".)

**Refined syntax** — reuse the FULL existing condition vocabulary per rule
(ordered list, first match wins), instead of the rough parallel
Conditions/Values arrays:
```ini
[SomeTeamType.AIExt]
ScriptSwitch=Defend,Regroup            ; ordered rule names; first whose conditions pass wins
ScriptSwitch.Defend.Script=DEFEND_BASE_SCRIPT
ScriptSwitch.Defend.RequiredOwnerPowerMax=-1        ; owner in power deficit
ScriptSwitch.Defend.RequiredOwnerBuildingsUnderAttackMin=1   ; NEW condition (see below)
ScriptSwitch.Defend.Latch=yes          ; once switched, don't flip back (no thrash)
ScriptSwitch.Defend.ResetLine=yes      ; restart the new script at action 0 (default yes)
ScriptSwitch.Regroup.Script=REGROUP_SCRIPT
ScriptSwitch.Regroup.RequiredEnemySuperWeapons=NUKE
ScriptSwitch.Regroup.RequiredEnemySuperWeaponsReadyMax=0     ; nuke armed → pull back
```
Evaluated each team update. Hook: NOT the already-double-hooked TeamClass::Update
redirect at 0x6E9443 — instead a SIDE-EFFECT-ONLY hook (return 0, just swap
`pTeam` script pointer + reset line index) which chains safely with
Antares/Phobos regardless of load order. Confirm a clean sub-address in
TeamClass::AI via Ghidra; the swap sets the team's CurrentScript + line index.

**Two small NEW conditions this unlocks (broadly useful):**
- `RequiredOwnerBuildingsUnderAttack` (count of owner structures currently taking
  fire) — needed for the "base under attack" trigger.
- `RequiredStructureOnMap=NAMISL` style (a structure type exists anywhere / owned
  by target) — Rex's STRUCTURE_DETECTED.

**Why this is high value: it SUBSUMES much of the superweapon "scripting" wish
list for free.** "Wait for the enemy nuke/weatherstorm to resolve, then advance"
= an attack team + a ScriptSwitch rule (RequiredEnemySuperWeapons ready → WAIT
script; clear → back to attack). "Wait for enemy to lose AA, then push" = a
condition (enemy AA DPS/units low, via existing DPS Lock=AA) → switch to the
push script. The only part these DON'T cover is actually FIRING a superweapon
(separate track below).

### Idea B — Steamroll TeamType flag (like Reinforce, but never "full")
> **ROUTING → ATTExt, SHIPPED 2026-09-25** (commit 9d92c65, CI green run
> 36114377123, DLL deployed byte-verified 266752 bytes, NOT yet in-game tested).

`Reinforce=yes` (modenc) makes the AI keep producing to refill a team until
`IsFullStrength`. **Steamroll = always rebuild the taskforce, ignore
IsFullStrength, to flood/overwhelm** — and STOP when the trigger/team is
destroyed (so a script that self-destructs the trigger when "no enemy buildings
left" ends the flood). Also good for endlessly re-flooding garrison infantry.

**Shipped as a TeamType sidecar tag, no new hook needed.** Turned out both
levers are plain PUBLIC fields already in the YRpp headers this DLL already
includes — `TeamTypeClass::Reinforce` and `TeamClass::IsFullStrength` — so no
Ghidra work was required after all:
- `[TeamTypeID.AIExt] Steamroll=yes` — at parse time (the same sweep that reads
  FormationKeep/GuardMe/Escort) force-sets `pTT->Reinforce = true`.
- Per tick, in the ALREADY-hooked 0x6E9443 side-effect co-hook, force
  `pTeam->IsFullStrength = false` for tagged TeamTypes. A plain field
  correction, not a control-flow redirect, so it composes with whatever reads
  the field afterward — vanilla's own check or Antares' MacroHacks gate — no
  load-order fighting. Stops itself the moment the team dies.
Docs: INI_REFERENCE.md "Steamroll" subsection, TESTING.md Test L.

### Idea C — Superweapon scripting / AI build-at-location (the hard track)
> **ROUTING → split.** SW-fire targeting + build placement = Antares. The reactive WHEN-decisioning = DoctrineExt.

The genuinely large behaviours: ally low on power → AI builds powered defenses on
the ally's base, or fires a reinforcement/paradrop SW onto the ally base; wait
for enemy AA to drop then paradrop-SW; target-selection intelligence per SW.
- Antares ALREADY has SW-targeting AI (`Ext/SWType/Hooks.Targeting.cpp`, incl.
  "if no enemy yet, reinforce own base") — so extend/PR Antares rather than
  reimplement. Cross-ref the existing `AISuperWeaponValues` schema already in
  this TODO (per-SW value tables + HPWeighting).
- "Build at designated locations / directional defense" overlaps Batch-1 items
  #8/#9 (base-planner RE) — same hard base-building track.
- Keep as a research track; much of the *reactive* intent is delivered cheaply by
  Idea A (ScriptSwitch) + SW-awareness conditions. Only the actual SW-FIRE and
  build-placement need the base/SW-AI work.

## ═══ Antares-ownership findings (2026-08-21) ═══

Two batch-2 items are ALREADY in Antares / belong in Antares — do NOT build in
AITriggerTypeExt (would be dead code behind Antares's replacements):

- **Gather-distance (10053/10054) — ALREADY EXISTS in Antares.**
  `TeamClass_GatherAtEnemyBase_Distance` @0x6EF8A1 makes script `53,X` compute
  `AISafeDistance + X`; `TeamClass_GatherAtFriendlyBase_Distance` @0x6EFB69 makes
  `54,X` = `AIFriendlyDistance.Get(AISafeDistance) + X`. So the second number is
  already the customizable offset — no new action code needed. Just document for
  modders (and the wave generator can emit `53,<dist>` directly). Antares also
  adds `[General] AIFriendlyDistance` for the own-base gather default.

- **Steamroll → Antares PR (not AITriggerTypeExt).** Antares FULLY REPLACES the AI
  production functions (`HouseClass_AI_UnitProduction` 0x4FEA60, InfantryProduction
  0x4FEEE0, AircraftProduction 0x4FF210) via `Ext/House/MacroHacks.h`. The
  team-fill skip is there: `(!Team->Type->Reinforce || Team->IsFullStrength) &&
  (IsForcedActive || IsHasBeen) -> skip`. Steamroll = a TeamType flag that keeps a
  team wanting replacements even at full strength → change that condition to
  `(!Reinforce || (IsFullStrength && !Steamroll))` INSIDE MacroHacks. Small Antares
  contribution; hooking 0x4FEA60 from our DLL would be dead code (Antares owns it).
  Same pattern as per-team TeamRetaliate (Antares owns TeamClass::AttackedBy 0x6EB432).

Takeaway: reactive AI-BEHAVIOR features (production, retaliation, gather) live in
Antares (which owns those subsystems, open source). AITriggerTypeExt's sweet spot
is trigger CONDITIONS + the ScriptSwitch reactive-script layer.

## ═══ Script order-of-operations validator (2026-08-24) ═══

Built `script_validator.py` — validates ScriptType action sequences against the
grammar MINED from vanilla aimd.ini (88 scripts). Catches the class of bug where
the tool emits an ordering vanilla AI never uses.

- **Hard rules (ERRORS):** unload(8/9) must follow MoveToEnemyBldg(47), never a
  gather/regroup(53/54); WaitLoaded(43) must follow a load(14/10103);
  gather-at-enemy(53) is an OUTSIDE-base assembly point — never load/unload/deploy
  right after it; deploy(63) belongs at the friendly base (after 54), not after 53.
- **Soft layer (WARN):** transitions never seen in vanilla (minus accepted idioms
  like 49->6 loop-back and 43->53 fight-while-loaded). Flagged a real one: Iron
  Curtain Assault activates IC at base (54->55->53) then travels — IC likely
  expires before reaching the enemy; vanilla does 53->55 (IC when at the target).
- **Integrated** into wave_generator `_get_or_create_script` — every dynamically
  generated script is validated at build time, ERRORs printed with the fix.

FIXED:
- `_bfrt_script_actions`: BFRT is OPEN-TOPPED (passengers fire from inside) — removed
  the unload(8); it now gathers near the enemy and attacks while loaded (53->0).
  This was the reported "transport deploys while gathered outside enemy base" bug.

STILL FLAGGED (need the proper fix — per-matchup BwP targeting):
- `_naval_transport_script` and the static "Transport Drop Attack" template still do
  53->8 (unload after gather). For a PLAIN (non-open-topped) transport the vanilla-
  correct pattern is `47,<BWP_NEAREST+enemy_bldg_idx> -> 8 -> 46,<same>` (move to a
  specific enemy building, unload there, attack it). Blocker: the enemy building
  index is matchup-specific (GACNST vs NACNST vs YACNST differ by [BuildingTypes]
  position), and 47 targets ONE type. Vanilla solves this with SEPARATE scripts per
  matchup ("Allied Transport vs Sov Conyard" 47,25 vs "vs Allied Con" 47,2).
  → Migrate these to BwP-resolved per-matchup scripts (the tool already has
  BwPResolver + _building_idx). The validator flags them until then.

OPEN — "AI infantry randomly attacking the ground": not necessarily an ordering
bug (validator is ordering-focused). Likely a deployfire/target-argument issue OR
the current ScriptSwitch TEST section swapping team 808ABA72-G to arbitrary scripts.
Needs the debug.log (which trigger/team/script the offending infantry run) to pin.

## BUG FIXED: Flying transports softlock load-based waves (Rex report, 2026-09-05)

Symptom (in-game): GDI engineer-rush team "1 BlackHawk Transport 5 Engineer"
(taskforce 93AB30DD-G) softlocks — the engineers chase the SHAD to board it,
crowd the landing cell, the SHAD can't touch down, retries another spot, the
engineers follow, forever.

Root cause: SHAD is a Jumpjet VEHICLETYPE (Passengers=5, Speed=28, cheap), so
it passed every load-based transport filter (`category == 'Vehicle'`, fast,
affordable, has slots). Any Aircraft/Jumpjet-mobility transport has this
problem: it must LAND to board ground passengers, and the AI's boarding crowd
blocks the pad.

Fix (wave_generator.py): new `_is_flying_transport(rec)` helper
(mobility in Aircraft/Jumpjet) now excludes flyers at all six transport-pick
sites: engineer-rush viable loop, suicide-rush `_get_transports`,
`_find_parasite_carrier`, `_find_gunner_vehicle`, `_maybe_load_infantry`
(flyers can't satisfy the gunner/opentop tag), and the
`generate_gunner_waves` collector. Excluded in Rex's rules: SHAD, ZEP, SCHP,
DISK.

Also fixed while verifying (rulesmd_parser.py): the LOCOMOTOR_MOBILITY GUID
lookup NEVER matched — table keys keep `{}` braces but the parsed locomotor
string strips them, so every unit silently fell through to the MovementZone
fallback. Now compares brace-stripped. True behavioral delta on Rex's rules:
jumpjet units (SHAD/ZEP/SCHP/DISK/HIND/SCHD/LUNR) are now `Jumpjet` instead
of `Aircraft` (restores the documented land-mix design), CMON/SMON become
`Teleport`, and the DNOB water animal (Walk loco + Water zone) misclassifies
Naval→Land (harmless, ambient unit). Next regen may mix jumpjets into land
teams — that's the intended design finally taking effect.

LIVE aimd HOT-FIX (no regen — the zone-threat test rig's hand tags live in
the current aimd): disabled the 9 GDI Engineer Rush triggers that use the
SHAD taskforce (28706–28714: 7FB18B1E/90740641/C30C4CDF/40AC7A64/6417395D/
F82202A0/E94BBC41/8E78B36A/6D70404E) by setting their per-difficulty enable
flags to 0,0,0. GDI keeps engineer rushes via the IFV-taskforce "Rush 4/5/6"
triggers. Backup: aimd.ini.pre-shadfix-bak. The disabled triggers heal
themselves on the next tool regen (SHAD can no longer be picked).

## Air-transport delivery — SOLVED with vanilla's 58-anchor pattern (Rex, 2026-09-07)
> **ROUTING → TOOL** (remaining work: extend the 58-anchor to the still-flyer-excluded wave types listed below).

Rex's policy on the BlackHawk (SHAD): **never offensive, but DO use it as
a transport — that's its purpose.** Rex pointed out vanilla AI uses it
fine; he was right, and the earlier "flyer boarding is engine-broken"
claim in this file was WRONG (unearned root cause — the softlock was a
missing script step, not engine behavior).

**How vanilla boards a flyer** (vanilla_aimd.ini "Allied Transport vs Sov
Conyard" 0C954B4C-G, taskforce = Tanya + 3 GI + SHAD + GGI):
```
0=58,7    MoveToFriendlyBldg: EVERYONE rendezvous at own GAWEAP (0-based
          idx 7) — a STATIC anchor. The flyer lands there; nobody is
          chasing a moving landing target, so boarding just works.
1=14,0    load onto transport
2=43,0    wait until fully loaded
3=47,25   fly to enemy CY
4=8,2     unload — transport LEAVES the team (delivery only)
5=46,...  attack
```
Plus TeamType `TransportsReturnOnUnload=yes` → the flyer flies home.
The tool's engineer-rush script lacked the 58 anchor and the 43 wait
(`load → 47 → 8,0`) — fine for ground transports, softlock for flyers.

Shipped (tool):
- `generate_engineer_rush_waves`: flyers are VIABLE transports again; a
  flying transport now gets the vanilla air pattern —
  `58,<BwP-NEAREST own factory> → load → 43 → 47,<enemy CY> → 8,2 →
  0,2 → 49` + `TransportsReturnOnUnload=yes`. The anchor building comes
  from `transport.factory_anchor` (SHAD → GAWEAP, idx 7 in Rex's rules,
  BwP arg 131079 — same building vanilla anchors on). Ground transports
  keep the exact old script (no behavior change).
- `make_transport_drop_actions` / `_gen_aerial_transport_drop`: takes an
  `anchor_arg` and prepends the 58 rendezvous; the hard-disable was
  removed (wave is still dormant behind its DMPRNODEFAA dummy gate).
- `role_unit_filter`: aerial transports still barred from every COMBAT
  role (Rex's no-offensive policy — SHAD is is_combat_transport=True in
  his rules, so without this it could be drafted as a gunship).

Shipped (LIVE aimd hot-patch, no regen — zone-threat rig untouched):
3 new air scripts [8A100001/2/3-G] (58,131079 pattern, per enemy-CY
matchup), the 9 GDI Engineer Rush teams repointed to them +
TransportsReturnOnUnload=yes, and their 9 triggers RE-ENABLED with the
original difficulty flags. The shared ground scripts (361841CC/FC0E9195/
6B815BC6) still serve only the HTK/IFV taskforces — verified. Backups:
aimd.ini.pre-shadfix-bak (before trigger-disable),
aimd.ini.pre-airfix-bak (before this patch).

Still flyer-EXCLUDED (shared template scripts with no anchor; extend the
58 pattern to them if ever wanted): suicide-rush `_get_transports`,
`_find_parasite_carrier`, `_find_gunner_vehicle`, `_maybe_load_infantry`
tagging, `generate_gunner_waves`. Ground transports cover all of these
today, so nothing is lost.

TEST (next game as/vs GDI-side AI): expect the AI's engineers to walk to
its War Factory, the BlackHawk to land there and load, fly to your CY,
unload, and fly home. Watch for: pathing at a crowded factory exit, and
whether 58's NEAREST mode picks a sane factory when the AI has several.

## FormationKeep — throttle fast units so the slowest keep up (Rex, 2026-09-07)
> **ROUTING → ATTExt, SHIPPED 2026-09-18** (commit 5976e1f, deployed, not yet
> in-game tested; Mode=stop only — match deferred). Remaining here: the TOOL
> follow-up (auto-tag FormationKeep=yes on knowingly mixed-speed taskforces).

TeamType-level DLL feature (`[TeamID.AIExt]`, same family as ScriptSwitch /
MissileEvasive). The tool already tries to group similar-speed units into
taskforces; this is the RUNTIME half: when a team IS mixed-speed, the AI
physically stops/slows its faster members so everyone stays within a
radius of the slowest — no more tanks arriving one at a time ahead of
the infantry.

```ini
[SomeTeam.AIExt]
FormationKeep=no          ; master switch (default off, zero cost unset)
FormationKeep.Radius=5    ; cells: how far a member may range ahead of the
                          ; rearmost/slowest member before being held
FormationKeep.Mode=stop   ; stop  = halt outrunners until laggard closes
                          ; match = continuously throttle to laggard speed
FormationKeep.Resume=2    ; hysteresis: resume when laggard is back within
                          ; this many cells (prevents stop/go flapping)
```

Implementation notes (first pass):
- Evaluate in the existing TeamClass::Update co-hook (0x6E9443, already
  ours for ScriptSwitch), every N frames (~8) — walk members, find the
  rearmost (farthest from the team's current move destination), hold any
  member more than Radius cells ahead of it.
- Hold mechanism: `FootClass::SetSpeedPercentage(0.0)` / restore. The
  vfunc is an RX stub in YRpp — call the game address directly (the
  R0-stub footgun), resolve it via the encyclopedia/PDB first. CAUTION:
  other systems (Kratos AttachEffects, parasite drag) also drive
  SpeedPercentage — save/restore the prior value, and if collisions bite,
  fall back to a brief mission pause instead. `match` mode scales it to
  laggardSpeed/mySpeed instead of 0.
- Scope it to MOVE-phase script actions only (move/gather/regroup); release
  everyone the moment the team enters an attack action or combat — held
  units must never be sitting ducks under fire (or: auto-release any member
  that is currently targeted/damaged).
- MP-safe by construction: pure synced-state computation, no randomness.
- RE FIRST: vanilla `GuardSlower=` (TeamTypeClass.GuardSlower, parsed; 14
  vanilla teams set it; TeamClass.GuardSlowerIsNotUnderStrength exists) —
  find what the engine actually does with it. If it's a dormant/partial
  version of this feature, hook/extend its path instead of fighting it;
  if it's about under-strength recruiting (the field name hints so), it's
  unrelated and FormationKeep stands alone.
- Tool follow-up (later): keep speed-grouping as-is, but auto-tag
  FormationKeep=yes on taskforces the tool KNOWS it built mixed-speed
  (escort comps, BFRT+infantry, transport+guard) instead of silently
  accepting the spread.

## MissileEvasive — Team Missile-Dodge Scatter (Rex, 2026-09-01)
> **ROUTING → ATTExt, SHIPPED 2026-09-18** (commit 2b28fba, deployed, not yet
> in-game tested). Spec implemented as written (Cells / WH.Calc larger-wins)
> plus one added knob: MissileEvasive.MinDamage=100 detection filter so light
> AA rockets don't cause nonstop dancing. Test: TESTING.md Test J.

TeamType-level feature (`[TeamID.AIExt]`, same home as ScriptSwitch): when a
missile is detected targeting a team member's location, the unit always
scatters out of the blast area.

```ini
[SomeTeam.AIExt]
MissileEvasive=no          ; always scatter when a missile is detected
                           ; targeting their location (default off)
MissileEvasive.Cells=4     ; move this much (cells)
MissileEvasive.WH.Calc=yes ; also compute a distance from the incoming
                           ; missile's warhead size (blast radius).
                           ; The LARGER value wins: if MissileEvasive.Cells >
                           ; the WH-derived distance, use Cells; if the
                           ; WH-derived distance is larger, move that much.
```

Spec (Rex's wording, verbatim intent):
- `MissileEvasive` — always scatter when a missile is detected targeting
  their location.
- `MissileEvasive.Cells` — move this much.
- `MissileEvasive.WH.Calc` — move based on the warhead size of the missile.
  If `MissileEvasive.Cells` is larger than the WH.Calc distance, do what
  `MissileEvasive.Cells` states; if WH.Calc is larger, WH.Calc is how much
  the unit moves. (i.e. effective distance = max of the two when WH.Calc=yes.)

Implementation notes (first pass, unverified):
- Detection: scan live BulletClass objects (or hook bullet creation) for
  missile-type projectiles whose target coords fall within the incoming
  warhead's CellSpread of a team member's cell. Open design Q: what counts
  as a "missile" — ROT>0 homing bullets, V3/Dreadnought elite rockets,
  SW nukes, torpedoes? Probably a projectile-flag filter (Arcing/ROT/Vertical)
  plus maybe an opt-in list later.
- WH.Calc distance source: incoming warhead `CellSpread` (blast radius in
  cells) — scatter to just outside it; effective = max(Cells, CellSpread-derived).
- Acting: per-member scatter move (TechnoClass scatter / move mission) away
  from the predicted impact cell — NOT a script swap; the team's script should
  resume after the dodge. Natural co-hook: the existing TeamClass::Update
  0x6E9443 site (ScriptSwitch already lives there).
- Needs a per-member (or per-team) cooldown/latch so one tracked missile
  doesn't retrigger scatter every frame mid-flight.
- Sync: evaluate in game logic only, use ScenarioClass random for scatter
  direction — MP-desync-safe (same rule as everything else in the DLL).
- Precedent check before building: vanilla infantry Scatter= reacts to fire;
  Phobos/Ares/Kratos have no team-scoped missile-evasion (verify at build time).
