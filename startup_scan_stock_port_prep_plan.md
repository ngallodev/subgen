# Startup Scan Stock Port Prep Plan

## Purpose

Prepare the current startup-scan fork for a future replay into `C:\Users\Nathan\source\subgen-stock` without losing the stock default path or dragging in local-only artifacts.

This doc is current-state, file-by-file, and meant to capture the exact copy-in surface before any port.

## Current State

- The extracted startup-scan package is now a real package, not a placeholder list.
- `subgen.py` still owns the stock-facing seam and delegates startup behavior through thin wrappers.
- Benchmark and planner-trace output are opt-in via env vars.
- The repository already has dedicated startup-scan tests, so the replay surface should be defined around code plus validation files, not just code.

## Exact Additive Package Files

Copy these files into `subgen-stock` as the additive `subgen_startup_scan/` package payload:

- `subgen_startup_scan/__init__.py`
- `subgen_startup_scan/adapter.py`
- `subgen_startup_scan/association.py`
- `subgen_startup_scan/backend.py`
- `subgen_startup_scan/benchmarks.py`
- `subgen_startup_scan/classifier.py`
- `subgen_startup_scan/config.py`
- `subgen_startup_scan/db.py`
- `subgen_startup_scan/dependencies.py`
- `subgen_startup_scan/entrypoint.py`
- `subgen_startup_scan/facade.py`
- `subgen_startup_scan/forced.py`
- `subgen_startup_scan/inventory.py`
- `subgen_startup_scan/inventory_cache.py`
- `subgen_startup_scan/legacy.py`
- `subgen_startup_scan/policy.py`
- `subgen_startup_scan/runtime.py`
- `subgen_startup_scan/schema.py`
- `subgen_startup_scan/service.py`
- `subgen_startup_scan/signatures.py`
- `subgen_startup_scan/state.py`
- `subgen_startup_scan/traversal.py`
- `subgen_startup_scan/wiring.py`

Do not include `__pycache__`, benchmark JSONL, DB state, or any other local artifact in the replay set.

## Remaining Stock-Facing Seam In `subgen.py`

The stock-facing boundary is still concentrated in `subgen.py`. The thin seam that matters for porting is:

- `transcribe_existing()`
- `startup_scan_initialize()`
- `collect_startup_inventory()`
- `compute_subtitle_signature()`
- `_startup_scan_process_record()`
- `_startup_scan_refresh_processed_file()`
- `startup_scan_existing()`
- `_legacy_startup_scan_existing()`
- `_build_startup_scan_dependencies()`
- `_get_startup_scan_backend()`

These are the functions to keep stable, shrink, or preserve as explicit seam points. Everything else should stay inside `subgen_startup_scan/` unless stock compatibility still requires a wrapper.

## Stock Patch Files

These are the in-place files that should be patched in `subgen-stock` rather than copied wholesale:

- `subgen.py`
- `README.md`
- `subgen.xml`
- `tests/conftest.py`
- `tests/test_queue.py`
- `tests/test_integration.py`
- `tests/test_endpoints.py`
- `tests/test_bug_fixes.py`
- `tests/test_startup_scan_backend_selection.py`
- `tests/test_startup_scan_cache.py`
- `tests/test_startup_scan_planner_trace.py`

The doc goal here is to keep the runtime patch small and make the validation surface explicit.

## Benchmark-Only Optional Files And Flags

These are optional for measurement and should stay additive or opt-in only:

- `startup_scan_pr_benchmarks.md`
- `STARTUP_SCAN_BENCHMARK_LOGGING`
- `STARTUP_SCAN_BENCHMARK_LOG_PATH`
- `STARTUP_SCAN_PLANNER_TRACE_LOGGING`

The benchmark file is for reporting and comparison only. The env vars should remain quiet by default.

## Port Strategy

1. Copy the full `subgen_startup_scan/` package into `subgen-stock`.
2. Patch stock `subgen.py` so it keeps the thin seam above and still defaults to legacy behavior.
3. Patch stock docs and config to describe the env vars explicitly.
4. Keep benchmark and planner-trace output opt-in.
5. Port the startup-scan tests that validate backend selection, cache reuse, and trace gating.

## File-By-File Notes

- `subgen.py` keeps the runtime dispatch seam only.
- `subgen_startup_scan/backend.py` owns backend selection.
- `subgen_startup_scan/config.py` owns env parsing for backend and diagnostic toggles.
- `subgen_startup_scan/service.py` owns startup orchestration.
- `subgen_startup_scan/classifier.py` owns planning and decision logic.
- `subgen_startup_scan/inventory.py` owns inventory collection entrypoints.
- `subgen_startup_scan/traversal.py` owns directory walking.
- `subgen_startup_scan/inventory_cache.py` owns cache reuse.
- `subgen_startup_scan/association.py` owns subtitle/media matching helpers.
- `subgen_startup_scan/signatures.py` owns policy and inventory signatures.
- `subgen_startup_scan/db.py` owns the SQLite state layer.
- `subgen_startup_scan/schema.py` owns schema and migrations.
- `subgen_startup_scan/benchmarks.py` owns structured benchmark writes.
- `subgen_startup_scan/legacy.py` preserves legacy recursion behavior.
- `subgen_startup_scan/forced.py` handles forced-language startup dispatch.
- `subgen_startup_scan/runtime.py` and `subgen_startup_scan/wiring.py` stay package-internal unless a stock shim is still needed.

## Remaining Ambiguity Before Porting

- Whether `subgen-stock` should receive the current startup-scan test files in the same change or as a follow-up validation patch.
- Whether `startup_scan_pr_benchmarks.md` should stay in the fork only or be copied into stock as a reference note.
- Whether any of the remaining thin wrappers in `subgen.py` can be collapsed further without changing the stock seam shape.
