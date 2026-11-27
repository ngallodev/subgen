import hashlib
import logging
import os
import time
import uuid
from collections import defaultdict

from .classifier import classify_media
from .dependencies import StartupScanDependencies
from .policy import startup_scan_policy_benchmark_fields
from .roots import find_skip_marker, parse_roots


def _count_rows(db, table_name: str) -> int:
    counter = getattr(db, "count_rows", None)
    if counter is None:
        return 0
    return counter(table_name)


def _count_subtitles_missing_media_path(db) -> int:
    counter = getattr(db, "count_subtitles_missing_media_path", None)
    if counter is None:
        return 0
    return counter()


def _db_begin(db) -> None:
    begin = getattr(db, "begin", None)
    if begin:
        begin()


def _db_commit(db) -> None:
    commit = getattr(db, "commit", None)
    if commit:
        commit()


def _db_rollback(db) -> None:
    rollback = getattr(db, "rollback", None)
    if rollback:
        rollback()


def _inventory_signature_matches(deps: StartupScanDependencies, db, inventory_signature: dict) -> bool:
    if not hasattr(db, "get_meta"):
        return False
    return deps.inventory_signature_matches(db, inventory_signature)


def _start_monitor_observer(observer) -> None:
    observer.start()


def _setup_monitor(deps: StartupScanDependencies, benchmark_logger, folders: list[str]) -> None:
    with deps.benchmark_step("startup_scan.monitor_setup", benchmark_logger, monitor_enabled=bool(deps.monitor)):
        if deps.monitor:
            total_started_at = time.perf_counter()
            observer = deps.observer_factory()
            scheduled_count = 0
            schedule_started_at = time.perf_counter()
            for path in folders:
                if os.path.isdir(path) and not find_skip_marker(path, is_directory=True):
                    handler = deps.new_file_handler_factory()
                    observer.schedule(handler, path, recursive=True)
                    scheduled_count += 1
            schedule_ms = (time.perf_counter() - schedule_started_at) * 1000
            deps.retain_observer(observer)

            start_started_at = time.perf_counter()
            if deps.startup_scan_monitor_async_start:
                thread = deps.thread_factory(target=_start_monitor_observer, args=(observer,), daemon=True)
                thread.start()
                start_mode = "async"
            else:
                observer.start()
                start_mode = "sync"
            start_ms = (time.perf_counter() - start_started_at) * 1000
            benchmark_logger.write(
                "startup_scan.monitor_setup_breakdown",
                scheduled_count=scheduled_count,
                schedule_ms=round(schedule_ms, 3),
                start_ms=round(start_ms, 3),
                start_mode=start_mode,
                total_inner_ms=round((time.perf_counter() - total_started_at) * 1000, 3),
            )
            logging.info("Finished cached startup scan. Now watching for new files.")

def startup_scan_existing(deps: StartupScanDependencies, transcribe_folder_spec: str):
    folders = parse_roots(transcribe_folder_spec)
    if not folders:
        return

    logging.info("Starting cached startup scan for %s configured root(s).", len(folders))
    benchmark_logger = deps.benchmark_logger_factory(
        log_path=deps.startup_scan_benchmark_log_path,
        enabled=deps.startup_scan_benchmark or deps.startup_scan_benchmark_logging,
    )
    scan_id = uuid.uuid4().hex
    benchmark_logger.run_id = scan_id
    benchmark_logger.write(
        "startup_scan.begin",
        run_id=scan_id,
        backend="persistent",
        mode="startup",
        root_count=len(folders),
    )
    scan_started_at = time.perf_counter()
    if deps.skip_startup_scan:
        logging.info("SKIP_STARTUP_SCAN is enabled; skipping existing-file enumeration and recovering durable jobs through the workers.")
        _setup_monitor(deps, benchmark_logger, folders)
        return
    with deps.benchmark_step("startup_scan.total", benchmark_logger, folder_count=len(folders)):
        db = deps.startup_scan_db_factory(deps.startup_scan_db_path)
        try:
            inventory_started_at = time.perf_counter()
            with deps.benchmark_step("startup_scan.inventory", benchmark_logger, folder_count=len(folders)):
                inventory = deps.collect_startup_inventory(
                    folders,
                    recursive=True,
                    db=db,
                    use_directory_cache=False,
                )
            inventory_elapsed_ms = round((time.perf_counter() - inventory_started_at) * 1000, 3)

            inventory.media_files.sort(key=lambda record: (record["mtime"], record["path"]), reverse=True)
            policy_signature = deps.get_startup_policy_signature()
            inventory_signature = deps.inventory_signature(inventory, policy_signature)
            initial_media_rows = _count_rows(db, "media_files")
            initial_excluded_rows = _count_rows(db, "excluded_files")
            initial_subtitle_rows = _count_rows(db, "subtitle_files")
            benchmark_logger.write(
                "startup_scan.benchmark_run",
                run_id=scan_id,
                case=os.getenv("STARTUP_SCAN_BENCHMARK_CASE", "startup"),
                revision=os.getenv("STARTUP_SCAN_BENCHMARK_REVISION", "unknown"),
                dataset_fingerprint=inventory_signature.get("manifest"),
                policy_fingerprint=policy_signature,
                configuration={
                    "backend": "persistent",
                    "root_count": len(folders),
                    "classify_workers": deps.startup_scan_classify_workers,
                    "force_rewalk": deps.startup_scan_force_rewalk,
                },
                initial_rows={
                    "media": initial_media_rows,
                    "excluded": initial_excluded_rows,
                    "subtitles": initial_subtitle_rows,
                },
                worker_isolation=os.getenv("STARTUP_SCAN_BENCHMARK_WORKER_ISOLATION", "0") == "1",
            )
            benchmark_logger.write(
                "startup_scan.benchmark_traversal",
                run_id=scan_id,
                inventory_elapsed_ms=inventory_elapsed_ms,
                full_inventory_elapsed_ms=inventory_elapsed_ms,
                directories_enumerated=len(getattr(inventory, "directories", [])),
                file_stats=getattr(inventory, "file_stat_count", 0),
                media_count=len(inventory.media_files),
                subtitle_count=len(inventory.subtitle_files),
                cache_hits=getattr(inventory, "cache_hits", 0),
                cache_misses=getattr(inventory, "cache_misses", 0),
                error_count=len(getattr(inventory, "scan_errors", [])),
            )
            if deps.startup_scan_benchmark_include_policy and deps.get_startup_policy_payload:
                benchmark_logger.write(
                    "startup_scan.policy_payload",
                    policy_signature=policy_signature,
                    **startup_scan_policy_benchmark_fields(deps.get_startup_policy_payload()),
                )
            benchmark_logger.write(
                "startup_scan.cache_state",
                force_rewalk=deps.startup_scan_force_rewalk,
                cached_media_count=_count_rows(db, "media_files"),
                cached_excluded_count=_count_rows(db, "excluded_files"),
                media_count=len(inventory.media_files),
                subtitle_count=len(inventory.subtitle_files),
                policy_signature=policy_signature,
            )
            benchmark_logger.write(
                "startup_scan.directory_cache",
                hit_count=getattr(inventory, "cache_hits", 0),
                miss_count=getattr(inventory, "cache_misses", 0),
                directory_count=len(getattr(inventory, "child_dirs_by_dir", {})),
                stat_hit_count=getattr(inventory, "cache_reason_counts", {}).get("stat_match", 0),
                signature_hit_count=getattr(inventory, "cache_reason_counts", {}).get("entries_signature_match", 0),
                signature_mismatch_count=getattr(inventory, "cache_reason_counts", {}).get("entries_signature_mismatch", 0),
                no_cache_count=getattr(inventory, "cache_reason_counts", {}).get("no_cache", 0),
                decode_error_count=getattr(inventory, "cache_reason_counts", {}).get("decode_error", 0),
                list_error_count=getattr(inventory, "cache_reason_counts", {}).get("list_error", 0),
                dir_stat_count=getattr(inventory, "directory_stat_count", 0),
                dir_list_count=getattr(inventory, "directory_list_count", 0),
                file_stat_count=getattr(inventory, "file_stat_count", 0),
                quick_signature_checks=getattr(inventory, "quick_signature_checks", 0),
                full_scan_count=getattr(inventory, "full_directory_scans", 0),
                root_subtree_snapshot_bytes=getattr(inventory, "root_subtree_snapshot_bytes", 0),
                root_subtree_cache_used=getattr(inventory, "root_subtree_cache_used", 0),
            )
            benchmark_logger.write("startup_scan.metadata_initialized", folder_count=len(folders), recursive=True)
            root_results = getattr(inventory, "root_results", [])
            scan_errors = getattr(inventory, "scan_errors", [])
            for root_result in root_results:
                root_path = root_result.get("path", "")
                root_abs = os.path.abspath(root_path)
                try:
                    if os.path.isdir(root_path):
                        root_media_count = sum(
                            1 for item in inventory.media_files
                            if os.path.commonpath([root_abs, os.path.abspath(item["path"])]) == root_abs
                        )
                        root_subtitle_count = sum(
                            1 for item in inventory.subtitle_files
                            if os.path.commonpath([root_abs, os.path.abspath(item["path"])]) == root_abs
                        )
                    else:
                        root_media_count = sum(1 for item in inventory.media_files if item["path"] == root_path)
                        root_subtitle_count = sum(1 for item in inventory.subtitle_files if item["path"] == root_path)
                except (OSError, ValueError):
                    root_media_count = root_subtitle_count = 0
                logging.info(
                    "Startup scan root: path=%s status=%s media=%s subtitles=%s errors=%s.",
                    root_path, root_result.get("status", "unknown"), root_media_count,
                    root_subtitle_count, root_result.get("error_count", 0),
                )
                benchmark_logger.write(
                    "startup_scan.root",
                    run_id=scan_id,
                    root_id=hashlib.sha256(os.path.normcase(root_abs).encode("utf-8")).hexdigest()[:12],
                    status=root_result.get("status", "unknown"),
                    media_count=root_media_count,
                    subtitle_count=root_subtitle_count,
                    error_count=root_result.get("error_count", 0),
                )
            scan_complete = bool(root_results) and not scan_errors and all(
                result.get("status") == "success" for result in root_results
            )

            _db_begin(db)
            try:
                if hasattr(db, "prune_scanned_roots"):
                    prune_counts = db.prune_scanned_roots(root_results, inventory)
                    benchmark_logger.write("startup_scan.prune", **prune_counts)

                with deps.benchmark_step("startup_scan.subtitle_index", benchmark_logger, subtitle_count=len(inventory.subtitle_files)):
                    existing_subtitles = {
                        row["path"]: row
                        for row in db.get_all_subtitles()
                    } if hasattr(db, "get_all_subtitles") else {}
                    subtitle_rows_written = 0
                    for subtitle in inventory.subtitle_files:
                        existing = existing_subtitles.get(subtitle["path"])
                        if existing and all(
                            existing.get(field) == subtitle.get(field)
                            for field in ("size", "mtime", "media_path", "subtitle_type", "language")
                        ):
                            continue
                        db.upsert_subtitle(
                            path=subtitle["path"],
                            source_path=subtitle["path"],
                            size=subtitle["size"],
                            mtime=subtitle["mtime"],
                            media_path=subtitle.get("media_path"),
                            subtitle_type=subtitle.get("subtitle_type") or "external",
                            language=subtitle.get("language"),
                        )
                        subtitle_rows_written += 1
                    benchmark_logger.write(
                        "startup_scan.subtitle_index_changes",
                        observed=len(inventory.subtitle_files),
                        rows_written=subtitle_rows_written,
                        rows_reused=len(inventory.subtitle_files) - subtitle_rows_written,
                    )

                subtitle_rows_by_media = defaultdict(list)
                for subtitle_record in inventory.subtitle_files:
                    if subtitle_record.get("media_path"):
                        subtitle_rows_by_media[subtitle_record["media_path"]].append(subtitle_record)

                totals = classify_media(
                    deps,
                    db,
                    inventory,
                    subtitle_rows_by_media,
                    benchmark_logger,
                    policy_signature,
                )
                if scan_complete:
                    deps.store_inventory_signature(db, inventory_signature)
                    db.set_meta("last_full_scan_at", str(int(time.time())))
                _db_commit(db)
            except Exception:
                _db_rollback(db)
                raise

            benchmark_logger.write(
                "startup_scan.summary",
                media_count=len(inventory.media_files),
                subtitle_count=len(inventory.subtitle_files),
                reused_count=totals["reused"],
                excluded_count=totals["excluded"],
                queued_count=totals["queued"],
                ignored_count=totals["ignored"],
                cache_hit_count=totals["cache_hit"],
                cache_miss_count=totals["cache_miss"],
                force_rewalk=deps.startup_scan_force_rewalk,
            )
            benchmark_logger.write(
                "startup_scan.benchmark_summary",
                run_id=scan_id,
                scan_elapsed_ms=round((time.perf_counter() - scan_started_at) * 1000, 3),
                traversal_elapsed_ms=inventory_elapsed_ms,
                decision_elapsed_ms=round(totals.get("classification_elapsed_ms", 0.0), 3),
                sqlite_rows_written={
                    "media": totals.get("queued", 0) + totals.get("excluded", 0),
                    "subtitles": len(inventory.subtitle_files),
                },
                decision_count=totals.get("benchmark_decision_count", 0),
                per_file_median_ms=round(totals.get("benchmark_decision_median_ms", 0.0), 3),
                per_file_p95_ms=round(totals.get("benchmark_decision_p95_ms", 0.0), 3),
                outcomes=totals.get("benchmark_outcomes", {}),
                worker_starts=0 if os.getenv("STARTUP_SCAN_BENCHMARK_WORKER_ISOLATION", "0") == "1" else None,
                output_writes=0 if os.getenv("STARTUP_SCAN_BENCHMARK_WORKER_ISOLATION", "0") == "1" else None,
            )
            logging.info(
                "Startup scan complete: media=%s subtitles=%s queued=%s reused=%s excluded=%s ignored=%s cache_hits=%s cache_misses=%s.",
                len(inventory.media_files),
                len(inventory.subtitle_files),
                totals["queued"],
                totals["reused"],
                totals["excluded"],
                totals["ignored"],
                totals["cache_hit"],
                totals["cache_miss"],
            )
            if not scan_complete:
                logging.warning(
                    "Startup scan was partial: completed_roots=%s failed_roots=%s marker_skipped_roots=%s; the manifest was not advanced.",
                    sum(1 for result in root_results if result.get("status") == "success"),
                    sum(1 for result in root_results if result.get("status") == "error"),
                    sum(1 for result in root_results if result.get("status") == "marker_skipped"),
                )
            _setup_monitor(deps, benchmark_logger, folders)
            return {
                "files_found": len(inventory.media_files) + len(inventory.subtitle_files),
                "queued": totals["queued"],
                "errors": len(scan_errors),
                "media_count": len(inventory.media_files),
                "subtitle_count": len(inventory.subtitle_files),
            }
        finally:
            db.close()
