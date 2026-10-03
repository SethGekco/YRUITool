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

## Ground-truth flag lists (use these to answer "does this need Ares?")
Three canonical indexes, pulled from ModENC's support icons:
- `07_yr_supported_flags.md` — 1,812 flags supported in **vanilla Yuri's Revenge**.
  In this list → safe on any YR install.
- `08_ra2_supported_flags.md` — 1,526 flags supported in **vanilla Red Alert 2**
  (pre-YR). Most are also in the YR list.
- `09_ares_flags.md` — 411 flags that are **Ares-only** (work with Ares/Antares but
  NOT in vanilla RA2/YR). In this list → tell the user it needs Ares/Antares.

Decision rule: YR list → vanilla-safe; Ares-only list → needs the extension;
in neither → say you're unsure and point to the flag's ModENC page.

The `ares_manual.txt` reference explains *how* Ares flags behave;
`deezire_corrections.txt` lists known mistakes in the older INI Bible
(`ini_bible_*.txt`) — trust the corrections and ModENC over the Bible on exact facts.
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
