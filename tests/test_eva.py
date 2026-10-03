#!/usr/bin/env python3
"""
Test the Eva backend: Ollama client (mock transport), knowledge store, and Eva's
script extraction/validation. No Ollama server or Qt required.

    python3 tests/test_eva.py /path/to/rulesmd.ini
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.engine import parse_ini_lines, build_section_map
from yrbalance.ai.ollama_client import OllamaClient, OllamaError
from yrbalance.ai.knowledge import KnowledgeStore
from yrbalance.ai.eva import Eva, rules_summary


def main(path):
    ok = True
    lines = parse_ini_lines(path)
    section_map, _ = build_section_map(lines)

    # ── Ollama client with a fake transport ──────────────────────────────────
    def fake_transport(url, payload=None, method="GET", timeout=120):
        if url.endswith("/api/tags"):
            return {"models": [{"name": "llama3.1:latest"}, {"name": "qwen2.5:7b"}]}
        if url.endswith("/api/chat"):
            # Echo a canned reply containing a valid script.
            return {"message": {"role": "assistant", "content": _CANNED}}
        return {}

    client = OllamaClient(transport=fake_transport)
    models = client.list_models()
    cl_ok = models == ["llama3.1:latest", "qwen2.5:7b"]
    print(f"client list_models: {models}  {'OK' if cl_ok else 'FAIL'}")
    ok = ok and cl_ok

    # Unreachable server -> OllamaError.
    def boom(*a, **k):
        raise OSError("connection refused")
    try:
        OllamaClient(transport=boom).list_models()
        print("  FAIL  unreachable should raise"); ok = False
    except OllamaError:
        print("  OK    unreachable raises OllamaError")

    # ── Knowledge store (inbox -> read) ──────────────────────────────────────
    with tempfile.TemporaryDirectory() as d:
        ks = KnowledgeStore(base_dir=d)
        with open(os.path.join(ks.inbox, "notes.md"), "w") as f:
            f.write("Garrison weapons use OccupyWeapon.")
        ingested = ks.scan_inbox()
        moved = os.path.isfile(os.path.join(ks.read_dir, "notes.md"))
        gone = not os.path.isfile(os.path.join(ks.inbox, "notes.md"))
        ctx = ks.context_text()
        k_ok = (len(ingested) == 1 and moved and gone
                and "OccupyWeapon" in ctx and len(ks.list_docs()) == 1)
        print(f"knowledge inbox->read: ingested={ingested}, moved={moved}, "
              f"gone={gone}, in-context={'OccupyWeapon' in ctx}  "
              f"{'OK' if k_ok else 'FAIL'}")
        ok = ok and k_ok

        # ── Eva: extract + validate a script from a model reply ──────────────
        eva = Eva(client, ks, model="llama3.1:latest")
        reply = eva.ask("Halve the cost of all infantry.", section_map)
        e_ok = reply.valid and "[$INFANTRY]" in reply.script and "Cost" in reply.script
        print(f"eva ask -> valid script: {e_ok}  script has scope+Cost  "
              f"{'OK' if e_ok else 'FAIL'}")
        ok = ok and e_ok

    # ── Eva flags an invalid script ──────────────────────────────────────────
    def bad_transport(url, payload=None, method="GET", timeout=120):
        return {"message": {"content": "```\nCost = 5\n```"}}  # no [scope] header
    eva2 = Eva(OllamaClient(transport=bad_transport), None, model="x")
    r2 = eva2.ask("do a thing", section_map)
    bad_ok = (not r2.valid) and r2.error
    print(f"eva invalid script flagged: valid={r2.valid}, error set={bool(r2.error)}  "
          f"{'OK' if bad_ok else 'FAIL'}")
    ok = ok and bad_ok

    # ── URL learning: HTML -> text + fetch_url + add_doc ─────────────────────
    from yrbalance.ai.fetch import html_to_text, fetch_url
    html = ("<html><head><title>Garrison Guide</title>"
            "<style>.x{color:red}</style></head><body>"
            "<script>evil()</script><h1>Occupy</h1>"
            "<p>OccupyWeapon sets the garrison weapon.</p></body></html>")
    title, text = html_to_text(html)
    h_ok = (title == "Garrison Guide" and "OccupyWeapon" in text
            and "evil()" not in text and "color:red" not in text)
    print(f"html_to_text: title={title!r}, clean={h_ok}  {'OK' if h_ok else 'FAIL'}")
    ok = ok and h_ok

    t2, body = fetch_url("https://example.com/x", reader=lambda u, to: html)
    f_ok = t2 == "Garrison Guide" and "OccupyWeapon" in body
    print(f"fetch_url (injected reader): {f_ok}  {'OK' if f_ok else 'FAIL'}")
    ok = ok and f_ok
    try:
        fetch_url("ftp://nope")
        print("  FAIL  non-http should raise"); ok = False
    except ValueError:
        print("  OK    non-http URL rejected")

    with tempfile.TemporaryDirectory() as d:
        ks2 = KnowledgeStore(base_dir=d)
        p = ks2.add_doc("Garrison Guide", "OccupyWeapon is the garrison weapon.")
        add_ok = (os.path.isfile(p) and p.endswith(".txt")
                  and "OccupyWeapon" in ks2.context_text()
                  and len(ks2.list_docs()) == 1)
        print(f"knowledge.add_doc (fetched/chat): {add_ok}  "
              f"{'OK' if add_ok else 'FAIL'}")
        ok = ok and add_ok

    # ── Embeddings + retrieval index ─────────────────────────────────────────
    from yrbalance.ai.vector_index import KnowledgeIndex, chunk_text, cosine

    class FakeEmbedClient:
        VOCAB = ["occupy", "cost", "dog", "garrison", "techlevel", "weapon"]

        def embed(self, texts, model):
            return [[1.0 if w in t.lower() else 0.0 for w in self.VOCAB]
                    for t in texts]

    ch = chunk_text("x" * 2000, size=900, overlap=150)
    chunk_ok = len(ch) >= 2 and all(len(c) <= 900 for c in ch)
    cos_ok = abs(cosine([1, 0], [1, 0]) - 1.0) < 1e-9 and cosine([1, 0], [0, 1]) == 0
    print(f"chunking/cosine: chunks={len(ch)}, cos_ok={cos_ok}  "
          f"{'OK' if chunk_ok and cos_ok else 'FAIL'}")
    ok = ok and chunk_ok and cos_ok

    with tempfile.TemporaryDirectory() as d:
        ks3 = KnowledgeStore(base_dir=d)
        ks3.add_doc("garrison", "OccupyWeapon is the garrison weapon for infantry.")
        ks3.add_doc("economy", "Cost controls how expensive a unit is to build.")
        index = KnowledgeIndex(ks3, FakeEmbedClient(), embed_model="fake")
        n = index.rebuild()
        ndocs, nchunks = index.stats()
        retrieved = index.retrieve("garrison occupy weapon", top_k=1)
        idx_ok = (n >= 2 and ndocs == 2 and "OccupyWeapon" in retrieved
                  and "Cost controls" not in retrieved and not index.is_stale())
        print(f"index build+retrieve: {ndocs} docs/{nchunks} chunks, "
              f"top hit has garrison={'OccupyWeapon' in retrieved}  "
              f"{'OK' if idx_ok else 'FAIL'}")
        ok = ok and idx_ok

        # Eva uses retrieval when an index is attached.
        eva3 = Eva(client, ks3, model="x", index=index, top_k=1)
        ktext = eva3._knowledge_text("how do garrison weapons work?")
        eva_idx_ok = "OccupyWeapon" in ktext and "Cost controls" not in ktext
        print(f"eva uses retrieval: {eva_idx_ok}  {'OK' if eva_idx_ok else 'FAIL'}")
        ok = ok and eva_idx_ok

    # rules_summary is bounded and mentions scopes.
    rs = rules_summary(section_map)
    print(f"rules summary: {len(rs)} chars, has counts={'Category counts' in rs}")

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


_CANNED = """Here you go:
```ini
[$INFANTRY]
Cost = Cost * 0.5
```"""


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 tests/test_eva.py /path/to/rulesmd.ini")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
