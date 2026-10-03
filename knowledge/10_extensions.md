# Engine extensions: Ares/Antares, Phobos, KratosPP

Vanilla Yuri's Revenge can be extended by community DLLs. A flag/logic only works
if the matching extension is loaded. The three Eva knows about:

## Ares / Antares
The classic extension. Ares-only flags are listed in `09_ares_flags.md` (411 flags
not in vanilla RA2/YR), and `ares_manual.txt` explains how they behave. Antares is
an open-source reimplementation with Ares parity — "needs Ares" = "needs Antares".

## Phobos
A modern, actively developed extension. Its flags/logics are NOT tracked by
ModENC's support icons; they are documented in the official Phobos docs, which are
in this knowledge base as `phobos_*.txt`:
- `phobos_new_logics.txt` — new/enhanced logics (the big one)
- `phobos_fixed_logics.txt` — fixes/improvements to vanilla behavior
- `phobos_ai_mapping.txt` — AI scripting and mapping
- `phobos_ui.txt` — UI features
- `phobos_interop.txt` — interoperability
- `phobos_misc.txt` — miscellaneous
If a flag appears in these docs but not in the vanilla YR/RA2 lists, it needs the
Phobos extension. Phobos and Ares are different extensions — don't assume a Phobos
flag works under Ares or vice versa.

## KratosPP
Another extension; its docs are in this knowledge base as `kratos_*.txt` (where
present). Flags from those docs need KratosPP loaded.

## Rule of thumb for Eva
1. In `07_yr_supported_flags.md` → vanilla YR, safe everywhere.
2. In `08_ra2_supported_flags.md` → vanilla RA2 (usually also YR).
3. In `09_ares_flags.md` → needs Ares/Antares.
4. Documented only in `phobos_*.txt` / `kratos_*.txt` → needs that extension.
5. Otherwise → say you're unsure; point to ModENC or the extension's docs.
