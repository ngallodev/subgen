from .backend import StartupScanBackend, build_startup_scan_backend
from .benchmarks import BenchmarkLogger, benchmark_step
from .config import get_startup_scan_backend_name
from .db import StartupScanDB
from .dependencies import StartupScanDependencies
from .inventory import StartupInventory, collect_startup_inventory, is_relevant_inventory_file_name, is_subtitle_file_name
from .schema import SCHEMA_VERSION, ensure_startup_scan_schema
from .signatures import (
    compute_subtitle_signature,
    startup_scan_build_flat_inventory,
    startup_scan_collect_flat_media_records,
    startup_scan_flat_manifest,
    startup_scan_inventory_signature,
    startup_scan_inventory_signature_matches,
    startup_scan_store_inventory_signature,
)

__all__ = [
    "BENCHMARK_LOGGING_ENABLED",
    "BENCHMARK_LOG_PATH",
    "BenchmarkLogger",
    "SCHEMA_VERSION",
    "StartupInventory",
    "StartupScanBackend",
    "StartupScanDB",
    "StartupScanDependencies",
    "benchmark_step",
    "build_startup_scan_backend",
    "collect_startup_inventory",
    "compute_subtitle_signature",
    "ensure_startup_scan_schema",
    "get_startup_scan_backend_name",
    "is_relevant_inventory_file_name",
    "is_subtitle_file_name",
    "startup_scan_build_flat_inventory",
    "startup_scan_collect_flat_media_records",
    "startup_scan_flat_manifest",
    "startup_scan_inventory_signature",
    "startup_scan_inventory_signature_matches",
    "startup_scan_store_inventory_signature",
]


def __getattr__(name: str):
    if name == "BENCHMARK_LOGGING_ENABLED":
        from .config import get_startup_scan_benchmark_logging

        return get_startup_scan_benchmark_logging()
    if name == "BENCHMARK_LOG_PATH":
        from .config import get_startup_scan_benchmark_log_path

        return get_startup_scan_benchmark_log_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
