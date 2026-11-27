from types import FunctionType, SimpleNamespace

import pytest

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


def test_durable_language_detection_survives_restart_in_same_job(tmp_path):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"video")
    db = str(tmp_path / "jobs.sqlite")
    task = dict(_task(source), type="detect_language", transcribe_or_translate="translate")
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
