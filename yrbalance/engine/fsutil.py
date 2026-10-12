"""Small filesystem helpers shared across the engine and UI."""

import os


def sibling_file(folder, *names):
    """First existing file in `folder` matching any of `names` (case-insensitive),
    as a full path, or None. Used to find artmd/soundmd/aimd beside a rules file."""
    if not folder or not os.path.isdir(folder):
        return None
    low = {f.lower(): f for f in os.listdir(folder)}
    for n in names:
        if n.lower() in low:
            return os.path.join(folder, low[n.lower()])
    return None
