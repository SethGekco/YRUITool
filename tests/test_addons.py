#!/usr/bin/env python3
"""
Library addons: spec validation (the security boundary for model-authored
specs), interpretation against a rules map, persistence with re-validation on
load, and Eva's plan_addon with a stubbed client.

The validation cases are the point: every field in a spec comes from a language
model, so bad enums must clamp, INI-injection names must be refused, and lists
must cap — with reasons, not silent narrowing.

    python3 tests/test_addons.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RULES = """\
[AttachmentTypes]
0=ATTACHGUN
1=ATTACHRADAR
[ATTACHGUN]
AttachTo=HTNK
Offset=0,0,10
LimitPerUnit=2
[ATTACHRADAR]
AttachTo=MTNK
Offset=0,0,5
[InfantryTypes]
0=E1
[VehicleTypes]
0=HTNK
1=MTNK
[E1]
Cost=100
[HTNK]
Cost=900
AttachmentType=ATTACHGUN
[MTNK]
Cost=700
"""


class _Stub:
    """Minimal stand-in for OllamaClient — plan_addon only calls .chat()."""
    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, model, temperature=0.2, num_ctx=None):
        return self.reply


def main():
    ok = True
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home
        from yrbalance.engine import (
            validate_spec, scan_addon, spec_summary, load_addons, add_addon,
            remove_addon, addons_path, parse_ini_lines, build_section_map,
            MAX_COLUMNS,
        )
        from yrbalance.ai.eva import plan_addon

        rmap = build_section_map(parse_ini_lines(_w(home, "r.ini", RULES)))[0]

        # ── the AttachmentTypes case, end to end ────────────────────────────
        good = {"name": "AttachmentTypes", "source": "typelist",
                "list_section": "AttachmentTypes",
                "columns": [{"label": "Attached to", "tag": "AttachTo"},
                            {"label": "Offset", "tag": "Offset"},
                            {"label": "Limit", "tag": "LimitPerUnit"}],
                "group_by": "Attached to",
                "notes": "Every declared attachment and what it rides on."}
        spec, errors = validate_spec(good)
        rows = scan_addon(spec, rmap)
        by = {r.name: r for r in rows}
        base_ok = (not errors and len(rows) == 2
                   and by["ATTACHGUN"].values["Limit"] == "2"
                   and by["ATTACHGUN"].values["Attached to"] == "HTNK"
                   and by["ATTACHRADAR"].values["Limit"] == ""
                   and by["ATTACHGUN"].group == "HTNK"
                   and "AttachmentTypes" in spec_summary(spec))
        print(f"typelist addon reads a custom list + its tags: "
              f"{'OK' if base_ok else 'FAIL'}")
        if not base_ok:
            print(f"   errors={errors} rows={[r.name for r in rows]}")
        ok = ok and base_ok

        # ── require_tags / any_tags filtering ──────────────────────────────
        req, _ = validate_spec(dict(good, require_tags=["LimitPerUnit"],
                                    group_by=""))
        onto, _ = validate_spec({"name": "Carriers", "source": "technos",
                                 "any_tags": ["AttachmentType"],
                                 "columns": [{"label": "Type",
                                              "tag": "AttachmentType"}]})
        filt_ok = ([r.name for r in scan_addon(req, rmap)] == ["ATTACHGUN"]
                   and [r.name for r in scan_addon(onto, rmap)] == ["HTNK"])
        print(f"require_tags narrows; technos+any_tags finds the carrier unit: "
              f"{'OK' if filt_ok else 'FAIL'}")
        ok = ok and filt_ok

        pre, _ = validate_spec({"name": "Radar bits", "source": "sections",
                                "section_prefix": "ATTACHR",
                                "columns": ["AttachTo"]})
        pre_ok = [r.name for r in scan_addon(pre, rmap)] == ["ATTACHRADAR"]
        print(f"sections prefix + string-shorthand column: "
              f"{'OK' if pre_ok else 'FAIL'}")
        ok = ok and pre_ok

        # ── validation: the security boundary ───────────────────────────────
        inj, inj_err = validate_spec({
            "name": "Evil", "source": "typelist",
            "list_section": "Foo]\nBar=1\n[General",
            "columns": [{"label": "x", "tag": "Cost=1\n[Evil"}]})
        injection_ok = (any("not a plain INI name" in e for e in inj_err)
                        and inj.list_section == "" and inj.columns == [])
        print(f"INI-injection section/tag names refused: "
              f"{'OK' if injection_ok else 'FAIL'}")
        ok = ok and injection_ok

        _b, bad_src = validate_spec({"name": "x", "source": "exec",
                                     "list_section": "A",
                                     "columns": ["Cost"]})
        many = validate_spec({"name": "x", "source": "technos",
                              "columns": [f"Tag{i}" for i in range(30)]})
        gb = validate_spec({"name": "x", "source": "technos",
                            "columns": ["Cost"], "group_by": "Nope"})
        clamp_ok = (any("not one of" in e for e in bad_src)
                    and len(many[0].columns) == MAX_COLUMNS
                    and any("more than" in e for e in many[1])
                    and gb[0].group_by == "" and any("group_by" in e
                                                     for e in gb[1]))
        print(f"bad enum / over-long lists / bogus group_by all reported: "
              f"{'OK' if clamp_ok else 'FAIL'}")
        ok = ok and clamp_ok

        miss = validate_spec({"source": "typelist", "columns": ["Cost"]})[1]
        req_ok = (any("needs a title" in e for e in miss)
                  and any("list_section" in e for e in miss)
                  and validate_spec("not a dict")[1] == ["spec is not an object"])
        print(f"missing title / missing list_section / non-object reported: "
              f"{'OK' if req_ok else 'FAIL'}")
        ok = ok and req_ok

        # ── persistence re-validates, so a hand-edited file can't smuggle ───
        add_addon(spec)
        doc = json.load(open(addons_path()))
        doc["addons"].append({"name": "Smuggled", "source": "typelist",
                              "list_section": "X]\n[General",
                              "columns": ["Cost"]})
        json.dump(doc, open(addons_path(), "w"))
        names = [s.name for s in load_addons()]
        reval_ok = names == ["AttachmentTypes"]
        print(f"load re-validates and drops a tampered spec: "
              f"{'OK' if reval_ok else 'FAIL'} {names}")
        ok = ok and reval_ok
        remove_addon("AttachmentTypes")
        rm_ok = load_addons() == []
        print(f"remove_addon drops it: {'OK' if rm_ok else 'FAIL'}")
        ok = ok and rm_ok

        # ── Eva: good JSON, prose, and a hallucinated injection ─────────────
        s1, e1, _ = plan_addon(_Stub(json.dumps(good)), "m", "attachments")
        s2, e2, _ = plan_addon(_Stub("Sure! Here's how you could do that…"),
                               "m", "attachments")
        s3, e3, _ = plan_addon(_Stub(json.dumps(
            {"name": "x", "source": "typelist",
             "list_section": "A]\n[General", "columns": ["Cost"]})), "m", "x")
        s4, e4, _ = plan_addon(_Stub(json.dumps(
            {"error": "which tag marks an attachment?"})), "m", "x")
        eva_ok = (s1 is not None and not e1 and len(s1.columns) == 3
                  and s2 is None and e2
                  and s3 is not None and any("not a plain INI name" in e
                                             for e in e3)
                  and s4 is None and "which tag" in e4[0])
        print(f"plan_addon: valid spec / prose / injection / asks back: "
              f"{'OK' if eva_ok else 'FAIL'}")
        if not eva_ok:
            print(f"   e1={e1} e2={e2} e3={e3} e4={e4}")
        ok = ok and eva_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


def _w(d, name, text):
    p = os.path.join(d, name)
    open(p, "w").write(text)
    return p


if __name__ == "__main__":
    sys.exit(main())
