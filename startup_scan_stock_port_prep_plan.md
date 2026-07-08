# Startup Scan Stock Port Prep Plan

## Purpose

Prepare the current startup-scan work for an eventual replay into `C:\Users\Nathan\source\subgen-stock` such that:

- stock behavior remains available as the default path
- the current persistent/cache-backed startup scan is selectable with an env var
- benchmark and trace logging stay opt-in and quiet by default
- most custom logic lives in additive modules rather than large edits to `subgen.py`

This document is intentionally file-by-file and oriented around a future port, not a greenfield refactor.

## Research Summary

### Current fork state

- This repo already has a substantial extracted package in `subgen_startup_scan/`.
- `subgen.py` already routes startup behavior through a backend seam:
  - `_build_startup_scan_dependencies()`
  - `_get_startup_scan_backend()`
  - `transcribe_existing()`
- The latest warm startup numbers are already in the low-single-digit seconds total, with inventory around low seconds or below depending on cache state.
- Startup-scan coverage already has dedicated tests in:
  - `tests/test_startup_scan_backend_selection.py`
  - `tests/test_startup_scan_cache.py`
  - `tests/test_startup_scan_planner_trace.py`

### Current stock state

- `C:\Users\Nathan\source\subgen-stock\subgen.py` still performs the original direct recursive startup walk inside `transcribe_existing()`.
- `subgen-stock` does not currently have a `subgen_startup_scan/` package.
- `subgen-stock` currently documents `TRANSCRIBE_FOLDERS`, `MONITOR`, and `SKIP_STARTUP_SCAN`, but not backend selection or startup benchmark env vars.

### Current gap between fork and stock

The main remaining work is no longer "extract the backend" inside this fork. That is mostly done. The remaining work is:

- reduce remaining `subgen.py` coupling so the package is easier to transplant
- clearly separate stock-safe seams from persistent-backend internals
- make env/config/docs defaults explicit and quiet
- package the current implementation so it can be copied into stock with a shallow patch set

## End State

The target stock integration shape should be:

- `STARTUP_SCAN_BACKEND=legacy|persistent`
- stock default remains `legacy`
- `persistent` activates the extracted SQLite/cache-backed startup path
- benchmark output is disabled unless explicitly enabled
- planner trace output is disabled unless explicitly enabled
- stock `subgen.py` changes are limited to:
  - env parsing
  - dependency construction
  - backend dispatch
  - forced-language dispatch hook
  - processed-file refresh hook

Everything else should live under `subgen_startup_scan/`.

## Non-Goals

- Do not change stock defaults to `persistent` during first port-prep.
- Do not make benchmark logging default-on.
- Do not merge unrelated Docker/runtime tuning into the port-prep sequence.
- Do not rework transcription, queue semantics, or API routes unless required by the backend seam.
- Do not attempt to publish a standalone PyPI library in this phase.

## Current File Classification

### Files that are already close to stock-portable

- `subgen_startup_scan/backend.py`
- `subgen_startup_scan/benchmarks.py`
- `subgen_startup_scan/db.py`
- `subgen_startup_scan/schema.py`
- `subgen_startup_scan/config.py`
- `subgen_startup_scan/service.py`
- `subgen_startup_scan/classifier.py`
- `subgen_startup_scan/signatures.py`
- `subgen_startup_scan/traversal.py`
- `subgen_startup_scan/inventory_cache.py`
- `subgen_startup_scan/association.py`
- `subgen_startup_scan/legacy.py`
- `subgen_startup_scan/forced.py`
- `subgen_startup_scan/runtime.py`
- `subgen_startup_scan/wiring.py`
- `subgen_startup_scan/facade.py`
- `subgen_startup_scan/dependencies.py`
- `subgen_startup_scan/__init__.py`

These are the main additive payload for a later stock replay.

### Files that still define the stock seam

- `subgen.py`
- `README.md`
- `subgen.xml`
- `tests/conftest.py`
- `tests/test_queue.py`
- `tests/test_integration.py`
- `tests/test_endpoints.py`
- `tests/test_bug_fixes.py`

These are the files where stock-compatible entry behavior still matters.

### Files that still contain bridge wrappers and should be reviewed before replay

- `subgen.py`
- `subgen_startup_scan/adapter.py`
- `subgen_startup_scan/facade.py`
- `subgen_startup_scan/wiring.py`
- `subgen_startup_scan/runtime.py`
- `subgen_startup_scan/forced.py`

These are not necessarily wrong, but they determine whether the stock patch remains shallow or becomes a partial fork again.

## Remaining `subgen.py` Coupling To Reduce

The extracted package is in good shape, but `subgen.py` still contains a large number of startup-scan-prefixed bridge functions. Many are thin wrappers now, but they still matter for stock replay.

### Keep in `subgen.py` as stock-facing seam

- `_build_startup_scan_dependencies()`
- `_get_startup_scan_backend()`
- `transcribe_existing()`
- `_legacy_startup_scan_existing()`

These should remain thin and stable.

### Candidate wrappers to move or collapse further

- `startup_scan_initialize()`
- `_startup_scan_now()`
- `_startup_scan_get_media_row()`
- `_startup_scan_get_subtitle_rows()`
- `_startup_scan_record_media()`
- `_startup_scan_record_excluded()`
- `_startup_scan_record_subtitle()`
- `_startup_scan_update_state()`
- `_startup_scan_refresh_processed_file()`
- `_startup_scan_guess_subtitle_language()`
- `_startup_scan_guess_media_path()`
- `_startup_scan_is_media_candidate()`
- `_startup_scan_walk()`
- `_startup_scan_current_sidecar_state()`
- `_startup_scan_json_default()`
- `_startup_scan_deserialize_audio_tracks()`
- `_startup_scan_collect_root_files()`
- `_startup_scan_enrich_subtitle_records()`
- `_startup_scan_collect_records()`
- `_startup_scan_process_record()`
- `is_relevant_inventory_file_name()`
- `collect_startup_inventory()`
- `_startup_scan_flat_manifest()`
- `_startup_scan_collect_flat_media_records()`
- `_startup_scan_build_flat_inventory()`
- `_startup_scan_inventory_signature()`
- `_startup_scan_store_inventory_signature()`
- `_startup_scan_inventory_signature_matches()`
- `compute_subtitle_signature()`
- `get_startup_policy_signature()`
- `_startup_scan_plan_media_record()`
- `startup_scan_existing()`

The port-prep goal is not necessarily to delete every one of these immediately. The goal is to decide which ones are:

- true public seam wrappers that stock should keep
- temporary compatibility shims that can be removed
- helpers that belong entirely inside `subgen_startup_scan/`

## Port Strategy

Use the current fork as the source package and drive toward a stock replay that looks like:

1. add `subgen_startup_scan/` to stock
2. patch stock `subgen.py` with a thin backend seam
3. patch stock docs/config to expose the new env vars
4. port tests that validate backend selection and cache behavior
5. keep `legacy` default until persistent mode is explicitly enabled

## Concrete PR Sequence

### PR A: Freeze the stock-compatible seam in this fork

Goal:

- make the current seam explicit and small
- reduce leftover wrapper ambiguity in this repo before any stock replay

Files to touch in this repo:

- `subgen.py`
- `subgen_startup_scan/adapter.py`
- `subgen_startup_scan/facade.py`
- `subgen_startup_scan/wiring.py`
- `subgen_startup_scan/runtime.py`
- `subgen_startup_scan/__init__.py`
- `tests/test_startup_scan_backend_selection.py`
- `tests/test_startup_scan_cache.py`

Work:

- classify each remaining startup-scan wrapper in `subgen.py` as seam or package-internal
- remove or consolidate wrappers that exist only to bounce between package modules
- keep stock-facing dispatch functions stable
- make sure backend construction stays readable and replayable

Validation:

- `python -m pytest tests -q`
- warm restart benchmark still comparable
- no benchmark event schema changes

Why this comes first:

- stock replay is easier if this repo first stops behaving like an in-between migration state

### PR B: Make env-gating and defaults stock-safe

Goal:

- ensure instrumentation and alternate backend behavior are explicit and quiet by default

Files to touch in this repo:

- `subgen_startup_scan/config.py`
- `subgen_startup_scan/benchmarks.py`
- `subgen_startup_scan/classifier.py`
- `subgen.py`
- `README.md`
- `subgen.xml`

Files to patch later in stock:

- `C:\Users\Nathan\source\subgen-stock\subgen.py`
- `C:\Users\Nathan\source\subgen-stock\README.md`
- `C:\Users\Nathan\source\subgen-stock\subgen.xml`

Work:

- verify `STARTUP_SCAN_BACKEND` handling is explicit and documented
- verify stock-target default is documented as `legacy`
- keep `STARTUP_SCAN_BENCHMARK_LOGGING` opt-in only
- keep planner trace logging opt-in only
- verify no detailed timing/log spam leaks when flags are off
- write env-var behavior in a form that can be copied to stock with minimal edits

Validation:

- startup logs remain quiet by default
- benchmark rows appear only when enabled
- planner trace rows appear only when enabled

Why this matters:

- this is the main behavior guardrail that prevents the port from surprising stock users

### PR C: Package the stock replay surface explicitly

Goal:

- define exactly which files are additive copy-ins versus stock patches

Files to touch in this repo:

- `startup_scan_stock_port_prep_plan.md`
- `README.md`
- optionally a new manifest note if useful:
  - `startup_scan_stock_replay_manifest.md`

Files to catalog for later stock copy-in:

- `subgen_startup_scan/__init__.py`
- `subgen_startup_scan/backend.py`
- `subgen_startup_scan/benchmarks.py`
- `subgen_startup_scan/classifier.py`
- `subgen_startup_scan/config.py`
- `subgen_startup_scan/db.py`
- `subgen_startup_scan/dependencies.py`
- `subgen_startup_scan/entrypoint.py`
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
- `subgen_startup_scan/association.py`
- `subgen_startup_scan/facade.py`
- `subgen_startup_scan/adapter.py`

Stock patch targets:

- `C:\Users\Nathan\source\subgen-stock\subgen.py`
- `C:\Users\Nathan\source\subgen-stock\README.md`
- `C:\Users\Nathan\source\subgen-stock\subgen.xml`
- `C:\Users\Nathan\source\subgen-stock\tests/conftest.py`
- `C:\Users\Nathan\source\subgen-stock\tests/test_queue.py`
- `C:\Users\Nathan\source\subgen-stock\tests/test_integration.py`

Work:

- record the exact stock replay file list
- mark whether each file is additive, patch-in-place, or optional
- avoid replaying unrelated compose or Docker customizations into stock

Validation:

- replay manifest is sufficient to guide a future stock branch without rediscovery

### PR D: Port backend-selection and startup-scan tests into stock-shaped form

Goal:

- reduce risk that the stock replay changes semantics silently

Files to touch in this repo:

- `tests/test_startup_scan_backend_selection.py`
- `tests/test_startup_scan_cache.py`
- `tests/test_startup_scan_planner_trace.py`
- `tests/conftest.py`

Files to add later in stock:

- `C:\Users\Nathan\source\subgen-stock\tests/test_startup_scan_backend_selection.py`
- `C:\Users\Nathan\source\subgen-stock\tests/test_startup_scan_cache.py`
- optionally:
  - `C:\Users\Nathan\source\subgen-stock\tests/test_startup_scan_planner_trace.py`

Work:

- remove assumptions in tests that rely on fork-only defaults where possible
- make fixtures portable to stock paths and env defaults
- keep tests focused on seam behavior, cache correctness, and instrumentation gating

Validation:

- this repo test suite stays green
- port-ready tests have minimal dependency on unrelated local runtime customizations

Why this matters:

- stock replay without tests will drift quickly and become hard to maintain

### PR E: Prepare the stock patch itself

Goal:

- make the actual `subgen-stock` change set small and reviewable

Files to patch later in stock:

- `C:\Users\Nathan\source\subgen-stock\subgen.py`
- `C:\Users\Nathan\source\subgen-stock\README.md`
- `C:\Users\Nathan\source\subgen-stock\subgen.xml`
- `C:\Users\Nathan\source\subgen-stock\tests/conftest.py`
- `C:\Users\Nathan\source\subgen-stock\tests/test_queue.py`
- `C:\Users\Nathan\source\subgen-stock\tests/test_integration.py`
- `C:\Users\Nathan\source\subgen-stock\tests/test_startup_scan_backend_selection.py`
- `C:\Users\Nathan\source\subgen-stock\tests/test_startup_scan_cache.py`
- optionally `scan_index.py` only if a compatibility shim is still needed

Additive files to copy into stock:

- full `subgen_startup_scan/` package listed in PR C

Work:

- add backend env parsing to stock
- add dependency builder to stock
- dispatch stock `transcribe_existing()` through the backend seam
- keep stock `legacy` behavior intact
- route forced-language startup path through the same seam
- route processed-file refresh through the backend interface if required by the replay shape

Validation:

- stock starts with `STARTUP_SCAN_BACKEND=legacy`
- stock starts with `STARTUP_SCAN_BACKEND=persistent`
- stock defaults remain legacy and quiet
- stock benchmark logging remains opt-in

## File-By-File Port Map

### `subgen.py`

Current role:

- still owns the stock-facing runtime entrypoints
- still contains many startup-scan bridge helpers

Target role:

- minimal seam only
- build dependencies
- choose backend
- dispatch startup scan
- dispatch forced-language startup path
- dispatch processed-file refresh hook

Action:

- continue shrinking bridge logic here before stock replay

### `subgen_startup_scan/backend.py`

Current role:

- backend selection and backend types

Target role:

- final selector used by both this fork and stock replay

Action:

- keep stable
- avoid leaking fork-specific defaults here

### `subgen_startup_scan/dependencies.py`

Current role:

- dependency container for package logic

Target role:

- single translation boundary between stock `subgen.py` and persistent backend

Action:

- keep explicit
- prefer data passed in over imports back into `subgen.py`

### `subgen_startup_scan/config.py`

Current role:

- env parsing for backend behavior

Target role:

- authoritative definition of backend-related env vars

Action:

- ensure stock-safe defaults and doc alignment

### `subgen_startup_scan/service.py`

Current role:

- orchestrates startup scan lifecycle

Target role:

- own all persistent startup orchestration

Action:

- continue moving any residual orchestration out of `subgen.py` if still present

### `subgen_startup_scan/classifier.py`

Current role:

- planning, apply, and processed-file refresh logic

Target role:

- own all persistent queue-planning decisions and refresh updates

Action:

- verify no stray `subgen.py` logic duplicates this behavior

### `subgen_startup_scan/inventory.py`

Current role:

- inventory collection facade and coordination

Target role:

- package-local inventory entrypoint only

Action:

- keep separated from stock seam
- verify stock replay needs no edits here other than copy-in

### `subgen_startup_scan/traversal.py`

Current role:

- directory walk behavior

Target role:

- directory traversal implementation hidden behind package APIs

Action:

- keep isolated
- do not re-inline into stock

### `subgen_startup_scan/inventory_cache.py`

Current role:

- directory cache validation and reuse helpers

Target role:

- package-local cache implementation

Action:

- keep additive
- avoid making stock `subgen.py` aware of cache details

### `subgen_startup_scan/association.py`

Current role:

- subtitle/media association helpers

Target role:

- package-local enrichment only

Action:

- keep copy-in only for stock

### `subgen_startup_scan/signatures.py`

Current role:

- policy and inventory signatures

Target role:

- authoritative signature logic shared by both repos after replay

Action:

- make sure no duplicate signature code remains in `subgen.py`

### `subgen_startup_scan/db.py`

Current role:

- SQLite access layer

Target role:

- backend-owned state layer for persistent mode only

Action:

- keep totally optional from the stock legacy path perspective

### `subgen_startup_scan/schema.py`

Current role:

- schema and migrations

Target role:

- backend-owned schema layer

Action:

- avoid importing this on legacy startup unless needed

### `subgen_startup_scan/benchmarks.py`

Current role:

- structured benchmark writing

Target role:

- env-gated diagnostics only

Action:

- confirm quiet-by-default behavior before stock replay

### `subgen_startup_scan/legacy.py`

Current role:

- stock-style recursive startup path wrapper

Target role:

- preserved stock-compatible behavior

Action:

- use this as the main compatibility anchor for stock replay

### `subgen_startup_scan/forced.py`

Current role:

- forced-language startup dispatch

Target role:

- package-owned forced-language path

Action:

- keep the seam narrow in `subgen.py`

### `subgen_startup_scan/runtime.py`

Current role:

- startup runtime coordination helpers

Target role:

- package-internal runtime support

Action:

- verify whether any contents should merge into `service.py` or `backend.py` for a cleaner stock replay

### `subgen_startup_scan/facade.py`

Current role:

- wrapper entrypoints used by `subgen.py`

Target role:

- either stable compatibility surface or reduced if redundant

Action:

- review for unnecessary indirection before stock replay

### `subgen_startup_scan/adapter.py`

Current role:

- very small adapter surface

Target role:

- remove if unnecessary, otherwise keep as a named stock-bridge utility

Action:

- decide explicitly; do not leave it ambiguous

### `README.md`

Current role:

- fork docs already mention startup-scan env vars

Target role:

- source text for stock doc replay

Action:

- ensure wording distinguishes defaults in this fork from intended stock defaults

### `subgen.xml`

Current role:

- exposes persistent backend env vars in this fork

Target role:

- stock-ready config definitions with quiet defaults

Action:

- verify descriptions are stock-safe and not fork-specific

### `tests/test_startup_scan_backend_selection.py`

Current role:

- validates backend seam behavior

Target role:

- first test to replay into stock

Action:

- keep this portable and focused

### `tests/test_startup_scan_cache.py`

Current role:

- validates cache behavior and inventory semantics

Target role:

- primary persistent-mode correctness suite for stock replay

Action:

- continue using this as the main guard against cache regressions

### `tests/test_startup_scan_planner_trace.py`

Current role:

- validates opt-in planner trace behavior

Target role:

- optional but useful stock replay guard for instrumentation gating

Action:

- keep trace output assertions compatible with env-gated behavior

## Upgrade And Fix Checks To Fold Into Port Prep

These are not separate feature projects. They are checks that should happen while preparing the replay.

### 1. Verify stock has not moved startup semantics in ways we missed

Check:

- recent upstream `subgen-stock` changes to `transcribe_existing()`
- queue semantics
- monitor startup behavior
- forced-language handling

Risk:

- replaying our seam onto stale assumptions could overwrite upstream fixes

### 2. Keep benchmark and trace logging as a distinct subsystem

Check:

- benchmark writes are gated entirely by env vars
- no default log clutter
- no dependency on benchmark logging for correctness

Risk:

- instrumentation can accidentally become part of control flow

### 3. Keep legacy path independent of SQLite state

Check:

- `legacy` mode does not require DB initialization for normal operation
- schema setup is not forced when legacy is chosen unless unavoidable

Risk:

- stock users enabling only `TRANSCRIBE_FOLDERS` should not inherit persistent-state dependencies unexpectedly

### 4. Verify processed-file refresh hook is backend-owned cleanly

Check:

- post-transcription updates do not bypass backend selection

Risk:

- startup path becomes selectable, but steady-state runtime still writes persistent data through direct hooks

### 5. Review language-detection persistence assumptions

Check:

- audio-language persistence in DB remains correct
- media path to subtitle association remains explicit
- forced-language path and persistent refresh path write consistent metadata

Risk:

- stock replay preserves the seam but carries forward stale metadata bugs

### 6. Keep stock patch isolated from Docker customizations

Check:

- no compose-specific behavior is required for backend correctness
- persistent DB path defaults are documented but not tied to local compose assumptions

Risk:

- stock replay becomes entangled with this fork's local container setup

## Benchmark Plan During Port Prep

At each major port-prep phase, capture:

- warm restart benchmark from the running container
- whether `startup_scan.inventory` changes materially
- whether `startup_scan.total` changes materially
- whether cache hit/miss counters remain sane
- whether benchmark logs still parse as valid JSONL

For stock replay testing later, add:

- stock `legacy` baseline on a small sample
- stock `persistent` baseline on the same sample
- warm restart comparison after a temporary empty DB and after a warm cache

## Recommended Next Execution Order

1. reduce remaining bridge wrappers in `subgen.py` and `subgen_startup_scan/facade.py`
2. verify and tighten env-gating for benchmark and planner-trace output
3. create a replay manifest of additive package files versus stock patch files
4. make startup-scan tests more stock-portable where needed
5. only then begin applying the patch set into `subgen-stock`

## Exit Criteria

Port prep is complete when:

- `subgen.py` is clearly a thin seam rather than a half-migrated implementation
- every backend-specific module needed for stock replay is identified and additive
- benchmark and trace behavior are fully opt-in
- stock patch targets are explicitly listed
- test coverage for backend selection and cache behavior is ready to replay
- the future `subgen-stock` integration can be described as:
  - copy `subgen_startup_scan/`
  - patch `subgen.py`
  - patch docs/config
  - add startup-scan tests
