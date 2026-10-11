"""
script_validator.py — validate AI script action ordering against vanilla YR.

The wave generator emits ScriptType action sequences. Some orderings the engine
tolerates but the AI executes nonsensically — the classic being a transport that
UNLOADS while "gathered at the enemy base" (action 53), which drops units at the
AISafeDistance assembly point OUTSIDE the base instead of at the target. Vanilla
never does this: it moves to the enemy *building* (47) and unloads there (8).

This module encodes the order-of-operations rules DERIVED FROM VANILLA aimd.ini
(88 scripts mined) as:
  * HARD RULES  — high-confidence semantic constraints on load/unload/deploy/
                  gather ordering. Violations are ERRORS (the real bugs).
  * SOFT GRAMMAR — the set of action->action transitions vanilla actually uses.
                  A transition never seen in vanilla is a WARNING (review it).

`validate()` reports; `repair()` fixes the known-safe cases (gather-before-unload
-> move-to-building-before-unload). Extended/Phobos actions (>=1000) are opaque to
the vanilla grammar and skipped by the soft layer.
"""
from __future__ import annotations
from dataclasses import dataclass

# ── action ids we reason about (vanilla) ─────────────────────────────────────
ATTACK          = 0
GUARD_TIMED     = 5
JUMP            = 6
ATTACK_UNLOAD   = 8    # unload AT the target (attack-unload)
UNLOAD_DEPLOY   = 9    # unload / deploy units
SET_MISSION     = 11
LOAD            = 14   # load passengers into transports
SCATTER         = 21
WAIT_LOADED     = 43   # wait until transports fully loaded
ATTACK_BUILDING = 46
MOVE_TO_BUILDING= 47   # move ADJACENT to an enemy structure (this is where you unload)
REPEAT          = 49
GATHER_ENEMY    = 53   # GATHER at AISafeDistance OUTSIDE the enemy base (assembly)
REGROUP         = 54   # regroup/assemble at the friendly base
IRON_CURTAIN    = 55
MOVE_FRIENDLY   = 58   # move adjacent to a friendly structure
DEPLOY          = 63   # deploy / undeploy (used at the friendly base)

NAMES = {
    0:"Attack", 5:"GuardArea", 6:"Jump", 8:"AttackUnload", 9:"Unload/Deploy",
    11:"SetMission", 14:"LoadTransport", 21:"Scatter", 43:"WaitLoaded",
    46:"AttackBuilding", 47:"MoveToEnemyBldg", 49:"Repeat", 53:"GatherAtEnemy",
    54:"RegroupAtBase", 55:"IronCurtain", 57:"Chronowarp", 58:"MoveToFriendlyBldg",
    61:"?61", 62:"?62", 63:"Deploy",
}
def aname(a: int) -> str:
    if a >= 1000:
        return f"Ext({a})"
    return NAMES.get(a, f"Act{a}")

# ── vanilla transition whitelist (mined from vanilla aimd.ini, 88 scripts) ───
VANILLA_BIGRAMS = frozenset({
    (0,0),(0,6),(0,47),(0,49),(5,6),(5,49),(5,55),(5,58),(8,46),(8,49),(9,0),
    (9,46),(9,49),(11,49),(14,43),(21,11),(43,0),(43,47),(43,58),(46,0),(46,46),
    (46,47),(46,49),(47,8),(47,9),(47,46),(49,0),(49,11),(49,46),(49,58),(53,0),
    (53,5),(53,46),(53,47),(53,55),(54,0),(54,9),(54,14),(54,53),(54,57),(54,58),
    (54,61),(54,63),(55,0),(55,49),(57,5),(58,5),(58,11),(58,14),(58,21),(58,49),
    (61,0),(62,49),(63,49),(63,53),
})

# unload/deploy-at-destination actions and the moves that legitimately precede them
UNLOAD_LIKE   = {ATTACK_UNLOAD, UNLOAD_DEPLOY}
# actions that mean "we are at an assembly point, NOT at the target"
ASSEMBLY      = {GATHER_ENEMY, REGROUP}
# actions that put a unit adjacent to the actual target structure
AT_TARGET_MOVE= {MOVE_TO_BUILDING}
# load actions (vanilla 14 + Phobos 10103 "Load Onto Transports")
LOAD_LIKE     = {LOAD, 10103}
# pure control-flow destinations — structural, never an ordering-of-logistics bug
CONTROL_FLOW  = {JUMP, REPEAT}
# non-vanilla transitions that are nonetheless legitimate tool idioms:
#   (43,53) = fight-while-loaded (IFVs load, then gather+attack without unloading;
#             the actual bug — unload after gather — is caught by the hard rules)
ACCEPTED_EXTENSIONS = {(WAIT_LOADED, GATHER_ENEMY)}


@dataclass
class Finding:
    severity: str        # "ERROR" | "WARN"
    index: int           # action position
    code: str
    message: str


def validate(actions: list[tuple[int, int]]) -> list[Finding]:
    """Return ordering findings for a script's [(action, arg), ...] sequence."""
    seq = [a for a, _ in actions]
    out: list[Finding] = []
    n = len(seq)

    for i, a in enumerate(seq):
        prev = seq[i-1] if i > 0 else None

        # ── HARD RULE 1: unload happens AT the target (after MoveToEnemyBldg) ──
        if a in UNLOAD_LIKE:
            earlier = seq[:i]
            if MOVE_TO_BUILDING not in earlier:
                out.append(Finding("ERROR", i, "unload-without-approach",
                    f"{aname(a)} with no prior MoveToEnemyBldg(47): units unload "
                    f"wherever the team currently is (an assembly point), not at "
                    f"the target."))
            if prev in ASSEMBLY:
                out.append(Finding("ERROR", i, "unload-after-assembly",
                    f"{aname(a)} immediately after {aname(prev)}: this drops units "
                    f"at the {'outside-base gather' if prev==GATHER_ENEMY else 'friendly regroup'} "
                    f"point. Move to the enemy building (47) first, unload there."))

        # ── HARD RULE 2: WaitLoaded must directly follow a load (14 or Phobos 10103) ──
        if a == WAIT_LOADED and prev not in LOAD_LIKE:
            out.append(Finding("ERROR", i, "wait-without-load",
                f"WaitLoaded(43) not immediately after a load action (14/10103) "
                f"(prev={aname(prev) if prev is not None else 'start'})."))

        # ── HARD RULE 3: gather-at-enemy is an assembly point, not a work site ──
        if prev == GATHER_ENEMY and a in (ATTACK_UNLOAD, UNLOAD_DEPLOY, LOAD,
                                          WAIT_LOADED, DEPLOY):
            out.append(Finding("ERROR", i, "work-at-gather",
                f"{aname(a)} right after GatherAtEnemy(53): 53 assembles the team "
                f"OUTSIDE the base — never load/unload/deploy here. Vanilla follows "
                f"53 with attack/approach (0/46/47)."))

        # ── HARD RULE 4: deploy(63) belongs at the friendly base, before gather ─
        if a == DEPLOY and prev == GATHER_ENEMY:
            out.append(Finding("ERROR", i, "deploy-outside-base",
                f"Deploy(63) after GatherAtEnemy(53): vanilla deploys at the "
                f"friendly base (after Regroup 54), then gathers."))

        # ── SOFT: transition never seen in vanilla (both actions vanilla-range) ─
        # Skip pure control-flow destinations (jump/repeat loops) and accepted
        # tool idioms — they are structural, not logistics-ordering, issues.
        if (prev is not None and prev < 1000 and a < 1000
                and a not in CONTROL_FLOW
                and (prev, a) not in VANILLA_BIGRAMS
                and (prev, a) not in ACCEPTED_EXTENSIONS):
            if not any(f.index == i and f.severity == "ERROR" for f in out):
                out.append(Finding("WARN", i, "unseen-transition",
                    f"{aname(prev)}->{aname(a)} never occurs in vanilla scripts "
                    f"— review whether the AI executes it sensibly."))
    return out


def repair(actions: list[tuple[int, int]]) -> tuple[list[tuple[int, int]], list[str]]:
    """Fix known-safe ordering bugs. Currently: an assembly action (53/54) sitting
    immediately before an unload becomes MoveToEnemyBldg(47), so the transport
    unloads AT the target. Returns (new_actions, notes)."""
    seq = [list(p) for p in actions]
    notes: list[str] = []
    for i in range(1, len(seq)):
        a = seq[i][0]
        prev = seq[i-1][0]
        if a in UNLOAD_LIKE and prev in ASSEMBLY:
            notes.append(f"line {i-1}: {aname(prev)}({seq[i-1][1]}) -> "
                         f"MoveToEnemyBldg(47) so {aname(a)} unloads at the target")
            seq[i-1] = [MOVE_TO_BUILDING, 0]
    return [tuple(p) for p in seq], notes


# ── standalone: scan the generator's own template library ────────────────────
def _scan_library() -> int:
    import script_generator as sg
    total_err = 0
    for phobos in (False, True):
        lib = sg._build_library(phobos)
        header = f"\n===== template library (phobos={phobos}) : {len(lib)} templates ====="
        printed_header = False
        for t in lib:
            findings = validate(list(t.actions))
            if not findings:
                continue
            if not printed_header:
                print(header); printed_header = True
            print(f"\n  [{t.name}]  {['%d,%d'%(a,g) for a,g in t.actions]}")
            for f in findings:
                total_err += (f.severity == "ERROR")
                print(f"    {f.severity:5s} @line {f.index} ({f.code}): {f.message}")
                if f.severity == "ERROR":
                    fixed, notes = repair(list(t.actions))
                    for note in notes:
                        print(f"          FIX: {note}")
    print(f"\n===== total ERRORS: {total_err} =====")
    return total_err


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    raise SystemExit(1 if _scan_library() else 0)
