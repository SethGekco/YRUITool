"""PyInstaller entry point — a top-level launcher with absolute imports."""

import sys

from yrbalance.ui import main

if __name__ == "__main__":
    sys.exit(main())
