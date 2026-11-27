import hashlib
import json
import logging
import os
import time
from typing import Any

from .benchmarks import round_benchmark_fields, summarize_parallel_plan_detail
from .dependencies import StartupScanDependencies


def _planner_trace(enabled: bool) -> dict[str, Any] | None:
    if not enabled:
        return None
    return {
        "active_check_ms": 0.0,
        "has_audio_ms": 0.0,
        "audio_tracks_ms": 0.0,
        "audio_langs_ms": 0.0,
        "choose_language_ms": 0.0,
        "skip_check_ms": 0.0,
        "detect_branch_ms": 0.0,
        "audio_track_count": 0,
        "audio_lang_count": 0,
        "used_cached_audio_tracks": False,
    }


def prepare_media_queue_job(
    deps: StartupScanDependencies,
    file_path: str,
    transcription_type: str,
    force_language: Any,
    audio_tracks=None,
    subtitle_rows=None,
    current_sidecar_state: str | None = None,
    **task_kwargs,
):
    trace = _planner_trace(deps.startup_scan_planner_trace_logging)
    metadata_kwargs = {
        key: value
        for key, value in task_kwargs.items()
        if key not in {"path", "type", "transcribe_or_translate", "force_language", "audio_tracks"}
    }

    started_at = time.perf_counter()
    if deps.task_queue.is_active(file_path):
        if trace is not None:
            trace["active_check_ms"] = (time.perf_counter() - started_at) * 1000
        return {
            "status": "active",
            "reason": "already_queued",
            "details": "File is already queued or processing.",
            "planner_trace": trace,
        }
    if trace is not None:
        trace["active_check_ms"] = (time.perf_counter() - started_at) * 1000

    if deps.describe_skip_reason_pre_audio:
        started_at = time.perf_counter()
        skipped, skip_reason, skip_details = deps.describe_skip_reason_pre_audio(
            file_path,
            force_language,
            subtitle_rows=subtitle_rows,
            current_sidecar_state=current_sidecar_state,
        )
        if trace is not None:
            trace["skip_check_ms"] = (time.perf_counter() - started_at) * 1000
        if skipped:
            return {
                "status": "skip",
                "reason": skip_reason,
                "details": skip_details,
                "force_language": force_language,
                "audio_tracks": [],
                "audio_langs": [],
                "planner_trace": trace,
            }

    started_at = time.perf_counter()
    if audio_tracks is None:
        try:
            audio_tracks = deps.get_audio_tracks(file_path)
        except Exception as exc:
            task = {
                "path": file_path,
                "type": transcription_type,
                "transcribe_or_translate": transcription_type,
                "force_language": force_language,
                "audio_tracks": None,
            }
            task.update(metadata_kwargs)
            return {
                "status": "retryable_error",
                "reason": "media_probe_failed",
                "details": str(exc),
                "task": task,
                "force_language": force_language,
                "audio_tracks": [],
                "audio_langs": [],
                "planner_trace": trace,
            }
        has_audio_flag = bool(audio_tracks)
    else:
        has_audio_flag = bool(audio_tracks)
        audio_tracks = [
            {
                **track,
                "language": track["language"]
                if isinstance(track.get("language"), deps.language_code)
                else deps.language_code.from_string(track.get("language")),
            }
            for track in audio_tracks
            if isinstance(track, dict)
        ]
        if trace is not None:
            trace["used_cached_audio_tracks"] = True
    if trace is not None:
        trace["has_audio_ms"] = 0.0
        trace["audio_tracks_ms"] = (time.perf_counter() - started_at) * 1000
        trace["audio_track_count"] = len(audio_tracks)

    if not has_audio_flag:
        return {
            "status": "skip",
            "reason": "no_audio",
            "details": "File has no audio track.",
            "force_language": force_language,
            "audio_tracks": [],
            "audio_langs": [],
            "planner_trace": trace,
        }

    started_at = time.perf_counter()
    audio_langs = [
        track["language"]
        if isinstance(track.get("language"), deps.language_code)
        else deps.language_code.from_string(track.get("language"))
        for track in audio_tracks
    ]
    if trace is not None:
        trace["audio_langs_ms"] = (time.perf_counter() - started_at) * 1000
        trace["audio_lang_count"] = len(audio_langs)

    started_at = time.perf_counter()
    force_language = deps.choose_transcribe_language(
        file_path,
        force_language,
        audio_tracks=audio_tracks,
    )
    if trace is not None:
        trace["choose_language_ms"] = (time.perf_counter() - started_at) * 1000

    started_at = time.perf_counter()
    if deps.describe_skip_reason_with_context:
        skipped, skip_reason, skip_details = deps.describe_skip_reason_with_context(
            file_path,
            force_language,
            audio_langs=audio_langs,
            subtitle_rows=subtitle_rows,
            current_sidecar_state=current_sidecar_state,
        )
    else:
        skipped, skip_reason, skip_details = deps.describe_skip_reason(
            file_path,
            force_language,
            audio_langs=audio_langs,
        )
    if trace is not None:
        trace["skip_check_ms"] = (time.perf_counter() - started_at) * 1000
    if skipped:
        return {
            "status": "skip",
            "reason": skip_reason,
            "details": skip_details,
            "force_language": force_language,
            "audio_tracks": audio_tracks,
            "audio_langs": audio_langs,
            "planner_trace": trace,
        }

    started_at = time.perf_counter()
    if not force_language and deps.should_whisper_detect_audio_language:
        detect_task = {
            "path": file_path,
            "type": "detect_language",
            "transcribe_or_translate": transcription_type,
            "force_language": force_language,
            "audio_tracks": audio_tracks,
        }
        detect_task.update(metadata_kwargs)
        if trace is not None:
            trace["detect_branch_ms"] = (time.perf_counter() - started_at) * 1000
        return {
            "status": "detect",
            "task": detect_task,
            "reason": "detect_language",
            "details": "Queued language detection instead of transcription.",
            "force_language": force_language,
            "audio_tracks": audio_tracks,
            "audio_langs": audio_langs,
            "planner_trace": trace,
        }
    if trace is not None:
        trace["detect_branch_ms"] = (time.perf_counter() - started_at) * 1000

    task = {
        "path": file_path,
        "type": transcription_type,
        "transcribe_or_translate": transcription_type,
        "force_language": force_language,
        "audio_tracks": audio_tracks,
    }
    task.update(metadata_kwargs)

    return {
        "status": "queue",
        "task": task,
        "reason": "queued",
        "details": "Queued for transcription.",
        "force_language": force_language,
        "audio_tracks": audio_tracks,
        "audio_langs": audio_langs,
        "planner_trace": trace,
    }


def queue_cached_job(deps: StartupScanDependencies, queue_path: str, cached_row: dict) -> bool:
    if deps.task_queue.is_active(queue_path):
        return False

    task_type = cached_row.get("task_type") or "transcribe"
    transcription_type = cached_row.get("transcription_type") or deps.transcribe_or_translate
    if task_type == "detect_language":
        task = {
            "path": queue_path,
            "type": "detect_language",
            "transcribe_or_translate": transcription_type,
            "force_language": deps.language_code.NONE,
            "mtime": int(cached_row.get("mtime") or 0),
            "policy_signature": cached_row.get("policy_signature") or "",
        }
    else:
        audio_tracks = deps.deserialize_audio_tracks(
            cached_row.get("audio_tracks_json"),
            language_code=deps.language_code,
        )
        force_language_code = cached_row.get("force_language_code") or ""
        force_language = (
            deps.language_code.from_string(force_language_code)
            if force_language_code
            else deps.language_code.NONE
        )
        task = {
            "path": queue_path,
            "type": transcription_type,
            "transcribe_or_translate": transcription_type,
            "force_language": force_language,
            "audio_tracks": audio_tracks,
            "mtime": int(cached_row.get("mtime") or 0),
            "policy_signature": cached_row.get("policy_signature") or "",
        }

    deps.task_queue.put(task)
    return True


def plan_media_record(
    deps: StartupScanDependencies,
    media: dict,
    queue_path: str,
    cached_row: dict | None,
    inventory,
    subtitle_rows: list,
    current_sidecar_state: str | None,
):
    plan_started_at = time.perf_counter()
    cached_audio_tracks = None
    if cached_row and cached_row.get("has_audio") and _matches_media_fingerprint(media, cached_row):
        cached_audio_tracks = deps.deserialize_audio_tracks(
            cached_row.get("audio_tracks_json"),
            language_code=deps.language_code,
        ) or None
    plan = prepare_media_queue_job(
        deps,
        queue_path,
        deps.transcribe_or_translate,
        force_language=deps.language_code.NONE,
        audio_tracks=cached_audio_tracks,
        subtitle_rows=subtitle_rows,
        current_sidecar_state=current_sidecar_state,
        mtime=media["mtime"],
        policy_signature=deps.get_startup_policy_signature() if deps.get_startup_policy_signature else "",
    )
    plan_ms = (time.perf_counter() - plan_started_at) * 1000

    if plan["status"] in {"skip", "active"}:
        signature_ms = 0.0
        subtitle_signature = ""
        matching_subtitles = []
        if plan["status"] == "skip":
            signature_started_at = time.perf_counter()
            subtitle_signature, matching_subtitles = deps.compute_subtitle_signature(
                media["path"],
                inventory,
                {},
            )
            signature_ms = (time.perf_counter() - signature_started_at) * 1000
        return {
            "media": media,
            "queue_path": queue_path,
            "cached_row": cached_row,
            "subtitle_rows": subtitle_rows,
            "current_sidecar_state": current_sidecar_state,
            "subtitle_signature": subtitle_signature,
            "matching_subtitles": matching_subtitles,
            "plan": plan,
            "signature_ms": signature_ms,
            "subtitle_signature_ms": signature_ms,
            "plan_ms": plan_ms,
            "queue_plan_ms": plan_ms,
        }

    signature_started_at = time.perf_counter()
    subtitle_signature, matching_subtitles = deps.compute_subtitle_signature(
        media["path"],
        inventory,
        {},
    )
    signature_ms = (time.perf_counter() - signature_started_at) * 1000

    return {
        "media": media,
        "queue_path": queue_path,
        "cached_row": cached_row,
        "subtitle_rows": subtitle_rows,
        "current_sidecar_state": current_sidecar_state,
        "subtitle_signature": subtitle_signature,
        "matching_subtitles": matching_subtitles,
        "plan": plan,
        "signature_ms": signature_ms,
        "subtitle_signature_ms": signature_ms,
        "plan_ms": plan_ms,
        "queue_plan_ms": plan_ms,
    }


def _matches_media_fingerprint(media_record: dict, cached_row: dict) -> bool:
    return (
        cached_row.get("size") == media_record["size"]
        and cached_row.get("mtime") == media_record["mtime"]
        and cached_row.get("mtime_ns") == media_record.get("mtime_ns")
        and cached_row.get("device") == media_record.get("device")
        and cached_row.get("inode") == media_record.get("inode")
    )


def _matches_cached_record(media_record: dict, cached_row: dict | None, policy_signature: str) -> bool:
    return bool(
        cached_row
        and _matches_media_fingerprint(media_record, cached_row)
        and cached_row.get("policy_signature") == policy_signature
    )


def _cache_miss_reason(prefix: str, media_record: dict[str, Any], cached_row: dict[str, Any] | None, policy_signature: str) -> str:
    if cached_row is None:
        return f"{prefix}_missing"
    if cached_row.get("size") != media_record["size"]:
        return f"{prefix}_size_mismatch"
    if cached_row.get("mtime") != media_record["mtime"]:
        return f"{prefix}_mtime_mismatch"
    if cached_row.get("mtime_ns") != media_record.get("mtime_ns"):
        return f"{prefix}_mtime_ns_mismatch"
    if cached_row.get("device") != media_record.get("device") or cached_row.get("inode") != media_record.get("inode"):
        return f"{prefix}_identity_mismatch"
    if cached_row.get("policy_signature") != policy_signature:
        return f"{prefix}_policy_mismatch"
    return f"{prefix}_other"


def _aggregate_planner_trace(plan_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    traces = [result.get("plan", {}).get("planner_trace") for result in plan_results]
    traces = [trace for trace in traces if trace]
    if not traces:
        return None

    numeric_keys = [
        "active_check_ms",
        "has_audio_ms",
        "audio_tracks_ms",
        "audio_langs_ms",
        "choose_language_ms",
        "skip_check_ms",
        "detect_branch_ms",
    ]
    aggregate: dict[str, Any] = {
        "pending_count": len(plan_results),
        "traced_count": len(traces),
        "cached_audio_track_plan_count": sum(1 for trace in traces if trace.get("used_cached_audio_tracks")),
        "audio_track_count_total": sum(trace.get("audio_track_count", 0) for trace in traces),
        "audio_lang_count_total": sum(trace.get("audio_lang_count", 0) for trace in traces),
    }
    for key in numeric_keys:
        total = sum(trace.get(key, 0.0) for trace in traces)
        aggregate[f"{key}_total"] = total
        aggregate[f"{key}_avg"] = total / len(traces)
        aggregate[f"{key}_max"] = max(trace.get(key, 0.0) for trace in traces)
    return round_benchmark_fields(aggregate)


def _get_all_media_rows(db) -> list[dict[str, Any]]:
    getter = getattr(db, "get_all_media", None)
    if getter is None:
        return []
    return getter()


def _get_all_excluded_rows(db) -> list[dict[str, Any]]:
    getter = getattr(db, "get_all_excluded", None)
    if getter is None:
        return []
    return getter()


def _enqueue_subtitles(deps: StartupScanDependencies, task: dict[str, Any]) -> bool:
    """Admit the completed planner decision without re-running eligibility."""
    return bool(deps.task_queue.put(task))


def classify_media(
    deps: StartupScanDependencies,
    db,
    inventory,
    subtitle_rows_by_media: dict[str, list[dict[str, Any]]],
    benchmark_logger,
    policy_signature: str,
):
    classification_started_at = time.perf_counter()
    totals = {
        "reused": 0,
        "excluded": 0,
        "queued": 0,
        "ignored": 0,
        "cache_hit": 0,
        "cache_miss": 0,
    }
    benchmark_decision_samples: list[float] = []
    benchmark_outcomes: dict[str, int] = {}

    probe_cache_before = deps.probe_cache_info()

    with deps.benchmark_step("startup_scan.classify_media", benchmark_logger, media_count=len(inventory.media_files)):
        cached_media_by_path = {row["path"]: row for row in _get_all_media_rows(db)}
        cached_excluded_by_path = {row["path"]: row for row in _get_all_excluded_rows(db)}
        pending_media = []
        preflight_cache_hits = 0
        preflight_cache_misses = 0
        preflight_reason_counts: dict[str, int] = {}
        subtitle_signature_cache: dict = {}
        verified_completion_paths: set[str] = set()
        completion_loader = getattr(deps.task_queue, "verified_completions", None)
        completion_check_ms_per_item = 0.0
        if completion_loader and not deps.startup_scan_force_rewalk and inventory.media_files:
            completion_candidates = {
                deps.path_mapping(media["path"]): media
                for media in inventory.media_files
            }
            completion_started_at = time.perf_counter()
            verified_completion_paths = completion_loader(
                completion_candidates,
                operation=deps.transcribe_or_translate,
                policy_signature=policy_signature,
            )
            completion_check_ms_per_item = (
                (time.perf_counter() - completion_started_at) * 1000
                / max(len(completion_candidates), 1)
            )
            benchmark_logger.write(
                "startup_scan.completed_output_validation",
                candidates=len(completion_candidates),
                verified_count=len(verified_completion_paths),
                elapsed_ms=round((time.perf_counter() - completion_started_at) * 1000, 3),
            )

        with deps.benchmark_step(
            "startup_scan.classify_media.preflight",
            benchmark_logger,
            media_count=len(inventory.media_files),
        ):
            for media in inventory.media_files:
                queue_path = deps.path_mapping(media["path"])
                cached_row = None if deps.startup_scan_force_rewalk else cached_media_by_path.get(queue_path)
                cached_excluded = None if deps.startup_scan_force_rewalk else cached_excluded_by_path.get(queue_path)
                subtitle_rows = subtitle_rows_by_media.get(media["path"], [])
                current_sidecar_state = None

                if deps.startup_scan_force_rewalk:
                    reason = "force_rewalk"
                    preflight_reason_counts[reason] = preflight_reason_counts.get(reason, 0) + 1
                    pending_media.append((media, queue_path, cached_row, subtitle_rows, current_sidecar_state))
                    preflight_cache_misses += 1
                    continue

                if queue_path in verified_completion_paths:
                    totals["reused"] += 1
                    totals["cache_hit"] += 1
                    benchmark_decision_samples.append(completion_check_ms_per_item)
                    benchmark_outcomes["skip"] = benchmark_outcomes.get("skip", 0) + 1
                    cached_outcome = "skip"
                    benchmark_logger.write(
                        "startup_scan.benchmark_decision",
                        run_id=getattr(benchmark_logger, "run_id", None),
                        file_id=hashlib.sha256(os.path.normcase(os.path.abspath(queue_path)).encode("utf-8")).hexdigest()[:16],
                        outcome=cached_outcome,
                        reason="verified_completion",
                        cache_source="durable_job",
                        evaluation_ms=round(completion_check_ms_per_item, 3),
                        admission_ms=0.0,
                    )
                    preflight_cache_hits += 1
                    preflight_reason_counts["verified_completion"] = preflight_reason_counts.get("verified_completion", 0) + 1
                    continue

                if _matches_cached_record(media, cached_row, policy_signature):
                    assert cached_row is not None
                    current_sidecar_state = deps.current_sidecar_state(subtitle_rows)
                    signature_started_at = time.perf_counter()
                    sidecar_signature, _ = deps.compute_subtitle_signature(
                        media["path"], inventory, subtitle_signature_cache
                    )
                    sidecar_validation_ms = (time.perf_counter() - signature_started_at) * 1000
                    sidecar_matches = sidecar_signature == cached_row.get("subtitle_signature")
                    if (
                        cached_row.get("decision") == "excluded"
                        and sidecar_matches
                        and not inventory.scan_errors
                        and not inventory.skipped_roots
                    ):
                        totals["reused"] += 1
                        totals["excluded"] += 1
                        totals["cache_hit"] += 1
                        benchmark_decision_samples.append(sidecar_validation_ms)
                        benchmark_outcomes["skip"] = benchmark_outcomes.get("skip", 0) + 1
                        benchmark_logger.write(
                            "startup_scan.benchmark_decision",
                            run_id=getattr(benchmark_logger, "run_id", None),
                            file_id=hashlib.sha256(os.path.normcase(os.path.abspath(queue_path)).encode("utf-8")).hexdigest()[:16],
                            outcome="skip",
                            reason=cached_row.get("reason") or "cached_exclusion",
                            cache_source="sqlite",
                            evaluation_ms=round(sidecar_validation_ms, 3),
                            admission_ms=0.0,
                        )
                        preflight_cache_hits += 1
                        preflight_reason_counts["cached_exclusion"] = preflight_reason_counts.get("cached_exclusion", 0) + 1
                        continue
                    if (
                        cached_row.get("decision") == "eligible"
                        and cached_row.get("reason") != "completed"
                        and sidecar_matches
                        and cached_row.get("audio_tracks_json")
                    ):
                        decision_started_at = time.perf_counter()
                        if queue_cached_job(deps, queue_path, cached_row):
                            totals["reused"] += 1
                            totals["queued"] += 1
                            cached_outcome = "queued"
                        else:
                            totals["ignored"] += 1
                            cached_outcome = "already_active"
                        admission_ms = (time.perf_counter() - decision_started_at) * 1000
                        cached_ms = sidecar_validation_ms + admission_ms
                        benchmark_decision_samples.append(cached_ms)
                        benchmark_outcomes[cached_outcome] = benchmark_outcomes.get(cached_outcome, 0) + 1
                        benchmark_logger.write(
                            "startup_scan.benchmark_decision",
                            run_id=getattr(benchmark_logger, "run_id", None),
                            file_id=hashlib.sha256(os.path.normcase(os.path.abspath(queue_path)).encode("utf-8")).hexdigest()[:16],
                            outcome=cached_outcome,
                            reason="cached_eligible",
                            cache_source="sqlite",
                            evaluation_ms=round(cached_ms, 3),
                            admission_ms=round(admission_ms, 3),
                        )
                        preflight_cache_hits += 1
                        totals["cache_hit"] += 1
                        preflight_reason_counts["cached_eligible"] = preflight_reason_counts.get("cached_eligible", 0) + 1
                        continue
                    reason = "media_sidecar_mismatch" if not sidecar_matches else "media_sidecar_validation"
                else:
                    media_reason = _cache_miss_reason("media", media, cached_row, policy_signature)
                    excluded_reason = _cache_miss_reason("excluded", media, cached_excluded, policy_signature)
                    if media_reason != "media_missing":
                        reason = media_reason
                    else:
                        reason = excluded_reason

                preflight_reason_counts[reason] = preflight_reason_counts.get(reason, 0) + 1
                pending_media.append((media, queue_path, cached_row, subtitle_rows, current_sidecar_state))
                preflight_cache_misses += 1

        benchmark_logger.write(
            "startup_scan.classify_media.workload",
            preflight_cache_hits=preflight_cache_hits,
            preflight_cache_misses=preflight_cache_misses,
            pending_count=len(pending_media),
            workers=deps.startup_scan_classify_workers,
            **dict(sorted(preflight_reason_counts.items())),
        )

        def _plan_pending(item):
            media, queue_path, cached_row, subtitle_rows, current_sidecar_state = item
            if deps.plan_media_record:
                return deps.plan_media_record(
                    media,
                    queue_path,
                    cached_row,
                    inventory,
                    subtitle_rows,
                    current_sidecar_state,
                )
            return plan_media_record(
                deps,
                media,
                queue_path,
                cached_row,
                inventory,
                subtitle_rows,
                current_sidecar_state,
            )

        with deps.benchmark_step(
            "startup_scan.classify_media.parallel_plan",
            benchmark_logger,
            pending_count=len(pending_media),
            workers=deps.startup_scan_classify_workers,
        ):
            if pending_media:
                if deps.startup_scan_classify_workers > 1 and len(pending_media) > 1:
                    with deps.thread_pool_executor_factory(max_workers=deps.startup_scan_classify_workers) as executor:
                        plan_results = list(executor.map(_plan_pending, pending_media))
                else:
                    plan_results = [_plan_pending(item) for item in pending_media]
            else:
                plan_results = []

        if plan_results:
            benchmark_logger.write(
                "startup_scan.classify_media.parallel_plan_breakdown",
                pending_count=len(plan_results),
                signature_total_ms=round(sum(result.get("signature_ms", 0.0) for result in plan_results), 3),
                signature_avg_ms=round(sum(result.get("signature_ms", 0.0) for result in plan_results) / len(plan_results), 3),
                signature_max_ms=round(max(result.get("signature_ms", 0.0) for result in plan_results), 3),
                plan_total_ms=round(sum(result.get("plan_ms", 0.0) for result in plan_results), 3),
                plan_avg_ms=round(sum(result.get("plan_ms", 0.0) for result in plan_results) / len(plan_results), 3),
                plan_max_ms=round(max(result.get("plan_ms", 0.0) for result in plan_results), 3),
                skip_count=sum(1 for result in plan_results if result.get("plan", {}).get("status") == "skip"),
                active_count=sum(1 for result in plan_results if result.get("plan", {}).get("status") == "active"),
                queue_count=sum(1 for result in plan_results if result.get("plan", {}).get("status") in {"queue", "queued"}),
                detect_count=sum(1 for result in plan_results if result.get("plan", {}).get("status") == "detect"),
            )
            if deps.startup_scan_planner_trace_logging:
                parallel_plan_detail = summarize_parallel_plan_detail(plan_results)
                if parallel_plan_detail:
                    benchmark_logger.write("startup_scan.classify_media.parallel_plan_detail", **parallel_plan_detail)
            planner_trace = _aggregate_planner_trace(plan_results)
            if planner_trace:
                benchmark_logger.write("startup_scan.classify_media.parallel_plan_trace", **planner_trace)

        with deps.benchmark_step(
            "startup_scan.classify_media.apply",
            benchmark_logger,
            planned_count=len(plan_results),
        ):
            apply_stats = {
                "cached_excluded_count": 0,
                "cached_no_sidecar_requeue_count": 0,
                "cached_signature_requeue_count": 0,
                "miss_skip_count": 0,
                "miss_active_count": 0,
                "miss_queue_count": 0,
                "db_upsert_media_count": 0,
                "db_upsert_excluded_count": 0,
                "queue_request_count": 0,
                "db_upsert_media_ms": 0.0,
                "db_upsert_excluded_ms": 0.0,
                "queue_request_ms": 0.0,
                "json_serialize_ms": 0.0,
            }
            admission_times_by_path: dict[str, float] = {}
            final_outcomes_by_path: dict[str, str] = {}
            for result in plan_results:
                media = result["media"]
                queue_path = result["queue_path"]
                cached_row = result["cached_row"]
                current_sidecar_state = result["current_sidecar_state"]
                subtitle_signature = result["subtitle_signature"]
                matching_subtitles = result["matching_subtitles"]
                plan = result["plan"]
                logging.debug(
                    "Startup scan decision: path=%s status=%s reason=%s cache=miss.",
                    media["path"],
                    plan.get("status", "unknown"),
                    plan.get("reason", "unspecified"),
                )

                reusable_cached_record = _matches_cached_record(media, cached_row, policy_signature)
                if reusable_cached_record and cached_row.get("decision") == "excluded":
                    reusable_cached_record = bool(
                        plan.get("status") == "skip"
                        and plan.get("reason") == cached_row.get("reason")
                        and subtitle_signature == (cached_row.get("subtitle_signature") or "")
                    )
                if reusable_cached_record:
                    totals["cache_hit"] += 1
                    if cached_row.get("decision") == "excluded":
                        final_outcomes_by_path[queue_path] = "skip"
                        apply_stats["cached_excluded_count"] += 1
                        totals["reused"] += 1
                        totals["excluded"] += 1
                        db_started_at = time.perf_counter()
                        db.upsert_excluded(
                            path=queue_path,
                            source_path=media["path"],
                            size=media["size"],
                            mtime=media["mtime"],
                            reason=cached_row.get("reason") or "cached",
                            details=cached_row.get("reason") or "cached",
                            policy_signature=policy_signature,
                        )
                        apply_stats["db_upsert_excluded_count"] += 1
                        apply_stats["db_upsert_excluded_ms"] += (time.perf_counter() - db_started_at) * 1000
                        db_started_at = time.perf_counter()
                        db.upsert_media(
                            path=queue_path,
                            source_path=media["path"],
                            size=media["size"],
                            mtime=media["mtime"],
                        mtime_ns=media.get("mtime_ns"),
                        device=media.get("device"),
                        inode=media.get("inode"),
                            subtitle_signature=cached_row.get("subtitle_signature") or "",
                            has_audio=bool(cached_row.get("has_audio", 0)),
                            audio_language=cached_row.get("audio_language"),
                            subtitle_state=cached_row.get("subtitle_state", "unknown"),
                            decision="excluded",
                            reason=cached_row.get("reason") or "cached",
                            policy_signature=policy_signature,
                            task_type=cached_row.get("task_type"),
                            transcription_type=cached_row.get("transcription_type"),
                            force_language_code=cached_row.get("force_language_code"),
                            audio_tracks_json=cached_row.get("audio_tracks_json"),
                            audio_langs_json=cached_row.get("audio_langs_json"),
                        )
                        apply_stats["db_upsert_media_count"] += 1
                        apply_stats["db_upsert_media_ms"] += (time.perf_counter() - db_started_at) * 1000
                        continue

                    cached_state = cached_row.get("subtitle_state", "unknown")
                    if current_sidecar_state == "none" and cached_state in {"none", "unknown", "internal"}:
                        apply_stats["cached_no_sidecar_requeue_count"] += 1
                        queue_started_at = time.perf_counter()
                        cached_admitted = queue_cached_job(deps, queue_path, cached_row)
                        if cached_admitted:
                            totals["reused"] += 1
                            totals["queued"] += 1
                            final_outcomes_by_path[queue_path] = "queued"
                        else:
                            totals["ignored"] += 1
                            final_outcomes_by_path[queue_path] = "already_active"
                        apply_stats["queue_request_count"] += 1
                        apply_stats["queue_request_ms"] += (time.perf_counter() - queue_started_at) * 1000
                        db_started_at = time.perf_counter()
                        db.upsert_media(
                            path=queue_path,
                            source_path=media["path"],
                            size=media["size"],
                            mtime=media["mtime"],
                            mtime_ns=media.get("mtime_ns"),
                            device=media.get("device"),
                            inode=media.get("inode"),
                            subtitle_signature=cached_row.get("subtitle_signature") or "",
                            has_audio=bool(cached_row.get("has_audio", 0)),
                            audio_language=cached_row.get("audio_language"),
                            subtitle_state=cached_state,
                            decision="eligible",
                            reason=cached_row.get("reason") or "cached",
                            policy_signature=policy_signature,
                            task_type=cached_row.get("task_type"),
                            transcription_type=cached_row.get("transcription_type"),
                            force_language_code=cached_row.get("force_language_code"),
                            audio_tracks_json=cached_row.get("audio_tracks_json"),
                            audio_langs_json=cached_row.get("audio_langs_json"),
                        )
                        apply_stats["db_upsert_media_count"] += 1
                        apply_stats["db_upsert_media_ms"] += (time.perf_counter() - db_started_at) * 1000
                        continue

                    if cached_row.get("subtitle_signature") == subtitle_signature:
                        apply_stats["cached_signature_requeue_count"] += 1
                        totals["reused"] += 1
                        queue_started_at = time.perf_counter()
                        cached_admitted = queue_cached_job(deps, queue_path, cached_row)
                        if cached_admitted:
                            totals["queued"] += 1
                            final_outcomes_by_path[queue_path] = "queued"
                        else:
                            totals["ignored"] += 1
                            final_outcomes_by_path[queue_path] = "already_active"
                        apply_stats["queue_request_count"] += 1
                        apply_stats["queue_request_ms"] += (time.perf_counter() - queue_started_at) * 1000
                        db_started_at = time.perf_counter()
                        db.upsert_media(
                            path=queue_path,
                            source_path=media["path"],
                            size=media["size"],
                            mtime=media["mtime"],
                            mtime_ns=media.get("mtime_ns"),
                            device=media.get("device"),
                            inode=media.get("inode"),
                            subtitle_signature=subtitle_signature,
                            has_audio=bool(cached_row.get("has_audio", 0)),
                            audio_language=cached_row.get("audio_language"),
                            subtitle_state=cached_row.get("subtitle_state", "unknown"),
                            decision="eligible",
                            reason=cached_row.get("reason") or "cached",
                            policy_signature=policy_signature,
                            task_type=cached_row.get("task_type"),
                            transcription_type=cached_row.get("transcription_type"),
                            force_language_code=cached_row.get("force_language_code"),
                            audio_tracks_json=cached_row.get("audio_tracks_json"),
                            audio_langs_json=cached_row.get("audio_langs_json"),
                        )
                        apply_stats["db_upsert_media_count"] += 1
                        apply_stats["db_upsert_media_ms"] += (time.perf_counter() - db_started_at) * 1000
                        continue

                totals["cache_miss"] += 1
                if plan["status"] == "skip":
                    apply_stats["miss_skip_count"] += 1
                    totals["excluded"] += 1
                    db_started_at = time.perf_counter()
                    db.upsert_excluded(
                        path=queue_path,
                        source_path=media["path"],
                        size=media["size"],
                        mtime=media["mtime"],
                        reason=plan["reason"],
                        details=plan["details"],
                        policy_signature=policy_signature,
                    )
                    apply_stats["db_upsert_excluded_count"] += 1
                    apply_stats["db_upsert_excluded_ms"] += (time.perf_counter() - db_started_at) * 1000
                    db_started_at = time.perf_counter()
                    db.upsert_media(
                        path=queue_path,
                        source_path=media["path"],
                        size=media["size"],
                        mtime=media["mtime"],
                        mtime_ns=media.get("mtime_ns"),
                        device=media.get("device"),
                        inode=media.get("inode"),
                        subtitle_signature=subtitle_signature,
                        has_audio=False,
                        audio_language="",
                        subtitle_state="external" if matching_subtitles else "unknown",
                        decision="excluded",
                        reason=plan["reason"],
                        policy_signature=policy_signature,
                        task_type=None,
                        transcription_type=None,
                        force_language_code=None,
                        audio_tracks_json=None,
                        audio_langs_json=None,
                    )
                    apply_stats["db_upsert_media_count"] += 1
                    apply_stats["db_upsert_media_ms"] += (time.perf_counter() - db_started_at) * 1000
                    continue

                if plan["status"] == "active":
                    apply_stats["miss_active_count"] += 1
                    totals["ignored"] += 1
                    final_outcomes_by_path[queue_path] = "already_active"
                    continue

                if plan["status"] in {"queue", "detect", "retryable_error"}:
                    apply_stats["miss_queue_count"] += 1
                    json_started_at = time.perf_counter()
                    task = plan["task"]
                    audio_tracks_json = json.dumps(
                        plan.get("audio_tracks") or [],
                        default=lambda value: deps.json_default(
                            value,
                            language_code=deps.language_code,
                        ),
                    )
                    audio_langs_json = json.dumps(
                        [lang.to_iso_639_2_t() if lang else "" for lang in plan.get("audio_langs") or []]
                    )
                    apply_stats["json_serialize_ms"] += (time.perf_counter() - json_started_at) * 1000
                    queue_started_at = time.perf_counter()
                    admitted = _enqueue_subtitles(
                        deps,
                        task,
                    )
                    admission_times_by_path[queue_path] = (time.perf_counter() - queue_started_at) * 1000
                    apply_stats["queue_request_count"] += 1
                    apply_stats["queue_request_ms"] += (time.perf_counter() - queue_started_at) * 1000
                    db_started_at = time.perf_counter()
                    probe_succeeded = plan["status"] != "retryable_error"
                    db.upsert_media(
                        path=queue_path,
                        source_path=media["path"],
                        size=media["size"],
                        mtime=media["mtime"],
                        mtime_ns=media.get("mtime_ns"),
                        device=media.get("device"),
                        inode=media.get("inode"),
                        subtitle_signature=subtitle_signature,
                        has_audio=bool(plan.get("audio_tracks")) if probe_succeeded else None,
                        audio_language=(task.get("force_language").to_iso_639_2_t() if task.get("force_language") else "") if probe_succeeded else None,
                        subtitle_state="external" if matching_subtitles else "unknown",
                        decision="eligible" if probe_succeeded else "retryable_error",
                        reason=plan["reason"],
                        policy_signature=policy_signature,
                        task_type=task.get("type") or task.get("transcribe_or_translate") or deps.transcribe_or_translate,
                        transcription_type=task.get("transcribe_or_translate") or deps.transcribe_or_translate,
                        force_language_code=task.get("force_language").to_iso_639_2_t() if task.get("force_language") else "",
                        audio_tracks_json=audio_tracks_json if probe_succeeded else None,
                        audio_langs_json=audio_langs_json if probe_succeeded else None,
                    )
                    apply_stats["db_upsert_media_count"] += 1
                    apply_stats["db_upsert_media_ms"] += (time.perf_counter() - db_started_at) * 1000
                    if admitted:
                        totals["queued"] += 1
                        final_outcomes_by_path[queue_path] = "queued"
                    else:
                        totals["ignored"] += 1
                        final_outcomes_by_path[queue_path] = "already_active"

            for result in plan_results:
                plan = result["plan"]
                file_id = hashlib.sha256(os.path.normcase(os.path.abspath(result["queue_path"])).encode("utf-8")).hexdigest()[:16]
                evaluation_ms = result.get("plan_ms", 0.0) + result.get("signature_ms", 0.0)
                outcome = final_outcomes_by_path.get(result["queue_path"], plan.get("status", "unknown"))
                benchmark_decision_samples.append(evaluation_ms)
                benchmark_outcomes[outcome] = benchmark_outcomes.get(outcome, 0) + 1
                benchmark_logger.write(
                    "startup_scan.benchmark_decision",
                    run_id=getattr(benchmark_logger, "run_id", None),
                    file_id=file_id,
                    outcome=outcome,
                    reason=plan.get("reason", "unspecified"),
                    cache_source="miss",
                    evaluation_ms=round(evaluation_ms, 3),
                    admission_ms=round(admission_times_by_path.get(result["queue_path"], 0.0), 3),
                )

            benchmark_logger.write(
                "startup_scan.classify_media.apply_breakdown",
                **round_benchmark_fields(apply_stats),
            )

    probe_cache_after = deps.probe_cache_info()
    benchmark_logger.write(
        "startup_scan.probe_cache",
        hits=probe_cache_after.hits - probe_cache_before.hits,
        misses=probe_cache_after.misses - probe_cache_before.misses,
        size=probe_cache_after.currsize,
        maxsize=probe_cache_after.maxsize,
    )
    sorted_samples = sorted(benchmark_decision_samples)
    p95_index = max(0, int((len(sorted_samples) - 1) * 0.95))
    totals["benchmark_decision_count"] = len(sorted_samples)
    totals["benchmark_decision_median_ms"] = (
        (sorted_samples[(len(sorted_samples) - 1) // 2] + sorted_samples[len(sorted_samples) // 2]) / 2
        if sorted_samples else 0.0
    )
    totals["benchmark_decision_p95_ms"] = sorted_samples[p95_index] if sorted_samples else 0.0
    totals["benchmark_outcomes"] = benchmark_outcomes
    totals["classification_elapsed_ms"] = (time.perf_counter() - classification_started_at) * 1000
    return totals
