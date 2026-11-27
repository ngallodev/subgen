# Smart startup scan and completion tracking

## Design decision

Keep SQLite for reusable inventory and probe results. Add durable work tracking for the persistent backend and reconcile it against the media and subtitle files the service can see. A scan can reuse a cached decision when its inputs still match. A subtitle job succeeds when the worker publishes and verifies the actual output.

The upstream reference is [McCloudS/subgen at ff8a231](https://github.com/McCloudS/subgen/blob/ff8a231fef1962dda63d3fe0ef89545c327159eb/subgen.py#L2540). It walks the configured roots, applies live skip rules, respects directory skip markers, and reports missing or unreadable roots. Its queue prevents duplicate in-process submissions. The persistent backend adds SQLite inventory and decision caching. It needs to preserve the upstream scan behavior while making restart recovery explicit. Keep the legacy backend available as a reference during the rollout.

### Problems in the local implementation

| Area | Behavior to fix |
| --- | --- |
| Restart | The scan stores its manifest after submission to an in-memory queue. A manifest hit reports zero jobs and returns. A restart between queueing and subtitle publication can leave the file unprocessed. |
| Inventory | A stat or name-list match can restore a whole cached subtree without checking its descendants. Adding a file below the configured root, removing a nested subtitle, or editing a file in place can leave that snapshot stale. The force-rewalk setting can still pass a DB-backed inventory cache to traversal. |
| Classification | Cached exclusions depend on the media stat and policy, so a removed sidecar can leave an exclusion in force. A planner can reuse old or absent audio metadata on a cache miss and treat a failed probe as no audio. Pre-audio skip rules can treat an unresolved language as unknown before probing. |
| Completion | The generator catches exceptions, then the worker still refreshes SQLite. The refresh predicts a subtitle path from the input language, while the writer can use a detected language or produce LRC. It can index a predicted file that does not exist. |
| Queue | Classifier apply can run eligibility again through the legacy enqueue helper. Task kwargs can overwrite the detection task type, and force currently bypasses both eligibility and active-path deduplication. |
| Folder handling | The legacy and persistent paths process settings and markers differently. A list of roots separated by spaces can be interpreted as one missing path. |

## Startup reconciliation and persistent jobs

Use a scan to refresh file facts, evaluate eligibility, and reconcile durable jobs. Store skip decisions separately from job state. The in-memory priority queue can wake workers and preserve ordering while SQLite records the work that must survive a restart.

1. Parse and validate the roots. Take a fresh inventory and record which roots were scanned successfully.
2. Reconcile unfinished work and verified outputs. Restore due jobs from their persisted request parameters. Revalidate the source and applicable policy before processing a normal job.
3. Evaluate each media file against the current policy and sidecars. A cached decision still has to reconcile with the job and output records. A matching manifest can reuse facts or decisions with matching identities. It cannot end reconciliation.
4. Commit the durable job before reporting it as queued. Wake the worker after commit. The worker can also find due jobs directly, which covers a process exit between commit and notification.
5. Commit the job claim and release the SQLite transaction before probing or transcribing. Keep file I/O, model work, and the scan itself outside long write transactions.

Use the following lifecycle for file-backed jobs. Direct uploads can retain their existing request lifecycle.

| State | Meaning and next action |
| --- | --- |
| Pending | A durable request awaits a claim. |
| Running | One worker holds a claim with an expiry and a claim token. Renew it during long processing and make expired claims recoverable. |
| Publishing | The worker has prepared and validated a temporary artifact and recorded the intended output and fingerprint. Reconcile this state after interruption. |
| Retry due | A transient probe, input, model, or storage failure has a bounded backoff. Persist attempt count, retry time, and a reason code. |
| Succeeded | A verified output is present and linked to the media version and job that produced it. |
| Failed | A permanent error or exhausted retry budget needs a new request or a relevant input change. |
| Superseded | The media changed, or ordinary work became unnecessary before publication. Replan the current input where appropriate. |

Persist the canonical media path, input fingerprint, operation, requested language, force status, and relevant output policy with the request. Persist the actual output identity and language upon success. Retain successful history for diagnosis and keep one active equivalent request. Claims and reservations must use atomic writes. If planning commits work just before a crash, the next worker can pick it up without repeating the scan.

An ordinary queued job whose output becomes available independently can end with a superseded outcome after revalidation. Explicit force requests preserve their intent across restarts. If their source changes, report the request as superseded and require a new force request for the replacement unless the API explicitly supports processing the new version. Apply backoff to failed probes, unstable input, transient SQLite errors, and failed writes. A successfully probed file with no audio is a definitive skip for that media fingerprint.

Migrate existing databases additively. Old media and sidecar rows can provide cache hints after validation. Reconcile the library to recover eligible work, and create verified completion records only when there is reliable evidence of a prior publication. Existing sidecars still satisfy ordinary skip policy where applicable. Never prune a root's rows after a partial or failed read. Prune derived records for vanished media or sidecars only after a successful scan of that scope.

## Inventory, probes, and skip invalidation

Walk each configured root and stat relevant files. Keep per-directory caches for inexpensive association and reuse expensive probes by file identity. A directory stat describes the directory entry list and cannot prove that nested files remain unchanged. An optional watch-based shortcut needs defined event coverage and overflow recovery before it can stand in for live validation.

- Use a normalized canonical path, size, nanosecond modification time, and available file identity such as device and inode. Use the timestamp resolution exposed by the filesystem and retain a conservative invalidation path for network mounts that cannot provide strong identity. A filename or a same-second timestamp alone proves little about a replacement at the same path.
- Key successful probes by the media fingerprint and probe schema version. Store a successful empty track result distinctly from absent metadata or a probe error. Reuse a valid media probe across sidecar edits and policy changes.
- Key each eligibility decision by the media fingerprint, planner version, relevant policy signature, skip-marker scope, and matching sidecar fingerprint when a subtitle can affect the outcome. Reevaluate on added, changed, renamed, or deleted sidecars. Use the same association rules for indexed and live checks, with an explicit stem boundary and a deterministic rule for competing matches.
- Include the settings used by the relevant decision. These include path mapping for identity, language and external/embedded skip policies, forced-subtitle handling, and output naming settings where expected filenames matter. Output settings cover the SRT/LRC choice, custom language naming, Subgen/model suffixes, and task operation. Preserve ordering in preferred language settings where order changes the result.
- Define force rewalk as bypassing saved directory and decision state for a scan and rebuilding from current filesystem observations. Keep that setting independent of the per-file force endpoint.

Restrict pre-probe skips to evidence that settles eligibility regardless of unresolved audio language. Use the shared skip policy after probing for language-dependent cases. When a previously managed generated subtitle belongs to an older media version, invalidate the completion record and apply the configured stale-output policy. Keep ordinary skip decisions for independent sidecars separate from proof that Subgen completed the current media version.

## Verify and publish the actual subtitle

Make the worker's generation result explicit. Return the output language, the format, and the pathname produced for this job. Derive the filename from the resolved job operation, detected or configured output language, and naming settings. LRC and SRT can follow the same publication protocol.

1. Check the media fingerprint before processing and again before publishing. A changed or vanished input must never be recorded as the one that produced the subtitle.
2. Generate into a unique temporary file in the output directory using the intended SRT or LRC format. Close it and validate that it is a regular file with a readable, valid artifact for the format. Define how a legitimate silent result is represented and keep it distinguishable from a truncated or failed render.
3. Reserve the canonical output path and persist publishing intent with the output fingerprint. Publish with an atomic same-directory replacement while the worker still owns the claim and reservation. A failed forced rerun leaves a prior valid subtitle in place.
4. Verify the published path and fingerprint, then commit the output row and succeeded state together. Emit success and trigger completion callbacks and metadata refresh after the commit. Report callback failures separately so they do not trigger a duplicate transcription.
5. On restart, reconcile a publishing row against its recorded media and artifact fingerprints. A matching published file can finalize interrupted bookkeeping. Otherwise clean up the operation's temporary artifact and retry according to job policy.

The publication fence must prevent a worker whose claim expired from replacing an output after another worker took over. Acquire the reservation before writing a shared destination, and fence the claim check with publication so a takeover cannot occur between authorization and replacement. Scope the reservation to the canonical final output so that media aliases and different files that generate the same basename are serialized too. A cache refresh records a single verified output. The scanner owns the manifest and policy metadata.

## Queue admission and force

Use one planner to produce typed decisions, such as queue, detect language, skip, already active, or retryable error. Carry the actual task type, operation, requested language, source fingerprint, and priority into admission. Enqueue the completed plan once. Language detection should preserve the same request parameters and proceed under the same logical job. If the final filename depends on detection, reserve it when resolved, before writing.

| Request | Admission behavior |
| --- | --- |
| Ordinary eligible media | Submit once or return already active if equivalent work is in progress. |
| Forced request for idle valid media | Bypass ordinary eligibility checks, including existing subtitles and the normal skip rules. Validate the file, operation, and ability to process audio. Persist force intent and return a job ID after admission. |
| Repeated equivalent forced request | Return the active job ID and a distinct already active outcome. |
| Force against a queued compatible ordinary job | Atomically promote the job to forced before a worker claims it, or return an explicit active conflict if promotion is unavailable. |
| Force against a running incompatible request or reserved output | Return busy with the active job ID when available. A caller can retry after it clears. |
| Force after the prior job finishes | Accept new work and allow a verified replacement of the existing output. |

Equivalent requests use canonical media and request parameters. Output reservations use the canonical destination. Requests that resolve to the same artifact must acquire the same reservation even when their operations, language arguments, media extensions, or path aliases differ. Preserve a stable request ID and priority ordering while admission returns the real result. A duplicate or conflict should not appear as freshly queued.

Keep the bulk scan's language override distinct from the single-file force operation. The language override controls language selection and follows normal eligibility. An explicit force request can override a directory skip when the caller names the file directly. Automatic startup, monitoring, and scheduled recovery of ordinary work must respect the marker.

## Root configuration and diagnostics

Share one root parser across startup, the batch API, and monitor setup where they accept the same format. Keep the upstream pipe separator and support spaces inside paths. Trim entry whitespace, deduplicate normalized roots, and report each missing or unreadable root with context about container mounts or permissions. A single-file root can be evaluated without starting a watcher for that file. Canonicalize path mapping consistently so enumeration, queue IDs, sidecar matching, and completion refer to the same media.

Honor `SKIP_STARTUP_SCAN` for automatic enumeration while keeping monitoring available when configured. Recover already accepted jobs without enumerating the library. Apply `.subgen_skip` to a directory and its descendants in enumeration and the watcher, including configured roots beneath a marker. Recheck ordinary pending work against current markers before execution. Include upstream-style per-root files-found, queued, and error counts. Surface partial scans and preserve successful roots even if another root fails.

### Log levels

Use structured fields with stable event names and reason codes. File-level normal diagnostics can include the path where configured. Exported benchmark output should use anonymized root labels and file IDs by default.

| Level | Events and fields |
| --- | --- |
| INFO | Scan start and end with scan ID, backend, run mode, and root count. Per-root summary with status, counts, and phase timings. Final reconciliation counts for eligible, enqueued, already active, skipped, and retryable files. Job success after verified publication. |
| WARNING | Missing or unreadable root, partial scan, transient probe or write failure, and scheduled retry. Include the affected scope, reason code, attempt, and retry time where applicable. |
| ERROR | Scan or DB failure that prevents work from being reconciled, exhausted job retries, and publication failures that need attention. Include scan or job ID and the root cause. |
| DEBUG | Per-file decision and reason, cache validation details, media fingerprint changes, sidecar match result, and queue conflict details. Keep these out of normal summary logs. |

## Benchmark design

Benchmark the current upstream commit and the rebased smart implementation against the same immutable dataset. The historical timings used scan log boundaries or internal persistent timers. Use the worker-free procedure below for the new baseline and per-file results. [Historical startup timings](../startup_scan_pr_benchmarks.md)

| Case | Code and initial state | Measured work |
| --- | --- | --- |
| Upstream baseline | Unmodified upstream scanner semantics at a pinned SHA, with a measurement adapter and no persistent inventory. Start in a fresh process. | Its normal walk, skip checks, real metadata probes, and admission into the isolated queue. |
| Smart scan cold | Pinned smart commit, fresh process, and a unique SQLite path with zero persisted inventory, decision, and completion entries. | Full filesystem inventory, evaluation, queue admission, and initial SQLite writes. |
| Smart scan warm | Same smart commit and dataset, fresh process, and a clean copy of the SQLite database produced by an untimed worker-free cold seed. Record seed row counts and reuse an equivalent copy per repetition. | Live validation, reuse and invalidation work, queue reconciliation, and admission for eligible items. |

### Isolate workers and hold inputs constant

Add `STARTUP_SCAN_BENCHMARK=1`, off by default, to enable startup scan timing records. `STARTUP_SCAN_BENCHMARK_LOGGING=1` remains a compatibility switch for the existing timing events. The full benchmark harness should run one scan to completion in a dedicated command or entry point before any runtime workers, model, API server, observers, or outbound callbacks start. Use a non-consuming admission sink that records typed plans and applies normal duplicate rules. Exercise normal persistent reconciliation and writes against an isolated benchmark database. Use a read-only dataset mount and a separate output directory for benchmark logs and state. Fail the benchmark setup if it cannot enforce worker isolation.

For upstream, use a temporary measurement adapter or overlay at the pinned checkout that intercepts worker startup and the queue boundary while preserving the upstream walk, skip logic, probes, and real admission result. Measure the scan in the dedicated process. Record the adapter revision so the result can be reproduced. Assert zero worker starts, zero transcription or translation invocations, and zero output writes for all three cases.

Keep media paths, skip and naming policy, runtime and ffprobe versions, machine, filesystem/mount, and benchmark log settings comparable. Use a dataset containing media with generated SRT and LRC, matching and unrelated external subtitles, embedded subtitles, missing subtitles, and marked subtrees. Include nested folders and at least one file whose decision requires audio probing. Record anonymized dataset counts and fingerprint, code SHAs, policy fingerprint, probe concurrency, and whether the OS or network filesystem cache was already warm. Database cold describes persisted inventory state. Run a fresh process for each repetition and alternate cases where feasible to limit filesystem-cache ordering effects.

### Capture two separate phases

Use monotonic time. Upstream walks and evaluates files in one loop, so time the iterator's filesystem work independently and time candidate evaluation around its normal queue helper. On the smart path time filesystem enumeration and relevant stats inside inventory collection. Report DB loading and materialization separately from fresh filesystem work. Start each file's evaluation timer when the planner begins work on that candidate. Stop it after the final skip or admission outcome. For parallel planning, report preflight, planner service time, apply time, and overall phase elapsed time so scheduling and batching are visible.

| Benchmark event | Fields |
| --- | --- |
| `startup_scan.benchmark_run` | Run ID, case, revision, dataset and policy fingerprints, configuration, initial SQLite row counts, worker isolation mode. |
| `startup_scan.benchmark_traversal` | Filesystem elapsed time, full inventory elapsed time, directories enumerated, file stats, relevant media and subtitle counts, cache hits and misses, errors. |
| `startup_scan.benchmark_decision` | Run ID, anonymized file ID, outcome and reason, cache source, evaluation and admission timing, optional successful probe time. |
| `startup_scan.benchmark_summary` | Scan wall time, traversal and decision phase elapsed time, SQLite apply/commit time, counts by outcome and reason, median and p95 per-file timing, worker-start and output-write counts. |

Buffer per-file measurements and export outside timed phases. Report wall-clock elapsed time separately from summed per-file work, since parallel file durations can exceed phase wall time. Keep a count of non-media and marker exclusions so the common media cohort can be compared across cases. Compare the set of planned media and their outcomes before interpreting speedups. Document legitimate policy differences and investigate unexpected missing or extra jobs. Report timings across repeated runs and include case counts and cache-hit rates alongside speed ratios. A warm case with missing subtitles must still account for those jobs.

## Implementation and acceptance

1. Preserve upstream root validation, marker handling, and summaries. Consolidate parsers and establish structured planner outcomes and queue admission with force deduplication.
2. Implement explicit verified output publication and the durable job lifecycle. Migrate the database and implement recovery for interrupted claims and publication.
3. Replace unsafe subtree and exclusion shortcuts with validated fingerprints and prove that a warm scan still reconciles eligible work. Retain the reusable facts and instrument the new scan without changing eligibility.
4. Run the three worker-free benchmarks and review parity and timing before expanding the persistent backend rollout.

Acceptance cases include a restart with pending work and an unchanged manifest, a crash before and after publishing, a failed forced rerun with an existing valid subtitle, repeat force requests, and colliding output names. Exercise nested additions and sidecar removal, same-name replacement, changed skip and naming policy, marker additions and removals, files changed while queued, missing roots, and transient probe failures. Demonstrate correct SRT and LRC indexing and an accurate report for each benchmark case with no worker activity.
