from subgen_startup_scan.benchmarks import BenchmarkLogger, benchmark_step
from subgen_startup_scan.db import StartupScanDB
from subgen_startup_scan.schema import SCHEMA_VERSION, ensure_startup_scan_schema

__all__ = [
    "BENCHMARK_LOGGING_ENABLED",
    "BENCHMARK_LOG_PATH",
    "BenchmarkLogger",
    "SCHEMA_VERSION",
    "StartupScanDB",
    "benchmark_step",
    "ensure_startup_scan_schema",
]


def __getattr__(name: str):
    if name == "BENCHMARK_LOGGING_ENABLED":
        from subgen_startup_scan.config import get_startup_scan_benchmark_logging

        return get_startup_scan_benchmark_logging()
    if name == "BENCHMARK_LOG_PATH":
        from subgen_startup_scan.config import get_startup_scan_benchmark_log_path

        return get_startup_scan_benchmark_log_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
