# Tool behaviour and invariants (things Eva should know)

- `Damage = 1` is NEVER scaled by the tool — weapons with damage 1 are a
  convention for utility/chip weapons (deployers, some detectors). Leave them.
- `TechLevel = -1` (and anything < 1) means the techno is NOT player-buildable.
  Exclude these when balancing buildable units: `{EXCLUDE} TechLevel = <1 {/EXCLUDE}`.
- Valid player TechLevels are 1–10. TechLevel 11+ is also effectively unbuildable
  in normal play.
- Garrison weapons: infantry fire from a garrisoned building via `OccupyWeapon`
  (regular) and `EliteOccupyWeapon` (elite). These are real weapon sections and the
  tool scales them with the infantry's weapon ratios by default. To target them
  explicitly, edit those weapon sections by name.
- Open-topped transports fire passengers' weapons out; damage is scaled by
  `OpenTopped...` damage multipliers on the transport, not on the passenger.
- Range protection: very short-range weapons (attack dogs at Range ~2.25, melee)
  should not have their Range stretched. Prefer excluding them or leaving Range
  alone for them.
- Cost can scale three ways in the tabs: flat (× ratio), × TechLevel, or × building
  depth. In a formula you express the same thing directly, e.g.
  `Cost = TechLevel * 100`.
- Soylent is the sell/refund value. It makes sense to tie it to the final Cost:
  `Soylent = Cost * 0.5`.
- Every edit the tool writes carries a `; vanilla=` comment, which powers Preview,
  per-change Undo (History tab), and restore-to-vanilla. Use `#raw: true` only if
  you deliberately want un-annotated values.
- Buildings don't have Speed or Soylent; use Debris / Adjacent instead.
