"""Run four aggregate-only startup comparisons on a read-only media dataset.

Default cold/warm use the legacy backend. Smart cold/warm use the persistent
backend. Every scan runs in a fresh process with transcription workers disabled.
The final report contains timings and counts only; temporary identifiers and
state are deleted after parity checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
import uuid
from collections import Counter
from pathlib import Path


def _git_sha(path: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _path_id(path: str) -> str:
    return hashlib.sha256(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()[:16]


def _code_fingerprint(repo: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(repo.rglob("*.py")):
        if any(part in {".git", ".venv", "__pycache__", "site-packages"} for part in path.parts):
            continue
        digest.update(path.relative_to(repo).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _dataset_fingerprint(roots: list[str]) -> str:
    digest = hashlib.sha256()
    for root in roots:
        root_path = Path(root)
        if root_path.is_file():
            paths = [root_path]
        elif root_path.is_dir():
            paths = [Path(directory) / name for directory, _, names in os.walk(root) for name in names]
        else:
            raise RuntimeError(f"Dataset root does not exist or cannot be read: {root}")
        for path in sorted(paths, key=lambda item: os.path.normcase(str(item))):
            try:
                stat = path.stat()
            except OSError as exc:
                raise RuntimeError(f"Could not fingerprint dataset file {path}: {exc}") from exc
            digest.update(os.path.normcase(str(path.relative_to(root_path.parent if root_path.is_file() else root_path))).encode("utf-8"))
            digest.update(f"{stat.st_size}:{getattr(stat, 'st_mtime_ns', int(stat.st_mtime * 1e9))}:{stat.st_dev}:{stat.st_ino}\n".encode("ascii"))
    return digest.hexdigest()


def _run_child(case: str, repo: Path, roots: list[str], state: Path, output: Path, *, backend: str):
    state.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    log_path = output / f"{case}.jsonl"
    result_path = output / f"{case}.result.json"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker-case",
        case,
        "--worker-repo",
        str(repo),
        "--worker-state",
        str(state),
        "--worker-log",
        str(log_path),
        "--worker-result",
        str(result_path),
        "--backend",
        backend,
    ]
    for root in roots:
        command.extend(["--root", root])
    env = os.environ.copy()
    untimed_seed = case == "warm-seed"
    env.update(
        {
            "STARTUP_SCAN_BACKEND": backend,
            "STARTUP_SCAN_DB_PATH": str(state / "startup_scan.db"),
            "STARTUP_SCAN_BENCHMARK_LOG_PATH": str(log_path),
            "STARTUP_SCAN_BENCHMARK_CASE": case,
            "STARTUP_SCAN_BENCHMARK_REVISION": _git_sha(repo),
            "STARTUP_SCAN_BENCHMARK": "0" if untimed_seed else "1",
            "STARTUP_SCAN_BENCHMARK_WORKER_ISOLATION": "1",
            "STARTUP_SCAN_BENCHMARK_UNTIMED_SEED": "1" if untimed_seed else "0",
            "STARTUP_SCAN_BENCHMARK_LOGGING": "0" if untimed_seed else "1",
            "MONITOR": "False",
        }
    )
    env["STARTUP_SCAN_BENCHMARK_DATASET_FINGERPRINT"] = os.getenv(
        "STARTUP_SCAN_BENCHMARK_DATASET_FINGERPRINT", "unknown"
    )
    subprocess.run(command, cwd=repo, env=env, check=True)
    return json.loads(result_path.read_text(encoding="utf-8"))


def _copy_sqlite(source: Path, destination: Path) -> None:
    if not source.exists():
        raise RuntimeError(f"Expected SQLite seed database was not created: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as destination_db:
        source_db.backup(destination_db)


def _decision_metrics(records: list[dict]) -> dict:
    decisions = {}
    samples = []
    outcomes = Counter()
    skip_reason_counts = Counter()
    non_media_count = 0
    duplicate_decisions = 0
    invalid_decisions = 0
    probe_failures = 0
    probe_summary = next(
        (row for row in records if row.get("event") == "startup_scan.benchmark_probe_summary"),
        {},
    )
    cache_source_counts = Counter()
    planner_calls = 0
    for row in records:
        if row["event"] != "startup_scan.benchmark_decision":
            continue
        outcome = row.get("outcome")
        cache_source = row.get("cache_source", "unknown")
        cache_source_counts[cache_source] += 1
        if cache_source in {"miss", "none"}:
            planner_calls += 1
        if outcome == "non_media":
            non_media_count += 1
            continue
        file_id = row.get("file_id")
        samples.append(float(row["evaluation_ms"]))
        outcomes[outcome] += 1
        if outcome == "skip":
            skip_reason_counts[_skip_reason_category(row.get("reason", ""))] += 1
        if row.get("reason") == "media_probe_failed":
            probe_failures += 1
        if not file_id:
            invalid_decisions += 1
        elif file_id in decisions:
            duplicate_decisions += 1
        elif row.get("reason") == "media_probe_failed":
            invalid_decisions += 1
        elif outcome in {"queued", "already_active", "queue", "detect"}:
            decisions[file_id] = "eligible"
        elif outcome == "skip":
            decisions[file_id] = "skipped"
        else:
            invalid_decisions += 1

    samples.sort()
    return {
        "decision_outcomes": decisions,
        "decision_count": len(samples),
        "decision_median_ms": round(statistics.median(samples), 3) if samples else 0.0,
        "decision_p95_ms": round(samples[math.ceil(len(samples) * 0.95) - 1], 3) if samples else 0.0,
        "duplicate_media_decisions": duplicate_decisions,
        "invalid_media_decisions": invalid_decisions,
        "probe_request_count": int(probe_summary.get("probe_requests", 0)),
        "probe_failure_count": int(probe_summary.get("probe_failures", probe_failures)),
        "cache_source_counts": dict(cache_source_counts),
        "planner_call_count": planner_calls,
        "non_media_count": non_media_count,
        "outcomes": dict(outcomes),
        "skip_reason_counts": dict(skip_reason_counts),
    }


def _skip_reason_category(reason: str) -> str:
    text = str(reason).lower()
    if "marker" in text and "skip" in text:
        return "skip_marker"
    if "lrc" in text:
        return "lrc_exists"
    if "audio" in text and ("unknown" in text or "no_audio" in text or "no audio" in text):
        return "audio_language_or_track"
    if "subtitle" in text or "caption" in text:
        return "subtitle_already_present"
    if "probe" in text or "permission" in text or "inaccessible" in text:
        return "probe_or_access_issue"
    if not reason or "unreported" in text or "legacy_queue_result" in text:
        return "skip_reason_unreported"
    return "other_skip_reason"


def _compare_decisions(baseline_result: dict, candidate_result: dict) -> dict:
    baseline = baseline_result["decision_outcomes"]
    candidate = candidate_result["decision_outcomes"]
    missing = baseline.keys() - candidate.keys()
    extra = candidate.keys() - baseline.keys()
    changed = {file_id for file_id in baseline.keys() & candidate.keys() if baseline[file_id] != candidate[file_id]}
    errors = {
        field
        for row in (baseline_result, candidate_result)
        for field in ("duplicate_media_decisions", "invalid_media_decisions")
        if row[field]
    }
    if any((row.get("summary") or {}).get("errors", 0) for row in (baseline_result, candidate_result)):
        errors.add("scan_errors")
    if baseline_result.get("directories_enumerated") != candidate_result.get("directories_enumerated"):
        errors.add("directory_walk_count_mismatch")
    if baseline_result.get("directory_fingerprint") != candidate_result.get("directory_fingerprint"):
        errors.add("directory_walk_coverage_mismatch")
    if any(row.get("directory_scan_errors", 0) for row in (baseline_result, candidate_result)):
        errors.add("directory_walk_errors")
    if not baseline or not candidate:
        errors.add("empty_media_cohort")
    return {
        "matches": not (missing or extra or changed or errors),
        "missing_media_count": len(missing),
        "extra_media_count": len(extra),
        "changed_decision_count": len(changed),
        "errors": sorted(errors),
    }


def _public_case(result: dict) -> dict:
    """Return aggregate-only fields; discard paths, per-file IDs, and state details."""
    summary = result.get("summary") or {}
    return {
        "case": result.get("case"),
        "scan_elapsed_ms": result.get("scan_elapsed_ms"),
        "filesystem_elapsed_ms": result.get("filesystem_elapsed_ms"),
        "inventory_elapsed_ms": result.get("inventory_elapsed_ms"),
        "iterator_traversal_ms": result.get("iterator_traversal_ms"),
        "decision_median_ms": result.get("decision_median_ms"),
        "decision_p95_ms": result.get("decision_p95_ms"),
        "media_decisions": result.get("decision_count"),
        "non_media_files": result.get("non_media_count"),
        "directories_walked": result.get("directories_enumerated"),
        "directory_walk_errors": result.get("directory_scan_errors"),
        "files_walked": result.get("walk_files_seen"),
        "media_files": result.get("media_count"),
        "subtitle_files": result.get("subtitle_count"),
        "skip_reason_counts": result.get("skip_reason_counts", {}),
        "worker_starts": result.get("worker_starts"),
        "subtitle_generation_calls": result.get("transcription_or_translation_calls"),
        "subtitle_writes": result.get("output_writes"),
        "probe_failure_count": result.get("probe_failure_count"),
        "probe_request_count": result.get("probe_request_count"),
        "planner_call_count": result.get("planner_call_count"),
        "cache_source_counts": result.get("cache_source_counts", {}),
        "invalid_decision_count": result.get("invalid_media_decisions"),
        "duplicate_decision_count": result.get("duplicate_media_decisions"),
        "scan_errors": summary.get("errors", 0),
    }


def _summary_counts(summary) -> dict:
    if not isinstance(summary, dict):
        return {}
    fields = ("files_found", "queued", "errors", "media_count", "subtitle_count", "ignored_count", "excluded_count")
    counts = {key: summary[key] for key in fields if isinstance(summary.get(key), (int, float))}
    folders = summary.get("folders")
    if isinstance(folders, list):
        for field in ("files_found", "queued", "errors"):
            counts[field] = sum(
                int(row.get(field, 0)) for row in folders if isinstance(row, dict) and isinstance(row.get(field, 0), (int, float))
            )
    return counts


def _worker(args) -> int:
    if os.getenv("STARTUP_SCAN_BENCHMARK_WORKER_ISOLATION") != "1":
        raise RuntimeError("Worker isolation must be enabled for benchmark runs")
    is_untimed_seed = os.getenv("STARTUP_SCAN_BENCHMARK_UNTIMED_SEED") == "1"
    if os.getenv("STARTUP_SCAN_BENCHMARK") != "1" and not is_untimed_seed:
        raise RuntimeError("STARTUP_SCAN_BENCHMARK must be enabled for benchmark runs")

    import logging
    import runpy
    import threading
    import time as monotonic_time

    worker_start_count = 0
    blocked_worker_start_attempts = 0
    original_thread_start = threading.Thread.start

    def isolated_thread_start(thread, *thread_args, **thread_kwargs):
        nonlocal worker_start_count, blocked_worker_start_attempts
        target = getattr(thread, "_target", None)
        if getattr(target, "__name__", "") == "transcription_worker":
            blocked_worker_start_attempts += 1
            return None
        return original_thread_start(thread, *thread_args, **thread_kwargs)

    threading.Thread.start = isolated_thread_start
    records = []

    import logging

    class SkipReasonHandler(logging.Handler):
        def __init__(self):
            super().__init__(logging.DEBUG)
            self.current_reason = None

        def emit(self, record):
            message = record.getMessage()
            if "skip" in message.lower() or "already exist" in message.lower():
                self.current_reason = _skip_reason_category(message)

    skip_reason_handler = SkipReasonHandler()
    root_logger = logging.getLogger()
    original_root_handlers = root_logger.handlers[:]
    original_root_level = root_logger.level

    def emit(event: str, **fields):
        records.append({"event": event, "ts": time.time(), **fields})

    original_walk = os.walk
    original_scandir = os.scandir
    traversal_ms = 0.0
    directory_enumeration_ms = 0.0
    directories = 0
    files_found = 0
    directory_scan_errors = 0
    visited_directories = set()

    def timed_walk(*walk_args, **walk_kwargs):
        iterator = iter(original_walk(*walk_args, **walk_kwargs))

        def rows():
            nonlocal traversal_ms
            while True:
                started = monotonic_time.perf_counter()
                try:
                    row = next(iterator)
                except StopIteration:
                    traversal_ms += (monotonic_time.perf_counter() - started) * 1000
                    return
                traversal_ms += (monotonic_time.perf_counter() - started) * 1000
                yield row

        return rows()

    sys.path.insert(0, str(args.worker_repo))
    namespace = runpy.run_path(str(args.worker_repo / "subgen.py"), run_name="subgen_benchmark")
    scan_globals = namespace["transcribe_existing"].__globals__
    original_get_audio_tracks = scan_globals.get("get_audio_tracks")
    probe_requests = 0
    probe_failures = 0

    def measured_get_audio_tracks(*call_args, **call_kwargs):
        nonlocal probe_requests, probe_failures
        probe_requests += 1
        try:
            return original_get_audio_tracks(*call_args, **call_kwargs)
        except Exception:
            probe_failures += 1
            raise

    if original_get_audio_tracks:
        scan_globals["get_audio_tracks"] = measured_get_audio_tracks

    def timed_scandir(path):
        nonlocal directory_enumeration_ms, directories, files_found, directory_scan_errors
        started = monotonic_time.perf_counter()
        try:
            with original_scandir(path) as iterator:
                entries = list(iterator)
        except OSError:
            directory_scan_errors += 1
            raise
        finally:
            directory_enumeration_ms += (monotonic_time.perf_counter() - started) * 1000
        directories += 1
        files_found += len(entries)
        visited_directories.add(_path_id(os.fspath(path)))

        class ScandirSnapshot(list):
            def __enter__(self):
                self._iterator = iter(self)
                return self

            def __next__(self):
                return next(self._iterator)

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        return ScandirSnapshot(entries)

    os.walk = timed_walk
    os.scandir = timed_scandir
    generated_count = 0
    original_gen_subtitles = scan_globals.get("gen_subtitles")

    def forbid_generation(*call_args, **call_kwargs):
        nonlocal generated_count
        generated_count += 1
        raise RuntimeError("Subtitle generation was attempted during the startup benchmark")

    if original_gen_subtitles:
        scan_globals["gen_subtitles"] = forbid_generation

    # Keep runtime log text out of the benchmark artifacts and container logs.
    # Skip details are reduced to fixed reason categories by this handler.
    root_logger.handlers = [skip_reason_handler]
    root_logger.setLevel(logging.DEBUG)

    if args.backend == "legacy":
        queue_helper = scan_globals["gen_subtitles_queue"]

        def measured_queue(path, *call_args, **call_kwargs):
            skip_reason_handler.current_reason = None
            started = monotonic_time.perf_counter()
            accepted = queue_helper(path, *call_args, **call_kwargs)
            elapsed = (monotonic_time.perf_counter() - started) * 1000
            is_media = scan_globals["has_video_extension"](path) or scan_globals["has_audio_extension"](path)
            outcome = "queued" if accepted else "skip" if is_media else "non_media"
            emit(
                "startup_scan.benchmark_decision",
                run_id=args.worker_case,
                file_id=_path_id(path),
                outcome=outcome,
                reason=("eligible" if accepted else skip_reason_handler.current_reason or "skip_reason_unreported"),
                cache_source="none",
                evaluation_ms=round(elapsed, 3),
                admission_ms=None,
            )
            return accepted

        scan_globals["gen_subtitles_queue"] = measured_queue
    else:
        class BufferedBenchmarkLogger(scan_globals["BenchmarkLogger"]):
            def write(self, event, **fields):
                if self.enabled:
                    emit(event, **fields)

        scan_globals["BenchmarkLogger"] = BufferedBenchmarkLogger

    started_at = monotonic_time.perf_counter()
    try:
        summary = namespace["transcribe_existing"]("|".join(args.root))
    finally:
        os.walk = original_walk
        os.scandir = original_scandir
        if original_get_audio_tracks:
            scan_globals["get_audio_tracks"] = original_get_audio_tracks
        root_logger.handlers = original_root_handlers
        root_logger.setLevel(original_root_level)
    scan_elapsed_ms = round((monotonic_time.perf_counter() - started_at) * 1000, 3)
    if worker_start_count != 0 or generated_count != 0:
        raise RuntimeError(
            "Benchmark isolation failed: "
            f"started_threads={worker_start_count}, blocked_transcription_workers={blocked_worker_start_attempts}, "
            f"generations={generated_count}"
        )

    if records:
        emit(
            "startup_scan.benchmark_probe_summary",
            probe_requests=probe_requests,
            probe_failures=probe_failures,
        )
        with open(args.worker_log, "w", encoding="utf-8") as handle:
            for row in records:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    if summary and "folders" in summary:
        summary = dict(summary)
        summary["folders"] = [
            {**{key: value for key, value in root.items() if key != "path"}, "root_id": _path_id(root["path"])}
            for root in summary["folders"]
        ]
    traversal_event = next((r for r in records if r.get("event") == "startup_scan.benchmark_traversal"), {})
    run_event = next((r for r in records if r.get("event") == "startup_scan.benchmark_run"), {})
    result = {
        "case": args.worker_case,
        "backend": args.backend,
        "revision": os.getenv("STARTUP_SCAN_BENCHMARK_REVISION") or _git_sha(args.worker_repo),
        "code_fingerprint": _code_fingerprint(args.worker_repo),
        "dataset_fingerprint": os.getenv("STARTUP_SCAN_BENCHMARK_DATASET_FINGERPRINT", "unknown"),
        "root_count": len(args.root),
        "scan_elapsed_ms": scan_elapsed_ms,
        "iterator_traversal_ms": round(traversal_ms, 3),
        "filesystem_elapsed_ms": round(directory_enumeration_ms, 3),
        "full_inventory_elapsed_ms": traversal_event.get("inventory_elapsed_ms"),
        "inventory_elapsed_ms": traversal_event.get("inventory_elapsed_ms"),
        "directories_enumerated": directories,
        "directory_scan_errors": directory_scan_errors,
        "directory_fingerprint": hashlib.sha256("\n".join(sorted(visited_directories)).encode("ascii")).hexdigest(),
        "walk_files_seen": files_found,
        "media_count": traversal_event.get("media_count"),
        "subtitle_count": traversal_event.get("subtitle_count"),
        **_decision_metrics(records),
        "policy_fingerprint": run_event.get("policy_fingerprint"),
        "initial_rows": run_event.get("initial_rows"),
        "cache_hits": traversal_event.get("cache_hits"),
        "cache_misses": traversal_event.get("cache_misses"),
        "worker_starts": worker_start_count,
        "blocked_worker_start_attempts": blocked_worker_start_attempts,
        "transcription_or_translation_calls": generated_count,
        "output_writes": 0,
        "summary": _summary_counts(summary),
    }
    Path(args.worker_result).write_text(json.dumps(result, indent=2), encoding="utf-8")
    logging.info(
        "Worker-free startup benchmark complete: case=%s media_decisions=%s scan_ms=%s",
        args.worker_case, result["decision_count"], scan_elapsed_ms,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", help="Media root; repeat to include multiple roots")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--dataset-read-only", action="store_true")
    parser.add_argument("--worker-case")
    parser.add_argument("--worker-repo", type=Path)
    parser.add_argument("--worker-state", type=Path)
    parser.add_argument("--worker-log")
    parser.add_argument("--worker-result")
    parser.add_argument("--backend", choices=["legacy", "persistent"], default="persistent")
    args = parser.parse_args()

    if args.worker_case:
        return _worker(args)
    if not args.root or not args.output_dir or not args.dataset_read_only:
        parser.error("--root, --output-dir, and --dataset-read-only are required")
    if args.repetitions < 1:
        parser.error("--repetitions must be at least one")

    args.root = [str(Path(root).resolve()) for root in args.root]
    output_dir = args.output_dir.resolve()
    for root in args.root:
        root_path = Path(root).resolve()
        try:
            output_dir.relative_to(root_path)
        except ValueError:
            continue
        parser.error("--output-dir must be outside every media root")
    output_dir.mkdir(parents=True, exist_ok=True)
    current_repo = Path(__file__).resolve().parents[1]
    dataset_fingerprint = _dataset_fingerprint(args.root)
    os.environ["STARTUP_SCAN_BENCHMARK_DATASET_FINGERPRINT"] = dataset_fingerprint
    case_results = []
    decision_parity = []
    for repetition in range(args.repetitions):
        repetition_dir = output_dir / f"run-{repetition + 1:02d}-{uuid.uuid4().hex[:8]}"
        try:
            baseline = {}
            for case in ("default-cold", "default-warm"):
                result = _run_child(
                    case, current_repo, args.root,
                    repetition_dir / f"{case}-state", repetition_dir / case, backend="legacy",
                )
                baseline[case] = result
                case_results.append(result)

            smart_cold_state = repetition_dir / "smart-cold-state"
            smart_cold = _run_child(
                "smart-cold", current_repo, args.root,
                smart_cold_state, repetition_dir / "smart-cold", backend="persistent",
            )
            case_results.append(smart_cold)

            smart_warm_state = repetition_dir / "smart-warm-state"
            _copy_sqlite(smart_cold_state / "startup_scan.db", smart_warm_state / "startup_scan.db")
            smart_warm = _run_child(
                "smart-warm", current_repo, args.root, smart_warm_state,
                repetition_dir / "smart-warm", backend="persistent",
            )
            case_results.append(smart_warm)
            decision_parity.append({
                "default-warm": _compare_decisions(baseline["default-cold"], baseline["default-warm"]),
                "smart-cold": _compare_decisions(baseline["default-cold"], smart_cold),
                "smart-warm": _compare_decisions(baseline["default-cold"], smart_warm),
            })
        finally:
            shutil.rmtree(repetition_dir, ignore_errors=True)

    dataset_unchanged = _dataset_fingerprint(args.root) == dataset_fingerprint
    case_groups = {
        case: [result for result in case_results if result.get("case") == case]
        for case in ("default-cold", "default-warm", "smart-cold", "smart-warm")
    }
    case_medians = {
        case: {
            "scan_elapsed_ms": round(statistics.median([row["scan_elapsed_ms"] for row in rows]), 3) if rows else None,
            "filesystem_elapsed_ms": round(statistics.median([row.get("filesystem_elapsed_ms", 0.0) for row in rows]), 3) if rows else None,
            "inventory_elapsed_ms": round(statistics.median([row.get("inventory_elapsed_ms", 0.0) for row in rows if row.get("inventory_elapsed_ms") is not None]), 3) if any(row.get("inventory_elapsed_ms") is not None for row in rows) else None,
            "decision_median_ms": round(statistics.median([row.get("decision_median_ms", 0.0) for row in rows]), 3) if rows else None,
            "decision_p95_ms": round(statistics.median([row.get("decision_p95_ms", 0.0) for row in rows]), 3) if rows else None,
            "decision_count_median": round(statistics.median([row.get("decision_count", 0) for row in rows]), 1) if rows else None,
            "directories_walked": round(statistics.median([row.get("directories_enumerated", 0) for row in rows]), 1) if rows else None,
            "files_walked": round(statistics.median([row.get("walk_files_seen", 0) for row in rows]), 1) if rows else None,
            "media_files": round(statistics.median([row.get("media_count") if row.get("media_count") is not None else row.get("decision_count", 0) for row in rows]), 1) if rows else None,
            "subtitle_files": round(statistics.median([row.get("subtitle_count") or 0 for row in rows]), 1) if rows else None,
            "planner_calls_median": round(statistics.median([row.get("planner_call_count", 0) for row in rows]), 1) if rows else None,
            "probe_requests_median": round(statistics.median([row.get("probe_request_count", 0) for row in rows]), 1) if rows else None,
            "probe_failures_median": round(statistics.median([row.get("probe_failure_count", 0) for row in rows]), 1) if rows else None,
            "invalid_decisions_median": round(statistics.median([row.get("invalid_media_decisions", 0) for row in rows]), 1) if rows else None,
            "duplicate_decisions_median": round(statistics.median([row.get("duplicate_media_decisions", 0) for row in rows]), 1) if rows else None,
            "cache_source_counts": {
                source: round(statistics.median([row.get("cache_source_counts", {}).get(source, 0) for row in rows]), 1)
                for source in sorted({source for row in rows for source in row.get("cache_source_counts", {})})
            },
            "skip_reason_counts": {
                reason: round(statistics.median([row.get("skip_reason_counts", {}).get(reason, 0) for row in rows]), 1)
                for reason in sorted({reason for row in rows for reason in row.get("skip_reason_counts", {})})
            },
        }
        for case, rows in case_groups.items()
    }
    baseline_ms = case_medians["default-cold"]["scan_elapsed_ms"]
    case_metrics = {}
    for case in ("default-warm", "smart-cold", "smart-warm"):
        parity_matched = dataset_unchanged and all(row[case]["matches"] for row in decision_parity)
        case_metrics[case] = {
            "eligibility_and_walk_parity_vs_default_cold": parity_matched,
        }
        case_metrics[case]["relative_to_default_cold"] = None
        smart_ms = case_medians[case]["scan_elapsed_ms"]
        if parity_matched and baseline_ms and smart_ms:
            case_metrics[case]["relative_to_default_cold"] = round(baseline_ms / smart_ms, 3)

    report = {
        "repetitions": args.repetitions,
        "dataset_unchanged": dataset_unchanged,
        "case_medians": case_medians,
        "case_validation": case_metrics,
        "parity_counts": [
            {case: {key: value for key, value in result.items() if key != "matches"} | {"matches": result["matches"]}
             for case, result in row.items()}
            for row in decision_parity
        ],
        "all_cases_worker_free": all(
            row.get("worker_starts") == 0 and row.get("transcription_or_translation_calls") == 0 and row.get("output_writes") == 0
            for row in case_results
        ),
        "cases": [_public_case(row) for row in case_results],
    }
    report_path = output_dir / "startup-scan-comparison.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("benchmark_summary_complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
