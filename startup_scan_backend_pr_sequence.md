# Startup Scan Backend PR Sequence

## Purpose

This document turns the backend extraction plan into a concrete pull request sequence with:

- exact files expected to change in each PR
- scope boundaries
- validation steps
- benchmark checkpoints
- rebase and `..\subgen-stock` integration considerations

It is intentionally execution-focused. The architectural rationale lives in [`startup_scan_backend_extraction_plan.md`](C:\Users\Nathan\source\subgen\startup_scan_backend_extraction_plan.md).

## Guiding Rules

Each PR should:

- preserve runtime behavior unless the PR explicitly targets a performance optimization
- keep benchmark and timing output env-gated
- avoid mixing backend extraction with unrelated cleanup
- minimize changes to stock-shaped code in [`subgen.py`](C:\Users\Nathan\source\subgen\subgen.py)
- make future replay into [`C:\Users\Nathan\source\subgen-stock`](C:\Users\Nathan\source\subgen-stock) mostly additive

Each PR should also answer:

- what new seam or module boundary exists after this PR
- whether the `legacy` path stayed intact
- whether warm and broad-start benchmarks regressed

## PR 0: Baseline and Comparison Artifacts

### Goal

Freeze the current state of the benchmark evidence and stock comparison artifacts before refactoring begins.

### Files touched

- [`startup_scan_backend_extraction_plan.md`](C:\Users\Nathan\source\subgen\startup_scan_backend_extraction_plan.md)
- [`startup_scan_backend_pr_sequence.md`](C:\Users\Nathan\source\subgen\startup_scan_backend_pr_sequence.md)
- [`output/jupyter-notebook/startup-scan-benchmark-analysis.ipynb`](C:\Users\Nathan\source\subgen\output\jupyter-notebook\startup-scan-benchmark-analysis.ipynb)
- optionally a small benchmark note if you want a checked-in summary:
  - `startup_scan_benchmark_baseline.md`

### Runtime code changes

- none required

### Validation

- notebook still opens and code cells execute top-to-bottom in plain Python validation
- baseline benchmark file path documented:
  - [`C:\Users\Nathan\AppData\Roaming\subgen\state\startup_scan_benchmarks.jsonl`](C:\Users\Nathan\AppData\Roaming\subgen\state\startup_scan_benchmarks.jsonl)

### Benchmark checkpoint

Record at minimum:

- warm cached startup numbers
- broad `/movies` startup numbers
- stock 5-file one-shot baseline
- current 5-file one-shot baseline

### `subgen-stock` relevance

- this PR defines the before-state we want to preserve or improve when integrating into stock later

## PR 1: Introduce the Backend Seam

### Goal

Add a runtime backend selector and route startup-scan entrypoints through a backend interface, without moving major logic yet.

### Files touched

- [`subgen.py`](C:\Users\Nathan\source\subgen\subgen.py)
- `subgen_startup_scan/__init__.py`
- `subgen_startup_scan/backend.py`
- `subgen_startup_scan/config.py`
- `subgen_startup_scan/dependencies.py`
- [`README.md`](C:\Users\Nathan\source\subgen\README.md)
- optionally:
  - [`docker-compose.yml`](C:\Users\Nathan\source\subgen\docker-compose.yml)
  - [`subgen.xml`](C:\Users\Nathan\source\subgen\subgen.xml)
- tests:
  - `tests/test_startup_scan_backend_selection.py`

### What changes here

- add `STARTUP_SCAN_BACKEND=legacy|persistent`
- create a backend protocol/factory
- create a dependency object built by `subgen.py`
- replace direct calls in `subgen.py` with:
  - `backend.initialize()`
  - `backend.startup_scan_existing(...)`
  - `backend.refresh_processed_file(...)`
- persistent backend may still delegate to existing functions in `subgen.py`
- legacy backend may temporarily wrap the existing/original flow or no-op selected hooks

### What should not change here

- no major function moves yet
- no schema changes
- no benchmark schema changes
- no performance tuning yet

### Validation

- app starts with `STARTUP_SCAN_BACKEND=persistent`
- app starts with `STARTUP_SCAN_BACKEND=legacy`
- unknown backend value fails clearly
- benchmark logging remains disabled unless explicitly enabled

### Benchmark checkpoint

- warm cached startup before and after PR should be materially unchanged
- broad-start numbers may vary slightly, but no structural regression should appear

### `subgen-stock` replay value

- this is the first PR that should be easy to replay into stock later

## PR 2: Move Schema, DB, and Benchmark Utilities Into the Package

### Goal

Move `scan_index.py` functionality into the backend package and leave a temporary compatibility shim if needed.

### Files touched

- [`scan_index.py`](C:\Users\Nathan\source\subgen\scan_index.py)
- `subgen_startup_scan/schema.py`
- `subgen_startup_scan/db.py`
- `subgen_startup_scan/benchmarks.py`
- `subgen_startup_scan/types.py`
- [`subgen.py`](C:\Users\Nathan\source\subgen\subgen.py)
- tests:
  - [`tests/test_startup_scan_cache.py`](C:\Users\Nathan\source\subgen\tests\test_startup_scan_cache.py)
  - optionally split:
    - `tests/startup_scan/test_db.py`
    - `tests/startup_scan/test_benchmarks.py`

### What changes here

- re-home schema version and migrations
- re-home `StartupScanDB`
- re-home `BenchmarkLogger` and `benchmark_step`
- update imports in `subgen.py`
- optionally keep `scan_index.py` as a re-export shim for one PR to reduce blast radius

### What should not change here

- do not move inventory logic yet
- do not move classifier logic yet
- do not change benchmark event names

### Validation

- schema migration tests still pass
- benchmark logger behavior is unchanged
- env-gated benchmark writing still works

### Benchmark checkpoint

- no meaningful warm-start or broad-start behavior change expected

### `subgen-stock` replay value

- this keeps utility code additive and easier to transplant later

## PR 3: Move Inventory and Subtitle/Media Linking Logic

### Goal

Extract directory walk, relevant-file filtering, directory cache validation, and subtitle/media linking into dedicated modules.

### Files touched

- [`subgen.py`](C:\Users\Nathan\source\subgen\subgen.py)
- `subgen_startup_scan/inventory.py`
- `subgen_startup_scan/signatures.py`
- `subgen_startup_scan/types.py`
- tests:
  - [`tests/test_startup_scan_cache.py`](C:\Users\Nathan\source\subgen\tests\test_startup_scan_cache.py)
  - or split into:
    - `tests/startup_scan/test_inventory.py`
    - `tests/startup_scan/test_signatures.py`

### What changes here

- move:
  - `StartupInventory`
  - relevant inventory file filtering
  - directory cache hit/miss logic
  - subtitle/media enrichment
  - inventory signature helpers
- keep `subgen.py` calling package functions instead of in-file helpers

### What should not change here

- no planner logic extraction yet
- no cold-start optimization yet

### Validation

- cache-hit behavior remains the same
- irrelevant-file signature test still passes
- subtitle/media linking tests still pass

### Benchmark checkpoint

- compare:
  - inventory duration
  - directory cache hit/miss counters
  - total startup on warm cache

### `subgen-stock` replay value

- this is the beginning of the main logic leaving `subgen.py`

## PR 4: Move the Persistent Startup-Scan Orchestration

### Goal

Move the persistent backend’s top-level orchestration into `service.py` while still preserving existing behavior.

### Files touched

- [`subgen.py`](C:\Users\Nathan\source\subgen\subgen.py)
- `subgen_startup_scan/service.py`
- `subgen_startup_scan/backend.py`
- `subgen_startup_scan/dependencies.py`
- tests:
  - `tests/startup_scan/test_service.py`
  - backend dispatch tests

### What changes here

- move the persistent implementation of:
  - `startup_scan_existing`
  - monitor setup for persistent mode
  - inventory/classify/apply sequencing
- `subgen.py` becomes a dispatch caller, not the home of orchestration

### What should not change here

- do not optimize planner behavior yet
- do not add optional CLI yet

### Validation

- persistent backend starts and completes startup scans
- legacy backend still starts
- monitor behavior still works for both modes as applicable

### Benchmark checkpoint

- warm cached startup should remain close to current values
- broad `/movies` run should still show the same basic bottleneck distribution

### `subgen-stock` replay value

- after this PR, the seam to add into stock is becoming clean and shallow

## PR 5: Move Classification, Planning, and Apply Logic

### Goal

Extract the planner/classifier path into `classifier.py`, including current benchmark aggregation.

### Files touched

- [`subgen.py`](C:\Users\Nathan\source\subgen\subgen.py)
- `subgen_startup_scan/classifier.py`
- `subgen_startup_scan/signatures.py`
- `subgen_startup_scan/dependencies.py`
- tests:
  - `tests/startup_scan/test_classifier.py`
  - benchmark breakdown tests

### What changes here

- move:
  - preflight
  - pending workload construction
  - parallel planning executor path
  - apply/update path
  - benchmark aggregation for classify/apply events

### What should not change here

- do not add new planner optimizations yet
- do not add sub-step planner instrumentation yet

### Validation

- current benchmark event names remain stable
- queue/planning semantics are unchanged
- persistent startup still produces comparable benchmark rows

### Benchmark checkpoint

- compare:
  - `startup_scan.classify_media.preflight`
  - `startup_scan.classify_media.parallel_plan`
  - `startup_scan.classify_media.apply`
  - `startup_scan.total`

### `subgen-stock` replay value

- this PR gives the clearest future integration package for the alternative startup path

## PR 6: Route Processed-File Refresh Through the Backend

### Goal

Make the worker completion hook backend-owned rather than directly coupled to persistent implementation details.

### Files touched

- [`subgen.py`](C:\Users\Nathan\source\subgen\subgen.py)
- `subgen_startup_scan/classifier.py`
- `subgen_startup_scan/service.py`
- `subgen_startup_scan/backend.py`
- tests:
  - `tests/startup_scan/test_refresh_processed_file.py`

### What changes here

- move `_startup_scan_refresh_processed_file` behind backend dispatch
- define legacy backend behavior for this hook
- remove direct persistent coupling from the worker path

### Validation

- persistent backend still updates DB state for processed files
- legacy backend does not break worker execution

### Benchmark checkpoint

- no startup timing change expected
- verify cache freshness behavior on files newly processed after startup

### `subgen-stock` replay value

- this keeps the long-lived runtime path clean for future stock integration

## PR 7: Introduce Planner Micro-Instrumentation

### Goal

Add env-gated benchmark detail inside `prepare_media_queue_job()` and closely related sub-steps so cold-start planner cost can be attributed precisely.

### Files touched

- [`subgen.py`](C:\Users\Nathan\source\subgen\subgen.py)
- `subgen_startup_scan/classifier.py`
- `subgen_startup_scan/benchmarks.py`
- `subgen_startup_scan/config.py`
- tests:
  - `tests/startup_scan/test_planner_instrumentation.py`
- docs:
  - [`README.md`](C:\Users\Nathan\source\subgen\README.md)
  - [`subgen.xml`](C:\Users\Nathan\source\subgen\subgen.xml) if exposed there

### Suggested env vars

- `STARTUP_SCAN_BENCHMARK_LOGGING`
- `STARTUP_SCAN_PLANNER_TRACE_LOGGING`

### What changes here

- time sub-steps such as:
  - audio track probe
  - language resolution
  - internal subtitle check
  - external subtitle search
  - path/subtitle naming checks
- keep emission opt-in and structured

### Validation

- default startup remains quiet
- enabling trace logging produces additional structured timing output

### Benchmark checkpoint

- rerun the notebook and verify planner sub-step visibility appears

### `subgen-stock` replay value

- this can be integrated into stock later without making default logs noisy

## PR 8: Add Cold-Start Skip Fast Paths

### Goal

Use the benchmark findings to reduce expensive uncached planning work for files that are ultimately skipped.

### Files touched

- [`subgen.py`](C:\Users\Nathan\source\subgen\subgen.py)
- `subgen_startup_scan/classifier.py`
- `subgen_startup_scan/inventory.py`
- `subgen_startup_scan/signatures.py`
- `subgen_startup_scan/types.py`
- tests:
  - `tests/startup_scan/test_fast_skip_paths.py`
  - updates to classifier and inventory tests

### Candidate optimizations

- trust subtitle inventory more aggressively before probing media
- avoid repeated recursive subtitle searches for uncached files
- add quick skip decisions when sidecar subtitle evidence is sufficient
- reduce redundant audio/subtitle metadata probing

### What changes here

- this is the first PR in the sequence that should intentionally improve broad cold-start performance

### Validation

- correctness of skip decisions is preserved
- no false-positive skips for files that should queue

### Benchmark checkpoint

- broad `/movies` run is the key comparison
- specifically compare:
  - `pending_count`
  - `parallel_plan` duration
  - `plan_avg_ms`
  - `queue_count` vs `skip_count`

### `subgen-stock` replay value

- this PR contains the performance logic most worth porting once the backend seam exists in stock

## PR 9: Isolate Legacy Mode More Completely

### Goal

Make `legacy` mode intentionally close to stock behavior and reduce persistent-backend bleed-through.

### Files touched

- [`subgen.py`](C:\Users\Nathan\source\subgen\subgen.py)
- `subgen_startup_scan/legacy_adapter.py`
- `subgen_startup_scan/backend.py`
- tests:
  - `tests/startup_scan/test_legacy_backend.py`

### What changes here

- tighten legacy adapter ownership
- minimize persistent-only imports or config assumptions in legacy mode
- improve stock-comparison clarity

### Validation

- stock-style startup behavior remains selectable
- persistent state is not accidentally required for legacy mode

### Benchmark checkpoint

- legacy 5-file one-shot baseline can be repeated against the same sample for sanity

### `subgen-stock` replay value

- after this PR, porting into stock should mostly be:
  - add backend seam
  - add package
  - default to legacy

## PR 10: Cleanup and Documentation Pass

### Goal

Remove transitional helpers, reduce duplicate code, and document the backend model clearly.

### Files touched

- [`subgen.py`](C:\Users\Nathan\source\subgen\subgen.py)
- [`scan_index.py`](C:\Users\Nathan\source\subgen\scan_index.py) if shim removal is appropriate
- `subgen_startup_scan/*`
- [`README.md`](C:\Users\Nathan\source\subgen\README.md)
- [`docker-compose.yml`](C:\Users\Nathan\source\subgen\docker-compose.yml)
- [`subgen.xml`](C:\Users\Nathan\source\subgen\subgen.xml)
- benchmark docs:
  - [`startup_scan_backend_extraction_plan.md`](C:\Users\Nathan\source\subgen\startup_scan_backend_extraction_plan.md)
  - [`startup_scan_backend_pr_sequence.md`](C:\Users\Nathan\source\subgen\startup_scan_backend_pr_sequence.md)

### What changes here

- remove duplicated helper code left in `subgen.py`
- remove temporary compatibility shims if safe
- document:
  - backend selection
  - benchmark env vars
  - operational expectations

### Validation

- both backends still start
- documentation matches actual env vars and defaults

### Benchmark checkpoint

- rerun notebook analysis and refresh any checked-in summary note

### `subgen-stock` replay value

- this should leave the fork in the cleanest state for future selective porting

## PR 11: Optional Admin CLI

### Goal

Add a small diagnostics/admin CLI on top of the extracted backend package after the runtime refactor is stable.

### Files touched

- `subgen_startup_scan/tools/__init__.py`
- `subgen_startup_scan/tools/admin_cli.py`
- optionally packaging glue:
  - `pyproject.toml` if introduced later
  - a small repo-local launcher script
- tests:
  - `tests/startup_scan/test_admin_cli.py`
- docs:
  - [`README.md`](C:\Users\Nathan\source\subgen\README.md)

### Suggested first commands

- `summary`
- `cache-stats`
- `bench-tail`
- `db-counts`
- `verify-backend`

### What changes here

- provide a durable replacement for repetitive ad hoc DB and benchmark inspection commands
- keep tooling outside the main runtime path

### Validation

- CLI works without importing `subgen.py`
- CLI is read-focused first

### Benchmark checkpoint

- none required except sanity that CLI output reflects actual persistent state

### `subgen-stock` replay value

- optional
- not required for stock integration, but useful if stock later adopts the persistent backend

## Suggested Test File Migration Path

This is a practical sequence for test splitting.

### Keep initially

- [`tests/test_startup_scan_cache.py`](C:\Users\Nathan\source\subgen\tests\test_startup_scan_cache.py)

### Then gradually split into

- `tests/startup_scan/test_backend_selection.py`
- `tests/startup_scan/test_db.py`
- `tests/startup_scan/test_benchmarks.py`
- `tests/startup_scan/test_inventory.py`
- `tests/startup_scan/test_signatures.py`
- `tests/startup_scan/test_classifier.py`
- `tests/startup_scan/test_service.py`
- `tests/startup_scan/test_refresh_processed_file.py`
- `tests/startup_scan/test_legacy_backend.py`
- `tests/startup_scan/test_planner_instrumentation.py`
- `tests/startup_scan/test_fast_skip_paths.py`
- `tests/startup_scan/test_admin_cli.py`

## Replay Into `subgen-stock`

The intended replay order into [`C:\Users\Nathan\source\subgen-stock`](C:\Users\Nathan\source\subgen-stock) is:

1. PR 1 seam
2. PR 2 utilities package
3. PR 9 legacy isolation if needed for clarity
4. PRs 3 to 6 persistent backend internals
5. PR 7 instrumentation
6. PR 8 performance optimizations
7. PR 10 cleanup
8. PR 11 optional CLI only if still useful

Why this order:

- stock should get the seam before it gets the implementation
- stock should default to legacy initially
- performance logic should arrive after the alternative backend is already selectable

## Minimal Comparison Checklist Per PR

For every PR after PR 1, capture:

1. warm cached startup
2. broad `/movies` startup
3. stock 5-file one-shot
4. current 5-file one-shot
5. whether benchmark output stayed env-gated by default

## Recommended First Three PRs To Actually Start

If we want the safest execution path, start here:

1. PR 1: backend seam
2. PR 2: schema/DB/bench utilities move
3. PR 3: inventory move

Reason:

- they create clean structure without yet touching the most performance-sensitive planner logic
- they also create the minimum replayable sequence for `subgen-stock`
