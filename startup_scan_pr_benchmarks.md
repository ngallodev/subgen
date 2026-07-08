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

## Practical Takeaways For PR Discussion

1. The seam reapply on stock-based `subgen.py` is functionally intact and does not cause a broad warm-start regression in overall startup time.
2. The persistent backend still delivers a large warm-cache benefit.
3. Empty-DB cold runs remain dominated by planner work, not by recursive directory inventory.
4. Any next performance PR should focus first on reducing `startup_scan.classify_media.parallel_plan` cost on uncached media.
5. Full-library empty-DB cold runs are impractical on the real library; sanitized subset cold/warm runs are the right repeatable benchmark format for PR evidence.

## Follow-Up Benchmark Recommendations

Recommended additions for later benchmark updates:

1. Add one more anonymized subset tier:
   - small: about `25-50` media files
   - medium: about `200-500` media files
2. Capture one legacy-path wall-clock run on the same anonymized subset for comparison against persistent cold and warm.
3. Repeat the subset cold/warm runs after planner optimizations to measure direct impact on:
   - `startup_scan.classify_media.parallel_plan`
   - `startup_scan.total`
