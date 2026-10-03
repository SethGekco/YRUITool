# Recipes — request → script

Worked examples. Match the user's request to the closest pattern and adapt the
numbers, scope, and conditions.

## Costs
Halve the cost of all infantry:
```
[$INFANTRY]
Cost = Cost * 0.5
```

Make everything cost more the higher its tech tier:
```
[$ALL]
Cost = TechLevel * 100
```

Cheaper early-game, pricier late-game (scale around tier 5):
```
[$UNIT]
Cost = Cost * (0.6 + 0.1 * TechLevel)
```

## Durability and firepower
Double all unit HP:
```
[$UNIT]
Strength = Strength * 2
```

Glass cannons — half HP, double primary damage:
```
[$UNIT]
Strength = Strength * 0.5
Damage = Damage * 2
```

## Speed / build time
Speed up all vehicles 20%:
```
[$VEHICLE]
Speed = Speed * 1.2
```

## Filtering (exclude / include)
Buff only *buildable* infantry (skip TechLevel -1/0 units) and skip the dog:
```
[$INFANTRY]
{EXCLUDE}
TechLevel = <1
[ADOG]
{/EXCLUDE}
Strength = Strength * 1.5
```

Only affect units that have a BuildLimit:
```
[$ALL]
{INCLUDE}
BuildLimit = !
{/INCLUDE}
Cost = Cost * 0.75
```

Change everything with the M60 as its primary weapon only:
```
[$INFANTRY]
{INCLUDE}
Primary = M60
{/INCLUDE}
Cost = Cost * 0.9
```

## Specific sections
Tweak a single unit:
```
[GGI]
Cost = 800
Strength = Strength * 1.25
```

Edit a weapon directly (by its section name):
```
[M60]
Damage = Damage * 1.2
ROF = ROF * 0.9
```

A [General] tweak:
```
[General]
BuildSpeed = 0.9
```

## Sight
Set every building's sight to a flat value:
```
[$STRUCTURE]
Sight = 10
```

## Soylent (sell refund)
Refund 50% of the (current) cost:
```
[$UNIT]
Soylent = Cost * 0.5
```

## Garrison weapons (OccupyWeapon / EliteOccupyWeapon)
Infantry that garrison a building fire the weapon named by their `OccupyWeapon`
(and `EliteOccupyWeapon` when elite) — each of those is a `[WeaponType]` section.
To buff garrison firepower you scale **those weapon sections**, not the infantry.
In vanilla YR they are `UCPara`/`UCElitePara`, `UCM1Carbine`/`UCEliteM1Carbine`,
`UCPsychicJab`/`UCElitePsychicJab`. Example — +50% garrison damage (never scale a
`Damage=1` utility weapon):
```
[UCPara]
Damage = Damage * 1.5
[UCElitePara]
Damage = Damage * 1.5
[UCM1Carbine]
Damage = Damage * 1.5
[UCEliteM1Carbine]
Damage = Damage * 1.5
[UCPsychicJab]
Damage = Damage * 1.5
[UCElitePsychicJab]
Damage = Damage * 1.5
```
To find the exact names in a given mod, read each garrisonable infantry's
`OccupyWeapon` / `EliteOccupyWeapon`. (The Infantry tab's garrison-weapons
modifier does this resolution automatically.)

## Life-steal / vampire (needs KratosPP)
NOT a vanilla or Ares feature. KratosPP provides it through its AttachEffect (AE)
`Vampire` effector: a unit carrying the AE heals a fraction of the damage it
deals. Author an `[AttachEffectType]` section and attach it to the units. Real
tags (see `kratos_usage.md`): `Vampire.Percent` (fraction healed; 0 = off) and
`Vampire.Chance` (default 100%). Example — tanks heal 25% of damage dealt:
```
[LifeStealAE]
Duration=-1
Vampire.Percent=25%
[HTNK]
AttachEffectTypes=LifeStealAE
[APOC]
AttachEffectTypes=LifeStealAE
```
Attach it via a warhead instead (heal on that warhead's hits) with
`AttachEffectTypes=LifeStealAE` + `AttachEffectChances=100%` in the `[WarheadType]`.
Only works if the mod loads KratosPP; in vanilla/Ares these tags do nothing.

## Using a fill-in parameter
```
#param: hp = 1.5
[$INFANTRY]
Strength = Strength * {hp}
```
