"""
Timestamped backups for in-place editing.

Before the tool overwrites a rules file, it copies the current file into a backup
folder with a timestamped name, and prunes to the newest `keep` backups for that
file. Backup folder and retention count are caller-supplied (UI settings).
"""

import os
import re
import shutil
import datetime


def default_backup_dir(src_path):
    """Default backups location: a 'yrbt_backups' folder next to the file."""
    return os.path.join(os.path.dirname(os.path.abspath(src_path)), "yrbt_backups")


def make_backup(src_path, backup_dir=None, keep=10, _stamp=None):
    """
    Copy src_path into backup_dir with a timestamped name; prune to `keep` newest
    backups of the same base file. Returns the backup path. keep<=0 disables pruning.
    """
    if not os.path.isfile(src_path):
        raise FileNotFoundError(src_path)
    stem, ext = os.path.splitext(os.path.basename(src_path))
    backup_dir = backup_dir or default_backup_dir(src_path)
    os.makedirs(backup_dir, exist_ok=True)

    stamp = _stamp or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(backup_dir, f"{stem}.{stamp}{ext}.bak")
    i = 1
    while os.path.exists(dest):
        dest = os.path.join(backup_dir, f"{stem}.{stamp}-{i}{ext}.bak")
        i += 1
    shutil.copy2(src_path, dest)

    _prune(backup_dir, stem, ext, keep)
    return dest


def list_backups(backup_dir, src_path):
    """Return backup paths for src_path's base name, newest first."""
    stem, ext = os.path.splitext(os.path.basename(src_path))
    return _matches(backup_dir, stem, ext)


def _matches(backup_dir, stem, ext):
    if not os.path.isdir(backup_dir):
        return []
    pat = re.compile(re.escape(stem) + r"\..*" + re.escape(ext) + r"\.bak$")
    files = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir)
             if pat.match(f)]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files


def _prune(backup_dir, stem, ext, keep):
    if not keep or keep <= 0:
        return
    for old in _matches(backup_dir, stem, ext)[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass
