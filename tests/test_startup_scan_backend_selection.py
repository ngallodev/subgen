import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from subgen_startup_scan.backend import build_startup_scan_backend
from subgen_startup_scan.dependencies import StartupScanDependencies


def _deps():
    return StartupScanDependencies(
        persistent_initialize=lambda: None,
        persistent_startup_scan_existing=lambda spec: None,
        persistent_refresh_processed_file=lambda path, transcription_type, force_language, audio_tracks=None: None,
        legacy_startup_scan_existing=lambda spec: None,
    )


def test_build_startup_scan_backend_defaults_to_persistent(monkeypatch):
    monkeypatch.delenv("STARTUP_SCAN_BACKEND", raising=False)
    backend = build_startup_scan_backend(_deps())
    assert backend.name() == "persistent"


def test_build_startup_scan_backend_honors_legacy(monkeypatch):
    monkeypatch.setenv("STARTUP_SCAN_BACKEND", "legacy")
    backend = build_startup_scan_backend(_deps())
    assert backend.name() == "legacy"


def test_build_startup_scan_backend_rejects_unknown_value(monkeypatch):
    monkeypatch.setenv("STARTUP_SCAN_BACKEND", "unknown")
    try:
        build_startup_scan_backend(_deps())
    except ValueError as exc:
        assert "STARTUP_SCAN_BACKEND" in str(exc)
    else:
        raise AssertionError("Expected ValueError for invalid STARTUP_SCAN_BACKEND")
