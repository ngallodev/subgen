"""Tests for DeduplicatedQueue."""
import sys
import os
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

# conftest.py has already patched sys.modules; safe to import subgen now
import subgen
from subgen import DeduplicatedQueue
from subgen_startup_scan.dependencies import StartupScanDependencies
from subgen_startup_scan.classifier import prepare_media_queue_job, queue_cached_job


@pytest.fixture()
def q():
    return DeduplicatedQueue()


def _task(path, task_type="transcribe"):
    return {"path": path, "type": task_type, "transcribe_or_translate": "transcribe", "force_language": None}


class TestPut:
    def test_new_item_returns_true(self, q):
        assert q.put(_task("/a/b.mkv")) is True

    def test_duplicate_while_queued_returns_false(self, q):
        q.put(_task("/a/b.mkv"))
        assert q.put(_task("/a/b.mkv")) is False

    def test_different_paths_both_accepted(self, q):
        assert q.put(_task("/a/b.mkv")) is True
        assert q.put(_task("/a/c.mkv")) is True

    def test_duplicate_while_processing_returns_false(self, q):
        q.put(_task("/a/b.mkv"))
        q.get()  # moves to _processing
        assert q.put(_task("/a/b.mkv")) is False

    def test_force_does_not_duplicate_queued_or_processing_work(self, q):
        task = _task("/a/b.mkv")
        assert q.put(task) is True
        assert q.put_with_result(task, force=True)["status"] == "already_active"
        active = q.get()
        assert q.put_with_result(task, force=True)["status"] == "busy"
        q.task_done()
        q.mark_done(active)
        assert q.put_with_result(task, force=True)["status"] == "queued"

    def test_file_path_aliases_share_a_deduplication_key(self, q):
        assert q.put(_task("movie.mkv")) is True
        assert q.put_with_result(_task("./movie.mkv"), force=True)["status"] == "already_active"
        assert q.put_with_result(_task(os.path.abspath("movie.mkv")), force=True)["status"] == "already_active"

    def test_extensionless_media_names_that_resemble_upload_ids_are_normalized(self, q, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        extensionless_media = tmp_path / "asr-recording"
        extensionless_media.write_bytes(b"media")
        assert q.put(_task("asr-recording"))
        assert q.put_with_result(_task("./asr-recording"), force=True)["status"] == "already_active"
        extensionless_media.unlink()
        active = q.get()
        q.task_done()
        q.mark_done(active, success=False)
        assert not q.is_active(os.path.abspath("asr-recording"))

    def test_file_backed_asr_shares_file_path_reservation_but_upload_ids_stay_opaque(self, q):
        asr_file = {
            "path": "movie.mkv",
            "type": "asr",
            "video_file": "./movie.mkv",
            "audio_content": b"audio",
        }
        assert q.put(asr_file)
        assert q.put_with_result(_task(os.path.abspath("movie.mkv")), force=True)["status"] == "already_active"

        opaque = {"path": "asr-abc123", "type": "asr", "audio_content": b"audio"}
        assert q.put(opaque)
        assert q.is_active("asr-abc123")

    def test_watcher_delegates_audio_probe_to_queue_planner(self, monkeypatch, tmp_path):
        path = str(tmp_path / "movie.mkv")
        queued = []
        monkeypatch.setattr(subgen, "_is_in_skipped_dir", lambda _: False)
        monkeypatch.setattr(subgen, "path_mapping", lambda value: value)
        monkeypatch.setattr(subgen, "has_audio", lambda _: (_ for _ in ()).throw(OSError("share unavailable")))
        monkeypatch.setattr(subgen, "gen_subtitles_queue", lambda *args: queued.append(args))

        subgen.NewFileHandler().create_subtitle(SimpleNamespace(is_directory=False, src_path=path))

        assert queued == [(path, subgen.transcribe_or_translate)]

    def test_file_backed_asr_uses_mapped_path_for_reservation(self, q):
        mapped = os.path.abspath(os.path.join("mounted", "movie.mkv"))
        asr_file = {
            "path": mapped,
            "type": "asr",
            "video_file": "Z:/client/movie.mkv",
            "audio_content": b"audio",
        }
        assert q.put(asr_file)
        assert q.put_with_result(_task(os.path.join("mounted", ".", "movie.mkv")), force=True)["status"] == "already_active"

    def test_detection_handoff_keeps_queue_work_pending_until_successor_finishes(self, q):
        detection = {"path": "/a/b.mkv", "type": "detect_language"}
        transcription = {"path": "/a/b.mkv", "type": "transcribe", "force": True}
        assert q.put(detection)
        active = q.get()

        assert q.handoff(active, transcription) is True
        assert q.unfinished_tasks == 1
        assert q.put_with_result(_task("/a/b.mkv"), force=True)["status"] == "already_active"

        successor = q.get()
        assert successor["force"] is True
        q.task_done()
        q.mark_done(successor)
        q.join()


class TestPriority:
    def test_detect_language_before_transcribe(self, q):
        q.put({"path": "/a.mkv", "type": "transcribe", "transcribe_or_translate": "transcribe", "force_language": None})
        q.put({"path": "/b.mkv", "type": "detect_language"})
        first = q.get()
        assert first["type"] == "detect_language"

    def test_asr_before_transcribe(self, q):
        q.put({"path": "/a.mkv", "type": "transcribe", "transcribe_or_translate": "transcribe", "force_language": None})
        q.put({"path": "asr-abc", "type": "asr"})
        first = q.get()
        assert first["type"] == "asr"

    def test_full_priority_order(self, q):
        q.put({"path": "/c.mkv", "type": "transcribe", "transcribe_or_translate": "transcribe", "force_language": None})
        q.put({"path": "asr-xyz", "type": "asr"})
        q.put({"path": "/d.mkv", "type": "detect_language"})
        assert q.get()["type"] == "detect_language"
        assert q.get()["type"] == "asr"
        assert q.get()["type"] == "transcribe"


class TestMarkDone:
    def test_mark_done_allows_requeue(self, q):
        task = _task("/a/b.mkv")
        q.put(task)
        popped = q.get()
        q.mark_done(popped)
        assert q.put(task) is True

    def test_without_mark_done_blocks_requeue(self, q):
        task = _task("/a/b.mkv")
        q.put(task)
        q.get()  # now _processing, but not marked done
        assert q.put(task) is False


class TestIsActive:
    def test_active_while_queued(self, q):
        q.put(_task("/a/b.mkv"))
        assert q.is_active("/a/b.mkv") is True

    def test_active_while_processing(self, q):
        q.put(_task("/a/b.mkv"))
        q.get()
        assert q.is_active("/a/b.mkv") is True

    def test_not_active_when_absent(self, q):
        assert q.is_active("/a/b.mkv") is False

    def test_not_active_after_done(self, q):
        task = _task("/a/b.mkv")
        q.put(task)
        popped = q.get()
        q.mark_done(popped)
        assert q.is_active("/a/b.mkv") is False


class TestStatusSnapshots:
    def test_get_queued_tasks_returns_list(self, q):
        q.put(_task("/a.mkv"))
        q.put(_task("/b.mkv"))
        assert len(q.get_queued_tasks()) == 2

    def test_get_processing_tasks_returns_list(self, q):
        q.put(_task("/a.mkv"))
        q.get()
        assert len(q.get_processing_tasks()) == 1


class TestConcurrency:
    def test_concurrent_puts_only_one_wins(self, q):
        successes = []
        lock = threading.Lock()

        def try_put():
            result = q.put(_task("/shared.mkv"))
            with lock:
                successes.append(result)

        threads = [threading.Thread(target=try_put) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sum(successes) == 1, "Exactly one put should succeed for the same path"


class TestForceQueueing:
    def test_force_enqueue_bypasses_skip_logic(self, monkeypatch):
        video = r"C:\media\movie.mkv"
        monkeypatch.setattr(subgen, "has_audio", lambda path: True)
        monkeypatch.setattr(subgen, "choose_transcribe_language", lambda *args, **kwargs: subgen.LanguageCode.ENGLISH)
        monkeypatch.setattr(subgen, "should_whisper_detect_audio_language", False)

        def fail_if_called(*args, **kwargs):
            raise AssertionError("describe_skip_reason should not run during force enqueue")

        monkeypatch.setattr(subgen, "describe_skip_reason", fail_if_called)

        put_calls = []

        def fake_put_with_result(item, block=True, timeout=None, force=False):
            put_calls.append(force)
            return {"queued": True, "status": "queued", "job_id": "test-job"}

        monkeypatch.setattr(subgen.task_queue, "put_with_result", fake_put_with_result)

        result = subgen.enqueue_media_job(
            video,
            "transcribe",
            force=True,
            audio_tracks=[{"language": subgen.LanguageCode.ENGLISH, "default": True}],
        )

        assert result["status"] == "queued"
        assert put_calls == [True]

    def test_force_helper_rejects_missing_path(self):
        result = subgen.queue_single_forced_file("")
        assert result["status"] == "error"

    def test_force_helper_requires_boolean_allow_invalid(self, tmp_path):
        media = tmp_path / "movie.mkv"
        media.touch()
        result = subgen.queue_single_forced_file(str(media), allow_invalid="false")
        assert result["status"] == "error"

    def test_allow_invalid_still_probes_unusual_extension(self, monkeypatch, tmp_path):
        media = tmp_path / "movie.media"
        media.touch()
        probes = []
        expected_tracks = [{"language": subgen.LanguageCode.ENGLISH, "default": True}]
        monkeypatch.setattr(subgen, "get_audio_tracks", lambda path: probes.append(path) or expected_tracks)
        monkeypatch.setattr(subgen, "should_whisper_detect_audio_language", False)
        monkeypatch.setattr(subgen.task_queue, "put_with_result", lambda *args, **kwargs: {"queued": True, "status": "queued"})

        result = subgen.queue_single_forced_file(
            str(media),
            force_language="eng",
            audio_tracks=expected_tracks,
            allow_invalid=True,
        )

        assert result["status"] == "queued"
        assert probes == [str(media)]


def test_legacy_scan_ignores_metadata_and_subtitle_files(monkeypatch, tmp_path):
    media = tmp_path / "movie.mkv"
    media.touch()
    (tmp_path / "movie.info.json").touch()
    (tmp_path / "movie.description").touch()
    (tmp_path / "movie.srt").touch()
    queued = []
    monkeypatch.setattr(subgen, "skip_startup_scan", False)
    monkeypatch.setattr(subgen, "monitor", False)
    monkeypatch.setattr(subgen, "find_skip_marker", lambda *args, **kwargs: None)
    monkeypatch.setattr(subgen, "gen_subtitles_queue", lambda path, *args, **kwargs: queued.append(path) or True)

    summary = subgen._legacy_startup_scan_existing(str(tmp_path))

    assert queued == [str(media)]
    assert summary["files_found"] == 4
    assert summary["unsupported_files"] == 3


def test_shared_planner_rejects_unsupported_extension_before_probe():
    probe_calls = []
    deps = StartupScanDependencies(
        task_queue=type("Queue", (), {"is_active": staticmethod(lambda path: False)})(),
        is_supported_media_file=lambda path: path.lower().endswith((".mp4", ".mkv")),
        get_audio_tracks=lambda path: probe_calls.append(path),
    )

    plan = prepare_media_queue_job(deps, "/media/movie.info.json", "transcribe", None)

    assert plan["status"] == "skip"
    assert plan["reason"] == "unsupported_media"
    assert probe_calls == []
    assert "task" not in plan


def test_shared_planner_does_not_queue_any_probe_exception():
    def fail_probe(path):
        raise RuntimeError("temporary share read failure")

    deps = StartupScanDependencies(
        task_queue=type("Queue", (), {"is_active": staticmethod(lambda path: False)})(),
        is_supported_media_file=lambda path: path.lower().endswith(".mkv"),
        get_audio_tracks=fail_probe,
    )

    plan = prepare_media_queue_job(deps, "/media/movie.mkv", "transcribe", None)

    assert plan["status"] == "probe_error"
    assert plan["reason"] == "media_probe_error"
    assert "task" not in plan


def test_cached_queue_rejects_unvalidated_retryable_row():
    queued = []
    deps = StartupScanDependencies(
        task_queue=type("Queue", (), {"is_active": staticmethod(lambda path: False), "put": lambda self, task: queued.append(task)})(),
        is_supported_media_file=lambda path: path.lower().endswith(".mkv"),
    )

    assert queue_cached_job(deps, "/media/movie.mkv", {"decision": "retryable_error", "audio_tracks_json": "[]"}) is False
    assert queued == []
