"""
Reference feed — teach the tool (and Eva) about new modding features without a
tool update.

The feed is a user-curated repo (see the YRBalanceTool-Reference scaffold) with a
`manifest.json` indexing one Markdown doc per feature. The tool fetches the
manifest, diffs each entry's `version` against a local ingested record, downloads
the new/updated docs, and adds them to the knowledge store for Eva's RAG.

TRUST BOUNDARY — feed content is treated strictly as reference KNOWLEDGE, never as
commands. This module only ever fetches text and writes it into the knowledge
store. It never executes code, changes tool settings, or touches mod files. Doc
locations are resolved relative to the manifest's own location, and absolute doc
URLs must share the manifest's host — a manifest can only pull docs from its own
repo, not arbitrary hosts.
"""

import json
import os
import re
from urllib.parse import urlsplit

from .fetch import _default_reader
from .knowledge import _default_base


def _is_url(s):
    return s.startswith(("http://", "https://"))


def _read_source(loc, timeout=20):
    """Return the RAW text of an http(s) URL or a local file path. Uses the raw
    reader (not fetch_url) so JSON manifests and Markdown docs come back verbatim,
    not HTML-to-text extracted."""
    if _is_url(loc):
        return _default_reader(loc, timeout)
    with open(loc, encoding="utf-8", errors="replace") as f:
        return f.read()


def _manifest_base(feed_url):
    """The location docs are resolved relative to — the manifest's directory."""
    if _is_url(feed_url):
        return feed_url.rsplit("/", 1)[0] if "/" in feed_url else feed_url
    return os.path.dirname(os.path.abspath(feed_url))


def _resolve_doc(feed_url, base, doc):
    """Resolve a manifest entry's `doc` to a fetchable location, enforcing that it
    stays within the manifest's own host/dir (no arbitrary remote fetches)."""
    doc = doc.strip().replace("\\", "/")
    if _is_url(doc):
        if not _is_url(feed_url) or urlsplit(doc).netloc != urlsplit(feed_url).netloc:
            raise ValueError(f"doc host not allowed (must match feed): {doc}")
        return doc
    if _is_url(base):
        return base + "/" + doc.lstrip("/")
    # local: keep it under the manifest's directory
    full = os.path.normpath(os.path.join(base, doc))
    if os.path.commonpath([full, base]) != os.path.normpath(base):
        raise ValueError(f"doc path escapes feed directory: {doc}")
    return full


def fetch_manifest(feed_url):
    """Parse the feed manifest → list of {id, title, doc, version, tags}. Accepts
    either {"entries": [...]} or a bare list."""
    data = json.loads(_read_source(feed_url))
    entries = data.get("entries", []) if isinstance(data, dict) else data
    out = []
    for e in entries or []:
        if isinstance(e, dict) and e.get("id") and e.get("doc"):
            out.append({
                "id": str(e["id"]),
                "title": str(e.get("title", e["id"])),
                "doc": str(e["doc"]),
                "version": str(e.get("version", "1")),
                "tags": [str(t) for t in e.get("tags", [])],
            })
    return out


def _record_path(base_dir):
    return os.path.join(base_dir, "reference_feed.json")


def load_record(base_dir=None):
    """{id: ingested_version} of what this install has already learned."""
    base_dir = base_dir or _default_base()
    try:
        with open(_record_path(base_dir), encoding="utf-8") as f:
            rec = json.load(f)
            return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_record(base_dir, rec):
    with open(_record_path(base_dir), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2, sort_keys=True)


def check_feed(feed_url, base_dir=None):
    """Diff the manifest against the ingested record. Returns
    {'new': [...], 'updated': [...], 'current': [...]} entry lists — no doc fetch."""
    base_dir = base_dir or _default_base()
    rec = load_record(base_dir)
    buckets = {"new": [], "updated": [], "current": []}
    for e in fetch_manifest(feed_url):
        prev = rec.get(e["id"])
        if prev is None:
            buckets["new"].append(e)
        elif prev != e["version"]:
            buckets["updated"].append(e)
        else:
            buckets["current"].append(e)
    return buckets


def _doc_name(entry):
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", entry["id"]).strip("_") or "ref"
    return f"ref_{slug}.md"


def ingest_entries(feed_url, entries, store):
    """Fetch each entry's doc and add it to the knowledge store under a stable
    name (ref_<id>.md, overwritten on update), with a provenance header. Records
    the ingested versions. Returns [(id, name, chars)] for what was ingested;
    entries that fail to fetch are skipped (reported via the raised list)."""
    base = _manifest_base(feed_url)
    rec = load_record(store.base_dir)
    done, failed = [], []
    for e in entries:
        try:
            loc = _resolve_doc(feed_url, base, e["doc"])
            text = _read_source(loc)
        except (OSError, ValueError) as ex:
            failed.append((e["id"], str(ex)))
            continue
        header = (
            f"; === Reference feature: {e['title']} ===\n"
            f"; feed id: {e['id']}  version: {e['version']}"
            + (f"  tags: {', '.join(e['tags'])}" if e["tags"] else "")
            + "\n; Source: tool reference feed (curated modding-feature notes).\n\n"
        )
        path = store.add_doc(_doc_name(e), header + text, overwrite=True)
        rec[e["id"]] = e["version"]
        done.append((e["id"], os.path.basename(path), len(text)))
    _save_record(store.base_dir, rec)
    return done, failed
