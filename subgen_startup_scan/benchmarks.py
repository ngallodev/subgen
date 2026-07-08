import json
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Any


BENCHMARK_LOG_PATH = os.getenv("STARTUP_SCAN_BENCHMARK_LOG_PATH", "/subgen/state/startup_scan_benchmarks.jsonl")
BENCHMARK_LOGGING_ENABLED = os.getenv("STARTUP_SCAN_BENCHMARK_LOGGING", "False").lower() in {"1", "true", "yes", "on"}


class BenchmarkLogger:
    _sanitized_paths: set[str] = set()
    _write_lock = threading.Lock()

    def __init__(self, log_path: str = BENCHMARK_LOG_PATH, enabled: bool = BENCHMARK_LOGGING_ENABLED):
        self.log_path = log_path
        self.enabled = enabled

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
