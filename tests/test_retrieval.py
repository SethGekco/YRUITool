#!/usr/bin/env python3
"""
Retrieval reserves a slot for curated recipe/cheatsheet chunks so bulk raw-INI
dumps can't crowd the instructional docs out of the top-k.

    python3 tests/test_retrieval.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yrbalance.ai.knowledge import KnowledgeStore
from yrbalance.ai.vector_index import KnowledgeIndex, _is_curated, _is_reference


class FakeClient:
    def embed(self, texts, model):
        return [[1.0, 0.0] for _ in texts]  # query points along raw-doc axis


def main():
    ok = True

    cls_ok = (_is_curated("02_recipes.md") and _is_curated("11_vanilla_reference.md")
              and not _is_curated("vanilla_yr_rulesmd.ini")
              and not _is_curated("ares_manual.txt"))
    print(f"_is_curated classification: {cls_ok}  {'OK' if cls_ok else 'FAIL'}")
    ok = ok and cls_ok

    ref_ok = (_is_reference("aiedit_yr_config.ini") and _is_reference("ref_actions.ini")
              and not _is_reference("aiedit_readme.md")       # prose, not a table
              and not _is_reference("vanilla_yr_rulesmd.ini")
              and not _is_reference("02_recipes.md"))
    print(f"_is_reference classification: {ref_ok}  {'OK' if ref_ok else 'FAIL'}")
    ok = ok and ref_ok

    with tempfile.TemporaryDirectory() as d:
        ks = KnowledgeStore(base_dir=os.path.join(d, "kb"))
        idx = KnowledgeIndex(ks, FakeClient(), "m")
        # Raw dump dominates on raw cosine; the recipe scores lower.
        idx.data = {"model": "m", "docs": {
            "vanilla_yr_rulesmd.ini": {"mtime": 0, "chunks": [
                {"text": "raw1", "vec": [1.0, 0.0]},
                {"text": "raw2", "vec": [0.99, 0.02]},
                {"text": "raw3", "vec": [0.98, 0.03]},
                {"text": "raw4", "vec": [0.97, 0.04]},
            ]},
            "02_recipes.md": {"mtime": 0, "chunks": [
                {"text": "[$BUILDING]\nCost = (Strength / 2) + 100", "vec": [0.6, 0.8]},
            ]},
            "aiedit_yr_config.ini": {"mtime": 0, "chunks": [
                {"text": "8=Unload,UnloadTypes,Unload transport.", "vec": [0.5, 0.86]},
            ]},
        }}

        # Without reservation, top-2 would be raw chunks only.
        _, src = idx.retrieve("building cost formula", top_k=2)
        names = [s["name"] for s in src]
        reserved_ok = "02_recipes.md" in names and "vanilla_yr_rulesmd.ini" in names
        print(f"curated chunk reserved into top-2: {names}  "
              f"{'OK' if reserved_ok else 'FAIL'}")
        ok = ok and reserved_ok

        # Both priority buckets reserved together (curated + reference), without
        # displacing each other, while a raw chunk still makes it.
        _, src3 = idx.retrieve("script action unload", top_k=3)
        n3 = [s["name"] for s in src3]
        both_ok = ("aiedit_yr_config.ini" in n3 and "02_recipes.md" in n3
                   and "vanilla_yr_rulesmd.ini" in n3)
        print(f"curated + reference both reserved into top-3: {n3}  "
              f"{'OK' if both_ok else 'FAIL'}")
        ok = ok and both_ok

        # When a curated chunk already ranks in, no duplication / no crash.
        idx.data["docs"]["02_recipes.md"]["chunks"][0]["vec"] = [1.0, 0.0]
        _, src2 = idx.retrieve("x", top_k=3)
        n2 = [s["name"] for s in src2]
        dup_ok = n2.count("02_recipes.md") == 1
        print(f"no duplicate when already ranked: {n2}  {'OK' if dup_ok else 'FAIL'}")
        ok = ok and dup_ok

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
