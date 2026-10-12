"""Reusable media-preview helpers (cleanup C1).

The palette resolution and audio playback/decoding logic used by the Library
detail pane, factored out of library_tab.py so other views (the Encyclopedia, a
future aimd preview) can reuse it. These are stateless functions — callers pass
the mod folder and media catalog and own any caching.

Kept here (not in engine/) because the audio player shells out to the desktop and
the callers are Qt UI; the image/palette decoding itself lives in engine.images.
"""

import os
import sys
import shutil
import subprocess

from ..engine import (load_palette, grayscale_palette, resolve_media, mix_id)

# Unit/terrain palette candidates, best first.
PAL_NAMES = ("unittem.pal", "temperat.pal", "isotemp.pal", "cameo.pal")


def _from_catalog(catalog, name):
    """Load a palette named `name` from the media catalog, or None."""
    if not catalog:
        return None
    try:
        found, _ = resolve_media(catalog, [name])
        for k, v in found.items():
            if k.lower() == name.lower():
                return load_palette(v)
    except Exception:
        pass
    return None


def unit_palette(folder, catalog):
    """Unit/terrain palette: a loose palette in `folder`, then unittem.pal from the
    catalog, else grayscale. Never raises."""
    for name in PAL_NAMES:
        p = os.path.join(folder, name) if folder else ""
        if p and os.path.isfile(p):
            try:
                return load_palette(open(p, "rb").read())
            except Exception:
                pass
    return _from_catalog(catalog, "unittem.pal") or grayscale_palette()


def cameo_palette(folder, catalog):
    """Dedicated cameo palette (cameo.pal) for SHP cameos — loose first, then the
    catalog. SHP cameos are NOT house-coloured. If cameo.pal can't be found, fall
    back to GRAYSCALE rather than the unit/terrain palette — a cameo rendered with
    the unit palette comes out as colour noise, whereas grayscale is at least a
    readable silhouette."""
    p = os.path.join(folder, "cameo.pal") if folder else ""
    if p and os.path.isfile(p):
        try:
            return load_palette(open(p, "rb").read())
        except Exception:
            pass
    return _from_catalog(catalog, "cameo.pal") or grayscale_palette()


def playable_bag_sounds(assets, catalog):
    """Names of audio assets that live in an audio .bag (decodable/playable), in
    order, de-duplicated. Only bag entries are playable samples; status labels
    aren't reliable here (a bag-found sound is marked 'present', not 'sound'), so
    we key purely off the catalog location."""
    out, seen = [], set()
    for a in assets:
        loc = catalog.get(a.name.upper()) or catalog.get(mix_id(a.name))
        if loc is not None and loc[0] == "bag" and a.name.upper() not in seen:
            seen.add(a.name.upper())
            out.append(a.name)
    return out


def find_audio_player():
    """Best-effort system WAV player (QtMultimedia isn't bundled). Returns an
    identifier used by play_wav_file, or None."""
    if sys.platform == "win32":
        return "winsound"
    if sys.platform == "darwin":
        return shutil.which("afplay")
    for cmd in ("paplay", "aplay", "ffplay", "mpv", "cvlc"):
        p = shutil.which(cmd)
        if p:
            return p
    return None


def play_wav_file(player, path):
    """Play a WAV file asynchronously via the detected player."""
    if player == "winsound":
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        return
    args = [player, path]
    if player.endswith("ffplay"):
        args = [player, "-nodisp", "-autoexit", path]
    elif player.endswith("cvlc"):
        args = [player, "--play-and-exit", path]
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
