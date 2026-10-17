# NPatch → Antares/Phobos conversion research

Groundwork for the planned Library feature that ports an NPatch-era mod onto a
modern engine-extension stack. **No code exists yet** — this is the research the
design rests on, written down because most of it came from the web and from a
local Phobos checkout, neither of which is guaranteed to still be there.

Verified 2026-10-13.

## The target is a stack, not an engine

- **Antares** is the engine-extension target, not Ares (standing directive for
  all YR work). Antares reconstructs **Ares 3.0p1** (internal 21.352.1218) and
  its docs are "identical to Ares one", so the Ares documentation and the
  411-flag list in `knowledge/09_ares_flags.md` describe the target surface.
  Deliberate deviations are recorded in Antares' own `CHANGELOG.md`.
- `Antares.dll` and `Ares.dll` **cannot coexist** (both load via Syringe).
- **Phobos** is a separate extension: *"independent of Ares and does NOT
  require Ares specifically to function… meant to accompany Ares rather than
  replace it, thus it won't introduce incompatibilities."* It absorbs a large
  share of what Ares/Antares lacks, so the converter must ask **which
  components the user runs** and emit accordingly.
- Phobos docs never mention Antares. The two share contributors (Antares'
  changelog back-ports Phobos PRs), so the combination is very likely fine —
  but it is **unverified**.

Lineage, for dating a mod's tags: RockPatch (pd, Jan 2005) → VK takes over Sept
2006 → **NPatch** from RockPatch 1.10 → NPatch declared dead **6 Nov 2008**.
Ares is literally "RockPatch 2", announced by pd Oct 2007. Editions shipped:
NPatch 1.00, SE (#0020), AE (#0011). The Ares wiki instead compares "NPatch"
vs "NPatch *Extended*"; that naming does not obviously map onto 1.00/SE/AE, and
edition determines which tags are legal — **resolve before trusting per-edition
claims**.

## Conversion rules decided by the user

| Case | Rule |
|---|---|
| EMP / Iron Curtain duration packed into `Damage=` | emit `Damage=1`, **not 0** — 0 can stop the weapon acquiring or firing — plus warhead `Verses=5%` against every armor it can hit, so effective damage is about 0.05 and rounds away |
| `OpenTopped` experience boolean | emit every `Experience.*` modifier as `1.0` |
| `LaserThickness` above the Prism cap | **do not clamp** when Phobos is in the target (see below) |
| TechLevel slider | report as **unsupported**, plainly. Superseded by the CNCNet lobby — CNCNet support is the next project phase |
| Chrono / DropPod / Paradrop factories | report as **unsupported**. Do **not** offer a workaround — no compatible one exists. Planned as its own work item later |

> **Never synthesise a lookalike.** When a feature cannot be ported
> *compatibly*, say so plainly. Do not emit an approximation that looks like the
> feature and behaves differently — a silently-wrong mod is worse than a
> reported gap.

## Tier 0 — plain renames

**UNBLOCKED 2026-10-16.** The MDK was fetched and extracted; the tag list now
exists on record.

```
NP_AE_MDK.exe (1,084,681 bytes)  RAR self-extracting archive
  -> NPatch.chm (400,113 bytes)  69 HTML doc pages
```

Extraction notes, because this cost two false starts:
- `7z` **lists** the SFX correctly but extracts every file as 0 bytes —
  "Unsupported Method", i.e. no non-free RAR codec. Use `unrar x`. Redirecting
  stderr to /dev/null hides this and leaves a directory of plausible-looking
  empty files.
- The CHM itself extracts fine with `7z`.
- Nothing had to be executed; the installer is just an SFX.

On record now:
- `knowledge/npatch_tags_by_page.txt` — 321 candidate tags across 55 doc pages,
  grouped by the page that documents them. `Reference` alone carries 107.
- `knowledge/npatch_to_modern.tsv` — the mapping table.

### Method, and the adjudicated result

```
321 candidate tags from the CHM
-126 vanilla      a tag in stock rulesmd is a doc EXAMPLE, not an NPatch addition
 195 real NPatch tags, adjudicated:

  T0-rename        37   verified 1:1 target name
  T1-reencode      85   verified target, different spelling/shape
  T1-switch         1   on/off tag with no target (the system is always on)
  T2-structural    12   verified target but the semantics move (see note column)
  T2-sides          6   Ares has 16 sides, but as definitions not per-side tags
  T3-ui            31   NPatch's bespoke menu/shell system — unportable
  T3-factory        4   Chrono/DropPod/ParaDrop bays — deferred work item
  T3-missile        5   custom-missile params; only Missile.TakeOffAnim exists
  T?-research      14   genuinely unresolved
```

**123 of 195 (63%) now have a verified target.** Only 14 are unresolved.

Three rules make this trustworthy:

1. **Vanilla wins outright.** An early pass labelled `Damage`, `Speed`,
   `Strength` and `Warhead` as "Phobos tags" purely because Phobos' docs also
   mention them. Any tag present in stock rulesmd is excluded regardless.
2. **A proposed target is emitted only if it exists** in that extension's own
   key set. Hand-authored semantic mappings and family rules both produce
   *candidates*; a candidate matching nothing real yields nothing. So the table
   cannot contain an invented tag. Four of my own candidates failed this way
   (the sides family) and are recorded as unmapped rather than asserted.
3. **Key sets come from source, not docs.** Antares `src/**` yields 866 keys
   against the 408 of the ModENC-derived list; Phobos `src/**` yields 1,694
   against 261 from its docs — which omit `AttachEffect.AttachTypes` and
   `LaserThickness`, both demonstrably read by the source. Keys the source
   builds by substitution (`Bounty.%sValue`) are expanded over the veterancy
   prefixes.

Corrections this produced to earlier guesses in this file:
- the lightning family is **`Lightning.*`**, not `LightningStorm.*`
- Ares spells them **`Prerequisite.StolenTechs`** and
  **`Prerequisite.RequiredTheaters`** (plural)
- `SurvivorDivisor` exists in Antares **verbatim**; no transform needed
- per-side load screens map to **`File.LoadScreen`**, and `MPC01`-`MPC16`
  to Ares `Color` entries

`knowledge/npatch_to_modern.tsv` carries tier, target extension, target tag,
how it was derived, and a note for every non-mechanical case.

## Tier 1 — same feature, different encoding

Mechanical once the rule is fixed. The decided cases are in the table above.
Others:

| NPatch | Target | Transform |
|---|---|---|
| `Cluster=` (count over 3x3) | `AirburstSpread` | count to spread; each cell hit by one cluster |
| Occupant damage (kill one/all, `CellSpread`) | Ares percentage + multiplier tags | boolean to parameterised |
| Warhead ripple (fixed radius) | customisable radius | emit the Psychic-Dominator radius to preserve behaviour |
| Prerequisite lists (max 3) | `Prerequisite.Lists` (unlimited) | near-trivial |
| VehicleType conversion via deploy | `Convert.Deploy` | see the wiki-error note below |

## Tier 2 — structural synthesis

- **NPatch `Upgrade` to AttachEffect.** Not a rename: NPatch Upgrade is a
  *separate superweapon type*. **Target Phobos**, whose model is a named-type
  registry referenced by name — close to 1:1 with NPatch's named Upgrades:

  ```ini
  AttachEffect.AttachTypes=        ; named effect types to attach
  AttachEffect.DurationOverrides=  ; .Delays= .InitialDelays= .RecreationDelays=
  AttachEffect.RequiredTypes=      ; .DisallowedTypes= .RequiredGroups= .DisallowedGroups=
  AttachEffect.RequiredMinCounts=  ; .RequiredMaxCounts= .DisallowedMinCounts= ...
  ```

  Ares' model instead forces synthesising a warhead **and** a GenericWarhead SW
  with `Duration=-1` per upgrade. Choosing Phobos demotes the hardest
  conversion in the project to a section rename plus key remap.
- **Superweapon scaffolding** — NPatch requires setup steps Ares does not; the
  converter removes rather than renames.
- **Custom armor / immunity** — Ares replaces *most NPatch immunity logic* with
  `Verses=` defaulting, so immunity flags collapse into `Verses` table rewrites.
- **Sides** — NPatch hardcodes graphic filenames, 1-3 extra sides; Ares has 16
  and no hardcoded names. Needs asset renames plus `File.*` / `Sidebar.*` tags.

## What Phobos absorbs (confirmed against a local checkout)

Checked in `/home/rex/Claude/PhobosCompatExt/Phobos` — `docs/*.md` plus source.

| NPatch feature | Phobos target | Notes |
|---|---|---|
| Custom radiation types | `[RadiationTypes]` + per-type sections; `RadType=` on weapons, `RadApplicationDelay.Building=`, `RadSiteWarhead.Detonate=`, `RadHasOwner=`, `RadHasInvoker=` | Ares has **no** Radiation tags at all |
| `AlternateFLHX=` (5 alt firing offsets) | `(Elite)(Prone/Deployed)<WeaponKey>FLH.BurstN` in **artmd.ini**, on the TechnoType *Image* | zero-based `N`, unlimited, absolute offsets (no lateral burst shift) |
| `ROF=` as burst delay | `Burst.Delays=` (weapon), `Burst.NoDelay=` | explicitly supersedes `BurstDelayX`; works on Infantry/Buildings; unlimited shots vs NPatch's first four |
| Screen shake only when visible | `ShakeIsLocal=` (WarheadType, rulesmd) | near-exact match |
| `LaserThickness` above the cap | `LaserThickness` on WeaponType | **implemented but undocumented** — `src/Ext/WeaponType/Body.cpp:178` reads it; zero mentions in `docs/`. ModENC (Phobos Build #20) is the only citation. Undocumented surface: could shift |
| Globals surviving save/load | unlimited `VariableNames` (global in rulesmd, local in map); Phobos "fully supports saving and loading" with its own filtering | **PARTIAL** — nowhere claims NPatch's specific bug is fixed. Needs a functional test, not a doc citation |

## Tier 3 — genuinely unportable

Report clearly; do not fake and do not approximate.

- Non-VTOL aircraft
- Veterancy-preserving unit morph
- SpySat superweapon
- Bespoke main menu
- **TechLevel slider** — superseded by the CNCNet lobby; CNCNet support is next
- **Chrono Factory / DropPod Factory / Paradrop Factory** — a significant gap,
  acknowledged. No *compatible* workaround exists, so none is offered. Planned
  as its own work item later. (Ares does have a `UnitDelivery` superweapon and
  Phobos adds per-InfantryType DropPod customisation plus `AIParadropMission`,
  but these do not reconstruct a factory that delivers newly built units —
  do not present them as a substitute.)

Plus one NPatch bugfix with no downstream equivalent: the burst counter not
resetting when a stop command is issued mid-burst.

## Traps

**1. The Ares wiki comparison page is stale. Never use it for a verdict.**
It lists "VehicleType conversion via deploy function" as an NPatch Extended
feature *missing from Ares*. Wrong, confirmed twice independently:
`knowledge/09_ares_flags.md` contains `Convert.Deploy/.Land/.Script/.Water`,
and Antares' changelog records a **bug fix to `Convert.Deploy` as an Ares
feature**.

> Rule: every "unportable" verdict is validated against the flag list and the
> source, never against the comparison prose. That document is 15+ years of
> drift; the flag list is mechanical.

**2. Name similarity is actively misleading.**
Phobos has `AlternateFLH.OnTurret` / `AlternateFLH.ApplyVehicle` — these are
**not** NPatch's `AlternateFLHX`. Phobos' are about `OpenTopped` transport
firing coordinates and mind-control link offsets, and live in artmd.ini. The
correct target is `<WeaponKey>FLH.BurstN`. Match on semantics, never on string
resemblance — this one would have produced silently wrong output.

**3. Two AttachEffect implementations, overlapping tag names.**
Ares has bare `AttachEffect` and `AttachEffect.Duration`; Phobos has
`AttachEffect.AttachTypes` and `AttachEffect.DurationOverrides`. Both parse the
same `AttachEffect.*` prefix off the same warheads. Phobos' "won't introduce
incompatibilities" claim is least likely to hold exactly here. **Needs an
in-game test before the converter commits to a target** — it cannot be settled
by reading source.

## Open questions

1. Extract an MDK, then build the Tier 0 rename table. Everything else is
   sized; this is the only unsized piece.
2. In-game test: Ares/Antares + Phobos AttachEffect coexistence (trap 3).
3. Verify Phobos works with Antares specifically, not just Ares.
4. Functional test: do globals survive save/load on the Phobos stack?
5. Resolve "NPatch Extended" against the shipped 1.00 / SE / AE editions.

## Sources

- [Ares wiki — Comparison with other YR Hacks and Patches](https://github.com/Ares-Developers/Ares/wiki/Comparison-with-other-YR-Hacks-and-Patches) — useful inventory, unreliable verdicts (trap 1)
- [ModENC — RockPatch](https://modenc.renegadeprojects.com/RockPatch) (lineage), [AttachEffect](https://modenc.renegadeprojects.com/AttachEffect) ("spiritual successor to NPatch's Upgrade logic")
- [Ares 3.0 docs](https://ares-developers.github.io/Ares-docs/) — also the Antares reference
- [Phobos docs](https://phobos.readthedocs.io/en/latest/); local checkout `/home/rex/Claude/PhobosCompatExt/Phobos`
- [NPatch downloads](http://vk.cncguild.net/NPATCH/Downloads.htm) (HTTPS cert is broken; plain HTTP works)
- Local: `knowledge/09_ares_flags.md` (411 Ares-only flags), `/home/rex/Claude/Antares-src/{README,CHANGELOG}.md`
