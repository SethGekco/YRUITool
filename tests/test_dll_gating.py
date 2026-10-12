#!/usr/bin/env python3
"""
Reference-feed DLL gating: detect_dlls finds extension DLLs beside a mod;
ingested feed entries carry their gating DLL; blocked_docs hides a feature note
when its DLL isn't present and reveals it when it is; and the knowledge store's
context_text honours the exclude set. Hermetic — builds its own feed + store.

    python3 tests/test_dll_gating.py
"""

import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.ai.knowledge import KnowledgeStore
from yrbalance.ai.dll_detect import detect_dlls, normalize_dll
from yrbalance.ai.reference_feed import (
    check_feed, ingest_entries, blocked_docs, load_record,
)


def _w(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    ok = True

    # --- detect_dlls: scans folder + parent ---
    with tempfile.TemporaryDirectory() as g:
        game = os.path.join(g, "game")
        mod = os.path.join(game, "mod")
        os.makedirs(mod)
        _w(os.path.join(game, "TechnoAttachmentExt.dll"), "x")
        _w(os.path.join(game, "Phobos.dll"), "x")
        _w(os.path.join(mod, "rulesmd.ini"), "[General]\n")
        found = detect_dlls(mod)                      # parent = game
        det_ok = {"technoattachmentext", "phobos"} <= found
        print(f"detect_dlls (folder+parent): {'OK' if det_ok else 'FAIL'} "
              f"{sorted(found)}")
        ok = ok and det_ok

    norm_ok = (normalize_dll("TechnoAttachmentExt.dll") == "technoattachmentext"
               and normalize_dll("") == "")
    print(f"normalize_dll: {'OK' if norm_ok else 'FAIL'}")
    ok = ok and norm_ok

    # --- ingest with dll field, then gate ---
    with tempfile.TemporaryDirectory() as feed, tempfile.TemporaryDirectory() as kb:
        _w(os.path.join(feed, "docs", "attach.md"), "# Attach\nTechnoAttachment tags.\n")
        _w(os.path.join(feed, "docs", "ares.md"), "# Ares\nCore engine tags.\n")
        manifest = os.path.join(feed, "manifest.json")
        _w(manifest, json.dumps({"entries": [
            {"id": "attach", "title": "Attachments", "doc": "docs/attach.md",
             "version": "1", "dll": "TechnoAttachmentExt",
             "dll_file": "TechnoAttachmentExt.dll"},
            {"id": "ares", "title": "Ares core", "doc": "docs/ares.md",
             "version": "1", "dll": "Ares", "dll_file": "Ares.dll"},
        ]}))

        store = KnowledgeStore(base_dir=kb)
        buckets = check_feed(manifest, base_dir=kb)
        done, failed = ingest_entries(manifest, buckets["new"], store)
        rec = load_record(kb)
        rec_ok = (not failed and isinstance(rec.get("attach"), dict)
                  and rec["attach"]["dll"] == "TechnoAttachmentExt"
                  and rec["attach"]["doc"] == "ref_attach.md")
        print(f"ingest records dll+doc: {'OK' if rec_ok else 'FAIL'}")
        ok = ok and rec_ok

        # No DLLs present → both DLL-gated docs blocked.
        none_blocked = blocked_docs(set(), base_dir=kb)
        b1 = none_blocked == {"ref_attach.md", "ref_ares.md"}
        print(f"no DLLs → both gated docs blocked: {'OK' if b1 else 'FAIL'} "
              f"{sorted(none_blocked)}")
        ok = ok and b1

        # Only TechnoAttachmentExt present → Ares doc blocked, attach doc shown.
        some = blocked_docs({"technoattachmentext"}, base_dir=kb)
        b2 = some == {"ref_ares.md"}
        print(f"one DLL present → only other blocked: {'OK' if b2 else 'FAIL'} "
              f"{sorted(some)}")
        ok = ok and b2

        # context_text honours exclude.
        full = store.context_text()
        gated = store.context_text(exclude={"ref_ares.md"})
        ctx_ok = "Core engine tags" in full and "Core engine tags" not in gated \
            and "TechnoAttachment tags" in gated
        print(f"context_text exclude works: {'OK' if ctx_ok else 'FAIL'}")
        ok = ok and ctx_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
