#!/usr/bin/env bash
# Build a standalone Linux executable with PyInstaller.
#   ./build_exe.sh            # onefile, console visible (good for trial runs)
#   ./build_exe.sh --windowed # onefile, no console window
#
# Output: dist/yrbalancetool  (a single self-contained binary)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "No .venv found. Create it first:  python3 -m venv .venv && . .venv/bin/activate && pip install -e ." >&2
    exit 1
fi
# shellcheck disable=SC1091
. .venv/bin/activate

python -m pip install --quiet --upgrade pyinstaller

WINDOWED=""
[ "${1:-}" = "--windowed" ] && WINDOWED="--windowed"

pyinstaller --noconfirm --clean --onefile $WINDOWED \
    --name yrbalancetool \
    --paths . \
    --add-data "presets/examples:presets/examples" \
    --add-data "knowledge:knowledge" \
    --add-data "docs:docs" \
    packaging/launcher.py

echo
echo "Built: $(pwd)/dist/yrbalancetool"
echo "Run it with:  ./dist/yrbalancetool"
