# Startup Scan Backend Extraction Plan

## Goal

Pull the persistent startup scan, inventory cache, benchmark logging, and SQLite-backed state management out of the main `subgen.py` flow and isolate them behind a selectable backend boundary.

The target outcome is:

- the original startup scan behavior remains available as a `legacy` backend
- the current persistent/cache-backed implementation becomes a `persistent` backend
- `subgen.py` becomes a thin dispatcher rather than the home of both implementations
- rebasing against the original upstream tree becomes easier because most custom logic moves into a separate internal package
- the performance improvements can later be integrated into `..\subgen-stock` with a minimal patch set that mostly adds a backend selector, a new internal package, and a small number of call-site changes

This plan assumes we are not publishing a separate external Python package yet. The extraction target is an internal package inside this repo.

For planning purposes, the future integration target is the stock/original source tree at `..\subgen-stock`.

## Non-Goals

- Do not redesign subtitle generation, webhook APIs, or worker queue behavior unless required to support the backend boundary.
- Do not attempt to make the persistent backend generic enough for unrelated applications.
- Do not remove the legacy startup scan until the persistent backend is fully extracted and validated.
- Do not change runtime behavior intentionally during the extraction, except where needed to preserve existing persistent-backend behavior behind the new selector.
- Do not make benchmark or timing output noisy by default. Any benchmark logging, timing output, or detailed instrumentation must remain explicitly env-gated.

## Desired End State

The codebase should support:

- `STARTUP_SCAN_BACKEND=legacy`
- `STARTUP_SCAN_BACKEND=persistent`

With:

- `legacy` using behavior that stays close to the original Subgen tree
- `persistent` using the current SQLite-backed inventory/cache implementation
- benchmark logging and timing instrumentation disabled by default unless an explicit env var enables them

`subgen.py` should only:

- read the backend selector
- build a dependency object
- call a narrow backend interface

The persistent backend package should own:

- schema and DB access
- benchmark logging
- inventory walking and cache validation
- subtitle/media linking during inventory
- policy signature computation
- classify/plan/apply startup scan flow
- processed-file cache refresh

The intended future merge shape for `..\subgen-stock` is:

- retain stock behavior as the default `legacy` path unless there is a deliberate reason to change the default
- add the persistent backend as an opt-in alternative selected by env var
- keep benchmark/timing instrumentation fully opt-in via env vars
- keep the diff against stock concentrated in a dispatcher seam plus the added backend package

In addition, the extracted backend should support non-runtime tooling cleanly:

- an optional diagnostics/admin CLI can be layered on top of the persistent backend without importing `subgen.py`
- optional notebook-based benchmark analysis can read the persistent DB and benchmark JSONL without depending on application internals

## Current Code Surface

This is the approximate current surface area that needs to move or be wrapped:

- `scan_index.py`
  - schema creation and migrations
  - `StartupScanDB`
  - `BenchmarkLogger`
  - `benchmark_step`
- `subgen.py`
  - startup scan DB helpers near the early/mid file
  - inventory collection and subtitle/media linking
  - inventory signature and manifest logic
  - classify/planning/apply logic
  - startup scan orchestration
  - processed-file refresh after successful subtitle generation
- tests in `tests/test_startup_scan_cache.py`

The extraction should reduce the amount of startup-scan-specific logic living directly in `subgen.py`.

It should also reduce the amount of custom code that would need to be manually threaded back into `..\subgen-stock`.

## Proposed Package Layout

Create a new internal package:

```text
subgen_startup_scan/
  __init__.py
  backend.py
  config.py
  types.py
  dependencies.py
  db.py
  schema.py
  benchmarks.py
  inventory.py
  signatures.py
  classifier.py
  service.py
  legacy_adapter.py
  tools/
    __init__.py
    admin_cli.py
```

Purpose of each module:

- `backend.py`
  - backend selection
  - backend interface definition
  - factory function returning the selected backend
- `config.py`
  - parse backend-specific env vars
  - read `STARTUP_SCAN_BACKEND`
  - expose a typed config object
- `types.py`
  - dataclasses and typed payloads
  - inventory records
  - startup summary records
  - classify/apply plan payloads
- `dependencies.py`
  - dependency container passed from `subgen.py`
  - wrappers for media probing, queue planning, path mapping, language helpers, subtitle naming, logging hooks
- `db.py`
  - extracted `StartupScanDB`
  - DB-specific query helpers
  - transaction helpers
- `schema.py`
  - schema version and migration logic
- `benchmarks.py`
  - extracted benchmark logging utilities
- `inventory.py`
  - directory walking
  - relevant-file filtering
  - subtitle/media linking
  - directory-state cache validation
- `signatures.py`
  - directory entries signature
  - subtitle signature
  - policy signature
  - inventory manifest helpers
- `classifier.py`
  - classify/preflight/planning/apply logic
  - queue planning statistics
  - persistent cache refresh helpers
- `service.py`
  - main orchestration for the persistent backend
  - startup scan lifecycle
  - monitor setup for this backend
- `legacy_adapter.py`
  - wrapper for original startup scan behavior if needed to normalize the interface
- `tools/admin_cli.py`
  - optional diagnostics/admin CLI for DB inspection, benchmark summaries, cache stats, and backend verification
  - not required for the first extraction PR
  - should only depend on the extracted backend package, not on `subgen.py`

## Backend Interface

Define a narrow backend protocol.

Example interface:

```python
class StartupScanBackend(Protocol):
    def initialize(self) -> None: ...
    def startup_scan_existing(self, transcribe_folder_spec: str) -> None: ...
    def refresh_processed_file(
        self,
        path: str,
        transcription_type: str,
        force_language,
        audio_tracks=None,
    ) -> None: ...
```

Optional additions if needed:

- `setup_monitor(folders: list[str]) -> None`
- `name() -> str`

Rules:

- `subgen.py` only talks to this interface
- the persistent backend handles all DB/cache details internally
- the legacy backend either no-ops unsupported operations or implements compatible behavior

## Dependency Boundary

The persistent backend currently depends on many `subgen.py` globals and helpers. Those should not remain as raw imports spread across the extracted modules.

Create a dependency object built by `subgen.py`.

Suggested contents:

```python
@dataclass
class StartupScanDependencies:
    logger: Any
    language_code_type: type
    has_video_extension: Callable[[str], bool]
    has_audio_extension: Callable[[str], bool]
    get_audio_tracks: Callable[[str], list]
    get_audio_languages: Callable[[str], list]
    get_subtitle_languages: Callable[[str], list]
    has_audio: Callable[[str], bool]
    choose_transcribe_language: Callable[..., Any]
    prepare_media_queue_job: Callable[..., dict]
    path_mapping: Callable[[str], str]
    name_subtitle: Callable[[str, Any], str]
    queue_put: Callable[[dict], None]
    queue_is_active: Callable[[str], bool]
    create_monitor_handler: Callable[[], Any]
    observer_factory: Callable[[], Any]
    policy_inputs_provider: Callable[[], dict]
```

The exact fields can be adjusted, but the goal is:

- all Subgen-specific behavior stays at the boundary
- extracted modules become easier to read and test
- sister-repo comparison stays cleaner because global coupling is reduced

Additional dependency-boundary rule:

- runtime backend modules must not import `subgen.py` directly
- `subgen.py` may construct and pass dependencies into the backend
- optional tooling such as an admin CLI should use the backend package directly and should not require the full app runtime

## Backend Selection Design

Add a single env var:

```text
STARTUP_SCAN_BACKEND=legacy
STARTUP_SCAN_BACKEND=persistent
```

Behavior:

- for future `..\subgen-stock` integration, prefer `legacy` as the default unless there is a deliberate decision to change stock behavior
- in this fork, the default can remain whatever is operationally required, but the code should make the default obvious and easy to flip
- do not couple backend selection to benchmark logging; benchmarking must remain separately controlled

Recommendation:

- during the extraction branch, keep the default explicitly documented rather than relying on hidden assumptions
- reject unknown values early with a clear log message

Benchmark and timing controls should remain separate env vars, for example:

```text
STARTUP_SCAN_BENCHMARK_LOGGING=false
STARTUP_SCAN_BENCHMARK_LOG_PATH=/subgen/state/startup_scan_benchmarks.jsonl
```

Rules:

- no benchmark JSONL writing unless `STARTUP_SCAN_BENCHMARK_LOGGING=true`
- no extra timing log spam in normal startup by default
- if future stock integration wants instrumentation, it should be opt-in and isolated to the persistent backend or a shared benchmark utility

Optional future tooling env vars can be added only if they are quiet by default. For example:

```text
STARTUP_SCAN_ADMIN_CLI_ENABLED=false
```

But this should not be introduced until there is a real tooling need.

Example control flow in `subgen.py`:

```python
startup_scan_backend = build_startup_scan_backend(config, dependencies)
startup_scan_backend.initialize()
startup_scan_backend.startup_scan_existing(transcribe_folders)
```

Worker completion hook:

```python
startup_scan_backend.refresh_processed_file(
    path,
    transcription_type,
    force_language,
    audio_tracks=audio_tracks,
)
```

## Extraction Sequence

### Phase 1: Stabilize the seam

1. Add `STARTUP_SCAN_BACKEND` env parsing.
2. Add `subgen_startup_scan/backend.py` and the backend interface.
3. Add a temporary persistent backend wrapper that delegates to existing functions still living in `subgen.py`.
4. Replace direct startup scan entry calls in `subgen.py` with backend dispatch calls.
5. Make sure benchmark/timing emission still requires explicit env enablement after the dispatcher is introduced.

Success criteria:

- no behavioral change yet
- selection flag exists
- one code path in `subgen.py` is responsible for backend selection
- benchmark output behavior is unchanged and remains opt-in

### Phase 2: Move the already-separated DB/benchmark layer

1. Move or re-home `scan_index.py` contents into:
   - `subgen_startup_scan/schema.py`
   - `subgen_startup_scan/db.py`
   - `subgen_startup_scan/benchmarks.py`
2. Keep `scan_index.py` as a compatibility shim temporarily if needed.
3. Update imports in `subgen.py` and tests.

Success criteria:

- DB access and benchmark code are no longer owned by a top-level utility file
- imports still work through the backend package
- the DB and benchmark layer is usable from optional tooling without importing the full app

### Phase 3: Move inventory logic

1. Extract `StartupInventory` dataclass to `types.py`.
2. Move inventory walkers and directory-cache validation to `inventory.py`.
3. Move subtitle/media linking helpers there as well.
4. Keep behavior identical:
   - relevant-file filtering
   - recursive walk
   - directory state reuse
   - cache-hit/miss counters

Success criteria:

- `subgen.py` no longer contains directory walk or directory-state cache logic
- the persistent backend uses only package-local inventory code

### Phase 4: Move signature and manifest logic

1. Extract:
   - policy signature generation
   - flat inventory signature helpers
   - subtitle signature computation
   - directory entries signature helpers
2. Put them in `signatures.py`.
3. Keep benchmark field names stable.

Success criteria:

- signature logic is fully centralized
- future cache invalidation changes affect one module instead of many helpers

### Phase 5: Move classification/planning/apply logic

1. Extract classify flow into `classifier.py`.
2. Move:
   - preflight checks
   - cache match checks
   - planning executor logic
   - apply/update logic
   - benchmark aggregation
3. Make queue planning depend only on the dependency object, not raw globals.

Success criteria:

- `subgen.py` no longer contains persistent startup scan planning logic
- benchmark hot spots are easier to profile in isolation
- future tooling can inspect planner outcomes from persisted state or benchmark files without reaching into runtime globals

### Phase 6: Move processed-file refresh logic

1. Extract `_startup_scan_refresh_processed_file` into `classifier.py` or `service.py`.
2. Update the transcription worker to call the backend method instead of the function directly.
3. Ensure the legacy backend either:
   - no-ops this hook
   - or implements the minimal behavior required by legacy mode

Success criteria:

- worker path no longer depends on persistent scan internals

### Phase 7: Preserve and isolate legacy behavior

1. Identify the original or closest-to-original startup scan code path.
2. Wrap it behind `legacy_adapter.py`.
3. Minimize changes to legacy mode:
   - avoid importing persistent DB code
   - avoid benchmark schema coupling
   - avoid accidental state writes to the persistent DB

Success criteria:

- `legacy` remains understandable and close to original upstream
- upstream diff against `legacy` path is small
- the same seam could be applied to `..\subgen-stock` without bringing over the persistent implementation inline

### Phase 8: Cleanup

1. Remove duplicate helper code from `subgen.py`.
2. Remove temporary shims once imports are stable.
3. Reduce top-level startup-scan-specific globals in `subgen.py`.
4. Document backend behavior in `README.md`, compose files, and XML config if relevant.

Success criteria:

- `subgen.py` becomes orchestration-focused again
- backend code is isolated under a single package
- extracted backend code is suitable for future reuse by a repo-local admin CLI and offline analysis tools

### Phase 9: Optional tooling layer

This phase is intentionally optional and should happen only after the backend split is stable.

1. Add a repo-local admin CLI around the extracted backend package.
2. Keep it out of the main runtime path.
3. Prefer a minimal command surface such as:
   - `summary`
   - `cache-stats`
   - `bench-tail`
   - `db-counts`
   - `verify-backend`
4. Make output machine-readable by default where practical.
5. Ensure it works equally well in this fork and can be copied into `..\subgen-stock` later if useful.

Success criteria:

- operations we currently do with ad hoc `docker exec` and SQL one-liners can be done with a stable local tool
- the tool is optional and does not complicate core startup behavior

## Detailed Function Move Map

This is the intended destination for major function groups.

### Move to `subgen_startup_scan/db.py`

- `StartupScanDB`
- DB connection helpers if still needed in persistent mode
- table count helpers
- subtitle relink helpers
- directory state upserts and fetches

### Move to `subgen_startup_scan/schema.py`

- `ensure_startup_scan_schema`
- schema version constants
- migration helpers

### Move to `subgen_startup_scan/benchmarks.py`

- `BenchmarkLogger`
- `benchmark_step`

### Move to `subgen_startup_scan/inventory.py`

- `StartupInventory`
- relevant subtitle/media file identification
- inventory collection
- directory cache reuse logic
- subtitle/media relationship enrichment

### Move to `subgen_startup_scan/signatures.py`

- directory entries signature helpers
- cached entries signature fallback
- inventory signature storage and comparison
- subtitle signature computation
- policy signature logic

### Move to `subgen_startup_scan/classifier.py`

- classify media planning flow
- cache hit/miss classification
- apply breakdown tracking
- processed-file refresh helper

### Move to `subgen_startup_scan/service.py`

- persistent backend implementation
- `initialize`
- `startup_scan_existing`
- monitor setup for persistent backend

### Keep in `subgen.py`

- environment loading common to the whole app
- queue implementation
- HTTP routes
- transcription worker
- media probing implementations
- subtitle generation functions
- thin backend selection and method calls

## Testing Strategy

Split tests into three categories.

### 1. Backend contract tests

Create a new test module for backend selection and interface behavior.

Examples:

- selecting `legacy` returns the legacy backend
- selecting `persistent` returns the persistent backend
- unknown backend value fails clearly
- worker hook calls `refresh_processed_file` on the selected backend
- benchmark logging remains disabled unless explicitly enabled

### 2. Persistent backend behavior tests

Move current startup scan cache tests to package-scoped modules such as:

- `tests/startup_scan/test_db.py`
- `tests/startup_scan/test_inventory.py`
- `tests/startup_scan/test_signatures.py`
- `tests/startup_scan/test_classifier.py`
- `tests/startup_scan/test_service.py`

Preserve existing coverage for:

- schema migration
- subtitle/media linking
- manifest hits
- directory cache hits
- irrelevant-file cache behavior
- parallel planning benchmark instrumentation

Add package-level tests for:

- backend modules operating from injected dependencies only
- benchmark logging staying silent when disabled
- DB and benchmark readers usable by optional tooling without importing `subgen.py`

### 3. Legacy smoke tests

Add lightweight tests that confirm:

- legacy backend still starts
- legacy backend does not require persistent DB state
- legacy backend ignores persistent-only state when selected

## Benchmark Validation Plan

After each extraction phase, collect:

- startup time on warm cache
- startup time on broad `/movies` mount
- directory cache hit/miss counters
- classify media timing
- monitor setup timing

Minimum benchmark checkpoints:

1. Before extraction begins
2. After backend dispatch is introduced
3. After DB/benchmark extraction
4. After inventory extraction
5. After classifier extraction
6. After final cleanup

The benchmark schema and event names should remain stable during the extraction so old and new runs stay comparable.

Additional benchmark gating requirements:

- default startup should not emit benchmark records unless enabled
- benchmark file writing must stay behind env control during and after the refactor
- any human-readable timing logs added for debugging should be disabled by default before integration into `..\subgen-stock`

Optional analysis workflow:

- if benchmark analysis becomes cumbersome in plain logs, use the `jupyter-notebook` skill to build a reproducible notebook against the JSONL benchmark file and SQLite DB
- treat the notebook as an analysis artifact, not as part of the runtime backend

## Benchmark-Driven Additions

The existing benchmark history adds several concrete requirements that should be tracked explicitly in the implementation plan.

Observed behavior from recent runs:

- warm-cache runs improved into the tens-of-seconds range
- broad first-time `/movies` startup is no longer dominated by recursive inventory walking
- the remaining dominant cold-start cost is `startup_scan.classify_media.parallel_plan`
- on the broad first-time run, most expensive planning work still ended in `skip`, not `queue`

This means the plan should include the following additional work items.

### Add planner micro-instrumentation

Add env-gated timing inside `prepare_media_queue_job()` and closely related helper paths so we can attribute planner cost by sub-step.

Target sub-steps:

- audio-track probing
- audio-language resolution
- internal subtitle inspection
- external subtitle existence checks
- sidecar naming/path checks
- any recursive subtitle search or per-file directory scans

Rules:

- disabled by default
- emitted only when an explicit env var enables detailed planning timings
- structured so results can still be summarized in the benchmark notebook

### Add cold-start skip fast paths

The data suggests the biggest remaining waste is expensive planning for uncached files that are ultimately skipped.

The persistent backend should therefore add explicit optimization work for:

- skipping files quickly when inventory already proves external sidecars exist
- reusing subtitle inventory and media inventory before probing media containers
- avoiding deep or repeated subtitle searches when cached directory inventory already answers the question

This should be treated as a first-class optimization track, not a side effect of the refactor.

### Add acceptance baselines

The backend extraction should preserve and measure at least two operational baselines:

1. warm cached startup on the smaller known library shape
2. first-time or low-cache startup on the broader `/movies` library shape

The plan should track expected ranges for:

- inventory duration
- classify/planning duration
- total startup duration
- directory cache hit/miss ratios

This matters because the cold broad run and the warm run optimize different things.

### Add a post-milestone benchmark review gate

After each major backend extraction milestone, review:

- whether warm starts regressed
- whether cold-start planning regressed
- whether benchmark output still stays quiet by default

This review should update the notebook or equivalent analysis artifact rather than relying on ad hoc terminal inspection.

## Upstream Rebase Strategy

The reason for the backend split is not only code cleanliness. It should directly reduce rebase pain.

Approach:

1. Keep `legacy` mode close to the original tree.
2. Move persistent behavior into `subgen_startup_scan/`.
3. Use the sister repo to compare:
   - stock/original `..\subgen-stock\subgen.py`
   - this repo’s `legacy` path
   - this repo’s persistent backend package
4. Prefer adding new files rather than editing core flow in many places.
5. Treat the future stock integration as a packaging problem:
   - add backend package
   - add env-gated selector
   - add env-gated benchmark hooks
   - keep intrusive edits to stock files minimal

Desired rebase outcome:

- future upstream changes mostly merge into `subgen.py`
- our persistent customization mostly lives outside the main conflict zone
- the same persistent backend package can be copied or replayed into `..\subgen-stock` with limited edits to stock files

## Risk Areas

### High risk

- hidden reliance on `subgen.py` globals
- benchmark field regressions
- queue planning behavior drift
- monitor/watchdog setup drift
- processed-file refresh inconsistencies

### Medium risk

- path mapping inconsistencies
- language code serialization differences
- subtitle/media linking drift after extraction
- test fixture churn from module moves

### Low risk

- schema/benchmark utility relocation if imports are updated carefully

## Practical Implementation Notes

- Do not move everything at once.
- Introduce the backend seam first, then move code behind it incrementally.
- Prefer temporary compatibility shims over giant one-shot edits.
- Preserve benchmark event names exactly during the refactor.
- Keep old helper names available temporarily where tests or runtime still reference them.
- Avoid changing compose defaults until both backends are runnable.
- When possible, prefer adding new modules over rewriting stock-shaped code in place so future integration into `..\subgen-stock` is mostly additive.
- Do not introduce a durable CLI during the core extraction unless it materially reduces debugging friction; runtime separation comes first.

## Milestone Checklist

### Milestone A: selectable backend exists

- `STARTUP_SCAN_BACKEND` parsed
- backend factory exists
- `subgen.py` uses backend dispatch

### Milestone B: persistent DB/benchmark code extracted

- `scan_index.py` logic moved into package
- runtime unchanged

### Milestone C: persistent inventory extracted

- directory walk and cache validation moved out
- benchmark parity preserved

### Milestone D: persistent classifier extracted

- planning/apply flow moved out
- worker refresh path routed through backend

### Milestone E: legacy path isolated

- legacy backend cleanly selectable
- minimal persistent coupling remains in core file

### Milestone F: cleanup complete

- duplicate helpers removed
- docs updated
- sister-repo comparison is straightforward

## Recommended First Refactor PR Scope

Keep the first PR intentionally small.

Recommended contents:

1. add `STARTUP_SCAN_BACKEND`
2. add `subgen_startup_scan/backend.py`
3. add a dependency object
4. route startup scan entrypoints through the backend
5. verify benchmark emission is still env-gated
6. no major code moves yet

Reason:

- it establishes the seam
- it minimizes regression risk
- it makes every later move smaller
- it creates the same seam we would want to add to `..\subgen-stock`
- it avoids prematurely mixing runtime refactor work with tooling work

## Recommended Second Refactor PR Scope

1. move `scan_index.py` functionality into the package
2. keep `scan_index.py` as a shim if needed
3. update imports
4. verify benchmark file compatibility

## Recommended Third Refactor PR Scope

1. move inventory and signature logic
2. keep persistent runtime behavior identical
3. compare benchmark counters before and after

## Recommended Fourth Refactor PR Scope

1. move classifier/planning/apply logic
2. route worker refresh through backend
3. benchmark the broad `/movies` startup again

## Recommended Fifth Refactor PR Scope

Optional, only after the backend split is stable:

1. add `subgen_startup_scan/tools/admin_cli.py`
2. expose a small repo-local command entrypoint for diagnostics
3. keep the command surface read-focused first
4. verify it can inspect persistent backend state without importing `subgen.py`

Reason:

- this is where the `cli-creator` skill is useful
- it gives us a durable replacement for repeated manual benchmark/DB inspection commands
- it should remain a thin layer on top of the extracted backend, not a second code path

## Exit Criteria

This plan is complete when all of the following are true:

- `subgen.py` does not contain the persistent scanner implementation details
- the persistent backend lives under `subgen_startup_scan/`
- a `legacy` backend remains selectable
- tests are organized by backend responsibility
- benchmark output is stable and comparable
- benchmark output remains disabled by default unless env-enabled
- sister-repo comparison against the original tree is materially easier
- the path to integrating the persistent backend into `..\subgen-stock` is clear and mostly additive
- optional tooling can be added on top of the extracted backend without re-coupling it to `subgen.py`
