import os


def get_startup_scan_backend_name(default: str = "persistent") -> str:
    value = (os.getenv("STARTUP_SCAN_BACKEND") or default).strip().lower()
    if value not in {"legacy", "persistent"}:
        raise ValueError(f"Unsupported STARTUP_SCAN_BACKEND value: {value}")
    return value


def get_startup_scan_planner_trace_logging(default: bool = False) -> bool:
    value = os.getenv("STARTUP_SCAN_PLANNER_TRACE_LOGGING")
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_startup_scan_monitor_async_start(default: bool = True) -> bool:
    value = os.getenv("STARTUP_SCAN_MONITOR_ASYNC_START")
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
