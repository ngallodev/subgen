import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import scan_index
import subgen_startup_scan
import subgen_startup_scan.benchmarks as benchmarks


def test_benchmark_module_exports_track_env_changes(monkeypatch, tmp_path):
    log_path = tmp_path / "benchmarks.jsonl"
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOGGING", "true")
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOG_PATH", str(log_path))

    assert benchmarks.BENCHMARK_LOGGING_ENABLED is True
    assert benchmarks.BENCHMARK_LOG_PATH == str(log_path)

    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOGGING", "false")
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOG_PATH", str(tmp_path / "updated.jsonl"))

    assert benchmarks.BENCHMARK_LOGGING_ENABLED is False
    assert benchmarks.BENCHMARK_LOG_PATH == str(tmp_path / "updated.jsonl")


def test_package_exports_track_env_changes(monkeypatch, tmp_path):
    log_path = tmp_path / "package.jsonl"
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOGGING", "true")
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOG_PATH", str(log_path))

    assert subgen_startup_scan.BENCHMARK_LOGGING_ENABLED is True
    assert subgen_startup_scan.BENCHMARK_LOG_PATH == str(log_path)

    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOGGING", "false")
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOG_PATH", str(tmp_path / "package-updated.jsonl"))

    assert subgen_startup_scan.BENCHMARK_LOGGING_ENABLED is False
    assert subgen_startup_scan.BENCHMARK_LOG_PATH == str(tmp_path / "package-updated.jsonl")


def test_scan_index_exports_track_env_changes(monkeypatch, tmp_path):
    log_path = tmp_path / "scan-index.jsonl"
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOGGING", "true")
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOG_PATH", str(log_path))

    assert scan_index.BENCHMARK_LOGGING_ENABLED is True
    assert scan_index.BENCHMARK_LOG_PATH == str(log_path)

    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOGGING", "false")
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOG_PATH", str(tmp_path / "scan-index-updated.jsonl"))

    assert scan_index.BENCHMARK_LOGGING_ENABLED is False
    assert scan_index.BENCHMARK_LOG_PATH == str(tmp_path / "scan-index-updated.jsonl")
