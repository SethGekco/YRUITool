"""
Knowledge store: drop reference files into an inbox; they're read and moved to a
'read' folder so Eva can use them as grounding context.

  ~/.yrbalancetool/knowledge/inbox/   <- drop .txt/.md/.ini/etc here
  ~/.yrbalancetool/knowledge/read/    <- moved here after ingestion

No embeddings — for a modest library the text is just concatenated into context
(capped). This is intentionally simple and dependency-free.
"""

import os
import re
import shutil

_TEXT_EXTS = {".txt", ".md", ".markdown", ".ini", ".yrs", ".yrbp", ".csv",
              ".log", ".rst", ""}


def _default_base():
    return os.path.join(os.path.expanduser("~"), ".yrbalancetool", "knowledge")


class KnowledgeStore:
    def __init__(self, base_dir=None):
        self.base_dir = base_dir or _default_base()
        self.inbox = os.path.join(self.base_dir, "inbox")
        self.read_dir = os.path.join(self.base_dir, "read")
        os.makedirs(self.inbox, exist_ok=True)
        os.makedirs(self.read_dir, exist_ok=True)

    def _unique_dest(self, name):
        dest = os.path.join(self.read_dir, name)
        if not os.path.exists(dest):
            return dest
        stem, ext = os.path.splitext(name)
        i = 2
        while os.path.exists(os.path.join(self.read_dir, f"{stem}_{i}{ext}")):
            i += 1
        return os.path.join(self.read_dir, f"{stem}_{i}{ext}")

    def seed_bundled(self, bundled_dir):
        """Copy bundled reference docs into read/. Adds missing ones and refreshes
        any whose shipped content changed (so encyclopedia updates reach existing
        installs); these are our reference docs, not user notes. Returns names
        added or refreshed."""
        if not bundled_dir or not os.path.isdir(bundled_dir):
            return []
        changed = []
        for fn in sorted(os.listdir(bundled_dir)):
            src = os.path.join(bundled_dir, fn)
            if not os.path.isfile(src):
                continue
            dest = os.path.join(self.read_dir, fn)
            if os.path.exists(dest) and self._same_file(src, dest):
                continue
            shutil.copy2(src, dest)
            changed.append(fn)
        return changed

    @staticmethod
    def _same_file(a, b):
        try:
            with open(a, "rb") as fa, open(b, "rb") as fb:
                return fa.read() == fb.read()
        except OSError:
            return False

    def add_doc(self, name, text, overwrite=False):
        """Write text directly into the read/ store (for fetched pages, chats).
        Returns the path. Sanitizes the name and ensures a .txt/.md extension.
        overwrite=True writes to a deterministic path (read/<name>), replacing any
        prior copy — used for feed docs so a re-ingest updates in place rather than
        piling up _1/_2 copies."""
        safe = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip() or "note"
        if not os.path.splitext(safe)[1]:
            safe += ".txt"
        dest = (os.path.join(self.read_dir, safe) if overwrite
                else self._unique_dest(safe))
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
        return dest

    def add_reference_ini(self, src_path, game):
        """Ingest a stock/vanilla INI as a tagged baseline reference.

        `game` is 'yr' or 'ra2'. The file is copied into read/ as
        `vanilla_<game>_<basename>` with a header marking it as the unmodified
        baseline (so Eva and its citations never confuse it with the loaded mod).
        Re-importing the same file overwrites the previous copy. Returns the path."""
        game = (game or "").lower()
        if game not in ("yr", "ra2"):
            raise ValueError("game must be 'yr' or 'ra2'")
        base = os.path.basename(src_path)
        name = f"vanilla_{game}_{base}"
        if not os.path.splitext(name)[1]:
            name += ".ini"
        with open(src_path, "r", encoding="utf-8", errors="replace") as f:
            body = f.read()
        label = "YR (Yuri's Revenge)" if game == "yr" else "RA2 (Red Alert 2)"
        header = (
            f"; === VANILLA {label} baseline reference ===\n"
            "; VANILLA BASELINE REFERENCE — unmodified official game file.\n"
            "; This is NOT the user's loaded mod. Use it only to cite stock "
            "defaults.\n"
        )
        dest = os.path.join(self.read_dir, name)  # deterministic: re-import replaces
        with open(dest, "w", encoding="utf-8") as f:
            f.write(header + body)
        return dest

    def scan_inbox(self):
        """Read each inbox file, move it to read/, return [(name, chars)]."""
        ingested = []
        for fn in sorted(os.listdir(self.inbox)):
            src = os.path.join(self.inbox, fn)
            if not os.path.isfile(src):
                continue
            if os.path.splitext(fn)[1].lower() not in _TEXT_EXTS:
                continue  # skip binaries
            try:
                with open(src, "r", encoding="utf-8", errors="replace") as f:
                    chars = len(f.read())
            except OSError:
                continue
            shutil.move(src, self._unique_dest(fn))
            ingested.append((fn, chars))
        return ingested

    def list_docs(self):
        """Return [(name, chars)] for everything already ingested."""
        out = []
        for fn in sorted(os.listdir(self.read_dir)):
            p = os.path.join(self.read_dir, fn)
            if not os.path.isfile(p):
                continue
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    out.append((fn, len(f.read())))
            except OSError:
                pass
        return out

    def context_text(self, budget=8000, exclude=None):
        """Concatenate ingested docs into a single context string, capped.
        `exclude` is an optional set of filenames to skip (e.g. feed docs whose
        DLL isn't present in the loaded mod)."""
        exclude = exclude or set()
        parts = []
        total = 0
        for fn in sorted(os.listdir(self.read_dir)):
            if fn in exclude:
                continue
            p = os.path.join(self.read_dir, fn)
            if not os.path.isfile(p):
                continue
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            chunk = f"### {fn}\n{text.strip()}\n"
            if total + len(chunk) > budget:
                chunk = chunk[: max(0, budget - total)]
            if chunk:
                parts.append(chunk)
                total += len(chunk)
            if total >= budget:
                break
        return "\n".join(parts)
