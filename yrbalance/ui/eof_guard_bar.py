"""
A dismissible bar for INIs the tool READ that end without a comment line.

The engine never reads an INI's last line, so a file ending on a real key has
that key silently ignored in game. The tool guards every INI it writes; for
files it only reads (the loaded mod, an old mod being ported from) it asks
first — one click appends ';' to each (backup kept), nothing else changes.
"""

import os
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from ..engine import scan_eof_guards, add_eof_guard, make_backup


class _Relay(QObject):
    """Lives on the UI thread; a worker's emit is queued back to it."""
    done = Signal(object)


class _Scan:
    """A background scan on a DAEMON Python thread. A QThread here aborted
    the process ("QThread: Destroyed while thread is still running") whenever
    a dialog closed before its scan finished; a daemon thread can't block or
    abort shutdown, and a result for a bar that is already gone is dropped."""

    def __init__(self, list_files, relay):
        self._list_files = list_files
        self._relay = relay
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def wait(self, msecs=None):
        self._thread.join(None if msecs is None else msecs / 1000)
        return not self._thread.is_alive()

    def _run(self):
        try:
            result = scan_eof_guards(self._list_files())
        except Exception:
            result = []
        try:
            self._relay.done.emit((self, result))
        except RuntimeError:
            pass                         # the bar (and its relay) were deleted


class EofGuardBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "EofGuardBar { background: #fff3cd; border: 1px solid #e0c36a; "
            "border-radius: 3px; } QLabel { color: #5c4500; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        self.label = QLabel()
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        self.fix_btn = QPushButton("Add ';' to them (backups kept)")
        self.fix_btn.clicked.connect(self.fix)
        lay.addWidget(self.fix_btn)
        close = QPushButton("Not now")
        close.clicked.connect(self.hide)
        lay.addWidget(close)
        self.missing = []
        self._scan = None
        self._pending = {}
        self._relay = _Relay(self)
        self._relay.done.connect(self._scan_done)
        self.hide()

    def check_later(self, list_files, who, note=""):
        """check() off the UI thread: `list_files()` (finding a mod's include
        tree re-reads every file — seconds on a big mod) and the scan run in
        the background; the bar appears when they finish."""
        scan = _Scan(list_files, self._relay)
        self._scan = scan
        self._pending[scan] = (who, note)
        scan.start()
        return scan

    def _scan_done(self, payload):
        scan, missing = payload
        who, note = self._pending.pop(scan, (None, None))
        if scan is self._scan and who is not None:  # a superseded scan is ignored
            self._show(missing, who, note)

    def check(self, paths, who, note=""):
        """Scan `paths` (files belonging to `who`, e.g. "the loaded mod");
        show the bar if any of them ends on a line the game never reads.
        `note` is appended to the message."""
        return self._show(scan_eof_guards(paths), who, note)

    def _show(self, missing, who, note):
        self.missing = missing
        if not self.missing:
            self.hide()
            return []
        shown = self.missing[:4]
        items = "; ".join(
            f"<b>{os.path.basename(p)}</b> ends on <code>{_esc(last) or '(empty)'}</code>"
            for p, last in shown)
        more = (f" … and {len(self.missing) - len(shown)} more"
                if len(self.missing) > len(shown) else "")
        self.label.setText(
            f"⚠ {len(self.missing)} INI file(s) of {who} end without a comment "
            f"line, so the game never reads their last line: {items}{more}."
            + (f" {note}" if note else ""))
        self.label.setToolTip("\n".join(p for p, _l in self.missing))
        self.show()
        return self.missing

    def fix(self):
        done, failed = 0, []
        for p, _last in self.missing:
            try:
                make_backup(p, None, keep=10)
                if add_eof_guard(p):
                    done += 1
            except OSError as e:
                failed.append(f"{os.path.basename(p)} ({e.strerror or e})")
        if failed:
            self.label.setText(f"Added ';' to {done} file(s); could not write "
                               f"{', '.join(failed)}.")
            self.fix_btn.hide()
        else:
            self.missing = []
            self.hide()
        return done


def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
