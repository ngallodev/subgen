# Startup Scan PR Benchmarks

## Scope

This document captures benchmark evidence for the startup-scan backend work in a format suitable for a pull request discussion.

Constraints followed in this artifact:

- no database files are checked into the repo
- no benchmark JSONL files are checked into the repo
- no media file names are included
- no media library paths are included
- only aggregate timings, configuration, and anonymized subset counts are recorded here

## Code Under Test

- branch: `codex/reapply-backend-seam`
- benchmark commit: `55bebbf`
- benchmarked app version in container logs: `2026.06.6`

## Benchmark Method

### Persistent backend, full-library warm run

Warm full-library runs were measured by starting the container with:

- `STARTUP_SCAN_BACKEND=persistent`
- existing host-mounted startup-scan DB
- existing host-mounted benchmark JSONL

These numbers are useful for comparing the current seam-reapplied `subgen.py` against the prior extracted-backend branch on a realistic warm cache.

### Persistent backend, subset cold/warm run with empty temp DB

Cold and warm subset runs were measured with:

- `STARTUP_SCAN_BACKEND=persistent`
- a temporary DB path under the host-mounted state volume
- a temporary benchmark JSONL path under the host-mounted state volume
- an anonymized representative subset (`Subset A`)

Sanitized subset description:

- `Subset A`
- media files: `33`
- subtitle files: `34`

Cold run definition:

- temp DB file removed before the run
- benchmark JSONL file removed before the run

Warm run definition:

- same subset
- same temp DB file reused from the cold run
- same temp benchmark JSONL file reused so the second block can be read as the warm rerun

### Local-only commands used

The actual subset path is intentionally omitted from this checked-in note.

Persistent subset benchmark template:

```bash
docker compose run -d --name subgen-bench \
  -e 'TRANSCRIBE_FOLDERS=<SANITIZED_SUBSET_A_PATH>' \
  -e STARTUP_SCAN_BACKEND=persistent \
  -e STARTUP_SCAN_DB_PATH=/subgen/state/pr-bench-subset-a.db \
  -e STARTUP_SCAN_BENCHMARK_LOG_PATH=/subgen/state/pr-bench-subset-a.jsonl \
  subgen
```

The dedicated temp files used for these runs were:

- `pr-bench-subset-a.db`
- `pr-bench-subset-a.jsonl`

These files remain local-only in the host-mounted state volume and are not intended for git.

## Results

### Full-Library Warm Comparison

| Scenario | Commit Context | `startup_scan.inventory` | `startup_scan.subtitle_relink` | `startup_scan.monitor_setup` | `startup_scan.total` |
| --- | --- | ---: | ---: | ---: | ---: |
| Persistent warm baseline before stock reset | prior extracted-backend branch | 1437.274 ms | 112.490 ms | 59.855 ms | 7080.813 ms |
| Persistent warm after seam reapply | `55bebbf` | 2554.762 ms | 377.958 ms | 36.409 ms | 6205.579 ms |
| Persistent warm after planner optimizations | current working tree verification run | 1949.813 ms | 473.110 ms | 102.183 ms | 6826.118 ms |

Observations:

- warm `total` improved from `7080.813 ms` to `6205.579 ms`
- warm `inventory` regressed from `1437.274 ms` to `2554.762 ms`
- warm `monitor_setup` improved from `59.855 ms` to `36.409 ms`
- warm `subtitle_relink` regressed from `112.490 ms` to `377.958 ms`

Interpretation:

- the seam reapply did not introduce a broad warm-start regression in total startup time
- the current warm path is still fast overall
- the main warm-path regressions are in `inventory` and `subtitle_relink`, not in total orchestration

### Subset A Persistent Cold vs Warm

| Scenario | DB State | `startup_scan.inventory` | `startup_scan.subtitle_index` | `startup_scan.classify_media.preflight` | `startup_scan.classify_media.parallel_plan` | `startup_scan.classify_media.apply` | `startup_scan.classify_media` | `startup_scan.subtitle_relink` | `startup_scan.monitor_setup` | `startup_scan.total` |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Subset A cold | empty temp DB | 6748.547 ms | 11.729 ms | 0.152 ms | 57940.420 ms | 15.962 ms | 58059.195 ms | n/a | 25.749 ms | 65310.959 ms |
| Subset A warm | same temp DB reused | 322.967 ms | n/a | n/a | n/a | n/a | n/a | 14.253 ms | 12.476 ms | 1670.380 ms |

Derived comparisons:

- warm `total` is about `39.1x` faster than cold on the same subset
- warm `inventory` is about `20.9x` faster than cold on the same subset

Cold-path dominance:

- `startup_scan.classify_media.parallel_plan` alone consumed `57940.420 ms`
- that is about `88.7%` of the cold-run `startup_scan.total`

Interpretation:

- the expensive part of the empty-DB cold path is not directory inventory
- the dominant cold bottleneck is planner work during `classify_media.parallel_plan`
- the persistent backend cache is highly effective once the DB is populated

### Subset A Optimization Progression

The same `Subset A` dataset was rerun after two cold-path planner optimizations:

1. defer subtitle-signature work for early `skip` and `active` outcomes
2. use `get_audio_tracks()` as the single audio probe in planner queue preparation instead of separate `has_audio()` and `get_audio_tracks()` calls

| Scenario | `startup_scan.inventory` | `startup_scan.classify_media.parallel_plan` | `startup_scan.classify_media` | `startup_scan.total` |
| --- | ---: | ---: | ---: | ---: |
| Baseline cold from `55bebbf` | 6748.547 ms | 57940.420 ms | 58059.195 ms | 65310.959 ms |
| After early skip/active signature deferral | 5630.881 ms | 51857.729 ms | 52092.994 ms | 58708.079 ms |
| After single-probe planner path | 5773.772 ms | 33582.755 ms | 33689.213 ms | 39939.568 ms |

Cold-path deltas versus baseline:

- deferring subtitle signatures reduced cold `parallel_plan` by `6082.691 ms` and cold `total` by `6602.880 ms`
- switching planner probe work to a single `get_audio_tracks()` call reduced cold `parallel_plan` by `24357.665 ms` and cold `total` by `25371.391 ms`
- combined cold improvement from baseline to the latest run:
  - `startup_scan.classify_media.parallel_plan`: `57940.420 ms` -> `33582.755 ms` (`24357.665 ms`, about `42.0%` faster)
  - `startup_scan.total`: `65310.959 ms` -> `39939.568 ms` (`25371.391 ms`, about `38.8%` faster)

Latest warm rerun on the same temp DB after both optimizations:

| Scenario | `startup_scan.inventory` | `startup_scan.subtitle_relink` | `startup_scan.monitor_setup` | `startup_scan.total` |
| --- | ---: | ---: | ---: | ---: |
| Baseline warm from `55bebbf` | 322.967 ms | 14.253 ms | 12.476 ms | 1670.380 ms |
| Warm after first planner optimization | 30.098 ms | 5.207 ms | 60.829 ms | 935.574 ms |
| Warm after both planner optimizations | 34.597 ms | 13.308 ms | 3.844 ms | 371.956 ms |

Interpretation:

- the new planner changes materially improved cold startup on the 33-file uncached subset
- warm subset performance also improved substantially and remained sub-second total
- inventory is no longer the dominant cold cost on this subset
- the remaining cold bottleneck is still planner work, but the next target has shifted

## Practical Takeaways For PR Discussion

1. The seam reapply on stock-based `subgen.py` is functionally intact and does not cause a broad warm-start regression in overall startup time.
2. The persistent backend still delivers a large warm-cache benefit.
3. Empty-DB cold runs remain dominated by planner work, not by recursive directory inventory.
4. Any next performance PR should focus first on reducing `startup_scan.classify_media.parallel_plan` cost on uncached media.
5. Full-library empty-DB cold runs are impractical on the real library; sanitized subset cold/warm runs are the right repeatable benchmark format for PR evidence.


## Planner Detail Rows

When planner-trace logging is enabled, `classify_media` now writes an additional opt-in row:

- `startup_scan.classify_media.parallel_plan_detail`
  - `pending_count`
  - `traced_count`
  - `subtitle_signature_total_ms`, `subtitle_signature_avg_ms`, `subtitle_signature_max_ms`
  - `queue_plan_total_ms`, `queue_plan_avg_ms`, `queue_plan_max_ms`
  - `probe_total_ms`, `probe_avg_ms`, `probe_max_ms`
  - `language_total_ms`, `language_avg_ms`, `language_max_ms`
  - `detect_total_ms`, `detect_avg_ms`, `detect_max_ms`
  - `active_check_ms_*`, `has_audio_ms_*`, `audio_tracks_ms_*`, `audio_langs_ms_*`
  - `choose_language_ms_*`, `skip_check_ms_*`, `detect_branch_ms_*`
  - `cached_audio_track_plan_count`
  - `audio_track_count_total`
  - `audio_lang_count_total`

The existing `startup_scan.classify_media.parallel_plan_breakdown` and `startup_scan.classify_media.parallel_plan_trace` rows remain unchanged, so older benchmark comparisons still line up.

Latest measured planner-detail takeaway from the final `Subset A` cold rerun:

- `subtitle_signature_total_ms`: `0.0 ms`
- `queue_plan_total_ms`: `132186.979 ms`
- `probe_total_ms`: `74126.712 ms`
- `language_total_ms`: `58059.864 ms`
- `detect_total_ms`: `0.0 ms`
- dominant sub-slices inside the trace rollup:
  - `audio_tracks_ms_total_ms`: `74126.525 ms`
  - `skip_check_ms_total_ms`: `58059.569 ms`
  - `has_audio_ms_total_ms`: `0.0 ms`

Interpretation:

- the previous `has_audio()` probe cost has effectively been eliminated from this path
- the next cold-start target is now `describe_skip_reason()` / skip-check work, with `get_audio_tracks()` still the largest remaining probe slice

## Follow-Up Benchmark Recommendations

Recommended additions for later benchmark updates:

1. Add one more anonymized subset tier:
   - small: about `25-50` media files
   - medium: about `200-500` media files
2. Capture one legacy-path wall-clock run on the same anonymized subset for comparison against persistent cold and warm.
3. Repeat the subset cold/warm runs after planner optimizations to measure direct impact on:
   - `startup_scan.classify_media.parallel_plan`
   - `startup_scan.total`
