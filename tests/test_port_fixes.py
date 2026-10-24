#!/usr/bin/env python3
"""
Porter regressions found porting R.I.P World Powers into a stock game install.

  1. A picked unit arrived UNARMED. Its weapon chain was pulled only for
     auto-added part units, so the unit's own Primary=/ElitePrimary= named
     weapons that existed only in the source — and the weapons' culture-
     converted Damage/ROF/Range, shown in the preview, never reached the file.
  2. Integer stats were written as fractions: Strength 1250 x 0.25 -> 312.5.
  3. Sound was never attempted: the porter only looked for a LOOSE soundmd.ini,
     and a stock install keeps it inside ra2md.mix.
  4. The engine never reads an INI's last line, so every written INI must end
     with a comment. Appending after an existing ';' made a ported section's
     final key the file's last line, where it was silently lost.
  5. A weapon the target ALSO defines — under the same name, differently —
     was silently dropped, so the ported unit fired the target's version.
     It is now imported under a minted name and the unit repointed at it.

    python3 tests/test_port_fixes.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VANILLA = """\
[InfantryTypes]
0=E1
[E1]
Cost=200
Strength=125
Speed=4
Primary=M60
[M60]
Damage=15
ROF=20
Range=5
Warhead=SA
Projectile=InvisibleLow
[SA]
Verses=100%,100%,100%,100%,100%,100%,100%,100%,100%,100%,100%
[InvisibleLow]
AG=yes
"""

TARGET = VANILLA + ";\n"         # a target that already ends in a guard

SOURCE = VANILLA.replace("0=E1", "0=E1\n1=BEETLE") + """\
[BEETLE]
Cost=1500
Strength=1250
Speed=6
Primary=BITE
ElitePrimary=BITEE
[BITE]
Damage=120
ROF=30
Range=1.5
Warhead=BITEWH
Projectile=InvisibleLow
[BITEE]
Damage=120
ROF=20
Range=1.5
Warhead=BITEWH
Projectile=SHARDS
[BITEWH]
Verses=100%,100%,100%,100%,100%,100%,100%,100%,100%,100%,100%
Bullets=yes
[SHARDS]
AG=yes
ShrapnelWeapon=SPLINTER
[SPLINTER]
Damage=5
ROF=10
Range=1
Warhead=BITEWH
Projectile=InvisibleLow
"""


def _w(d, name, text):
    p = os.path.join(d, name)
    with open(p, "w") as fh:
        fh.write(text)
    return p


def main():
    ok = True
    from yrbalance.engine import (
        parse_ini_lines, build_section_map, build_port_plan, apply_port_plan,
        load_ini_text, write_mix, get_val,
    )
    from yrbalance.engine.ini_model import (
        ensure_eof_guard, guard_ini_text, pop_eof_guard, EOF_GUARD,
    )
    from yrbalance.engine.sections import append_raw_section
    from yrbalance.engine.portmod import _fmt_stat, _expand_selection

    with tempfile.TemporaryDirectory() as d:
        sp = _w(d, "src.ini", SOURCE)
        tp = _w(d, "tgt.ini", TARGET)
        vp = _w(d, "van.ini", VANILLA)
        sm = build_section_map(parse_ini_lines(sp))[0]
        tl = parse_ini_lines(tp)
        tm = build_section_map(tl)[0]
        vm = build_section_map(parse_ini_lines(vp))[0]
        plan = build_port_plan(sm, tm, vm)

        # ── 1. a picked unit brings its whole weapon chain ──────────────────
        sel = _expand_selection(plan, {"BEETLE"})
        chain_ok = ({"BEETLE", "BITE", "BITEE", "BITEWH", "SHARDS",
                     "SPLINTER"} <= sel
                    and "InvisibleLow" not in sel)   # target already has it
        print(f"picked unit pulls Primary + ElitePrimary + warhead + "
              f"projectile + shrapnel: {'OK' if chain_ok else 'FAIL'} "
              f"{sorted(sel)}")
        ok = ok and chain_ok

        n0 = len(tl)
        apply_port_plan(tl, tm, plan, selected={"BEETLE"})
        added = "".join(tl[n0:])
        written_ok = all(f"[{s}]" in added for s in
                         ("BEETLE", "BITE", "BITEE", "BITEWH", "SHARDS",
                          "SPLINTER"))
        print(f"...and all of it is written: {'OK' if written_ok else 'FAIL'}")
        ok = ok and written_ok

        # ── 2. integer stats stay integers ──────────────────────────────────
        fmt_ok = (_fmt_stat("Strength", 312.5) == "312"
                  and _fmt_stat("Cost", 374.6) == "375"
                  and _fmt_stat("ROF", 20.0) == "20"
                  and _fmt_stat("Range", 2.4) == "2.4"
                  and _fmt_stat("Range", 5.0) == "5")
        st = get_val(tm, "BEETLE", "Strength") or ""
        int_ok = fmt_ok and "." not in st
        print(f"integer stats format as integers, Range kept fractional "
              f"(fixture factor is 1.0, so this checks formatting only): "
              f"{'OK' if int_ok else 'FAIL'} Strength={st}")
        ok = ok and int_ok

        # ── 4. the EOF guard ────────────────────────────────────────────────
        last_ok = tl[-1].strip() == EOF_GUARD
        print(f"porting into a guarded file keeps ';' as the last line: "
              f"{'OK' if last_ok else 'FAIL'} last={tl[-1].rstrip()!r}")
        ok = ok and last_ok

    g = ensure_eof_guard(["[A]\n", "X=1\n"])
    g2 = ensure_eof_guard(["[A]\n", "X=1"])          # no trailing newline
    g3 = ensure_eof_guard(["[A]\n", "X=1\n", ";\n"])  # already guarded
    g4 = ensure_eof_guard(["[A]\r\n", "X=1\r\n"])
    g5 = ensure_eof_guard(["[A]\n", "; a real comment\n"])
    guard_ok = (g[-1] == ";\n" and g2 == ["[A]\n", "X=1\n", ";\n"]
                and g3.count(";\n") == 1                 # idempotent
                and g4[-1] == ";\r\n"                    # keeps CRLF
                and g5[-1] == "; a real comment\n"       # any comment counts
                and ensure_eof_guard([]) == [";\n"])
    print(f"ensure_eof_guard: appends, idempotent, keeps CRLF: "
          f"{'OK' if guard_ok else 'FAIL'}")
    ok = ok and guard_ok

    src = ["[A]\n", "X=1\n"]
    txt = guard_ini_text(src)
    text_ok = (txt == "[A]\nX=1\n;\n" and src == ["[A]\n", "X=1\n"]
               and guard_ini_text("[A]\nX=1\n") == "[A]\nX=1\n;\n")
    print(f"guard_ini_text works on lists and strings, never mutates: "
          f"{'OK' if text_ok else 'FAIL'}")
    ok = ok and text_ok

    # Appending goes in FRONT of a trailing bare guard; a real trailing
    # comment is content and stays where it is.
    lines = ["[A]\n", "X=1\n", "\n", ";\n"]
    smap = build_section_map(lines)[0]
    append_raw_section(lines, smap, "B", ["[B]", "Y=2"], "\n")
    tail = [ln.strip() for ln in lines]
    pop_ok = (tail[-1] == ";" and "Y=2" in tail and tail.index("Y=2")
              < len(tail) - 1
              and smap["b"]["keys"]["y"][0] == tail.index("Y=2"))
    keep = ["[A]\n", "X=1\n", "; author note\n"]
    pop_eof_guard(keep)
    keep_ok = keep[-1] == "; author note\n"
    print(f"append lands before the guard, section map stays correct, real "
          f"comments untouched: {'OK' if pop_ok and keep_ok else 'FAIL'}")
    ok = ok and pop_ok and keep_ok

    # ── 5. same name, different definition -> imported under a new name ───
    long_wh = "W" * 23                                  # 23 + "_P" > 24
    col_tgt = VANILLA + """\
[ROCK]
Damage=50
ROF=40
Range=6
Warhead=SA
Projectile=InvisibleLow
[%s]
Verses=50%%,50%%,50%%,50%%,50%%,50%%,50%%,50%%,50%%,50%%,50%%
;
""" % long_wh
    col_src = VANILLA.replace("0=E1", "0=E1\n1=TANK") + """\
[TANK]
Cost=900
Strength=400
Primary=ROCK
Secondary=M60
[ROCK]
Damage=90
ROF=40
Range=6
Warhead=%s
Projectile=InvisibleLow
[%s]
Verses=100%%,100%%,100%%,100%%,100%%,100%%,100%%,100%%,100%%,100%%,100%%
""" % (long_wh, long_wh)
    from yrbalance.engine import (
        conflicts_in_selection, conflict_versions, CONFLICT_KEEP_TARGET,
        CONFLICT_SOURCE, CONFLICT_REBALANCED, CONFLICT_BOTH,
    )

    def port(resolutions):
        d = tempfile.mkdtemp(prefix="yrbt_col_")
        sm = build_section_map(parse_ini_lines(_w(d, "s.ini", col_src)))[0]
        tl = parse_ini_lines(_w(d, "t.ini", col_tgt))
        tm = build_section_map(tl)[0]
        vm = build_section_map(parse_ini_lines(_w(d, "v.ini", VANILLA)))[0]
        plan = build_port_plan(sm, tm, vm)
        summ = apply_port_plan(tl, tm, plan, selected={"TANK"},
                               resolutions=resolutions)
        # re-parse what would be written: the cached map must match the file
        tm2 = build_section_map(guard_ini_text(tl).splitlines(True))[0]
        return plan, summ, tl, tm, tm2

    def dmg(m, s):
        return get_val(m, s, "Damage")

    # default ("both"): minted copies, unit repointed, loaded mod untouched
    plan, summ, tl, tm, tm2 = port(None)
    rn = plan.renames
    wh_new = rn.get(long_wh, "")
    found = {c.name for c in conflicts_in_selection(plan, {"TANK"})}
    rock = next(c for c in plan.conflicts if c.name == "ROCK")
    tgt_v, raw_v, bal_v = conflict_versions(plan, rock)
    both_ok = (rn.get("ROCK") == "ROCK_P" and "M60" not in rn
               and 0 < len(wh_new) <= 24
               and found == {"ROCK", long_wh}
               and rock.kind == "weapon"
               and "Damage=50" in tgt_v and "Damage=90" in raw_v
               and "Damage=90" not in bal_v      # culture factor applied
               and get_val(tm2, "TANK", "Primary") == "ROCK_P"
               and get_val(tm2, "TANK", "Secondary") == "M60"
               and get_val(tm2, "ROCK_P", "Warhead") == wh_new
               and dmg(tm2, "ROCK_P") not in ("50", "90", None)
               and dmg(tm2, "ROCK") == "50"
               and any("different definitions" in w
                       for w in plan.global_warnings))
    print(f"conflict 'keep both' (default): minted names, unit repointed, "
          f"loaded mod's left alone, identical reused, versions shown: "
          f"{'OK' if both_ok else 'FAIL'} renames={rn}")
    ok = ok and both_ok

    # keep the loaded mod's: nothing of ROCK (nor its warhead) is imported
    plan, summ, tl, tm, tm2 = port({"ROCK": CONFLICT_KEEP_TARGET})
    keep_ok = (get_val(tm2, "TANK", "Primary") == "ROCK"
               and "rock_p" not in tm2 and wh_new.lower() not in tm2
               and dmg(tm2, "ROCK") == "50" and not summ["replaced"])
    print(f"conflict 'keep loaded mod's': unit uses it, no orphans imported: "
          f"{'OK' if keep_ok else 'FAIL'}")
    ok = ok and keep_ok

    # replace, as written / rebalanced: the loaded mod's section is rewritten
    # in place (its other users change too), header kept, indices stay true
    plan, summ, tl, tm, tm2 = port({"ROCK": CONFLICT_SOURCE,
                                     long_wh: CONFLICT_SOURCE})
    src_ok = (get_val(tm2, "TANK", "Primary") == "ROCK"
              and dmg(tm2, "ROCK") == "90"
              and get_val(tm2, "ROCK", "Warhead") == long_wh
              and get_val(tm2, long_wh, "Verses", ).startswith("100%")
              and "rock_p" not in tm2
              and sorted(summ["replaced"]) == sorted(["ROCK", long_wh])
              and "".join(tl).count("[ROCK]") == 1
              and tl[-1].strip() == ";"
              and all(tm[k]["start"] == tm2[k]["start"] for k in tm2))
    print(f"conflict 'replace, as written': rewritten in place, cached map "
          f"still matches the file: {'OK' if src_ok else 'FAIL'}")
    ok = ok and src_ok

    plan, summ, tl, tm, tm2 = port({"ROCK": CONFLICT_REBALANCED})
    bal_ok = (dmg(tm2, "ROCK") not in ("50", "90", None)
              and get_val(tm2, "ROCK", "Warhead") == wh_new   # wh: default both
              and get_val(tm2, "TANK", "Primary") == "ROCK"
              and summ["replaced"] == ["ROCK"])
    print(f"conflict 'replace, rebalanced' (warhead left on its default): "
          f"{'OK' if bal_ok else 'FAIL'} Damage={dmg(tm2, 'ROCK')}")
    ok = ok and bal_ok

    # ── 3. soundmd from loose, from a mix, or not at all ────────────────────
    with tempfile.TemporaryDirectory() as d:
        loose = os.path.join(d, "loose")
        packed = os.path.join(d, "packed")
        empty = os.path.join(d, "empty")
        for x in (loose, packed, empty):
            os.makedirs(x)
        _w(loose, "soundmd.ini", "[Snd]\nSounds=a\n")
        write_mix(os.path.join(packed, "expandmd99.mix"),
                  {"soundmd.ini": b"[Snd]\nSounds=b\n"})
        o1, p1, t1 = load_ini_text(loose, ("soundmd.ini", "sound.ini"))
        o2, p2, t2 = load_ini_text(packed, ("soundmd.ini", "sound.ini"))
        o3, p3, t3 = load_ini_text(empty, ("soundmd.ini", "sound.ini"))
        o4, _p4, _t4 = load_ini_text(packed, ("soundmd.ini",),
                                     search_mixes=False)
        snd_ok = (o1 == "loose" and p1.endswith("soundmd.ini")
                  and "Sounds=a" in t1
                  and o2 == "mix" and p2 is None and "Sounds=b" in t2
                  and (o3, p3, t3) == (None, None, None)
                  and o4 is None)
        print(f"load_ini_text: loose, packed in a mix, absent: "
              f"{'OK' if snd_ok else 'FAIL'} origins={o1},{o2},{o3}")
        ok = ok and snd_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
