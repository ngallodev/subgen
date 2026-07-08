# Startup Scan Next Improvements Plan

## Purpose

Track the next improvement slice after the current extracted package and port-prep docs landed.

The focus now is narrower than the earlier migration notes:

- keep the stock-facing seam in `subgen.py` thin and explicit
- keep backend selection and diagnostic output quiet by default
- preserve the current startup-scan tests that prove the seam still works

## Current Live State

- The extracted package is now the full `subgen_startup_scan/` source set:
  - `__init__.py`
  - `adapter.py`
  - `association.py`
  - `backend.py`
  - `benchmarks.py`
  - `classifier.py`
  - `config.py`
  - `db.py`
  - `dependencies.py`
  - `entrypoint.py`
  - `facade.py`
  - `forced.py`
  - `inventory.py`
  - `inventory_cache.py`
  - `legacy.py`
  - `policy.py`
  - `runtime.py`
  - `schema.py`
  - `service.py`
  - `signatures.py`
  - `state.py`
  - `traversal.py`
  - `wiring.py`
- The remaining stock-facing seam in `subgen.py` is the dispatcher plus its startup-scan wrappers:
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
- Diagnostic knobs are env-gated:
  - `STARTUP_SCAN_BACKEND`
  - `STARTUP_SCAN_BENCHMARK_LOGGING`
  - `STARTUP_SCAN_BENCHMARK_LOG_PATH`
  - `STARTUP_SCAN_PLANNER_TRACE_LOGGING`

## Next Slice

### 1. Keep the seam stock-safe

Goal:

- preserve legacy default behavior in stock
- avoid growing more wrappers in `subgen.py`
- only keep wrappers that are still needed as the stock compatibility boundary

Primary files:

- `subgen.py`
- `subgen_startup_scan/facade.py`
- `subgen_startup_scan/runtime.py`
- `subgen_startup_scan/wiring.py`
- `subgen_startup_scan/dependencies.py`
- `subgen_startup_scan/legacy.py`
- `tests/test_startup_scan_backend_selection.py`
- `tests/test_startup_scan_cache.py`

Validation:

- backend selection still resolves `legacy` and `persistent`
- cache reuse still works
- forced startup path still works
- no queue-semantic drift

### 2. Keep benchmark output opt-in

Goal:

- ensure benchmark and planner-trace output stay quiet unless explicitly enabled
- keep current structured rows compatible when logging is turned on

Primary files:

- `subgen_startup_scan/config.py`
- `subgen_startup_scan/benchmarks.py`
- `subgen_startup_scan/classifier.py`
- `README.md`
- `subgen.xml`
- `tests/test_startup_scan_planner_trace.py`

Validation:

- `STARTUP_SCAN_BENCHMARK_LOGGING=0` stays silent
- `STARTUP_SCAN_PLANNER_TRACE_LOGGING=0` stays silent
- enabling either flag still emits the existing structured rows

### 3. Improve measurement fidelity only if it stays additive

Goal:

- if the next bottleneck work needs more detail, add opt-in rows instead of changing existing ones

Primary files:

- `subgen_startup_scan/classifier.py`
- `subgen_startup_scan/benchmarks.py`
- `tests/test_startup_scan_planner_trace.py`
- `startup_scan_pr_benchmarks.md`

Validation:

- new benchmark rows remain additive
- existing row names and fields stay compatible
- instrumentation cost stays near-zero when disabled

## Files That Should Not Be Dragged Into The Next Slice

- Docker files
- local caches
- model artifacts
- benchmark JSONL or DB files
- unrelated runtime or compose changes

## Open Questions

- Whether the next code change should collapse more of the `subgen.py` wrappers before any further porting.
- Whether `startup_scan_pr_benchmarks.md` should become the canonical place for before/after measurement notes or stay as a local working log.
- Whether any additional stock tests are needed before copying the package into `subgen-stock`.
