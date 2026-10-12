#!/usr/bin/env python3
"""
Reference feed: check_feed diffs manifest versions against the local record
(new/updated/current), ingest_entries writes docs into the knowledge store with a
stable name (re-ingest replaces), version bumps re-ingest, and the trust-boundary
guards reject docs that escape the feed dir or point at another host. Builds its
own local feed + temp store; no network.

    python3 tests/test_reference_feed.py
"""

import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.ai.knowledge import KnowledgeStore
from yrbalance.ai.reference_feed import (
    fetch_manifest, check_feed, ingest_entries, load_record, _resolve_doc,
)


def _write(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    ok = True
    with tempfile.TemporaryDirectory() as feed, tempfile.TemporaryDirectory() as kb:
        manifest = os.path.join(feed, "manifest.json")
        _write(os.path.join(feed, "docs", "a.md"), "# Feature A\nAbout A.\n")
        _write(os.path.join(feed, "docs", "b.md"), "# Feature B\nAbout B.\n")

        def write_manifest(entries):
            _write(manifest, json.dumps({"entries": entries}))

        write_manifest([
            {"id": "a", "title": "Feature A", "doc": "docs/a.md", "version": "1",
             "tags": ["x"]},
            {"id": "b", "title": "Feature B", "doc": "docs/b.md", "version": "1"},
        ])

        store = KnowledgeStore(base_dir=kb)

        # manifest parse
        ents = fetch_manifest(manifest)
        parse_ok = len(ents) == 2 and ents[0]["id"] == "a"
        print(f"manifest parse: {'OK' if parse_ok else 'FAIL'}")
        ok = ok and parse_ok

        # first check → both new
        b = check_feed(manifest, base_dir=kb)
        new_ok = len(b["new"]) == 2 and not b["updated"] and not b["current"]
        print(f"check: all new: {'OK' if new_ok else 'FAIL'}  "
              f"{[e['id'] for e in b['new']]}")
        ok = ok and new_ok

        # ingest → docs land in the store with stable names, record updated
        done, failed = ingest_entries(manifest, b["new"], store)
        names = {n for _i, n, _c in done}
        ing_ok = (not failed and names == {"ref_a.md", "ref_b.md"}
                  and load_record(kb) == {"a": "1", "b": "1"}
                  and any(n == "ref_a.md" for n, _c in store.list_docs()))
        print(f"ingest: {'OK' if ing_ok else 'FAIL'}  {sorted(names)} failed={failed}")
        ok = ok and ing_ok

        # re-check → all current (nothing to do)
        b2 = check_feed(manifest, base_dir=kb)
        cur_ok = len(b2["current"]) == 2 and not b2["new"] and not b2["updated"]
        print(f"re-check: all current: {'OK' if cur_ok else 'FAIL'}")
        ok = ok and cur_ok

        # bump A's version → A updated, B current
        write_manifest([
            {"id": "a", "title": "Feature A", "doc": "docs/a.md", "version": "2"},
            {"id": "b", "title": "Feature B", "doc": "docs/b.md", "version": "1"},
        ])
        _write(os.path.join(feed, "docs", "a.md"), "# Feature A\nRevised A.\n")
        b3 = check_feed(manifest, base_dir=kb)
        upd_ok = (len(b3["updated"]) == 1 and b3["updated"][0]["id"] == "a"
                  and len(b3["current"]) == 1)
        print(f"version bump → updated: {'OK' if upd_ok else 'FAIL'}")
        ok = ok and upd_ok

        # re-ingest A replaces in place (no ref_a_1.md), content refreshed
        before = {n for n, _c in store.list_docs()}
        ingest_entries(manifest, b3["updated"], store)
        after = {n for n, _c in store.list_docs()}
        with open(os.path.join(store.read_dir, "ref_a.md"), encoding="utf-8") as f:
            refreshed = "Revised A." in f.read()
        replace_ok = after == before and refreshed and load_record(kb)["a"] == "2"
        print(f"re-ingest replaces in place + refreshes: "
              f"{'OK' if replace_ok else 'FAIL'}")
        ok = ok and replace_ok

        # trust boundary: doc escaping the feed dir / foreign host is rejected
        esc_ok = foreign_ok = False
        try:
            _resolve_doc(manifest, feed, "../../etc/passwd")
        except ValueError:
            esc_ok = True
        try:
            _resolve_doc("https://example.com/manifest.json",
                         "https://example.com", "https://evil.com/x.md")
        except ValueError:
            foreign_ok = True
        print(f"trust guards (path escape + foreign host): "
              f"{'OK' if esc_ok and foreign_ok else 'FAIL'}")
        ok = ok and esc_ok and foreign_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
