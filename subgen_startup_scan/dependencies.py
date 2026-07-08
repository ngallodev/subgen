from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class StartupScanDependencies:
    persistent_initialize: Callable[[], None] | None = None
    persistent_startup_scan_existing: Callable[[str], None] | None = None
    persistent_refresh_processed_file: Callable[[str, str, Any, Any], None] | None = None
    legacy_startup_scan_existing: Callable[[str], None] | None = None
    task_queue: Any = None
    language_code: Any = None
    has_audio: Callable[[str], bool] | None = None
    get_audio_tracks: Callable[[str], list[dict[str, Any]]] | None = None
    choose_transcribe_language: Callable[..., Any] | None = None
    describe_skip_reason: Callable[..., tuple[bool, str, str]] | None = None
    should_whisper_detect_audio_language: bool = False
    startup_scan_planner_trace_logging: bool = False
    startup_scan_force_rewalk: bool = False
    startup_scan_classify_workers: int = 1
    startup_scan_benchmark_log_path: str = ""
    startup_scan_benchmark_logging: bool = False
    startup_scan_db_path: str = ""
    transcribe_or_translate: str = "transcribe"
    monitor: Any = None
    observer_factory: Callable[[], Any] | None = None
    new_file_handler_factory: Callable[[], Any] | None = None
    path_mapping: Callable[[str], str] | None = None
    current_sidecar_state: Callable[[list[dict[str, Any]]], str] | None = None
    deserialize_audio_tracks: Callable[[str | None], list[dict[str, Any]]] | None = None
    json_default: Callable[[Any], Any] | None = None
    gen_subtitles_queue: Callable[..., Any] | None = None
    collect_startup_inventory: Callable[..., Any] | None = None
    compute_subtitle_signature: Callable[..., tuple[str, list[dict[str, Any]]]] | None = None
    inventory_signature: Callable[..., dict[str, Any]] | None = None
    inventory_signature_matches: Callable[..., bool] | None = None
    store_inventory_signature: Callable[..., None] | None = None
    get_startup_policy_signature: Callable[[], str] | None = None
    probe_cache_info: Callable[[], Any] | None = None
    benchmark_logger_factory: Callable[..., Any] | None = None
    benchmark_step: Callable[..., Any] | None = None
    startup_scan_db_factory: Callable[..., Any] | None = None
    thread_pool_executor_factory: Callable[..., Any] | None = None
    plan_media_record: Callable[..., dict[str, Any]] | None = None
    startup_scan_monitor_async_start: bool = True
    thread_factory: Callable[..., Any] | None = None
    retain_observer: Callable[[Any], None] | None = None
