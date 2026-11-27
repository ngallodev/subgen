import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

from tools import startup_scan_benchmark as benchmark


def test_upstream_writes_each_decision_once_after_the_scan(tmp_path, monkeypatch):
    worker_log = tmp_path / "upstream.jsonl"
    worker_result = tmp_path / "upstream.result.json"
    media_root = tmp_path / "media"
    media_root.mkdir()
    for name in ("queued.mkv", "skipped.mkv", "readme.txt"):
        (media_root / name).touch()

    (tmp_path / "subgen.py").write_text(
        """import os
from pathlib import Path

def has_video_extension(path):
    return path.endswith(".mkv")

def has_audio_extension(path):
    return False

def gen_subtitles_queue(path):
    return path.endswith("queued.mkv")

def transcribe_existing(root):
    for folder, _, names in os.walk(root):
        for name in names:
            gen_subtitles_queue(os.path.join(folder, name))
            assert not Path(os.environ["STARTUP_SCAN_BENCHMARK_LOG_PATH"]).exists()
    return {"errors": 0, "folders": [{"path": root, "files_found": 3}]}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK", "1")
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_WORKER_ISOLATION", "1")
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_LOG_PATH", str(worker_log))
    # Capture globals replaced by the adapter in its normally short-lived process.
    monkeypatch.setattr(os, "walk", os.walk)
    monkeypatch.setattr(threading.Thread, "start", threading.Thread.start)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(benchmark, "_git_sha", lambda repo: "pinned-upstream")

    assert benchmark._worker(
        SimpleNamespace(
            backend="legacy",
            root=[str(media_root)],
            worker_case="upstream",
            worker_log=worker_log,
            worker_repo=tmp_path,
            worker_result=worker_result,
        )
    ) == 0

    records = [json.loads(line) for line in worker_log.read_text(encoding="utf-8").splitlines()]
    result = json.loads(worker_result.read_text(encoding="utf-8"))
    assert len(records) == 3
    assert len({row["file_id"] for row in records}) == 3
    assert result["decision_count"] == 2
    assert result["non_media_count"] == 1
    assert result["filesystem_elapsed_ms"] >= 0
    assert result["directories_enumerated"] == 1
    assert result["walk_files_seen"] == 3
    assert result["outcomes"] == {"queued": 1, "skip": 1}
    assert result["worker_starts"] == 0
    assert result["summary"]["folders"] == [{"root_id": benchmark._path_id(str(media_root)), "files_found": 3}]


def _case(*decisions, **case_fields):
    records = [
        {
            "event": "startup_scan.benchmark_decision",
            "file_id": file_id,
            "outcome": outcome,
            "reason": reason,
            "evaluation_ms": 1.0,
        }
        for file_id, outcome, reason in decisions
    ]
    return {**benchmark._decision_metrics(records), "summary": {"errors": 0}, **case_fields}


def test_parity_checks_media_membership_and_eligibility():
    baseline = _case(("a", "queued", ""), ("b", "skip", ""), ("c", "non_media", ""))
    warm = _case(("a", "already_active", ""), ("b", "skip", ""))
    assert benchmark._compare_decisions(baseline, warm)["matches"] is True

    smart = _case(("a", "skip", ""), ("d", "queued", ""))
    mismatch = benchmark._compare_decisions(baseline, smart)
    assert mismatch["matches"] is False
    assert mismatch["changed_decision_examples"] == ["a"]
    assert mismatch["missing_media_examples"] == ["b"]
    assert mismatch["extra_media_examples"] == ["d"]


def test_parity_rejects_duplicate_decisions_and_probe_failures():
    baseline = _case(("a", "queued", ""), ("b", "skip", ""))
    smart = _case(("a", "queued", ""), ("a", "queued", ""), ("b", "queued", "media_probe_failed"))

    mismatch = benchmark._compare_decisions(baseline, smart)

    assert mismatch["matches"] is False
    assert mismatch["errors"] == ["duplicate_media_decisions", "invalid_media_decisions"]


def test_later_repetition_can_withhold_speedup(tmp_path, monkeypatch):
    media_root = tmp_path / "media"
    media_root.mkdir()
    output = tmp_path / "report"
    monkeypatch.setattr(benchmark, "_git_sha", lambda repo: "pinned")
    monkeypatch.setattr(benchmark, "_copy_sqlite", lambda src, dst: None)
    monkeypatch.setattr(
        benchmark.sys,
        "argv",
        [
            str(Path(benchmark.__file__)),
            "--root", str(media_root),
            "--output-dir", str(output),
            "--upstream-checkout", str(tmp_path),
            "--upstream-sha", "pinned",
            "--repetitions", "2",
            "--dataset-read-only",
        ],
    )
    monkeypatch.setenv("STARTUP_SCAN_BENCHMARK_DATASET_FINGERPRINT", "")

    def run_child(case, repo, roots, state, output, *, backend):
        outcome = "skip" if case == "smart-cold" and state.parent.name.startswith("run-02-") else "queued"
        return _case(("a", outcome, ""), case=case, scan_elapsed_ms=10)

    monkeypatch.setattr(benchmark, "_run_child", run_child)

    assert benchmark.main() == 0
    report = json.loads((output / "startup-scan-comparison.json").read_text(encoding="utf-8"))

    assert report["decision_parity"][0]["smart-cold"]["matches"] is True
    assert report["decision_parity"][1]["smart-cold"]["matches"] is False
    assert report["case_medians"]["smart-cold"]["speedup_vs_upstream"] is None
    assert report["case_medians"]["smart-warm"]["speedup_vs_upstream"] == 1.0
