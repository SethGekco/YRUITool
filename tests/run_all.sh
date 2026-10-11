#!/usr/bin/env bash
# Run the whole engine test suite. Pass a rulesmd.ini path (some tests need one).
#   ./tests/run_all.sh "/path/to/rulesmd.ini"
set -u
R="${1:-}"
here="$(cd "$(dirname "$0")" && pwd)"
pass=0; fail=0

need_rules=(smoke_engine test_pass1 test_techlevel test_selectors test_sections test_script test_generator test_pipeline test_filters test_history test_garrison)
no_rules=(test_ticket test_formula test_backup test_vanilla_ref test_retrieval test_lint test_portmod test_obtain test_mix test_encyclopedia test_includes test_bookmarks_history test_tags test_portai test_ailib test_library test_images test_audio test_vxl test_vpl test_aimd)
# test_eva needs a rules path (for rules_summary) but no Ollama/Qt.
need_rules+=(test_eva test_wavegen_analysis test_wavegen_taskforce test_wavegen_wave test_wavegen_roles test_wavegen_specialty test_wavegen_strategic test_wavegen_strategic_full test_wavegen_everything)

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

# PortDialog smoke (builds its own fixtures; needs PySide6 + offscreen Qt).
printf '%-18s ' "test_port_ui"
if [ ! -x "$venv_py" ]; then echo "SKIP (no .venv / PySide6)";
elif QT_QPA_PLATFORM=offscreen "$venv_py" "$here/test_port_ui.py" >/dev/null 2>&1; then echo OK; pass=$((pass+1)); else echo FAIL; fail=$((fail+1)); fi

# Porter per-file include opt-in smoke (PySide6 + offscreen Qt).
printf '%-18s ' "test_port_inc_ui"
if [ ! -x "$venv_py" ]; then echo "SKIP (no .venv / PySide6)";
elif QT_QPA_PLATFORM=offscreen "$venv_py" "$here/test_port_include_ui.py" >/dev/null 2>&1; then echo OK; pass=$((pass+1)); else echo FAIL; fail=$((fail+1)); fi

# MIX browser smoke (PySide6 + offscreen Qt).
printf '%-18s ' "test_mix_ui"
if [ ! -x "$venv_py" ]; then echo "SKIP (no .venv / PySide6)";
elif QT_QPA_PLATFORM=offscreen "$venv_py" "$here/test_mix_ui.py" >/dev/null 2>&1; then echo OK; pass=$((pass+1)); else echo FAIL; fail=$((fail+1)); fi

# Encyclopedia UI smoke (PySide6 + offscreen Qt).
printf '%-18s ' "test_encyclopedia_ui"
if [ ! -x "$venv_py" ]; then echo "SKIP (no .venv / PySide6)";
elif QT_QPA_PLATFORM=offscreen "$venv_py" "$here/test_encyclopedia_ui.py" >/dev/null 2>&1; then echo OK; pass=$((pass+1)); else echo FAIL; fail=$((fail+1)); fi

# AI library UI smoke (PySide6 + offscreen Qt).
printf '%-18s ' "test_ailib_ui"
if [ ! -x "$venv_py" ]; then echo "SKIP (no .venv / PySide6)";
elif QT_QPA_PLATFORM=offscreen "$venv_py" "$here/test_ailib_ui.py" >/dev/null 2>&1; then echo OK; pass=$((pass+1)); else echo FAIL; fail=$((fail+1)); fi

# Library tab UI smoke (PySide6 + offscreen Qt).
printf '%-18s ' "test_library_ui"
if [ ! -x "$venv_py" ]; then echo "SKIP (no .venv / PySide6)";
elif QT_QPA_PLATFORM=offscreen "$venv_py" "$here/test_library_ui.py" >/dev/null 2>&1; then echo OK; pass=$((pass+1)); else echo FAIL; fail=$((fail+1)); fi

# AI (aimd) tab UI smoke (PySide6 + offscreen Qt).
printf '%-18s ' "test_aimd_ui"
if [ ! -x "$venv_py" ]; then echo "SKIP (no .venv / PySide6)";
elif QT_QPA_PLATFORM=offscreen "$venv_py" "$here/test_aimd_ui.py" >/dev/null 2>&1; then echo OK; pass=$((pass+1)); else echo FAIL; fail=$((fail+1)); fi

# TaskForce generator dialog smoke (needs rules path + PySide6 + offscreen).
printf '%-18s ' "test_wavegen_tf_ui"
if [ ! -x "$venv_py" ] || [ -z "$R" ]; then echo "SKIP";
elif QT_QPA_PLATFORM=offscreen "$venv_py" "$here/test_wavegen_taskforce_ui.py" "$R" >/dev/null 2>&1; then echo OK; pass=$((pass+1)); else echo FAIL; fail=$((fail+1)); fi

# Wave generator dialog smoke (needs rules path + PySide6 + offscreen).
printf '%-18s ' "test_wavegen_wv_ui"
if [ ! -x "$venv_py" ] || [ -z "$R" ]; then echo "SKIP";
elif QT_QPA_PLATFORM=offscreen "$venv_py" "$here/test_wavegen_wave_ui.py" "$R" >/dev/null 2>&1; then echo OK; pass=$((pass+1)); else echo FAIL; fail=$((fail+1)); fi

# Specialty wave dialog smoke (needs rules path + PySide6 + offscreen).
printf '%-18s ' "test_wavegen_sp_ui"
if [ ! -x "$venv_py" ] || [ -z "$R" ]; then echo "SKIP";
elif QT_QPA_PLATFORM=offscreen "$venv_py" "$here/test_wavegen_specialty_ui.py" "$R" >/dev/null 2>&1; then echo OK; pass=$((pass+1)); else echo FAIL; fail=$((fail+1)); fi

# Strategic-trigger dialog smoke (needs rules path + PySide6 + offscreen).
printf '%-18s ' "test_wavegen_st_ui"
if [ ! -x "$venv_py" ] || [ -z "$R" ]; then echo "SKIP";
elif QT_QPA_PLATFORM=offscreen "$venv_py" "$here/test_wavegen_strategic_ui.py" "$R" >/dev/null 2>&1; then echo OK; pass=$((pass+1)); else echo FAIL; fail=$((fail+1)); fi

# Generate-everything dialog smoke (needs rules path + PySide6 + offscreen).
printf '%-18s ' "test_wavegen_all_ui"
if [ ! -x "$venv_py" ] || [ -z "$R" ]; then echo "SKIP";
elif QT_QPA_PLATFORM=offscreen "$venv_py" "$here/test_wavegen_everything_ui.py" "$R" >/dev/null 2>&1; then echo OK; pass=$((pass+1)); else echo FAIL; fail=$((fail+1)); fi

echo
echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ]
