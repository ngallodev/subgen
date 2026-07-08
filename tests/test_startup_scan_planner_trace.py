import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from language_code import LanguageCode
from subgen_startup_scan.benchmarks import summarize_parallel_plan_detail
from subgen_startup_scan.classifier import prepare_media_queue_job
from subgen_startup_scan.dependencies import StartupScanDependencies


class _TaskQueue:
    def __init__(self, active_paths=None):
        self.active_paths = set(active_paths or [])

    def is_active(self, path):
        return path in self.active_paths


def _deps(trace_enabled: bool) -> StartupScanDependencies:
    return StartupScanDependencies(
        task_queue=_TaskQueue(),
        language_code=LanguageCode,
        has_audio=lambda path: True,
        get_audio_tracks=lambda path: [{"language": LanguageCode.SPANISH}],
        choose_transcribe_language=lambda path, force_language, audio_tracks=None: force_language or LanguageCode.SPANISH,
        describe_skip_reason=lambda path, force_language, audio_langs=None: (False, "", ""),
        should_whisper_detect_audio_language=False,
        startup_scan_planner_trace_logging=trace_enabled,
    )


def test_prepare_media_queue_job_emits_trace_when_enabled():
    plan = prepare_media_queue_job(
        _deps(True),
        "/movies/demo.mkv",
        "transcribe",
        force_language=LanguageCode.NONE,
        audio_tracks=[{"language": "spa"}],
    )

    assert plan["status"] == "queue"
    assert plan["planner_trace"]["used_cached_audio_tracks"] is True
    assert plan["planner_trace"]["audio_track_count"] == 1
    assert plan["planner_trace"]["choose_language_ms"] >= 0


def test_prepare_media_queue_job_omits_trace_when_disabled():
    plan = prepare_media_queue_job(
        _deps(False),
        "/movies/demo.mkv",
        "transcribe",
        force_language=LanguageCode.NONE,
    )

    assert plan["status"] == "queue"
    assert plan["planner_trace"] is None


def test_benchmark_logger_defaults_to_quiet_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv("STARTUP_SCAN_BENCHMARK_LOGGING", raising=False)
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOG_PATH", str(tmp_path / "benchmarks.jsonl"))

    from subgen_startup_scan.benchmarks import BenchmarkLogger

    logger = BenchmarkLogger()

    assert logger.enabled is False

    logger.write("startup_scan.total", duration_ms=1.23)

    assert not (tmp_path / "benchmarks.jsonl").exists()


def test_benchmark_logger_writes_when_enabled(monkeypatch, tmp_path):
    log_path = tmp_path / "benchmarks.jsonl"
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOGGING", "true")
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOG_PATH", str(log_path))

    from subgen_startup_scan.benchmarks import BenchmarkLogger, benchmark_step

    logger = BenchmarkLogger()

    assert logger.enabled is True

    with benchmark_step("startup_scan.total", logger, folder_count=1):
        pass

    payload = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert payload["event"] == "startup_scan.total"
    assert payload["folder_count"] == 1


def test_parallel_plan_detail_summarizes_signature_plan_and_trace_slices():
    detail = summarize_parallel_plan_detail(
        [
            {
                "subtitle_signature_ms": 1.25,
                "queue_plan_ms": 2.5,
                "plan": {
                    "planner_trace": {
                        "active_check_ms": 0.1,
                        "has_audio_ms": 0.2,
                        "audio_tracks_ms": 0.3,
                        "audio_langs_ms": 0.4,
                        "choose_language_ms": 0.5,
                        "skip_check_ms": 0.6,
                        "detect_branch_ms": 0.7,
                        "audio_track_count": 2,
                        "audio_lang_count": 1,
                        "used_cached_audio_tracks": True,
                    }
                },
            },
            {
                "subtitle_signature_ms": 2.75,
                "queue_plan_ms": 4.5,
                "plan": {
                    "planner_trace": {
                        "active_check_ms": 0.8,
                        "has_audio_ms": 0.9,
                        "audio_tracks_ms": 1.0,
                        "audio_langs_ms": 1.1,
                        "choose_language_ms": 1.2,
                        "skip_check_ms": 1.3,
                        "detect_branch_ms": 1.4,
                        "audio_track_count": 3,
                        "audio_lang_count": 2,
                        "used_cached_audio_tracks": False,
                    }
                },
            },
        ]
    )

    assert detail["pending_count"] == 2
    assert detail["traced_count"] == 2
    assert detail["subtitle_signature_total_ms"] == 4.0
    assert detail["queue_plan_total_ms"] == 7.0
    assert detail["probe_total_ms"] == 4.8
    assert detail["language_total_ms"] == 3.6
    assert detail["detect_total_ms"] == 2.1
    assert detail["cached_audio_track_plan_count"] == 1
    assert detail["audio_track_count_total"] == 5
    assert detail["audio_lang_count_total"] == 3
