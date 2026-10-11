# AITriggerTypeExt — Hook Encyclopedia

Living reference for every hook this project touches: what it does, how we
confirmed it, what else in the YR modding ecosystem uses the same address
or function, and what's still open.

## Quick index

| Hook | Address | Size | Feature | Status |
|------|---------|------|---------|--------|
| CTOR | 0x41E350 | 5 | ExtData allocation | ✅ Active |
| ConditionMet gate | 0x41EAC0 | 5 | Trigger evaluation | ✅ Active |
| LoadFromINI per-item | 0x41F39F | 0xA | INI tag reading | ✅ Active (Body.cpp) |
| IPersistStream::Load prefix | 0x41E540 | 5 | Save/load | ✅ Active |
| IPersistStream::Load suffix | 0x41E5B2 | 6 | Save/load | ✅ Active |
| IPersistStream::Save prefix | 0x41E5C0 | 8 | Save/load | ✅ Active |
| IPersistStream::Save suffix | 0x41E5D4 | 6 | Save/load | ✅ Active |
| AITriggerTypeClass DTOR | 0x???????? | ? | ExtMap cleanup | ⏳ Deferred |
| HouseClass::FindEligibleAITeams | 0x6F0AB0 | — | Debug messages (WhoIsBest/Reject) | 🔍 Identified, not yet hooked |
| TeamTypeClass::CreateTeam | 0x6F09C0 | — | Debug messages (Start) | ✅ Hook point confirmed |
| Start event hook | 0x4F8AB2 | 9 | Debug messages (Start) | 🧪 Written, untested in-game |
| AITriggerTypeClass::RegisterSuccess | 0x41FD60 | 13 | Debug messages (Deleted) | 🧪 Written, untested in-game |
| AITriggerTypeClass::RegisterFailure | 0x41FE20 | 8 | Debug messages (Destroyed) | 🧪 Written, untested in-game |
| TeamClass DTOR | 0x6E8DE0 | 13 | Debug messages (TeamType-scoped, future) | 🔍 Identified, not yet hooked |

Status legend: ✅ active & confirmed in-game · 🧪 code written, not yet tested · 🔍 address/logic identified, no hook written yet · ⏳ deferred/low priority

---

## Team Lifecycle (Debug Messages feature)

### `HouseClass::FindEligibleAITeams` (WhoIsBest) — 0x6F0AB0
**What it does:** Called once per house per AI tick. Rolls a random chance
against the house's team ratio, checks/evicts over-cap teams, then loops
`AITriggerTypeClass::Array` calling `ConditionMet` on each trigger, builds
a weight-based distribution from the ones that pass, and does a weighted
random draw to pick a single winning trigger. Returns that trigger's
`Team1`/`Team2` (`AITriggerTypeClass+0xDC`/`+0xE0`) as candidates to spawn.
**Confirmed via:** Ghidra decompile, 2026-07-16. Matches DCoder's
`dc.strategy-x.com/src2/HouseClass/FindEligibleAITeams.cpp` reconstruction
almost line-for-line (field names differ, logic identical).
**Used by:** Vanilla AI only — not touched by Ares or Phobos as far as we've
found.
**Feature:** Home of the **Reject** event (Priority 5) — every trigger that
passes `ConditionMet` but loses the weighted draw is an implicit reject.
No dedicated hook written yet; would need to hook inside the distribution
loop and diff against the eventual winner.
**Tried:** Nothing yet — logic-only, no hook written.

### `TeamTypeClass::CreateTeam` — 0x6F09C0
**What it does:** Actual team instantiation. `ECX`=`TeamTypeClass*`,
stack arg=`HouseClass*`. Returns new `TeamClass*`.
**Confirmed via:** YRpp header (`TeamTypeClass.h`), community-maintained,
not something we had to reverse ourselves.
**Used by:** Called from the AI's `FindEligibleAITeams`→create loop
(0x4F8AA5-0x4F8AB9) AND separately from the TAction script-action dispatcher
(`FUN_006dd8b0`, scripted "Create Team" trigger action) — two independent
callers, so any hook here fires for both AI-spawned and scripted teams.
**Feature:** Backing function for the **Start** event — we hook its return,
not the call itself (see below).
**Tried:** Not hooked directly; hooked the return site instead for a live
`TeamClass*` to work with.

### Start event hook — 0x4F8AB2 (post-`CreateTeam` return)
**What it does:** Fires immediately after `CreateTeam` returns, `EAX` = the
new `TeamClass*`. Steals 9 bytes (`MOV EAX,[ESP+0x34]` / `INC EDI` /
`CMP EDI,EAX` / `JL 0x4F8AA5`).
**Confirmed via:** Ghidra instruction-boundary dump, 2026-07-16 — all 4
instructions land on clean boundaries, safe to steal as a group.
**Used by:** Us only.
**Feature:** **Start** event. `pTeam->Type` (+0x24) lets us match against
`AITriggerTypeClass::Team1`/`Team2` to find which trigger(s) spawned it.
**Tried:** Hook skeleton written (`return 0;` convention, matches our
existing `Hooks.cpp` style). Not yet compiled/tested in-game.

### `AITriggerTypeClass::RegisterSuccess` — 0x41FD60 / `RegisterFailure` — 0x41FE20
**What it does:** Non-virtual member functions on `AITriggerTypeClass`
itself (`ECX`=this). Called from inside `TeamClass`'s destructor
(`0x6E8DE0`) based on a per-team success/failure flag at `TeamClass+0x84`.
`RegisterSuccess` → team finished its script successfully.
`RegisterFailure` → team was destroyed before completing.
**Confirmed via:** Ghidra decompile of `TeamClass::~TeamClass` (0x6E8DE0),
2026-07-16 — direct call sites found, not inferred.
**Used by:** Vanilla only, as far as checked.
**Feature:** **Deleted** (`RegisterSuccess`) and **Destroyed**
(`RegisterFailure`) events, AITriggerType-scoped. Because `ECX` is already
the exact trigger to notify, no array-walking needed for this scope level.
**Tried:** Hook skeletons written, stolen-byte counts confirmed (13 / 8
bytes respectively, clean boundaries). Not yet compiled/tested in-game.

### `TeamClass` destructor entry — 0x6E8DE0
**What it does:** `ECX`=dying `TeamClass*`. Opens by resetting 4 vtable
pointers (MSVC multi-inheritance dtor pattern matching
`AbstractClass : IPersistStream, IRTTITypeInfo, INoticeSink, INoticeSource`).
Internally walks `AITriggerTypeClass::Array` for a `Team1`/`Team2` match,
then calls `RegisterSuccess`/`RegisterFailure` as above.
**Confirmed via:** Ghidra decompile, 2026-07-16. Found by tracing xrefs
*into* `RegisterSuccess`/`RegisterFailure` — this function is called via a
vtable slot (`+0x20`), not a direct `CALL`, which is why it didn't show up
in earlier direct-callsite searches.
**Used by:** Vanilla only, as far as checked. Also the eviction path in
`FindEligibleAITeams` calls this same vtable slot when forcibly retiring
an over-cap team.
**Feature:** Not needed for the AITriggerType-scoped Deleted/Destroyed
messages (RegisterSuccess/Failure cover that). Reserved for the future
**TeamType-scoped** "any team of this type died" sidecar feature.
**Tried:** Nothing — identified, not hooked. Deliberately deferred until
the TeamType sidecar work starts.

---

## Related upstream work (no code overlap, logged for reference)

**Phobos PR #2119** — "Additional AITriggerType conditions (# of Tech
Buildings / Bridge Repair Huts)". Adds new `ConditionType` values (8, 9)
to `ConditionMet`'s dispatch. Does not touch team creation/destruction —
purely extends what conditions a trigger can check. Logged here because
it lives in the same function (`ConditionMet`, 0x41E720) that our
`0x41EAC0` gate hook sits downstream of; worth a byte-diff check if this
PR merges into whatever Phobos version we build against, since new
condition branches could shift code inside that function.

---

## Remaining: AITriggerTypeClass DTOR (low priority, deferred)

AITriggerTypeClass objects are only destroyed on scenario unload, not
mid-game. Stale ExtMap entries don't affect gate logic correctness. This
hook is only needed if you observe memory growth across many scenario loads
in a single session.

When you want to find it: the destructor is not in the IPersistStream
vtable (slot 0 of 0x007E2A50 is QueryInterface). It will be called from
the DynamicVectorClass cleanup path when the Array is cleared on unload.
