import json
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
    force: bool = False,
    audio_tracks=None,
    subtitle_rows=None,
    current_sidecar_state: str | None = None,
    **task_kwargs,
):
    trace = _planner_trace(deps.startup_scan_planner_trace_logging)

    started_at = time.perf_counter()
    if not force and deps.task_queue.is_active(file_path):
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

    if not force and deps.describe_skip_reason_pre_audio:
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
        audio_tracks = deps.get_audio_tracks(file_path)
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

    if not force:
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
        detect_task = {"path": file_path, "type": "detect_language"}
        detect_task.update(task_kwargs)
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
        "transcribe_or_translate": transcription_type,
        "force_language": force_language,
        "audio_tracks": audio_tracks,
    }
    task.update(task_kwargs)

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
    if task_type == "detect_language":
        task = {"path": queue_path, "type": "detect_language"}
    else:
        audio_tracks = deps.deserialize_audio_tracks(cached_row.get("audio_tracks_json"))
        force_language_code = cached_row.get("force_language_code") or ""
        force_language = (
            deps.language_code.from_string(force_language_code)
            if force_language_code
            else deps.language_code.NONE
        )
        task = {
            "path": queue_path,
            "transcribe_or_translate": cached_row.get("transcription_type") or deps.transcribe_or_translate,
            "force_language": force_language,
            "audio_tracks": audio_tracks,
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
    plan = prepare_media_queue_job(
        deps,
        queue_path,
        deps.transcribe_or_translate,
        force_language=deps.language_code.NONE,
        audio_tracks=deps.deserialize_audio_tracks(cached_row.get("audio_tracks_json")) if cached_row else None,
        subtitle_rows=subtitle_rows,
        current_sidecar_state=current_sidecar_state,
    )
    plan_ms = (time.perf_counter() - plan_started_at) * 1000

    if plan["status"] in {"skip", "active"}:
        return {
            "media": media,
            "queue_path": queue_path,
            "cached_row": cached_row,
            "subtitle_rows": subtitle_rows,
            "current_sidecar_state": current_sidecar_state,
            "subtitle_signature": "",
            "matching_subtitles": [],
            "plan": plan,
            "signature_ms": 0.0,
            "subtitle_signature_ms": 0.0,
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


def _matches_cached_record(media_record: dict, cached_row: dict | None, policy_signature: str) -> bool:
    return bool(
        cached_row
        and cached_row.get("size") == media_record["size"]
        and cached_row.get("mtime") == media_record["mtime"]
        and cached_row.get("policy_signature") == policy_signature
    )


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
    aggregate = {
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


def _enqueue_subtitles(deps: StartupScanDependencies, queue_path: str, transcription_type: str, force_language, mtime: int) -> None:
    try:
        deps.gen_subtitles_queue(
            queue_path,
            transcription_type,
            force_language,
            mtime=mtime,
        )
    except TypeError as exc:
        if "mtime" not in str(exc):
            raise
        deps.gen_subtitles_queue(
            queue_path,
            transcription_type,
            force_language,
        )


def classify_media(
    deps: StartupScanDependencies,
    db,
    inventory,
    subtitle_rows_by_media: dict[str, list[dict[str, Any]]],
    benchmark_logger,
    policy_signature: str,
):
    totals = {
        "reused": 0,
        "excluded": 0,
        "queued": 0,
        "ignored": 0,
        "cache_hit": 0,
        "cache_miss": 0,
    }

    probe_cache_before = deps.probe_cache_info()

    with deps.benchmark_step("startup_scan.classify_media", benchmark_logger, media_count=len(inventory.media_files)):
        cached_media_by_path = {row["path"]: row for row in _get_all_media_rows(db)}
        cached_excluded_by_path = {row["path"]: row for row in _get_all_excluded_rows(db)}
        pending_media = []
        preflight_cache_hits = 0
        preflight_cache_misses = 0

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

                if _matches_cached_record(media, cached_excluded, policy_signature):
                    totals["reused"] += 1
                    totals["excluded"] += 1
                    db.upsert_excluded(
                        path=queue_path,
                        source_path=media["path"],
                        size=media["size"],
                        mtime=media["mtime"],
                        reason=cached_excluded.get("reason") or "cached",
                        details=cached_excluded.get("details") or "cached",
                        policy_signature=policy_signature,
                    )
                    db.upsert_media(
                        path=queue_path,
                        source_path=media["path"],
                        size=media["size"],
                        mtime=media["mtime"],
                        subtitle_signature=cached_row.get("subtitle_signature") if cached_row else "",
                        has_audio=bool(cached_row.get("has_audio", 0)) if cached_row else False,
                        audio_language=cached_row.get("audio_language") if cached_row else "",
                        subtitle_state=cached_row.get("subtitle_state", "unknown") if cached_row else "unknown",
                        decision="excluded",
                        reason=cached_excluded.get("reason") or (cached_row.get("reason") if cached_row else "cached"),
                        policy_signature=policy_signature,
                        task_type=cached_row.get("task_type") if cached_row else None,
                        transcription_type=cached_row.get("transcription_type") if cached_row else None,
                        force_language_code=cached_row.get("force_language_code") if cached_row else None,
                        audio_tracks_json=cached_row.get("audio_tracks_json") if cached_row else None,
                        audio_langs_json=cached_row.get("audio_langs_json") if cached_row else None,
                    )
                    preflight_cache_hits += 1
                    continue

                if _matches_cached_record(media, cached_row, policy_signature):
                    current_sidecar_state = deps.current_sidecar_state(subtitle_rows)
                    cached_state = cached_row.get("subtitle_state", "unknown")
                    if current_sidecar_state == "none" and cached_state in {"none", "unknown", "internal"}:
                        if queue_cached_job(deps, queue_path, cached_row):
                            totals["reused"] += 1
                            totals["queued"] += 1
                        else:
                            totals["ignored"] += 1
                        db.upsert_media(
                            path=queue_path,
                            source_path=media["path"],
                            size=media["size"],
                            mtime=media["mtime"],
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
                        preflight_cache_hits += 1
                        continue

                pending_media.append((media, queue_path, cached_row, subtitle_rows, current_sidecar_state))
                preflight_cache_misses += 1

        benchmark_logger.write(
            "startup_scan.classify_media.workload",
            preflight_cache_hits=preflight_cache_hits,
            pending_count=len(pending_media),
            workers=deps.startup_scan_classify_workers,
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
            for result in plan_results:
                media = result["media"]
                queue_path = result["queue_path"]
                cached_row = result["cached_row"]
                current_sidecar_state = result["current_sidecar_state"]
                subtitle_signature = result["subtitle_signature"]
                matching_subtitles = result["matching_subtitles"]
                plan = result["plan"]

                if _matches_cached_record(media, cached_row, policy_signature):
                    totals["cache_hit"] += 1
                    if cached_row.get("decision") == "excluded":
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
                        if queue_cached_job(deps, queue_path, cached_row):
                            totals["reused"] += 1
                            totals["queued"] += 1
                        else:
                            totals["ignored"] += 1
                        apply_stats["queue_request_count"] += 1
                        apply_stats["queue_request_ms"] += (time.perf_counter() - queue_started_at) * 1000
                        db_started_at = time.perf_counter()
                        db.upsert_media(
                            path=queue_path,
                            source_path=media["path"],
                            size=media["size"],
                            mtime=media["mtime"],
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
                        if queue_cached_job(deps, queue_path, cached_row):
                            totals["queued"] += 1
                        else:
                            totals["ignored"] += 1
                        apply_stats["queue_request_count"] += 1
                        apply_stats["queue_request_ms"] += (time.perf_counter() - queue_started_at) * 1000
                        db_started_at = time.perf_counter()
                        db.upsert_media(
                            path=queue_path,
                            source_path=media["path"],
                            size=media["size"],
                            mtime=media["mtime"],
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
                    continue

                if plan["status"] in {"queue", "detect"}:
                    apply_stats["miss_queue_count"] += 1
                    json_started_at = time.perf_counter()
                    task = plan["task"]
                    audio_tracks_json = json.dumps(plan.get("audio_tracks") or [], default=deps.json_default)
                    audio_langs_json = json.dumps(
                        [lang.to_iso_639_2_t() if lang else "" for lang in plan.get("audio_langs") or []]
                    )
                    apply_stats["json_serialize_ms"] += (time.perf_counter() - json_started_at) * 1000
                    queue_started_at = time.perf_counter()
                    _enqueue_subtitles(
                        deps,
                        queue_path,
                        task.get("transcribe_or_translate") or deps.transcribe_or_translate,
                        task.get("force_language", deps.language_code.NONE),
                        media["mtime"],
                    )
                    apply_stats["queue_request_count"] += 1
                    apply_stats["queue_request_ms"] += (time.perf_counter() - queue_started_at) * 1000
                    db_started_at = time.perf_counter()
                    db.upsert_media(
                        path=queue_path,
                        source_path=media["path"],
                        size=media["size"],
                        mtime=media["mtime"],
                        subtitle_signature=subtitle_signature,
                        has_audio=True,
                        audio_language=task.get("force_language").to_iso_639_2_t() if task.get("force_language") else "",
                        subtitle_state="external" if matching_subtitles else "unknown",
                        decision="eligible",
                        reason=plan["reason"],
                        policy_signature=policy_signature,
                        task_type=task.get("type", "transcribe"),
                        transcription_type=task.get("transcribe_or_translate"),
                        force_language_code=task.get("force_language").to_iso_639_2_t() if task.get("force_language") else "",
                        audio_tracks_json=audio_tracks_json,
                        audio_langs_json=audio_langs_json,
                    )
                    apply_stats["db_upsert_media_count"] += 1
                    apply_stats["db_upsert_media_ms"] += (time.perf_counter() - db_started_at) * 1000
                    totals["queued"] += 1

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
    return totals
