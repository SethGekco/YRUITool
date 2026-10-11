# AITriggerTypeExt — Full Tag Reference

This document lists every tag added by the AITriggerTypeExt DLL. Each tag
has a status marker so Rex can track what's implemented, tested, and
verified in-game. Update the markers as testing progresses.

**Status marker legend:**
- `[ ]` — designed but not implemented in DLL
- `[C]` — implemented in DLL (compiles clean)
- `[T]` — tested in-game (loads without errors)
- `[V]` — verified working (behaves as expected)
- `[X]` — implemented but confirmed broken (bug to fix)

**Document version:** matches tool version v31. Update when new tags land.

---

## Section 1: [Debug] in rulesmd.ini

Top-level control. Not per-trigger. Applies to all AITriggerType.AIExt
debug output game-wide.

### DisplayAIWaveMessages

- **Location:** `[Debug]` section in `rulesmd.ini`
- **Type:** enum
- **Default:** `no`
- **Valid values:** `no` / `yes` / `log` / `both`
- **Status:** `[ ]`
- **Meaning:**
  - `no` — silence all AIExt debug output
  - `yes` — show overlay messages in game HUD only
  - `log` — write to `debug.log` only
  - `both` — do both
- **Example:**
  ```ini
  [Debug]
  DisplayAIWaveMessages=both
  ```

---

## Section 2: [AITriggerTypeName.AIExt] — trigger-level lifecycle

Six tags fire once per state transition of the AITriggerType lifecycle,
regardless of which gates the trigger has. Attached to `.AIExt` section
by trigger name.

### DebugMessageDisplay.Start / .Cancel / .Finish

- **Location:** any `[TriggerName.AIExt]` section
- **Type:** CSF key (e.g. `STT:Key` or `NOSTR:Free Text` or `Free Text`)
- **Default:** none (no overlay message)
- **Status:** `[ ]`
- **Meaning:** shown as overlay message when the trigger transitions state
  - `Start` fires when AIExt prerequisites all pass; team about to build
  - `Cancel` fires when AIExt vetoes what vanilla passed; team NOT built
  - `Finish` fires when team has been dispatched (reserved for Phase 2)
- **Fires only when:** `[Debug].DisplayAIWaveMessages=yes` or `both`
- **Example:**
  ```ini
  [TT_TANK_RUSH.AIExt]
  DebugMessageDisplay.Start=NOSTR:Tank rush trigger fired
  DebugMessageDisplay.Cancel=NOSTR:Tank rush vetoed
  DebugMessageDisplay.Finish=NOSTR:Tank rush deployed
  ```

### DebugLog.Start / .Cancel / .Finish

- **Location:** any `[TriggerName.AIExt]` section
- **Type:** plain ASCII string
- **Default:** none (no log line)
- **Status:** `[ ]`
- **Meaning:** written to `debug.log` when trigger transitions state
- **Fires only when:** `[Debug].DisplayAIWaveMessages=log` or `both`
- **Difference from DebugMessageDisplay:** log line is a raw string, no
  CSF lookup, no localization. Written verbatim. Suited for verbose
  post-match analysis.
- **Example:**
  ```ini
  [TT_TANK_RUSH.AIExt]
  DebugLog.Start=TT_TANK_RUSH started building — all prereqs met
  DebugLog.Cancel=TT_TANK_RUSH vetoed by AIExt gates
  DebugLog.Finish=TT_TANK_RUSH team deployed to enemy base
  ```

---

## Section 3: [AITriggerTypeName.AIExt] — condition tags (gates)

These tags actually gate whether the trigger fires. Each is documented
with its variants (Min, Max, Current) and debug sub-fields.

### Condition family: Buildings / Units / SuperWeapons (list-form)

Family covers eight base gate names:
- `RequiredOwnerBuildings` / `RequiredEnemyBuildings` / `RequiredAlliesBuildings` / `RequiredNeutralBuildings`
- `RequiredOwnerUnits` / `RequiredEnemyUnits` / `RequiredAlliesUnits` / `RequiredNeutralUnits`
- `RequiredOwnerSuperWeapons` / `RequiredEnemySuperWeapons` / `RequiredAlliesSuperWeapons` / `RequiredNeutralSuperWeapons`

Each has three parallel-list siblings (Min / Max / Current) and one enum
tag (TargetHouseMode) that affects house selection for the Enemy variant.

#### RequiredOwnerBuildings (etc.)

- **Location:** `[TriggerName.AIExt]` section
- **Type:** comma-separated list of TechnoType IDs
- **Default:** none (no check)
- **Status:** `[ ]`
- **Meaning:** each TechnoType in the list is checked against the target
  house's owned instances. Gate passes when every entry falls within
  the corresponding Min-Max range.
- **Example:**
  ```ini
  RequiredOwnerBuildings=GABARR,GAWEAP,GATECH
  ```

#### RequiredOwnerBuildingsMin

- **Location:** `[TriggerName.AIExt]` section
- **Type:** comma-separated list of integers, same length as base list
- **Default:** empty list (equivalent to all 0 — "at least 0" is always true)
- **Status:** `[ ]`
- **Meaning:** per-index minimum. Positional match with the base list.
- **Example:**
  ```ini
  RequiredOwnerBuildingsMin=1,1,0
  ```

#### RequiredOwnerBuildingsMax

- **Location:** `[TriggerName.AIExt]` section
- **Type:** comma-separated list of integers, same length as base list
- **Default:** empty list (equivalent to all `-1` — no upper bound)
- **Status:** `[ ]`
- **Meaning:** per-index maximum. `-1` means uncapped.
- **Example:**
  ```ini
  RequiredOwnerBuildingsMax=-1,-1,3
  ```

#### RequiredOwnerBuildingsCurrent

- **Location:** `[TriggerName.AIExt]` section
- **Type:** READ-ONLY (no INI value); exists only as a debug anchor
- **Default:** N/A
- **Status:** `[ ]`
- **Meaning:** live count of each TechnoType in the base list, fetched
  from the target house at trigger evaluation time. Used by debug sub-
  fields to show current values, not by the gate itself.
- **Not settable:** the DLL should log an error if a modder writes an
  INI value for this tag (e.g. `RequiredOwnerBuildingsCurrent=GABARR`).
- **Example (Debug sub-fields only):**
  ```ini
  RequiredOwnerBuildingsCurrent.Debug.MessageDisplay=NOSTR:Current buildings
  RequiredOwnerBuildingsCurrent.Debug.ValueDisplay=yes
  ```

---

### Condition family: Credits / Power / PowerOutput / TechLevel (scalar-form)

Family covers per-house scalars. For each house (Owner, Enemy, Allies,
Neutral), the DLL supports:
- `RequiredXCreditsMin` / `RequiredXCreditsMax` / `RequiredXCreditsCurrent`
- `RequiredXPowerMin` / `RequiredXPowerMax` / `RequiredXPowerCurrent`
- `RequiredXPowerOutputMin` / `RequiredXPowerOutputMax` / `RequiredXPowerOutputCurrent`
- `RequiredXTechLevelMin` / `RequiredXTechLevelMax` / `RequiredXTechLevelCurrent`

Where X = Owner / Enemy / Allies / Neutral.

#### RequiredXCreditsMin (all 4 houses)

- **Location:** `[TriggerName.AIExt]` section
- **Type:** integer
- **Default:** none (no check)
- **Status:** `[ ]`
- **Meaning:** target house's current credits must be >= this value
- **Example:**
  ```ini
  RequiredOwnerCreditsMin=2000
  RequiredEnemyCreditsMin=5000
  ```

#### RequiredXCreditsMax (all 4 houses)

- **Location:** `[TriggerName.AIExt]` section
- **Type:** integer
- **Default:** none (no check)
- **Status:** `[ ]`
- **Meaning:** target house's current credits must be <= this value
- **Example:**
  ```ini
  RequiredEnemyCreditsMax=1500   ; fire when enemy is broke
  ```

#### RequiredXCreditsCurrent (all 4 houses)

- **Location:** `[TriggerName.AIExt]` section
- **Type:** READ-ONLY (no INI value)
- **Status:** `[ ]`
- **Meaning:** live credits value at evaluation time. Debug anchor only.

#### RequiredXPowerMin / Max / Current (all 4 houses)

- **Location:** `[TriggerName.AIExt]` section
- **Type:** integer (Min/Max only; Current is read-only)
- **Default:** none (no check)
- **Status:** `[ ]`
- **Meaning:** target house's NET power (output − drain). Negative means
  low-power state. `-100` means "50 units low."
- **Example:**
  ```ini
  RequiredEnemyPowerMax=-50    ; fire when enemy is low-power
  ```

#### RequiredXPowerOutputMin / Max / Current (all 4 houses)

- **Location:** `[TriggerName.AIExt]` section
- **Type:** integer (Min/Max only)
- **Default:** none (no check)
- **Status:** `[ ]`
- **Meaning:** total power production (Output only, before drain). Reads
  from house's power-production sum.
- **Example:**
  ```ini
  RequiredEnemyPowerOutputMin=200
  ```

#### RequiredXTechLevelMin / Max / Current (all 4 houses)

- **Location:** `[TriggerName.AIExt]` section
- **Type:** integer 0-10
- **Default:** none (no check)
- **Status:** `[ ]`
- **Meaning:** target house's current game tech level.

---

### Condition: RequiredElapsedTimeMin / Max / Current

- **Location:** `[TriggerName.AIExt]` section
- **Type:** integer (frames; 15 frames = 1 in-game second, regardless of
  game speed)
- **Default:** none (no check)
- **Status:** `[ ]`
- **Meaning:**
  - `Min` — trigger cannot fire until this many frames have elapsed since
    game start. Used for shifting AI from early-game patterns to mid/
    late-game.
  - `Max` — trigger cannot fire after this many frames. Used for phasing
    out early-game-only triggers.
  - `Current` — live game clock frame at evaluation time (READ-ONLY,
    debug anchor only).
- **Example (5-minute delay):**
  ```ini
  RequiredElapsedTimeMin=4500    ; 5 minutes × 60 seconds × 15 frames
  ```

---

### Condition: TargetHouseMode

- **Location:** `[TriggerName.AIExt]` section
- **Type:** enum
- **Default:** `Current` (matches vanilla behavior)
- **Status:** `[ ]`
- **Valid values:**
  - `Current` — check only the trigger's currently-targeted enemy house
  - `Any` — check any enemy house; gate passes if ANY match
  - `All` — check all enemy houses; gate passes only if ALL match
  - `Most_Buildings` — check the enemy house with the most buildings
  - `Least_Buildings` — check the enemy house with the fewest buildings
  - `Most_Units` — check the enemy house with the most units
  - `Least_Units` — check the enemy house with the fewest units
- **Meaning:** determines which enemy house(s) the `RequiredEnemy*` gates
  evaluate against.
- **Example:**
  ```ini
  TargetHouseMode=Any    ; fire when ANY enemy meets the conditions
  ```

---

### Condition: RequiredAlliesMode

- **Location:** `[TriggerName.AIExt]` section
- **Type:** enum
- **Default:** `All`
- **Status:** `[ ]`
- **Valid values:** same shape as TargetHouseMode
- **Meaning:** determines which allied house(s) the `RequiredAllies*`
  gates evaluate against.

---

## Section 4: Debug sub-fields (universal, per-gate)

Every gate documented in Section 3 supports the same nine debug sub-
fields. Format: `<GateName>.Debug.<SubField>=<value>`.

Not every sub-field applies to every gate type; the DLL silently ignores
sub-fields that don't apply to that gate (with one Debug::Log line at
load time).

### <GateName>.Debug.MessageDisplay

- **Type:** CSF key (`STT:Key` / `NOSTR:Text` / plain text)
- **Default:** none
- **Status:** `[ ]`
- **Meaning:** overlay text shown when this gate is evaluated in a
  triggering lifecycle event (governed by TriggerDisplay).
- **Example:**
  ```ini
  RequiredOwnerBuildings.Debug.MessageDisplay=NOSTR:Own buildings check
  ```

### <GateName>.Debug.ValueDisplay

- **Type:** boolean (`yes` / `no` / `true` / `false` / `1` / `0`)
- **Default:** `no`
- **Status:** `[ ]`
- **Meaning:** when `yes`, the computed value for this gate is appended
  to the MessageDisplay text as its own line.
- **Value format per gate type (DLL-owned):**
  - List (base): `GABARR, GAWEAP, GATECH` (TechnoType IDs only)
  - List (Min): `1, 1, 0` (integers matching base positions)
  - List (Max): `-1, -1, 3`
  - List (Current): `3, 0, 2` (live counts)
  - Scalar (Min/Max/Current): `2000` (single integer)
  - Enum: `Any` / `Current` / etc.
- **Example:**
  ```ini
  RequiredOwnerBuildings.Debug.ValueDisplay=yes
  ; Overlay output: "Own buildings check"
  ;                 "GABARR, GAWEAP, GATECH"
  ```

### <GateName>.Debug.LogMessage

- **Type:** plain ASCII string
- **Default:** none
- **Status:** `[ ]`
- **Meaning:** raw text written to `debug.log` when this gate is
  evaluated (governed by TriggerLog). No CSF lookup, no localization.
- **Example:**
  ```ini
  RequiredOwnerBuildings.Debug.LogMessage=Own buildings check evaluated
  ```

### <GateName>.Debug.LogWrite

- **Type:** boolean
- **Default:** `no`
- **Status:** `[ ]`
- **Meaning:** when `yes`, the DLL emits an automatic detailed log line
  after `LogMessage`, showing per-entry pass/fail status.
- **Format (DLL-owned):**
  - List: `GABARR: needs [1,-1], has 3 PASS; GAWEAP: needs [1,-1], has 0 FAIL`
  - Scalar: `needs [2000,+INF], has 1500 FAIL`
- **Example:**
  ```ini
  RequiredOwnerBuildings.Debug.LogWrite=yes
  ; debug.log gets:
  ;   [Frame 4521] TT_TANK_RUSH Own buildings check evaluated
  ;   GABARR: needs [1,-1], has 3 PASS
  ;   GAWEAP: needs [1,-1], has 0 FAIL
  ```

### <GateName>.Debug.TriggerDisplay

- **Type:** comma-list of enum
- **Default:** `finish`
- **Status:** `[ ]`
- **Valid values:** any combination of `start`, `cancel`, `finish`, `never`
  - `never` in the list or empty value = silent (overrides all others)
- **Meaning:** which lifecycle events cause MessageDisplay + ValueDisplay
  to fire. Sub-tags (Min/Max/Current) inherit from the parent base tag
  unless they set their own.
- **Example:**
  ```ini
  RequiredOwnerBuildings.Debug.TriggerDisplay=start,finish
  ; Fires on Start and Finish, not Cancel
  ```

### <GateName>.Debug.TriggerLog

- **Type:** comma-list of enum (same shape as TriggerDisplay)
- **Default:** `finish`
- **Status:** `[ ]`
- **Meaning:** which lifecycle events cause LogMessage + LogWrite to
  write to `debug.log`.
- **Example:**
  ```ini
  RequiredOwnerBuildings.Debug.TriggerLog=start,cancel,finish
  ```

### <GateName>.Debug.DetailsDisplay

- **Type:** boolean
- **Default:** `no`
- **Status:** `[ ]`
- **Meaning:** when `yes`, emit a bracket-formatted per-index row on
  overlay/log combining multiple field values on one line.
  See DetailsTypes for the format shape.

### <GateName>.Debug.DetailsTypes

- **Type:** comma-list of field names
- **Default:** none (Details won't render without this)
- **Status:** `[ ]`
- **Valid values for list-form gates:** `Type`, `Minimum`, `Maximum`, `Current`
- **Valid values for scalar-form gates:** `Minimum`, `Maximum`, `Current`
  (Type is invalid on scalars)
- **Meaning:** each parallel-list index (or single scalar value) renders
  as `[field1, field2, field3, ...]` in the modder's declared field order.
- **Format examples:**
  - `DetailsTypes=Type, Minimum, Maximum, Current` on a list with
    GABARR=3, GAWEAP=0 (Min=[1,1], Max=[-1,-1]) prints:
    ```
    [GABARR, 1, -1, 3]
    [GAWEAP, 1, -1, 0]
    ```
  - `DetailsTypes=Current, Minimum` on the same list prints:
    ```
    [3, 1]
    [0, 1]
    ```
  - `DetailsTypes=Minimum, Maximum, Current` on a credits scalar
    (Min=2000, Current=1500) prints:
    ```
    [2000, -1, 1500]
    ```
- **Notes:**
  - Field order in the bracket matches modder's declared order in the tag
  - Unknown or inapplicable field names get logged once, silently skipped
  - No `=` between field name and value — space-separated only

### <GateName>.Debug.DetailsTrigger

- **Type:** comma-list of enum (same shape as TriggerDisplay)
- **Default:** `finish`
- **Status:** `[ ]`
- **Meaning:** which lifecycle events cause DetailsDisplay to render.

---

## Section 5: rulesmd.ini optional lists (used by AIExt gates)

Modder-declared lists referenced by index or name from the .AIExt tags.

### [DPSGroupTypes] (planned, not yet in DLL)

Custom named groups for the DPS check system. Groups are TechnoType lists
with optional per-entry damage weights.

- **Location:** `[DPSGroupTypes]` section in `rulesmd.ini`
- **Status:** `[ ]`
- **Example:**
  ```ini
  [DPSGroupTypes]
  GROUP_ONE=HTNK,MTNK
  GROUP_ONE.Ratio=2.0,1.5

  SlowTowers=SLOWTWR1,SLOWTWR2
  SlowTowers.Ratio=10,10

  GeneralTanks=HTNK,LTNK,MTNK
  ; No .Ratio — all entries default to 1.0
  ```
- Referenced by future `DPSCheck.Types.N=` tag on AIExt sections.

### [AISuperWeaponValues] (planned)

Modder-declared value tables for superweapon targeting. Subsumes and
extends the vanilla `AIIonCannonXValue=` system.

- **Location:** `[AISuperWeaponValues]` section in `rulesmd.ini`
- **Status:** `[ ]`
- **Example:**
  ```ini
  [AISuperWeaponValues]
  IonCannon.Target=Enemy
  IonCannon.Weights=ConYard:100,WarFactory:100,Refinery:80
  IonCannon.HPWeighting=Inverse
  IonCannon.FireThreshold=200

  ForceShield.Target=Owner
  ForceShield.Weights=ConYard:100,BattleLab:90
  ForceShield.HPWeighting=Direct
  ForceShield.FireThreshold=150
  ```

---

## Section 6: Planned features not yet designed

Empty placeholders for future work — updates go here as features get scoped.

- **DPSCheck** — condition family for weapon-DPS thresholds. Uses
  `[DPSGroupTypes]`. Schema documented in TODO.md.
- **CreditsRateCheck** — condition family for house credits rate of
  change over a rolling window. Schema documented in TODO.md.
- **Debug messages on scripts** — CSF-based message display per script
  action, similar to trigger lifecycle messages. Sketched in TODO.md.

---

## Section 7: Testing workflow (Rex-specific)

Marker column above is for you to fill in as testing progresses.
Suggested workflow:

1. Land the base AIExt reader + trigger lifecycle debug system first.
   Test with a single trigger and confirm messages appear in-game.
   Mark those tags `[C]` after DLL builds, `[T]` after in-game load,
   `[V]` after actual message display.

2. Then land the six-field debug quad on ONE gate type (e.g.
   `RequiredOwnerBuildings`). Verify the full six sub-fields fire in
   the expected lifecycle events.

3. Extend to the rest of the gates.

4. Layer in Details display last since it depends on the base debug
   quads working.

For each testing session, add a note to Section 8 with what you tried
and what happened.

---

## Section 8: Test log

Fill in as you test. Timestamped entries.

**YYYY-MM-DD** — first entry once testing starts.

---

*Document generated by YR AI Wave Generator v31 tooling notes.*
*Update marker column and Section 8 as tags land, are tested, and
verified.*
