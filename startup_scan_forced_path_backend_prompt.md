# Startup Scan Forced-Language Backend Seam Prompt

Use this prompt for a `gpt-5.4-mini` subagent.

```text
Work in `C:\Users\Nathan\source\subgen`.

Stay on branch `codex/reapply-backend-seam`.

Current branch state to preserve:
- HEAD commit: `dfea2b3` (`Skip startup audio probes when inventory already proves subtitles exist`)
- tracked dirty file: `startup_scan_pr_benchmarks.md`
- do not overwrite or revert the current benchmark-note edit

Do not modify unrelated files. Do not touch or revert:
- `docker-compose.yml`
- `docker-compose.local.yml`
- untracked local artifacts, caches, models, output, `.codebase-memory/`, `.env`, `docker/`, `subgen-cuda/`
- benchmark JSONL / DB files under `%APPDATA%\subgen\state`
- any unrelated user edits

Primary goal:
Extract the forced-language startup path so it uses the same startup-scan backend seam as the normal startup path, instead of leaving a special-case orchestration path in `subgen.py`.

Scope constraints:
- keep the stock-facing seam in `subgen.py` thin
- preserve legacy default behavior
- do not change benchmark event names or fields
- do not start a broader startup-scan refactor beyond the forced-language slice
- keep benchmark / planner-trace logging opt-in only

Files you may modify:
- `subgen.py`
- `subgen_startup_scan/backend.py`
- `subgen_startup_scan/dependencies.py`
- `subgen_startup_scan/entrypoint.py`
- `subgen_startup_scan/forced.py`
- `subgen_startup_scan/wiring.py`
- `subgen_startup_scan/runtime.py`
- `tests/test_bug_fixes.py`
- `tests/test_startup_scan_backend_selection.py`
- `tests/test_startup_scan_cache.py`
- `tests/test_startup_scan_planner_trace.py`
- optionally `startup_scan_pr_benchmarks.md` only if a runtime benchmark materially changes

Files to inspect before editing:
- `subgen.py`
- `subgen_startup_scan/entrypoint.py`
- `subgen_startup_scan/backend.py`
- `subgen_startup_scan/forced.py`
- `subgen_startup_scan/dependencies.py`
- `tests/test_bug_fixes.py`
- `tests/test_startup_scan_backend_selection.py`

Current structural facts to preserve:
- `transcribe_existing_dispatch(...)` in `subgen_startup_scan/entrypoint.py` currently does:
  - normal path: `backend = get_backend(); backend.initialize(); backend.startup_scan_existing(...)`
  - forced-language path: `initialize(); run_forced_language_scan(...)`
- `subgen_startup_scan/forced.py` already contains `startup_scan_existing_forced_language(...)`
- `tests/test_bug_fixes.py` already has `TestForcedLanguageStartupScanExtraction.test_forced_language_path_uses_extracted_startup_scan`
- the forced-language path is already extracted into a helper, but it is not yet expressed as a backend capability

Current benchmark baselines to preserve:
- latest subset cold benchmark: `%APPDATA%\subgen\state\bench-nzb-v5-cold.jsonl`
  - `startup_scan.inventory`: `16152.210 ms`
  - `startup_scan.classify_media.parallel_plan`: `10.985 ms`
  - `startup_scan.classify_media.parallel_plan_detail.audio_tracks_ms_total_ms`: `0.000 ms`
  - `startup_scan.classify_media.parallel_plan_detail.skip_check_ms_total_ms`: `30.691 ms`
  - `startup_scan.classify_media.parallel_plan_detail.queue_plan_total_ms`: `31.057 ms`
  - `startup_scan.classify_media`: `254.011 ms`
  - `startup_scan.monitor_setup`: `27.642 ms`
  - `startup_scan.total`: `17924.375 ms`
- latest subset warm benchmark: `%APPDATA%\subgen\state\bench-nzb-v5-warm.jsonl`
  - `startup_scan.inventory`: `407.284 ms`
  - `startup_scan.subtitle_relink`: `4.180 ms`
  - `startup_scan.monitor_setup`: `4.016 ms`
  - `startup_scan.total`: `646.011 ms`
- broad warm reference still on disk:
  - `startup_scan.inventory`: `1949.813 ms`
  - `startup_scan.subtitle_relink`: `473.110 ms`
  - `startup_scan.monitor_setup`: `102.183 ms`
  - `startup_scan.total`: `6826.118 ms`

Implementation target:
1. Move forced-language startup orchestration behind the backend seam.
2. Make the forced path selectable through the same backend object / dependency wiring pattern as normal startup.
3. Keep `subgen.py` limited to thin wrapper / dependency assembly responsibilities.
4. Preserve the existing extracted helper behavior in `subgen_startup_scan/forced.py`.

Recommended design direction:
- extend the startup backend protocol with a dedicated forced-language startup method rather than keeping a special-case direct call from `transcribe_existing_dispatch(...)`
- add the minimal dependency injection needed so the persistent backend can run the extracted forced-language implementation
- keep legacy behavior explicit:
  - if `STARTUP_SCAN_BACKEND=legacy`, decide whether forced-language startup should still route through the extracted forced helper or remain a no-op / explicit fallback
  - whatever behavior you choose, codify it with tests and keep it unsurprising
- avoid duplicating startup-scan initialization logic between normal and forced paths

Non-goals:
- no new caching behavior
- no new benchmark event schemas
- no docker / compose changes
- no model / GPU changes
- no database schema changes unless strictly required by the forced-path seam extraction

Detailed tasks:

Task 1. Read and map the current forced path
- inspect `transcribe_existing_dispatch(...)`
- inspect `PersistentStartupScanBackend` and `LegacyStartupScanBackend`
- inspect `startup_scan_existing_forced_language(...)`
- inspect how `subgen.py` wires `package_forced_startup_scan_existing`

Task 2. Implement the seam
- introduce the smallest backend interface extension needed for forced startup
- update dependency wiring so the persistent backend can call the extracted forced helper
- adjust `transcribe_existing_dispatch(...)` so both normal and forced startup flow through the backend seam instead of one direct special-case path
- keep `subgen.py` wrapper count flat or lower if feasible

Task 3. Cover behavior with tests
- extend `tests/test_bug_fixes.py` to prove the forced path still enters the extracted helper through the new seam
- extend `tests/test_startup_scan_backend_selection.py` if backend selection or backend surface changes
- add or update tests so forced-language startup does not regress queue semantics or initialization ordering
- do not break existing planner / cache tests

Task 4. Validate locally
- run focused tests first:
  - `python -m pytest tests/test_bug_fixes.py tests/test_startup_scan_backend_selection.py tests/test_startup_scan_cache.py tests/test_startup_scan_planner_trace.py -q`
- if focused tests pass, run:
  - `python -m pytest tests -q`
- if tests fail, stop and report the exact failure

Task 5. Runtime verification
- rebuild runtime image:
  - `docker compose build subgen`
- restart runtime:
  - `docker compose up -d subgen`
- inspect logs only long enough to confirm startup is healthy
- if your seam change does not materially affect performance, do not regenerate the full benchmark packet
- if you do collect a benchmark, keep it additive and compare against the baselines above

Task 6. Commit
- make one focused commit if and only if tests and runtime verification pass
- commit message should describe the forced-language seam extraction clearly, for example:
  - `Route forced startup scans through the backend seam`

Validation requirements:
- preserve `175 passed` or better on `python -m pytest tests -q`
- no benchmark event compatibility changes
- no regression in the existing forced-language extraction test intent
- no extra tracked file churn outside the allowed list

Report back with:
- files changed
- exact tests run and results
- whether docker rebuild / restart passed
- whether any benchmark was rerun
- commit hash if committed
- remaining risks or follow-up work

Important reminders:
- use `apply_patch` for edits
- do not touch unrelated user changes
- do not rewrite the benchmark markdown unless you have a real new measured result worth adding
- keep the seam stock-port-friendly
```

## Planning Notes

Why this is the next slice:

- the cold planner bottleneck work is now in a good state after `dfea2b3`
- the remaining architectural debt is that forced-language startup still bypasses the backend abstraction in `entrypoint.py`
- that makes future replay into `subgen-stock` less clean than it needs to be

Current validated baseline for the next slice:

- focused startup-scan tests still pass:
  - `python -m pytest tests/test_startup_scan_cache.py tests/test_startup_scan_backend_selection.py tests/test_startup_scan_planner_trace.py -q`
  - result observed during planning: `32 passed in 1.98s`
- latest measured subset warm baseline remains:
  - `startup_scan.total`: `646.011 ms`
- latest measured subset cold baseline remains:
  - `startup_scan.total`: `17924.375 ms`

Live service note:

- the current `subgen` container was up during planning
- the shared `%APPDATA%\subgen\state\startup_scan_benchmarks.jsonl` file still showed older full-library warm rows, so the fresh subset benchmark files remain the reliable current baseline for this prompt
