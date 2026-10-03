# Support levels: vanilla YR vs Ares (how Eva should reason)

Red Alert 2 modding has two engine layers, and a flag only works if the running
engine supports it:

1. **Vanilla Yuri's Revenge** — the base `gamemd.exe`. Flags that ship with the
   game. These always work.
2. **Ares / Antares extension** — an optional DLL (Ares, or its open-source
   reimplementation Antares) that adds many new flags and logics. Ares-only flags
   do **nothing** (or are ignored) unless the mod runs Ares/Antares.

## ModENC's support icons
On [modenc.renegadeprojects.com](https://modenc.renegadeprojects.com) each flag's
page shows small game icons (Tiberian Dawn, Red Alert, Tiberian Sun, Firestorm,
Red Alert 2, Yuri's Revenge) plus extension icons. Reading them:
- A **coloured / filled Yuri's Revenge icon** = the flag is supported in vanilla YR.
- A greyed-out icon = NOT supported in that game.
- An **Ares icon** = the flag requires the Ares extension (so also Antares).

So before using a flag, check its ModENC page: if the YR icon is lit, it's safe in
base YR; if only the Ares icon is lit, the mod must load Ares/Antares.

## Practical heuristic for Eva
- If a flag appears in the **Ares Manual** reference (the `ares_manual.txt` doc in
  this knowledge base), treat it as an **Ares-only** feature and say so: "this
  needs the Ares/Antares extension."
- The core **vanilla YR** flags are listed in the tag glossary. Prefer those for
  changes that must work on any YR install.
- When unsure whether a flag is vanilla or Ares, say so plainly and suggest the
  user check its ModENC page, rather than guessing.
- Antares is a drop-in open-source Ares reimplementation with feature parity, so
  "needs Ares" and "needs Antares" mean the same thing here.

## Why this matters for balancing
The core balance tags Eva edits most — `Cost`, `Strength`, `Speed`, `Damage`,
`Range`, `ROF`, `TechLevel`, `Sight`, `BuildLimit`, `Soylent` — are all **vanilla
YR** and safe everywhere. Exotic logics (custom warhead effects, new superweapon
types, attached effects, per-unit immunities, etc.) are usually **Ares**; use them
only when the mod runs the extension.
