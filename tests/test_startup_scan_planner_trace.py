import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from language_code import LanguageCode
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
