#!/usr/bin/env bash
# Run the whole engine test suite. Pass a rulesmd.ini path (some tests need one).
#   ./tests/run_all.sh "/path/to/rulesmd.ini"
set -u
R="${1:-}"
here="$(cd "$(dirname "$0")" && pwd)"
pass=0; fail=0

need_rules=(smoke_engine test_pass1 test_techlevel test_selectors test_sections test_script test_generator test_pipeline test_filters)
no_rules=(test_ticket test_formula)

for f in "${need_rules[@]}"; do
    printf '%-18s ' "$f"
    if [ -z "$R" ]; then echo "SKIP (no rules path)"; continue; fi
    if python3 "$here/$f.py" "$R" >/dev/null 2>&1; then echo OK; pass=$((pass+1)); else echo FAIL; fail=$((fail+1)); fi
done
for f in "${no_rules[@]}"; do
    printf '%-18s ' "$f"
    if python3 "$here/$f.py" >/dev/null 2>&1; then echo OK; pass=$((pass+1)); else echo FAIL; fail=$((fail+1)); fi
done

# UI test needs PySide6 (project venv) and offscreen Qt.
printf '%-18s ' "test_ui"
venv_py="$(dirname "$here")/.venv/bin/python"
if [ -z "$R" ]; then echo "SKIP (no rules path)";
elif [ ! -x "$venv_py" ]; then echo "SKIP (no .venv / PySide6)";
elif QT_QPA_PLATFORM=offscreen "$venv_py" "$here/test_ui.py" "$R" >/dev/null 2>&1; then echo OK; pass=$((pass+1)); else echo FAIL; fail=$((fail+1)); fi

echo
echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ]
