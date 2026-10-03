"""Application entry point."""

import os
import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def main(argv=None):
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("YRBalanceTool")
    window = MainWindow()
    # Self-test (used to verify a frozen build): construct the window, report how
    # many bundled preset scripts were found, and exit without showing the UI.
    if os.environ.get("YRBT_SELFTEST"):
        n = window.script_panel.preset_box.count() - 1  # minus the placeholder row
        kdocs = len(window.eva_tab.knowledge.list_docs())
        print(f"selftest OK: window built, {n} bundled preset script(s), "
              f"{kdocs} knowledge doc(s)")
        return 0
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
