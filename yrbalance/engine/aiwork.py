"""
AI Builder *working file* — an in-progress aimd.ini you build up separately from
any mod, backed by a sidecar in ~/.yrbalancetool/ and exported on demand.

The AI Builder points its usual load / edit / generate / pull machinery at this
file, so "create a working file" is really just "make the Builder edit this sidecar
instead of the loaded mod's aimd.ini". Nothing here touches a real mod until
export_working() copies it out (the UI backs up the destination first).

Seeds:
  • blank          — empty AI registries,
  • vanilla        — the ingested vanilla YR aimd (knowledge dir), with an
                     automatic fallback that extracts it from the game's mixes,
  • an existing aimd — e.g. a copy of the loaded mod's, to build on.
"""

from .ini_model import guard_ini_text
import os
import shutil

from .ini_model import parse_ini_lines, build_section_map

_BLANK = (
    "; AI Builder working file — build here, then Export into a mod's aimd.ini.\n"
    "; Nothing in here affects any mod until you export it.\n\n"
    "[TaskForces]\n\n[ScriptTypes]\n\n[TeamTypes]\n\n[AITriggerTypes]\n")


def _root():
    return os.path.join(os.path.expanduser("~"), ".yrbalancetool")


def working_path():
    return os.path.join(_root(), "ai_working.ini")


def working_exists(path=None):
    return os.path.isfile(path or working_path())


def knowledge_vanilla_aimd():
    """Path to the ingested vanilla YR aimd in the knowledge dir (may not exist)."""
    return os.path.join(_root(), "knowledge", "read", "vanilla_yr_aimd.ini")


def resolve_vanilla_aimd(game_dir=None):
    """A path to a vanilla YR aimd.ini to seed from, or None.

    Prefers the ingested knowledge-dir copy (same convention as
    vanilla_yr_rulesmd.ini). If that's absent and `game_dir` is given, extracts
    aimd.ini straight out of the game's mixes and caches it in the knowledge dir —
    so vanilla game data is never shipped in the repo, only pulled from the user's
    own install on demand."""
    kp = knowledge_vanilla_aimd()
    if os.path.isfile(kp):
        return kp
    if game_dir:
        try:
            from .mixlib import find_in_mixes
            data = find_in_mixes([game_dir], "aimd.ini", max_depth=4)
        except Exception:
            data = None
        if data:
            os.makedirs(os.path.dirname(kp), exist_ok=True)
            header = (
                b"; === VANILLA YR AI ===\r\n"
                b"; VANILLA BASELINE REFERENCE - extracted from the game's mixes.\r\n"
                b"; This is NOT the user's loaded mod. Stock/default values only.\r\n")
            with open(kp, "wb") as fh:
                fh.write(header + data)
            return kp
    return None


def _write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="latin-1", errors="replace", newline="") as fh:
        fh.write(guard_ini_text(text))
    return path


def new_blank(path=None):
    """Create an empty working file (the four AI registries, no entries)."""
    return _write_text(path or working_path(), _BLANK)


def new_from_vanilla(game_dir=None, path=None):
    """Seed the working file from the vanilla YR aimd. Raises FileNotFoundError if
    no vanilla source is available (and none could be extracted from game_dir)."""
    src = resolve_vanilla_aimd(game_dir)
    if not src:
        raise FileNotFoundError(
            "No vanilla YR aimd.ini available. Load a mod from your YR install so "
            "it can be extracted, or ingest one into the knowledge folder.")
    path = path or working_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shutil.copyfile(src, path)
    return path


def new_from_file(src_path, path=None):
    """Seed the working file from an existing aimd.ini (e.g. the loaded mod's)."""
    if not src_path or not os.path.isfile(src_path):
        raise FileNotFoundError(f"No aimd to copy: {src_path!r}")
    path = path or working_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shutil.copyfile(src_path, path)
    return path


def load_working(path=None):
    """(lines, section_map) for the working file, or (None, None) if it doesn't
    exist yet."""
    path = path or working_path()
    if not os.path.isfile(path):
        return None, None
    lines = parse_ini_lines(path)
    return lines, build_section_map(lines)[0]


def export_working(dest_path, path=None):
    """Copy the working file to `dest_path` (overwriting). The caller is expected to
    back up an existing destination first (the UI uses make_backup)."""
    path = path or working_path()
    if not os.path.isfile(path):
        raise FileNotFoundError("No working file to export.")
    shutil.copyfile(path, dest_path)
    return dest_path
