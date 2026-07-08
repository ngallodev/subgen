import logging
import os
import time
from collections import defaultdict

from .classifier import classify_media
from .dependencies import StartupScanDependencies
from .wiring import backfill_subtitle_links


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
                if os.path.isdir(path):
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
    folders = [path for path in transcribe_folder_spec.split("|") if path]
    if not folders:
        return

    logging.info("Starting cached startup scan for media files.")
    benchmark_logger = deps.benchmark_logger_factory(
        log_path=deps.startup_scan_benchmark_log_path,
        enabled=deps.startup_scan_benchmark_logging,
    )
    with deps.benchmark_step("startup_scan.total", benchmark_logger, folder_count=len(folders)):
        db = deps.startup_scan_db_factory(deps.startup_scan_db_path)
        try:
            with deps.benchmark_step("startup_scan.inventory", benchmark_logger, folder_count=len(folders)):
                inventory = deps.collect_startup_inventory(folders, recursive=True, db=db)

            inventory.media_files.sort(key=lambda record: (record["mtime"], record["path"]), reverse=True)
            policy_signature = deps.get_startup_policy_signature()
            inventory_signature = deps.inventory_signature(inventory, policy_signature)
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

            _db_begin(db)
            try:
                db.set_meta("last_full_scan_at", str(int(time.time())))

                if not deps.startup_scan_force_rewalk and _inventory_signature_matches(deps, db, inventory_signature):
                    missing_media_path_count = _count_subtitles_missing_media_path(db)
                    if missing_media_path_count:
                        with deps.benchmark_step(
                        "startup_scan.subtitle_relink",
                        benchmark_logger,
                        subtitle_count=len(inventory.subtitle_files),
                        missing_media_path_count=missing_media_path_count,
                    ):
                            backfill_subtitle_links(db, inventory.subtitle_files)
                    benchmark_logger.write(
                        "startup_scan.manifest_hit",
                        file_count=inventory_signature["file_count"],
                        media_count=inventory_signature["media_count"],
                        subtitle_count=inventory_signature["subtitle_count"],
                        policy_signature=policy_signature,
                    )
                    benchmark_logger.write(
                        "startup_scan.summary",
                        media_count=inventory_signature["media_count"],
                        subtitle_count=inventory_signature["subtitle_count"],
                        reused_count=_count_rows(db, "media_files"),
                        excluded_count=_count_rows(db, "excluded_files"),
                        queued_count=0,
                        ignored_count=0,
                        cache_hit_count=0,
                        cache_miss_count=0,
                        force_rewalk=False,
                    )
                    db.set_meta("last_full_scan_at", str(int(time.time())))
                    _db_commit(db)
                    _setup_monitor(deps, benchmark_logger, folders)
                    return

                with deps.benchmark_step("startup_scan.subtitle_index", benchmark_logger, subtitle_count=len(inventory.subtitle_files)):
                    for subtitle in inventory.subtitle_files:
                        db.upsert_subtitle(
                            path=subtitle["path"],
                            source_path=subtitle["path"],
                            size=subtitle["size"],
                            mtime=subtitle["mtime"],
                            media_path=subtitle.get("media_path"),
                            subtitle_type=subtitle.get("subtitle_type") or "external",
                            language=subtitle.get("language"),
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
            _setup_monitor(deps, benchmark_logger, folders)
        finally:
            db.close()
