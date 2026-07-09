import json
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Any

from .config import get_startup_scan_benchmark_log_path, get_startup_scan_benchmark_logging

def __getattr__(name: str):
    if name == "BENCHMARK_LOG_PATH":
        return get_startup_scan_benchmark_log_path()
    if name == "BENCHMARK_LOGGING_ENABLED":
        return get_startup_scan_benchmark_logging()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class BenchmarkLogger:
    _sanitized_paths: set[str] = set()
    _write_lock = threading.Lock()

    def __init__(self, log_path: str | None = None, enabled: bool | None = None):
        self.log_path = log_path if log_path is not None else get_startup_scan_benchmark_log_path()
        self.enabled = enabled if enabled is not None else get_startup_scan_benchmark_logging()

    def _resolve_path(self) -> str:
        candidate_paths = [self.log_path]
        fallback_path = os.path.join(tempfile.gettempdir(), "subgen_startup_scan_benchmarks.jsonl")
        if fallback_path not in candidate_paths:
            candidate_paths.append(fallback_path)
        last_error = None
        for candidate_path in candidate_paths:
            try:
                log_dir = os.path.dirname(candidate_path) or "."
                os.makedirs(log_dir, exist_ok=True)
                if candidate_path != self.log_path:
                    logging.warning("Startup benchmark log path %s is unavailable; using %s instead.", self.log_path, candidate_path)
                return candidate_path
            except OSError as exc:
                last_error = exc
        raise last_error

    def _sanitize_path(self, path: str) -> None:
        if path in self._sanitized_paths:
            return
        self._sanitized_paths.add(path)
        if not os.path.exists(path):
            return
        valid_lines = []
        invalid_count = 0
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    invalid_count += 1
                    continue
                valid_lines.append(json.dumps(payload, sort_keys=True))
        if invalid_count == 0:
            return
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            for line in valid_lines:
                handle.write(line)
                handle.write("\n")
        os.replace(temp_path, path)
        logging.warning("Sanitized startup benchmark log at %s and removed %s malformed line(s).", path, invalid_count)

    def write(self, event: str, **fields):
        if not self.enabled:
            return
        payload = {"ts": time.time(), "event": event, **fields}
        with self._write_lock:
            path = self._resolve_path()
            self._sanitize_path(path)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True))
                handle.write("\n")


@contextmanager
def benchmark_step(event: str, logger: BenchmarkLogger | None = None, **fields):
    logger = logger or BenchmarkLogger()
    started_at = time.perf_counter()
    try:
        yield
    finally:
        logger.write(event, duration_ms=round((time.perf_counter() - started_at) * 1000, 3), **fields)


def round_benchmark_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        key: round(value, 3) if isinstance(value, float) else value
        for key, value in fields.items()
    }


def summarize_parallel_plan_detail(plan_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not plan_results:
        return None

    traces = [(result.get("plan") or {}).get("planner_trace") for result in plan_results]
    traces = [trace for trace in traces if trace]
    if not traces:
        return None

    signature_total = sum(result.get("subtitle_signature_ms", result.get("signature_ms", 0.0)) for result in plan_results)
    plan_total = sum(result.get("queue_plan_ms", result.get("plan_ms", 0.0)) for result in plan_results)
    trace_count = len(traces)

    detail = {
        "pending_count": len(plan_results),
        "traced_count": trace_count,
        "subtitle_signature_total_ms": round(signature_total, 3),
        "subtitle_signature_avg_ms": round(signature_total / len(plan_results), 3),
        "subtitle_signature_max_ms": round(
            max(result.get("subtitle_signature_ms", result.get("signature_ms", 0.0)) for result in plan_results),
            3,
        ),
        "queue_plan_total_ms": round(plan_total, 3),
        "queue_plan_avg_ms": round(plan_total / len(plan_results), 3),
        "queue_plan_max_ms": round(max(result.get("queue_plan_ms", result.get("plan_ms", 0.0)) for result in plan_results), 3),
        "cached_audio_track_plan_count": sum(1 for trace in traces if trace.get("used_cached_audio_tracks")),
        "audio_track_count_total": sum(trace.get("audio_track_count", 0) for trace in traces),
        "audio_lang_count_total": sum(trace.get("audio_lang_count", 0) for trace in traces),
    }

    trace_fields = [
        "active_check_ms",
        "has_audio_ms",
        "audio_tracks_ms",
        "audio_langs_ms",
        "choose_language_ms",
        "skip_check_ms",
        "detect_branch_ms",
    ]
    for field in trace_fields:
        total = sum(trace.get(field, 0.0) for trace in traces)
        detail[f"{field}_total_ms"] = total
        detail[f"{field}_avg_ms"] = total / trace_count
        detail[f"{field}_max_ms"] = max(trace.get(field, 0.0) for trace in traces)

    detail["probe_total_ms"] = round(
        detail["active_check_ms_total_ms"]
        + detail["has_audio_ms_total_ms"]
        + detail["audio_tracks_ms_total_ms"]
        + detail["audio_langs_ms_total_ms"],
        3,
    )
    detail["probe_avg_ms"] = round(detail["probe_total_ms"] / trace_count, 3)
    detail["probe_max_ms"] = round(
        max(
            trace.get("active_check_ms", 0.0)
            + trace.get("has_audio_ms", 0.0)
            + trace.get("audio_tracks_ms", 0.0)
            + trace.get("audio_langs_ms", 0.0)
            for trace in traces
        ),
        3,
    )
    detail["language_total_ms"] = round(
        detail["choose_language_ms_total_ms"] + detail["skip_check_ms_total_ms"],
        3,
    )
    detail["language_avg_ms"] = round(detail["language_total_ms"] / trace_count, 3)
    detail["language_max_ms"] = round(
        max(trace.get("choose_language_ms", 0.0) + trace.get("skip_check_ms", 0.0) for trace in traces),
        3,
    )
    detail["detect_total_ms"] = round(detail["detect_branch_ms_total_ms"], 3)
    detail["detect_avg_ms"] = round(detail["detect_total_ms"] / trace_count, 3)
    detail["detect_max_ms"] = round(max(trace.get("detect_branch_ms", 0.0) for trace in traces), 3)

    return round_benchmark_fields(detail)
