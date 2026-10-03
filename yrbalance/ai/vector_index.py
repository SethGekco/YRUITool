"""
Embedding index for Eva's knowledge (RAG retrieval).

Each ingested doc is split into overlapping chunks, embedded via Ollama, and stored
with its vector in a JSON index next to the knowledge folder. At query time only
the most relevant chunks are pulled into the prompt — so the knowledge base can grow
arbitrarily while each request stays small.

Pure-Python cosine (no numpy): fine for thousands of chunks; "takes a while" on huge
sets, which is an accepted trade. Indexing is incremental (only changed/new docs are
re-embedded; a change of embedding model triggers a full rebuild).
"""

import os
import json
import math


def chunk_text(text, size=900, overlap=150):
    """Split text into overlapping character windows."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    step = max(1, size - overlap)
    return [text[i:i + size] for i in range(0, len(text), step)]


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class KnowledgeIndex:
    def __init__(self, knowledge, client, embed_model="nomic-embed-text"):
        self.knowledge = knowledge
        self.client = client
        self.embed_model = embed_model
        self.path = os.path.join(knowledge.base_dir, "index.json")
        self.data = self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"model": self.embed_model, "docs": {}}

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    def _read_files(self):
        rd = self.knowledge.read_dir
        out = {}
        for fn in sorted(os.listdir(rd)):
            p = os.path.join(rd, fn)
            if os.path.isfile(p):
                out[fn] = os.path.getmtime(p)
        return out

    def is_stale(self):
        if self.data.get("model") != self.embed_model:
            return True
        files = self._read_files()
        docs = self.data.get("docs", {})
        if set(files) != set(docs):
            return True
        return any(docs[fn].get("mtime") != mt for fn, mt in files.items())

    def stats(self):
        docs = self.data.get("docs", {})
        chunks = sum(len(d.get("chunks", [])) for d in docs.values())
        return len(docs), chunks

    def rebuild(self, progress=None):
        """(Re)embed changed/new docs; drop deleted ones. Returns chunks embedded."""
        if self.data.get("model") != self.embed_model:
            self.data = {"model": self.embed_model, "docs": {}}
        docs = self.data["docs"]
        files = self._read_files()

        for fn in list(docs):
            if fn not in files:
                del docs[fn]

        embedded = 0
        for fn, mtime in files.items():
            if fn in docs and docs[fn].get("mtime") == mtime:
                continue
            try:
                with open(os.path.join(self.knowledge.read_dir, fn),
                          "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            chunks = chunk_text(text)
            vecs = self.client.embed(chunks, self.embed_model) if chunks else []
            docs[fn] = {"mtime": mtime,
                        "chunks": [{"text": c, "vec": v}
                                   for c, v in zip(chunks, vecs)]}
            embedded += len(chunks)
            if progress:
                progress(f"indexed {fn} ({len(chunks)} chunks)")

        self.data["model"] = self.embed_model
        self._save()
        return embedded

    def retrieve(self, query, top_k=6, max_chars=6000):
        """
        Return (context_text, sources) for a query. sources is a list of
        {"name", "score"} for the docs whose chunks were included, best first.
        Empty ("", []) if there is no index.
        """
        docs = self.data.get("docs", {})
        if not docs:
            return "", []
        qv = self.client.embed([query], self.embed_model)[0]
        scored = []
        for fn, d in docs.items():
            for ch in d.get("chunks", []):
                scored.append((cosine(qv, ch["vec"]), fn, ch["text"]))
        scored.sort(key=lambda x: x[0], reverse=True)

        parts, total = [], 0
        sources = {}  # name -> best score, insertion-ordered (best first)
        for score, fn, text in scored[:top_k]:
            chunk = f"### {fn} (relevance {score:.2f})\n{text.strip()}\n"
            if total + len(chunk) > max_chars:
                chunk = chunk[: max(0, max_chars - total)]
            if chunk:
                parts.append(chunk)
                total += len(chunk)
                sources[fn] = max(sources.get(fn, score), score)
            if total >= max_chars:
                break
        source_list = [{"name": n, "score": sc} for n, sc in sources.items()]
        return "\n".join(parts), source_list
