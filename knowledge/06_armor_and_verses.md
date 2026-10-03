# Armor types and warhead Verses (vanilla YR damage model)

How much damage a hit deals depends on the **warhead's `Verses`** vs the target's
**`Armor`**. This is the core of combat balance.

## Armor types (TechnoType `Armor=`)
Vanilla Yuri's Revenge armor classes, in order:
`none, flak, plate, light, medium, heavy, wood, steel, concrete, special_1, special_2`
- Infantry usually `none`, `flak`, or `plate`.
- Light vehicles `light`; tanks `heavy`; aircraft `light`.
- Buildings `wood`, `steel`, or `concrete`.
(Ares/Antares can add custom armor types, which also extend the Verses list.)

## Warhead `Verses=` (damage vs each armor)
`Verses=` is a comma-separated list of percentages, one per armor type **in the
order above**. 100% = full damage, 0% = immune, >100% = extra damage.
```
[AP]                  ; armor-piercing warhead
Verses=100%,100%,100%,60%,75%,100%,... ; vs none,flak,plate,light,medium,heavy,...
```
So an anti-tank warhead has high % vs `heavy`/`medium` and low % vs infantry armor;
an anti-infantry warhead is the reverse.

## Effective damage
`actual = Weapon.Damage × Verses[target Armor] × (veterancy/elite modifiers)`.
To rebalance combat you can change the **weapon `Damage`** (scales all targets) or
the **warhead `Verses`** (changes who it's strong/weak against) — not both blindly.

## Balance notes
- Don't scale a weapon whose `Damage=1` (utility/chip — leave it).
- Raising `Damage` on a general-purpose weapon buffs it against everything; narrow
  a unit's role by editing its warhead's `Verses` instead.
- `ROF` is cooldown between shots (lower = faster). DPS ≈ Damage ÷ ROF.
- Splash: warhead `CellSpread` / `Spread` set area damage radius.
