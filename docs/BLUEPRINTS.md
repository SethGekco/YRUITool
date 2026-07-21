# Blueprints

A **blueprint** is a plain-text snapshot of the entire tool state — every tab's
settings plus the custom script. Save one to keep or share a balance; anyone can
load it and hit Apply to reproduce your changes, or edit it by hand in Notepad.

Save/load from the **Blueprint** menu. The file extension is `.yrbp` (it's just
text — `.txt` works too).

## Format

INI-flavored and hand-editable:

```ini
# YRBalanceTool blueprint
#name: My Rebalance
#created: 2026-07-20

[Infantry]
Strength = 2.0
Cost = 0.5
CostMode = flat        ; flat | techlevel | depth
CostBase = vanilla     ; vanilla | current
TL.Recompute = no      ; recompute TechLevel from build order
TL.Write = no
TL.Use = no
Sight.Equal = no
... (every field is listed so you can see all the knobs)

[Naval]
Cost = link            ; 'link' = inherit the Vehicle value
Speed = 1.2            ; a number overrides the link for that field
AdvGroup = link        ; link/own for the cost-scaling + TechLevel group

[Form.General]
Section = General
BuildSpeed = 0.9       ; an active tag
#GrowthRate =          ; a '#' row is inactive — uncomment to enable

[Global]
RangeProtect = yes     ; protect short-range weapons from Range scaling
RangeThreshold = 3.0   ; skip Range scaling when vanilla Range is below this

[Script]
[$INFANTRY]
Elite = yes
```

Notes:
- **Naval**: a field set to `link` inherits the Vehicle tab's value; give it a
  number to override just that field.
- **Form tabs** (General/Gameplay/Super Weapons): rows prefixed with `#` are
  inactive. Remove the `#` to apply them.
- **`[Script]` is terminal** — everything after its header is raw modifier-script
  text (see `SCRIPTS.md`), so it can contain its own `[scope]` headers.

## Undo (restore to vanilla)

Every value the tool writes carries its original in a `; vanilla=` comment, so
**File → Restore a file to vanilla** rolls an edited rules file's balance numbers
back to vanilla. (Whole sections/tags added by generator scripts are not removed —
that's a separate, heavier undo.)
