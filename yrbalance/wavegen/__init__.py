"""
Vendored AITriggerTypeExt / AI Wave Generator (see VENDOR_SNAPSHOT.md), as a proper
package: intra-module imports are package-relative so it bundles in the frozen build.
Clean, headless APIs live in analysis.py (and later generate.py); the raw modules are
being library-ized per WAVEGEN_MERGE_PLAN.md (removing input()/file-move).
"""
