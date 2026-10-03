# Rules tag glossary (starter — verify and extend with your own notes)

Common Red Alert 2 / Yuri's Revenge rules tags. This is a seed; drop more notes
into the knowledge inbox or paste ModENC pages to deepen Eva's understanding.

## TechnoType (infantry / vehicle / aircraft / building) — common
- `Cost` — credits to build. Also seeds refund (Soylent) and AI value.
- `Strength` — hit points.
- `TechLevel` — build tier (1–10). -1 = not player-buildable.
- `Prerequisite` — structures required to build it; `Prerequisite.ListN` for
  alternative lists; `PrerequisiteOverride` bypasses.
- `BuildLimit` — max buildable at once (negative = cumulative limit).
- `Owner` — which countries can build it.
- `Points` — score value when killed.
- `Primary` / `Secondary` — weapon section names; `ElitePrimary` / `EliteSecondary`
  for veteran versions; `Weapon1..` / `EliteWeapon1..` on some types.
- `Soylent` — explicit sell refund (else derived from Cost).

## Infantry-specific
- `Speed` — movement speed.
- `Sight` — reveal radius.
- `OccupyWeapon` / `EliteOccupyWeapon` — weapon used while garrisoning a building.
- `C4` / `Engineer` / `Agent` / `Infiltrate` — special-role flags.

## Vehicle-specific
- `Speed`, `ROT` (turn rate), `Passengers`, `SizeLimit`.
- `OpenTopped` — passengers fire out; `OpenTransportWeapon` controls their weapon.

## Building-specific
- `Adjacent` — how far from other buildings it can be placed.
- `Powered` / `Power` — power produced (+) or consumed (-).
- `MaxNumberOccupants` — garrison capacity (for garrisonable civilian buildings).
- `Sight`, `Armor`.

## Weapon section
- `Damage` — base damage (before warhead modifiers). `Damage=1` is special (utility).
- `ROF` — rate of fire (lower = faster).
- `Range` — reach in cells.
- `Projectile`, `Warhead`, `Speed`, `Report` (sound), `Anim` (muzzle flash).

## Warhead section
- `Verses` — damage vs each armor type (percentages).
- `InfDeath`, `Spread` (splash cells), `CellSpread`.

## [General] (global gameplay)
- `BuildSpeed`, `GrowthRate` (ore), `RepairPercent` / `RepairStep`,
  `VeteranRatio`, `Crate*` (crate behaviour), `NeutralTechBuildings`.
