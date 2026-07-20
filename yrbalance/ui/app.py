"""Application entry point."""

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def main(argv=None):
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("YRBalanceTool")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
