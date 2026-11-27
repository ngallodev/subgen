# Warm smart queue cache

## Decision

Make the persistent scan a metadata reconciliation pass followed by planning for the subset whose decision is missing or no longer valid. Reuse an unchanged skip decision or verified completed output without probing the media. Reuse a valid pending plan through the durable queue. Keep the legacy backend and the existing opt-in settings for persistent startup and benchmarking.

SQLite can answer whether Subgen already has a decision and what facts supported it. A fresh metadata check establishes whether those facts still apply. A directory timestamp alone cannot establish that every descendant or file is unchanged, especially across a restart on SMB. The first implementation should cover every configured directory and eliminate repeated media inspection, subtitle linking, and writes wherever the observed inputs allow it.

## What the recorded run tells us

The benchmark walked 166 entries in one directory. Smart cold and warm both recorded 62 media decision events. An event represents coverage of an item and can come from SQLite or from a fresh plan. The default run recorded 63 events. Resolve that one-item cohort mismatch before asserting speedup or eligibility parity. Legacy file enumeration can pass nonregular entries to the queue helper, while persistent inventory filters to regular entries without following symlinks. Path mapping and extension checks can also affect cohort selection. Record fixed count categories to identify which difference applies.

| Case | Scan wall time | Per-file median | Per-file p95 |
| --- | ---: | ---: | ---: |
| Default cold | 216.669 s | 1.952 s | 5.203 s |
| Default warm | 100.981 s | 0.909 s | 2.193 s |
| Smart cold | 59.104 s | 2.524 s | 4.657 s |
| Smart warm | 54.186 s | 2.107 s | 5.284 s |

Smart cold can overlap media planning across several threads. Parallel probes can raise individual latency while reducing the elapsed time of the whole scan. The smart miss timer also includes subtitle signature computation, while the default timer wraps the queue helper and includes admission. The smart fast path currently puts admission time into its evaluation sample. Separate those phases before attributing any seconds to cache lookup, probe, signatures, or admission. The default cold and warm cases run in separate processes and have no persistent cache. Their run order allows filesystem, server, and I/O conditions to differ.

The warm report says that 50 baseline items were absent from its valid comparison set and flags invalid decision events. It does not record a probe failure count. In `classifier.py`, the cached apply branches can admit a job and continue without setting a final outcome. The resulting event falls back to the planner status `queue` or `detect`, which the runner rejects. The runner also rejects `media_probe_failed`. Distinguish these cases before drawing conclusions from the prior 50 missing entries. The benchmark deletes its temporary state after producing its report, so the saved aggregate cannot recover the breakdown.

Smart cold runs no workers in the benchmark. The warm run can test reuse of exclusions, audio metadata, and eligible plans, but its copied scan DB cannot demonstrate reuse of jobs completed by a worker. It also omits the cold job journal, so warm eligibility can still lead to fresh admission. Add a separate verification scenario for completed outputs and recovery using fixtures.

## Cache validity

Use canonical source identity and request semantics for lookups. A cache row can be reused only when its evidence version, source identity, and decision policy match, and the relevant filesystem scope was observed successfully.

| State | Evidence for reuse | Warm behavior |
| --- | --- | --- |
| `excluded` due to a source property | Same media fingerprint, policy, and a typed reason whose inputs are in the source, such as no audio confirmed by a successful probe | Keep the exclusion without calling the planner. |
| `excluded` due to subtitles or LRC | Same media fingerprint, policy, and relevant sidecar evidence | Keep the exclusion. Replan when a matching sidecar is added, changed, removed, or becomes uncertain. |
| `completed` | Durable successful publication for a compatible operation and language on this media version, with the recorded output present as a regular file with its verified fingerprint | Reuse the completion and issue no normal startup admission. |
| `eligible` plan | Same fingerprint, policy, and sidecar evidence, with a valid serialized task and any required probe metadata | Use the plan without evaluating eligibility again. Recover or deduplicate active work through the durable queue. |
| `retryable_error`, unknown, partial observation, or old evidence | Prior state cannot settle eligibility | Send to the normal planning or retry path. |

Use source size, nanosecond mtime, and platform identity where available. A changed mount/device identity, missing strong fingerprint, malformed data, or known metadata error should cause a conservative miss. Use fingerprints sampled by inventory when writing rows so an extra stat cannot mix two media versions. A cached audio probe is reusable by media fingerprint even when a policy or sidecar change requires a fresh decision. Keep an explicit success flag for the probe so an empty track list from a successful probe can be distinguished from failure or a missing probe.

Use the existing policy fingerprint, with a version bump for changes in interpretation. Check that it covers naming, matching, operation, language, output format, and roots/path mapping semantics relevant to a persisted decision. Compute it once per run. A change invalidates the affected plans and skips. Old rows with absent evidence can seed independent facts such as fingerprint-matched probe metadata. Reclassify their decisions once and replace them with versioned evidence.

Use stable skip codes with explicit dependencies. A generic `skipped` code should depend on all relevant source and sidecar evidence until the planner emits specific reasons. Store a real empty sidecar signature separately from missing evidence. Record the signature for skipped items as well as eligible items. Base it on all files that the skip rules can consider, including nested subtitle locations and exact output or LRC paths. The directory relationships used for signing must match the actual policy search scope. An inventory for a single-file root or a mapped source can lack the relevant siblings. Enumerate that scope explicitly or fall back to ordinary validation. Matching evidence from a partial directory listing cannot authorize a cached exclusion or completion.

Use the durable job journal as the authority for publication. Check that the successful row belongs to the same source fingerprint, operation, and compatible requested language and that the actual output matches the recorded metadata. The worker has already verified the file digest when it committed publication. A matching metadata fingerprint is enough during normal startup. On metadata mismatch, use conservative replanning or revalidate the digest only for the exceptional case. An existing subtitle without matching publication evidence can still satisfy ordinary skip rules. A cached `eligible` row with a `completed` reason is an old representation and cannot serve as proof by itself.

## Startup flow

1. Parse roots and resolve cache settings. Load current policy and scoped SQLite metadata in batches. Keep `SKIP_STARTUP_SCAN` and `STARTUP_SCAN_FORCE_REWALK` semantics. Recover due durable work using the existing worker claim path, including when the startup scan is skipped.
2. Walk every covered directory on normal startup. Filter by supported extension before statting ordinary file entries. Check marker changes and compare candidate metadata against the saved snapshot. Reuse stored inventory records for matching entries. Preserve the current symlink policy. Track complete and partial coverage independently so an unreadable or skipped subtree cannot be treated as empty and pruned.
3. Build a sidecar index once per run from the observed metadata and use it to compare relevant dependencies. Reuse links for unchanged rows. Recalculate only affected scopes when a sidecar or its matching scope changes. A per-run directory index also avoids rebuilding and sorting recursive sidecar lists for each media item. Precompute that index before parallel planning or use a concurrency-safe lazy cache.
4. Classify each observed media item in a cheap preflight. Emit `cached_excluded` or `cached_completed` for compatible resolved items. Hand compatible plans to one durable admission path. Plan only unknown or invalid items. Reuse valid audio metadata during a replan when available. Persist the plan's actual final outcome through apply so telemetry and decisions agree. A previously cached plan must never override a new planner result after invalidation.
5. Write changed inventory and decision rows plus prunable deletions. Avoid upserts of identical subtitle, media, and exclusion rows. For unresolved or changed jobs, deduplicate in the journal and preserve lease, retry, detection handoff, and output reservation behavior. Refresh the scan cache as a derived record after a verified publication. A failure to refresh the derived cache must leave the successful journal row discoverable. Use the consistent journal read and admission checks to close the race where a worker publishes while startup is considering admission for the same source.
6. Advance a root's stored coverage only after a successful traversal and commit. Keep prior evidence for missing, unreadable, or marker-pruned roots as appropriate. Where another configured root overlaps a subtree, an incomplete scan cannot establish deletion of shared rows. Preserve directory coverage validation, monitor setup, and the ability for watcher events to trigger fresh planning. Explicit `/force` bypasses cached completion, exclusion, and normal skip policy through the existing forced admission path. It still uses durable deduplication and publication safeguards.

The existing `use_directory_cache=False` deliberately avoids stale whole-subtree snapshots. Re-enabling that old shortcut or restoring an unconditional manifest return would miss changes and can also skip admission or recovery. Treat the default warm traversal as a metadata comparison over the live tree. To skip physical directory work in a future implementation, require an authoritative subtree version or a persisted, gap-checked change feed that covers downtime, in-place changes, and descendant changes. Lost or unsupported coverage falls back to the live walk. No such contract is assumed for the current Docker/SMB setup.

## Observability and acceptance

Keep normal INFO output to totals, DEBUG to diagnostics, and benchmark instrumentation off by default. Publish aggregate-only records with fixed keys and safe error categories. Leave file paths, names, hashes, raw exception text, and root structure out of the saved benchmark report. Transient internal comparison keys can remain in memory for parity and then be discarded.

- Report roots and directories enumerated, entries seen, relevant files statted, media and subtitle counts, metadata records reused, and read or stat errors. Compare directory coverage with the default backend for each run.
- Report media observed, cached exclusions, cached completions, cached eligible plans, actual planner calls, actual probe calls, cached probe uses, durable admits, already active jobs, retryable errors, and skip counts by typed reason. Include per-reason cache misses for missing/old rows, media fingerprint, policy, sidecar evidence, incomplete observations, output mismatch, failed probe, and forced rewalk.
- Time walking, metadata and sidecar validation, SQLite reads and writes, planner evaluation, probe, and queue admission separately. Publish per-file median/p95 and sample counts for cached validation and actual planner execution. A zero-sample cohort should have no timing value. Measure equivalent decision boundaries for both backends, and keep transcription, translation, and language-detection execution outside timings.
- Use separate counters for successful events, actual probe failures, invalid outcome codes, duplicates, and missing items. Compare media coverage and meaningful eligibility across all four startup cases. Completion suppression and active journal recovery require their own expected outcomes. Record the smart cold and warm planner and probe counts alongside the number of files observed.

An unchanged fixture containing resolved and reusable rows should have the same file and directory coverage on both smart runs. With valid evidence and no unresolved rows, smart warm should make zero planner calls, zero media probes, and zero subtitle or media row rewrites. An eligible fixture may cause durable admission in the benchmark with a fresh job journal. A fixture with a completed job and preserved journal should generate no new admission for that item. A dirty fixture should replan exactly the affected items where the scope is known. Include additions, nested marker changes, deleted or modified sidecars, replaced media at the same path, policy changes, missing output, temporary access failures, workers already active, old DB rows, and `/force` after a completion. Compare default cold, default warm, smart cold, and smart warm on each anonymized dataset after the coverage and decision checks pass.

## Implementation order

1. Fix final outcome reporting and retain aggregate miss and probe breakdowns. Preserve coverage checks and establish the cause of any difference in the media cohort.
2. Add per-media decision evidence and a preflight short circuit for trusted exclusions and eligible plans, with verified completion from the durable journal. Preserve the original planner as the fallback. Version and migrate evidence additively so existing DBs can warm again after one scan.
3. Trim directory inventory work to live metadata comparison and reuse unchanged records, associations, and SQLite rows. Keep full recursive scope validation on filesystems without reliable change tracking.
4. Verify the completed and retry fixtures and rerun the four benchmark cases. Treat a faster run as valid only when the saved counts show matching expected outcomes and the measured work was isolated from transcription.
