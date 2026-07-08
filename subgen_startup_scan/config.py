import os


def _get_env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_startup_scan_backend_name(default: str = "persistent") -> str:
    value = (os.getenv("STARTUP_SCAN_BACKEND") or default).strip().lower()
    if value not in {"legacy", "persistent"}:
        raise ValueError(f"Unsupported STARTUP_SCAN_BACKEND value: {value}")
    return value


def get_startup_scan_benchmark_log_path(
    default: str = "/subgen/state/startup_scan_benchmarks.jsonl",
) -> str:
    return (os.getenv("STARTUP_SCAN_BENCHMARK_LOG_PATH") or default).strip()


def get_startup_scan_benchmark_logging(default: bool = False) -> bool:
    return _get_env_flag("STARTUP_SCAN_BENCHMARK_LOGGING", default)


def get_startup_scan_planner_trace_logging(default: bool = False) -> bool:
    return _get_env_flag("STARTUP_SCAN_PLANNER_TRACE_LOGGING", default)


def get_startup_scan_monitor_async_start(default: bool = True) -> bool:
    return _get_env_flag("STARTUP_SCAN_MONITOR_ASYNC_START", default)
