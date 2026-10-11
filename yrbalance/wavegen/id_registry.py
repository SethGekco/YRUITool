"""
id_registry.py
==============
Shared global ID registry for all YR-AI-Tool generated entries.

IDs in aimd.ini are globally unique 8-character hex strings suffixed with -G:
    XXXXXXXX-G  (e.g. 0CADDA1C-G)

They appear as section headers AND in list sections across TaskForces,
ScriptTypes, TeamTypes, AITriggerTypes, etc. A duplicate ID anywhere in
the file causes the game to misread or silently drop entries.

This module provides a SINGLE shared registry that all generators use,
preventing collisions across all section types — now and in the future.
"""

import re
import secrets
from pathlib import Path


_SECTION_ID  = re.compile(r"^\[([0-9A-Fa-f]{8}-G)\]")
_LIST_ID     = re.compile(r"^\d+=([0-9A-Fa-f]{8}-G)")


class GlobalIDRegistry:
    """
    Single source of truth for all IDs in an aimd.ini file.

    All generators (taskforce, script, team, trigger) must share one
    instance of this registry so no two entries ever receive the same ID.
    """

    def __init__(self, existing_ids: set[str] | None = None):
        self._used: set[str] = set(existing_ids or [])

    @classmethod
    def from_aimd(cls, aimd_path: str | Path) -> "GlobalIDRegistry":
        """
        Scan aimd.ini and collect ALL IDs regardless of which section
        they appear in — section headers, list entries, everything.
        """
        path = Path(aimd_path)
        if not path.exists():
            return cls()

        existing: set[str] = set()
        with path.open(encoding="latin-1") as fh:
            for line in fh:
                s = line.strip()
                if not s or s.startswith(';'):
                    continue
                m = _SECTION_ID.match(s) or _LIST_ID.match(s)
                if m:
                    existing.add(m.group(1))

        return cls(existing)

    def generate(self) -> str:
        """Generate a new globally unique ID."""
        while True:
            candidate = f"{secrets.token_hex(4).upper()}-G"
            if candidate not in self._used:
                self._used.add(candidate)
                return candidate

    def register(self, id_: str):
        """Manually register an ID (e.g. one loaded from an existing file)."""
        self._used.add(id_)

    def is_used(self, id_: str) -> bool:
        return id_ in self._used

    @property
    def count(self) -> int:
        return len(self._used)
