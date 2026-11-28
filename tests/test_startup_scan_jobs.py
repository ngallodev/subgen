from types import FunctionType, SimpleNamespace

import pytest

import hashlib

import subgen_startup_scan.jobs as jobs_module
from subgen_startup_scan.jobs import DurableJobStore


def _task(path):
    return {
        "path": str(path),
        "type": "transcribe",
        "transcribe_or_translate": "transcribe",
        "force_language": "",
        "audio_tracks": [],
        "mtime": int(path.stat().st_mtime),
        "force": False,
        "policy_signature": "policy-v1",
    }


def test_durable_jobs_deduplicate_force_and_retry(tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    store = DurableJobStore(str(tmp_path / "jobs.sqlite"))
    try:
        task = _task(source)
        first = store.admit(task)
        duplicate = store.admit(task, force=True)

        assert first.accepted is True
        assert duplicate.accepted is False
        assert duplicate.job_id == first.job_id
        assert duplicate.status == "promoted"

        claimed = store.claim(first.job_id)
        assert claimed is not None
        assert claimed["_job_id"] == first.job_id
        assert claimed["_claim_token"]
        store.finish(claimed, success=False, error="temporary write failure")

        row = store.connection.execute(
            "SELECT status, attempts, active_key FROM subtitle_jobs WHERE job_id=?",
            (first.job_id,),
        ).fetchone()
        assert row["status"] == "retry_due"
        assert row["attempts"] == 1
        assert row["active_key"]
    finally:
        store.close()


def test_durable_jobs_allow_new_request_after_success(tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    store = DurableJobStore(str(tmp_path / "jobs.sqlite"))
    try:
        first = store.admit(_task(source))
        claimed = store.claim(first.job_id)
        store.finish(claimed, success=True)

        second = store.admit(_task(source), force=True)
        assert second.accepted is True
        assert second.job_id != first.job_id
    finally:
        store.close()


def test_durable_job_preserves_validated_unusual_extension_override(tmp_path):
    source = tmp_path / "movie.media"
    source.write_bytes(b"video")
    store = DurableJobStore(str(tmp_path / "jobs.sqlite"))
    try:
        task = dict(_task(source), force=True, allow_unsupported_extension=True)
        admitted = store.admit(task, force=True)
        claimed = store.claim(admitted.job_id)

        assert admitted.accepted is True
        assert claimed["allow_unsupported_extension"] is True
    finally:
        store.close()


def test_durable_language_detection_survives_restart_in_same_job(tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    db = str(tmp_path / "jobs.sqlite")
    task = dict(_task(source), type="detect_language", transcribe_or_translate="translate", allow_unsupported_extension=True)
    store = DurableJobStore(db)
    try:
        admitted = store.admit(task, force=True)
        claimed = store.claim(admitted.job_id)
        detected = {"type": "transcribe", "force_language": "jpn"}
        assert not store.complete_detection(dict(claimed, _claim_token="stale"), detected)
        assert store.complete_detection(claimed, detected)
        assert store.admit(task, force=True).job_id == admitted.job_id
        store.connection.execute("UPDATE subtitle_jobs SET lease_until=0 WHERE job_id=?", (admitted.job_id,))
    finally:
        store.close()

    recovered = DurableJobStore(db)
    try:
        resumed = recovered.claim()
        assert resumed["_job_id"] == admitted.job_id
        assert resumed["_claim_token"] != claimed["_claim_token"]
        assert resumed["type"] == "transcribe"
        assert resumed["transcribe_or_translate"] == "translate"
        assert resumed["force_language"] == "jpn"
        assert resumed["policy_signature"] == "policy-v1"
        assert resumed["force"] is True
        assert resumed["allow_unsupported_extension"] is True
    finally:
        recovered.close()


def test_worker_transcribes_under_the_detected_jobs_claim(tmp_path, monkeypatch):
    import subgen

    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    monkeypatch.setenv("STARTUP_SCAN_BACKEND", "persistent")
    monkeypatch.setattr(subgen, "startup_scan_db_path", str(tmp_path / "scan.sqlite"))
    work = subgen.DeduplicatedQueue()
    try:
        task = dict(_task(source), type="detect_language")
        admitted = work.put_with_result(task, force=True)
        stages = []

        def detect(path, *, original_task_data):
            stages.append(("detect", original_task_data["_job_id"], original_task_data["_claim_token"]))
            return {"type": "transcribe", "force_language": subgen.LanguageCode.from_string("ja")}

        def transcribe(path, operation, language, *, audio_tracks, job_context):
            stages.append(("transcribe", job_context["_job_id"], job_context["_claim_token"]))
            assert language.to_iso_639_1() == "ja"
            assert job_context["force"] is True
            return {"output_path": str(source.with_suffix(".srt"))}

        class EndWorker(Exception):
            pass

        def stop_after_cleanup():
            raise EndWorker

        # The app starts its default workers at import. Give this worker its own queue.
        worker = FunctionType(
            subgen.transcription_worker.__code__,
            {
                **vars(subgen),
                "task_queue": work,
                "detect_language_task": detect,
                "gen_subtitles": transcribe,
                "get_startup_policy_signature": lambda: "policy-v1",
                "_get_startup_scan_backend": lambda: SimpleNamespace(refresh_processed_file=lambda *args, **kwargs: None),
                "delete_model": stop_after_cleanup,
            },
        )
        with pytest.raises(EndWorker):
            worker()
        assert len(stages) == 2
        assert stages[0][0] == "detect"
        assert stages[1][0] == "transcribe"
        assert stages[0][1:] == stages[1][1:]
        assert stages[1][1] == admitted["job_id"]
        assert work.unfinished_tasks == 0
        assert work._durable.connection.execute("SELECT COUNT(*) FROM subtitle_jobs").fetchone()[0] == 1
    finally:
        work._durable.close()


def test_worker_retries_a_transient_source_stat_error(tmp_path, monkeypatch):
    import subgen

    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    monkeypatch.setenv("STARTUP_SCAN_BACKEND", "persistent")
    monkeypatch.setattr(subgen, "startup_scan_db_path", str(tmp_path / "scan.sqlite"))
    work = subgen.DeduplicatedQueue()
    try:
        task = dict(_task(source), force=True)
        admitted = work.put_with_result(task, force=True)
        original_stat = subgen.os.stat

        def transient_stat_error(path, *args, **kwargs):
            if str(path) == str(source):
                raise PermissionError("temporary network share permission failure")
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(subgen.os, "stat", transient_stat_error)

        class EndWorker(Exception):
            pass

        worker = FunctionType(
            subgen.transcription_worker.__code__,
            {
                **vars(subgen),
                "task_queue": work,
                "get_startup_policy_signature": lambda: "policy-v1",
                "delete_model": lambda: (_ for _ in ()).throw(EndWorker()),
            },
        )
        with pytest.raises(EndWorker):
            worker()

        row = work._durable.connection.execute(
            "SELECT status, attempts, active_key FROM subtitle_jobs WHERE job_id=?",
            (admitted["job_id"],),
        ).fetchone()
        assert row["status"] == "retry_due"
        assert row["attempts"] == 1
        assert row["active_key"]
    finally:
        work._durable.close()


def test_durable_claim_skips_paths_reserved_by_memory_queue(tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    other = tmp_path / "other.mkv"
    other.write_bytes(b"video")
    store = DurableJobStore(str(tmp_path / "jobs.sqlite"))
    try:
        blocked = store.admit(_task(source), force=True)
        available = store.admit(_task(other), force=True)

        claimed = store.claim(exclude_paths=[str(source)])
        assert claimed["path"] == str(other)
        store.finish(claimed, success=True)
        row = store.connection.execute(
            "SELECT status FROM subtitle_jobs WHERE job_id=?", (blocked.job_id,)
        ).fetchone()
        assert row["status"] == "pending"
    finally:
        store.close()


def test_durable_admission_does_not_persist_behind_memory_asr_reservation(tmp_path, monkeypatch):
    import subgen

    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    monkeypatch.setenv("STARTUP_SCAN_BACKEND", "persistent")
    monkeypatch.setattr(subgen, "startup_scan_db_path", str(tmp_path / "scan.sqlite"))
    work = subgen.DeduplicatedQueue()
    try:
        assert work.put({
            "path": str(source),
            "type": "asr",
            "video_file": str(source),
            "audio_content": b"audio",
        })
        restored = work._durable.admit(_task(source), force=True)
        result = work.put_with_result(dict(_task(source), force=True), force=True)

        assert result["status"] == "already_active"
        assert work._durable.connection.execute(
            "SELECT COUNT(*) FROM subtitle_jobs"
        ).fetchone()[0] == 1
        active = work.get()
        with pytest.raises(subgen.queue.Empty):
            work.get(timeout=0.01)
        assert work._durable.connection.execute(
            "SELECT status FROM subtitle_jobs WHERE job_id=?", (restored.job_id,)
        ).fetchone()["status"] == "pending"
        work.task_done()
        work.mark_done(active, success=True)
    finally:
        work._durable.close()


def test_failed_durable_publication_restores_previous_subtitle(tmp_path, monkeypatch):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    output = tmp_path / "movie.eng.srt"
    output.write_bytes(b"previous valid subtitle")
    temp = tmp_path / "movie.eng.srt.tmp"
    temp.write_bytes(b"new subtitle")
    store = DurableJobStore(str(tmp_path / "jobs.sqlite"))
    try:
        admitted = store.admit(dict(_task(source), force=True), force=True)
        claimed = store.claim(admitted.job_id)
        digest = hashlib.sha256(temp.read_bytes()).hexdigest()
        assert store.set_publish_intent(claimed, str(output), str(temp), digest)
        real_digest = jobs_module._sha256_file

        def corrupted_final_digest(path):
            if str(path) == str(output) and output.read_bytes() == b"new subtitle":
                return "corrupt"
            return real_digest(path)

        monkeypatch.setattr(jobs_module, "_sha256_file", corrupted_final_digest)
        with pytest.raises(RuntimeError, match="digest changed"):
            store.publish_file(claimed, str(temp), str(output), digest)

        assert output.read_bytes() == b"previous valid subtitle"
        assert not list(tmp_path.glob("*.subgen-backup-*"))
    finally:
        store.close()


def test_partial_subtitle_backup_failure_does_not_touch_previous_output(tmp_path, monkeypatch):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    output = tmp_path / "movie.eng.srt"
    output.write_bytes(b"previous valid subtitle")
    temp = tmp_path / "movie.eng.srt.tmp"
    temp.write_bytes(b"new subtitle")
    store = DurableJobStore(str(tmp_path / "jobs.sqlite"))
    try:
        admitted = store.admit(dict(_task(source), force=True), force=True)
        claimed = store.claim(admitted.job_id)
        digest = hashlib.sha256(temp.read_bytes()).hexdigest()
        assert store.set_publish_intent(claimed, str(output), str(temp), digest)

        def partial_copy(src, dst):
            with open(dst, "wb") as backup:
                backup.write(b"partial")
            raise OSError("simulated network interruption")

        monkeypatch.setattr(jobs_module.shutil, "copy2", partial_copy)
        with pytest.raises(OSError, match="network interruption"):
            store.publish_file(claimed, str(temp), str(output), digest)

        assert output.read_bytes() == b"previous valid subtitle"
        assert not list(tmp_path.glob("*.subgen-backup-*"))
    finally:
        store.close()
