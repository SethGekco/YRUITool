# Vanilla baseline reference: stock RA2 & YR INI values

If the user has ingested them, this knowledge base contains the **unmodified
official game INI files** (Westwood/EA, Aug 2004), as local-only documents:

- `vanilla_yr_rulesmd.ini` — stock **Yuri's Revenge** rules (every vanilla unit,
  building, weapon, warhead with its real default values).
- `vanilla_ra2_rules.ini` — stock **Red Alert 2** (pre-YR) rules.
- `vanilla_yr_aimd.ini` / `vanilla_ra2_ai.ini` — stock AI scripts/teams/triggers.

(These are copyrighted game files, so they are kept local and not shipped with the
tool. They may be absent; if so, say the user can add them by placing tagged copies
of their stock `rules.ini` / `rulesmd.ini` in the local knowledge folder.)

## What they are FOR
Answering "what is the stock/default value of X?" and "is my change a buff or a
nerf versus vanilla?". Examples Eva can now cite exactly:
- Rhino Tank `[HTNK]`: `Cost=900`, `Strength=400`, `Armor=heavy`, `TechLevel=2`.
- Compare the user's current value against the vanilla baseline to judge magnitude.
- "What did YR change from RA2?" → diff the two rules files for the same section.

## CRITICAL: vanilla ≠ the loaded mod
These files are the **stock baseline**, NOT the rules file the user currently has
open in the tool. The tool already gives Eva a live summary of the *loaded* mod.
So:
- To state a stock default → quote `vanilla_*`.
- To state what the user's mod currently has → use the loaded-rules summary, never
  the vanilla file.
- When citing, name the source (e.g. "vanilla_yr_rulesmd.ini") so it's clear you
  mean the baseline, not their mod.
- A section/value present in the user's mod but absent from vanilla = mod-added
  content; don't claim a vanilla default for it.

## Retrieval note
These are large (~740 KB for YR rules). They are only useful once the vector index
is built (`ollama pull nomic-embed-text` → "Rebuild memory index" in the Eva tab);
without it, retrieval can only skim them. Each retrieved chunk cites its
`vanilla_*` filename, which is the signal that it's baseline data.
