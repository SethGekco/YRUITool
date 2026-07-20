#!/usr/bin/env bash
# Run the whole engine test suite. Pass a rulesmd.ini path (some tests need one).
#   ./tests/run_all.sh "/path/to/rulesmd.ini"
set -u
R="${1:-}"
here="$(cd "$(dirname "$0")" && pwd)"
pass=0; fail=0

need_rules=(smoke_engine test_pass1 test_techlevel test_selectors test_sections test_script)
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

echo
echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ]
