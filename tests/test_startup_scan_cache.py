"""
Tests for the SQLite-backed startup scan cache.
"""
import os
import sqlite3
import sys
import json
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import subgen
from language_code import LanguageCode


def _set_db_path(monkeypatch, tmp_path):
    db_path = tmp_path / "scan.db"
    monkeypatch.setattr(subgen, "startup_scan_db_path", str(db_path))
    return db_path


def test_startup_scan_initialize_creates_schema(monkeypatch, tmp_path):
    db_path = _set_db_path(monkeypatch, tmp_path)

    subgen.startup_scan_initialize()

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert {"scan_meta", "media_files", "excluded_files", "subtitle_files", "scan_state"} <= tables


def test_startup_scan_db_migrates_legacy_schema(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE media_files (
              path TEXT PRIMARY KEY,
              size INTEGER NOT NULL,
              mtime INTEGER NOT NULL,
              has_audio INTEGER NOT NULL DEFAULT 0,
              audio_language TEXT,
              subtitle_state TEXT NOT NULL DEFAULT 'unknown',
              decision TEXT NOT NULL DEFAULT 'unknown',
              reason TEXT,
              last_seen INTEGER NOT NULL
            );
            CREATE TABLE excluded_files (
              path TEXT PRIMARY KEY,
              size INTEGER NOT NULL,
              mtime INTEGER NOT NULL,
              reason TEXT NOT NULL,
              details TEXT,
              last_seen INTEGER NOT NULL
            );
            CREATE TABLE subtitle_files (
              path TEXT PRIMARY KEY,
              size INTEGER NOT NULL,
              mtime INTEGER NOT NULL,
              media_path TEXT,
              subtitle_type TEXT NOT NULL DEFAULT 'external',
              language TEXT,
              last_seen INTEGER NOT NULL
            );
            """
        )

    from scan_index import StartupScanDB

    db = StartupScanDB(str(db_path))
    db.upsert_media(
        path="/movies/movie.mkv",
        source_path="/movies/movie.mkv",
        size=1,
        mtime=2,
        subtitle_signature="sig",
        has_audio=True,
        audio_language="eng",
        subtitle_state="unknown",
        decision="eligible",
        reason="cached",
        policy_signature="policy",
        task_type="transcribe",
        transcription_type="transcribe",
        force_language_code="eng",
        audio_tracks_json="[]",
        audio_langs_json="[\"eng\"]",
    )
    row = db.get_media("/movies/movie.mkv")
    db.close()

    assert row["source_path"] == "/movies/movie.mkv"
    assert row["subtitle_signature"] == "sig"
    assert row["policy_signature"] == "policy"
    assert row["task_type"] == "transcribe"


def test_startup_scan_collect_records_indexes_subtitles(monkeypatch, tmp_path):
    media = tmp_path / "movie.mkv"
    media.touch()
    subtitle = tmp_path / "movie.subgen.eng.srt"
    subtitle.touch()

    monkeypatch.setattr(subgen, "path_mapping", lambda value: value)

    media_records, subtitle_records = subgen._startup_scan_collect_records(str(tmp_path))

    assert len(media_records) == 1
    assert len(subtitle_records) == 1
    assert subtitle_records[0]["media_path"] == str(media)
    assert subtitle_records[0]["subtitle_type"] == "generated"


def test_startup_scan_collect_records_prefers_local_basename(monkeypatch, tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()

    first_media = first_dir / "movie.mkv"
    first_media.touch()
    first_subtitle = first_dir / "movie.eng.srt"
    first_subtitle.touch()

    second_media = second_dir / "movie.mkv"
    second_media.touch()
    second_subtitle = second_dir / "movie.eng.srt"
    second_subtitle.touch()

    monkeypatch.setattr(subgen, "path_mapping", lambda value: value)

    media_records, subtitle_records = subgen._startup_scan_collect_records(str(tmp_path))

    assert len(media_records) == 2
    assert len(subtitle_records) == 2
    assert {record["media_path"] for record in subtitle_records} == {
        str(first_media),
        str(second_media),
    }


def test_startup_scan_process_record_reuses_cached_eligible(monkeypatch, tmp_path):
    db_path = _set_db_path(monkeypatch, tmp_path)
    subgen.startup_scan_initialize()

    media_path = str(tmp_path / "movie.mkv")
    media_record = {"path": media_path, "size": 123, "mtime": 456}
    queued = []

    monkeypatch.setattr(subgen, "gen_subtitles_queue", lambda *args, **kwargs: queued.append(args))

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        subgen._startup_scan_record_media(
            conn,
            media_path,
            123,
            456,
            True,
            LanguageCode.ENGLISH,
            "none",
            "eligible",
            "cached eligible",
            1,
        )
        subgen._startup_scan_process_record(conn, media_record, [], LanguageCode.NONE)
        stored = conn.execute(
            "SELECT decision, reason FROM media_files WHERE path = ?",
            (media_path,),
        ).fetchone()

    assert len(queued) == 1
    assert stored["decision"] == "queued"
    assert stored["reason"] == "cached eligible"


def test_startup_scan_process_record_records_exclusion(monkeypatch, tmp_path):
    db_path = _set_db_path(monkeypatch, tmp_path)
    subgen.startup_scan_initialize()

    media_path = str(tmp_path / "movie.mkv")
    media_record = {"path": media_path, "size": 321, "mtime": 654}

    monkeypatch.setattr(
        subgen,
        "prepare_media_queue_job",
        lambda *args, **kwargs: {
            "status": "skip",
            "reason": "audio_language_skipped",
            "details": "Contains a skipped audio language.",
            "audio_tracks": [{"language": LanguageCode.ENGLISH}],
            "force_language": LanguageCode.ENGLISH,
        },
    )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        subgen._startup_scan_process_record(conn, media_record, [], LanguageCode.NONE)
        media_row = conn.execute(
            "SELECT decision, reason, subtitle_state FROM media_files WHERE path = ?",
            (media_path,),
        ).fetchone()
        excluded_row = conn.execute(
            "SELECT reason, details FROM excluded_files WHERE path = ?",
            (media_path,),
        ).fetchone()

    assert media_row["decision"] == "excluded"
    assert media_row["subtitle_state"] == "none"
    assert excluded_row["reason"] == "audio_language_skipped"
    assert excluded_row["details"] == "Contains a skipped audio language."


def test_media_probe_helpers_share_cached_probe(monkeypatch, tmp_path):
    media = tmp_path / "movie.mkv"
    media.touch()

    probe_calls = []

    def fake_probe(path, *args, **kwargs):
        probe_calls.append(path)
        return {
            "streams": [
                {
                    "codec_type": "audio",
                    "index": 1,
                    "codec_name": "aac",
                    "channels": 2,
                    "tags": {"language": "eng", "title": "Main"},
                    "disposition": {"default": 1, "forced": 0, "original": 0},
                },
                {
                    "codec_type": "subtitle",
                    "tags": {"language": "eng"},
                },
            ]
        }

    monkeypatch.setattr(subgen.ffmpeg, "probe", fake_probe)

    assert subgen.get_audio_tracks(str(media))
    assert subgen.get_audio_languages(str(media))
    assert subgen.get_subtitle_languages(str(media))
    assert subgen.has_internal_subtitle_in_language(str(media), LanguageCode.ENGLISH)
    assert subgen.has_audio(str(media))

    assert len(probe_calls) == 1


def test_startup_scan_total_benchmark_wraps_followup_work(monkeypatch, tmp_path):
    events = []

    class FakeLogger:
        def __init__(self, *args, **kwargs):
            pass

        def write(self, event, **fields):
            events.append(("write", event))

    class FakeDB:
        def __init__(self, *args, **kwargs):
            pass

        def set_meta(self, *args, **kwargs):
            pass

        def close(self):
            events.append(("db", "close"))

    class FakeInventory:
        def __init__(self):
            self.media_files = []
            self.subtitle_files = []

    @contextmanager
    def fake_benchmark_step(event, logger, **fields):
        events.append(("enter", event))
        try:
            yield
        finally:
            events.append(("exit", event))

    monkeypatch.setattr(subgen, "BenchmarkLogger", FakeLogger)
    monkeypatch.setattr(subgen, "benchmark_step", fake_benchmark_step)
    monkeypatch.setattr(subgen, "collect_startup_inventory", lambda folders, recursive=True, db=None: FakeInventory())
    monkeypatch.setattr(subgen, "StartupScanDB", FakeDB)
    monkeypatch.setattr(subgen, "get_startup_policy_signature", lambda: "sig")
    monkeypatch.setattr(subgen, "monitor", False)

    subgen.startup_scan_existing(str(tmp_path))

    assert events[0] == ("enter", "startup_scan.total")
    assert events[-1] == ("exit", "startup_scan.total")
    assert events.index(("write", "startup_scan.metadata_initialized")) < events.index(("exit", "startup_scan.total"))
    assert events.index(("exit", "startup_scan.monitor_setup")) < events.index(("exit", "startup_scan.total"))


def test_startup_scan_shortcuts_when_manifest_matches(monkeypatch, tmp_path):
    events = []

    class FakeLogger:
        def __init__(self, *args, **kwargs):
            pass

        def write(self, event, **fields):
            events.append((event, fields))

    class FakeDB:
        def __init__(self, *args, **kwargs):
            self.meta = {}

        def get_meta(self, key):
            return self.meta.get(key)

        def set_meta(self, key, value):
            self.meta[key] = value

        def count_rows(self, table_name):
            return 3 if table_name == "media_files" else 1

        def begin(self):
            events.append(("begin", {}))

        def commit(self):
            events.append(("commit", {}))

        def rollback(self):
            events.append(("rollback", {}))

        def close(self):
            events.append(("close", {}))

    class FakeInventory:
        def __init__(self):
            self.media_files = [{"path": str(tmp_path / "movie.mkv"), "mtime": 1, "size": 1}]
            self.subtitle_files = [{"path": str(tmp_path / "movie.srt"), "mtime": 1, "size": 1}]

    @contextmanager
    def fake_benchmark_step(event, logger, **fields):
        events.append(("enter", {"event": event, **fields}))
        try:
            yield
        finally:
            events.append(("exit", {"event": event, **fields}))

    monkeypatch.setattr(subgen, "BenchmarkLogger", FakeLogger)
    monkeypatch.setattr(subgen, "benchmark_step", fake_benchmark_step)
    monkeypatch.setattr(subgen, "collect_startup_inventory", lambda folders, recursive=True, db=None: FakeInventory())
    monkeypatch.setattr(subgen, "StartupScanDB", FakeDB)
    monkeypatch.setattr(subgen, "get_startup_policy_signature", lambda: "sig")
    monkeypatch.setattr(subgen, "monitor", False)
    monkeypatch.setattr(subgen, "startup_scan_force_rewalk", False)

    db = FakeDB()
    signature = subgen._startup_scan_inventory_signature(FakeInventory(), "sig")
    db.meta.update(
        {
            "flat_inventory_manifest": signature["manifest"],
            "flat_inventory_media_manifest": signature["media_manifest"],
            "flat_inventory_subtitle_manifest": signature["subtitle_manifest"],
            "flat_inventory_policy_signature": signature["policy_signature"],
        }
    )

    monkeypatch.setattr(subgen, "StartupScanDB", lambda *args, **kwargs: db)

    subgen.startup_scan_existing(str(tmp_path))

    assert any(event == "startup_scan.manifest_hit" for event, _ in events)
    assert not any(event == "startup_scan.classify_media" for event, _ in events)


def test_startup_scan_manifest_hit_backfills_subtitle_media_links(monkeypatch, tmp_path):
    events = []
    relinked = []

    class FakeLogger:
        def __init__(self, *args, **kwargs):
            pass

        def write(self, event, **fields):
            events.append((event, fields))

    class FakeDB:
        def __init__(self, *args, **kwargs):
            self.meta = {}

        def get_meta(self, key):
            return self.meta.get(key)

        def set_meta(self, key, value):
            self.meta[key] = value

        def count_rows(self, table_name):
            return 1

        def count_subtitles_missing_media_path(self):
            return 1

        def begin(self):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

        def upsert_subtitle(self, **kwargs):
            relinked.append(kwargs)

    class FakeInventory:
        def __init__(self):
            self.media_files = [{"path": str(tmp_path / "movie.mkv"), "mtime": 1, "size": 1}]
            self.subtitle_files = [
                {
                    "path": str(tmp_path / "movie.eng.srt"),
                    "mtime": 1,
                    "size": 1,
                    "media_path": str(tmp_path / "movie.mkv"),
                    "subtitle_type": "external",
                    "language": "eng",
                }
            ]
            self.child_dirs_by_dir = {}

    @contextmanager
    def fake_benchmark_step(event, logger, **fields):
        events.append((event, fields))
        yield

    monkeypatch.setattr(subgen, "BenchmarkLogger", FakeLogger)
    monkeypatch.setattr(subgen, "benchmark_step", fake_benchmark_step)
    monkeypatch.setattr(subgen, "collect_startup_inventory", lambda folders, recursive=True, db=None: FakeInventory())
    monkeypatch.setattr(subgen, "monitor", False)
    monkeypatch.setattr(subgen, "startup_scan_force_rewalk", False)
    monkeypatch.setattr(subgen, "get_startup_policy_signature", lambda: "sig")

    db = FakeDB()
    signature = subgen._startup_scan_inventory_signature(FakeInventory(), "sig")
    db.meta.update(
        {
            "flat_inventory_manifest": signature["manifest"],
            "flat_inventory_media_manifest": signature["media_manifest"],
            "flat_inventory_subtitle_manifest": signature["subtitle_manifest"],
            "flat_inventory_policy_signature": signature["policy_signature"],
        }
    )
    monkeypatch.setattr(subgen, "StartupScanDB", lambda *args, **kwargs: db)

    subgen.startup_scan_existing(str(tmp_path))

    assert relinked
    assert any(event == "startup_scan.subtitle_relink" for event, _ in events)


def test_inventory_signature_changes_when_media_mtime_changes(tmp_path):
    media = {
        "path": str(tmp_path / "movie.mkv"),
        "size": 100,
        "mtime": 10,
    }
    subtitle = {
        "path": str(tmp_path / "movie.eng.srt"),
        "size": 20,
        "mtime": 5,
    }

    first = subgen.StartupInventory(
        media_files=[dict(media)],
        subtitle_files=[dict(subtitle)],
        subtitle_files_by_dir={},
        child_dirs_by_dir={},
    )
    second = subgen.StartupInventory(
        media_files=[{**media, "mtime": 11}],
        subtitle_files=[dict(subtitle)],
        subtitle_files_by_dir={},
        child_dirs_by_dir={},
    )

    sig1 = subgen._startup_scan_inventory_signature(first, "policy")
    sig2 = subgen._startup_scan_inventory_signature(second, "policy")

    assert sig1["manifest"] != sig2["manifest"]
    assert sig1["media_manifest"] != sig2["media_manifest"]


def test_startup_scan_subtitle_index_preserves_media_links(monkeypatch, tmp_path):
    written = []

    class FakeLogger:
        def __init__(self, *args, **kwargs):
            pass

        def write(self, event, **fields):
            pass

    class FakeDB:
        def __init__(self, *args, **kwargs):
            self.meta = {}

        def count_rows(self, table_name):
            return 0

        def get_meta(self, key):
            return self.meta.get(key)

        def set_meta(self, key, value):
            self.meta[key] = value

        def begin(self):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

        def get_all_media(self):
            return []

        def get_all_excluded(self):
            return []

        def upsert_excluded(self, **kwargs):
            pass

        def upsert_media(self, **kwargs):
            pass

        def upsert_subtitle(self, **kwargs):
            written.append(kwargs)

    class FakeInventory:
        def __init__(self):
            self.media_files = []
            self.subtitle_files = [
                {
                    "path": str(tmp_path / "movie.subgen.spa.srt"),
                    "size": 1,
                    "mtime": 1,
                    "media_path": str(tmp_path / "movie.mkv"),
                    "subtitle_type": "generated",
                    "language": "spa",
                }
            ]
            self.subtitle_files_by_dir = {}
            self.child_dirs_by_dir = {}
            self.cache_hits = 0
            self.cache_misses = 0
            self.cache_reason_counts = {}
            self.directory_stat_count = 0
            self.directory_list_count = 0
            self.file_stat_count = 0
            self.quick_signature_checks = 0
            self.full_directory_scans = 0

    @contextmanager
    def fake_benchmark_step(event, logger, **fields):
        yield

    monkeypatch.setattr(subgen, "BenchmarkLogger", FakeLogger)
    monkeypatch.setattr(subgen, "benchmark_step", fake_benchmark_step)
    monkeypatch.setattr(subgen, "collect_startup_inventory", lambda folders, recursive=True, db=None: FakeInventory())
    monkeypatch.setattr(subgen, "StartupScanDB", FakeDB)
    monkeypatch.setattr(subgen, "monitor", False)
    monkeypatch.setattr(subgen, "startup_scan_force_rewalk", True)

    subgen.startup_scan_existing(str(tmp_path))

    assert written
    assert written[0]["media_path"] == str(tmp_path / "movie.mkv")
    assert written[0]["subtitle_type"] == "generated"
    assert written[0]["language"] == "spa"


def test_startup_scan_reuses_cached_directory_state(monkeypatch, tmp_path):
    db_path = _set_db_path(monkeypatch, tmp_path)
    db = subgen.StartupScanDB(str(db_path))

    root = tmp_path / "library"
    child = root / "season1"
    child.mkdir(parents=True)

    media = child / "episode.mkv"
    media.touch()
    subtitle = child / "episode.eng.srt"
    subtitle.touch()

    scandir_calls = []
    real_scandir = subgen.os.scandir

    def counting_scandir(path):
        scandir_calls.append(path)
        return real_scandir(path)

    monkeypatch.setattr(subgen.os, "scandir", counting_scandir)

    first_inventory = subgen.collect_startup_inventory([str(root)], recursive=True, db=db)
    first_call_count = len(scandir_calls)

    scandir_calls.clear()
    second_inventory = subgen.collect_startup_inventory([str(root)], recursive=True, db=db)

    db.close()

    assert first_call_count >= 2
    assert len(scandir_calls) == 0
    assert len(first_inventory.media_files) == 1
    assert len(second_inventory.media_files) == 1


def test_startup_scan_reuses_cached_directory_state_when_mtime_changes(monkeypatch, tmp_path):
    db_path = _set_db_path(monkeypatch, tmp_path)
    db = subgen.StartupScanDB(str(db_path))

    root = tmp_path / "library"
    root.mkdir()
    media = root / "episode.mkv"
    media.touch()
    subtitle = root / "episode.eng.srt"
    subtitle.touch()

    first_inventory = subgen.collect_startup_inventory([str(root)], recursive=True, db=db)
    root_stat = root.stat()
    bumped_mtime = int(root_stat.st_mtime) + 5
    os.utime(root, (bumped_mtime, bumped_mtime))
    second_inventory = subgen.collect_startup_inventory([str(root)], recursive=True, db=db)

    db.close()

    assert len(first_inventory.media_files) == 1
    assert len(second_inventory.media_files) == 1
    assert second_inventory.cache_hits == 1
    assert second_inventory.cache_reason_counts["entries_signature_match"] == 1
    assert second_inventory.quick_signature_checks == 1
    assert second_inventory.full_directory_scans == 0


def test_startup_scan_ignores_irrelevant_files_for_directory_signature(monkeypatch, tmp_path):
    db_path = _set_db_path(monkeypatch, tmp_path)
    db = subgen.StartupScanDB(str(db_path))

    root = tmp_path / "library"
    root.mkdir()
    media = root / "episode.mkv"
    media.touch()
    subtitle = root / "episode.eng.srt"
    subtitle.touch()

    first_inventory = subgen.collect_startup_inventory([str(root)], recursive=True, db=db)

    junk = root / "notes.txt"
    junk.write_text("first version", encoding="utf-8")
    root_stat = root.stat()
    bumped_mtime = int(root_stat.st_mtime) + 5
    os.utime(root, (bumped_mtime, bumped_mtime))

    second_inventory = subgen.collect_startup_inventory([str(root)], recursive=True, db=db)

    junk.write_text("second version", encoding="utf-8")
    root_stat = root.stat()
    bumped_mtime = int(root_stat.st_mtime) + 5
    os.utime(root, (bumped_mtime, bumped_mtime))

    third_inventory = subgen.collect_startup_inventory([str(root)], recursive=True, db=db)

    db.close()

    assert len(first_inventory.media_files) == 1
    assert len(second_inventory.media_files) == 1
    assert len(third_inventory.media_files) == 1
    assert second_inventory.cache_hits == 1
    assert third_inventory.cache_hits == 1
    assert second_inventory.cache_reason_counts["entries_signature_match"] == 1
    assert third_inventory.cache_reason_counts["entries_signature_match"] == 1
    assert second_inventory.quick_signature_checks == 1
    assert third_inventory.quick_signature_checks == 1
    assert second_inventory.full_directory_scans == 0
    assert third_inventory.full_directory_scans == 0


def test_collect_startup_inventory_links_subtitles_to_media(monkeypatch, tmp_path):
    db_path = _set_db_path(monkeypatch, tmp_path)
    db = subgen.StartupScanDB(str(db_path))

    root = tmp_path / "library"
    child = root / "season1"
    child.mkdir(parents=True)

    media = child / "episode.mkv"
    media.touch()
    subtitle = child / "episode.subgen.spa.srt"
    subtitle.touch()

    first_inventory = subgen.collect_startup_inventory([str(root)], recursive=True, db=db)
    second_inventory = subgen.collect_startup_inventory([str(root)], recursive=True, db=db)

    db.close()

    assert first_inventory.subtitle_files[0]["media_path"] == str(media)
    assert first_inventory.subtitle_files[0]["subtitle_type"] == "generated"
    assert first_inventory.subtitle_files[0]["language"] == "spa"
    assert second_inventory.subtitle_files[0]["media_path"] == str(media)


def test_refresh_processed_file_persists_audio_languages(monkeypatch, tmp_path):
    db_path = _set_db_path(monkeypatch, tmp_path)
    subgen.startup_scan_initialize()

    media = tmp_path / "movie.mkv"
    media.touch()
    subtitle = tmp_path / "movie.subgen.spa.srt"
    subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nhola\n", encoding="utf-8")

    monkeypatch.setattr(subgen, "path_mapping", lambda value: value)
    monkeypatch.setattr(subgen, "name_subtitle", lambda path, language: str(subtitle))

    audio_tracks = [
        {
            "index": 1,
            "codec": "aac",
            "channels": 2,
            "language": LanguageCode.SPANISH,
            "title": "Main",
            "default": True,
            "forced": False,
            "original": False,
            "commentary": False,
        }
    ]

    subgen._startup_scan_refresh_processed_file(
        str(media),
        "transcribe",
        LanguageCode.SPANISH,
        audio_tracks=audio_tracks,
    )

    db = subgen.StartupScanDB(str(db_path))
    media_row = db.get_media(str(media))
    subtitle_rows = db.get_all_subtitles()
    db.close()

    assert media_row["audio_language"] == "spa"
    assert media_row["force_language_code"] == "spa"
    assert json.loads(media_row["audio_langs_json"]) == ["spa"]
    assert json.loads(media_row["audio_tracks_json"])[0]["language"] == "spa"
    assert subtitle_rows[0]["media_path"] == str(media)
    assert subtitle_rows[0]["language"] == "spa"


def test_startup_scan_uses_parallel_planning_executor(monkeypatch, tmp_path):
    events = []

    class FakeLogger:
        def __init__(self, *args, **kwargs):
            pass

        def write(self, event, **fields):
            events.append((event, fields))

    class FakeDB:
        def __init__(self, *args, **kwargs):
            self.meta = {}

        def count_rows(self, table_name):
            return 0

        def get_meta(self, key):
            return self.meta.get(key)

        def set_meta(self, key, value):
            self.meta[key] = value

        def begin(self):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

        def get_all_media(self):
            return []

        def get_all_excluded(self):
            return []

        def upsert_excluded(self, **kwargs):
            pass

        def upsert_media(self, **kwargs):
            pass

        def upsert_subtitle(self, **kwargs):
            pass

    class FakeInventory:
        def __init__(self):
            self.media_files = [
                {"path": str(tmp_path / "one.mkv"), "size": 1, "mtime": 1},
                {"path": str(tmp_path / "two.mkv"), "size": 1, "mtime": 1},
            ]
            self.subtitle_files = []
            self.subtitle_files_by_dir = {}
            self.child_dirs_by_dir = {}

    class FakeExecutor:
        instances = []

        def __init__(self, max_workers):
            self.max_workers = max_workers
            self.map_calls = 0
            FakeExecutor.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def map(self, func, iterable):
            self.map_calls += 1
            return [func(item) for item in iterable]

    @contextmanager
    def fake_benchmark_step(event, logger, **fields):
        events.append((event, fields))
        yield

    def fake_plan_media_record(media, queue_path, cached_row, inventory, subtitle_rows, current_sidecar_state):
        return {
            "media": media,
            "queue_path": queue_path,
            "cached_row": cached_row,
            "current_sidecar_state": current_sidecar_state,
            "subtitle_signature": "sig",
            "matching_subtitles": [],
            "plan": {"status": "active", "reason": "cached", "details": "cached"},
        }

    monkeypatch.setattr(subgen, "BenchmarkLogger", FakeLogger)
    monkeypatch.setattr(subgen, "benchmark_step", fake_benchmark_step)
    monkeypatch.setattr(subgen, "collect_startup_inventory", lambda folders, recursive=True, db=None: FakeInventory())
    monkeypatch.setattr(subgen, "StartupScanDB", FakeDB)
    monkeypatch.setattr(subgen, "get_startup_policy_signature", lambda: "sig")
    monkeypatch.setattr(subgen, "monitor", False)
    monkeypatch.setattr(subgen, "startup_scan_force_rewalk", False)
    monkeypatch.setattr(subgen, "startup_scan_classify_workers", 4)
    monkeypatch.setattr(subgen, "ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(subgen, "_startup_scan_plan_media_record", fake_plan_media_record)

    subgen.startup_scan_existing(str(tmp_path))

    assert FakeExecutor.instances
    assert FakeExecutor.instances[0].max_workers == 4
    assert FakeExecutor.instances[0].map_calls == 1
    assert any(event == "startup_scan.classify_media.preflight" for event, _ in events)
    assert any(event == "startup_scan.classify_media.parallel_plan" for event, _ in events)
    assert any(event == "startup_scan.classify_media.apply" for event, _ in events)


def test_benchmark_logger_respects_env_toggle(tmp_path):
    from scan_index import BenchmarkLogger

    log_path = tmp_path / "benchmarks.jsonl"
    disabled = BenchmarkLogger(log_path=str(log_path), enabled=False)
    disabled.write("startup_scan.total", duration_ms=1.23)
    assert not log_path.exists()

    enabled = BenchmarkLogger(log_path=str(log_path), enabled=True)
    enabled.write("startup_scan.total", duration_ms=4.56, media_count=2)
    assert log_path.exists()

    line = log_path.read_text().strip()
    payload = json.loads(line)
    assert payload["event"] == "startup_scan.total"
    assert payload["media_count"] == 2


def test_benchmark_logger_sanitizes_malformed_jsonl(tmp_path):
    from scan_index import BenchmarkLogger

    log_path = tmp_path / "benchmarks.jsonl"
    log_path.write_text(
        '{"event":"startup_scan.total","ts":1}\nnot-json\n{"event":"startup_scan.summary","ts":2}\n',
        encoding="utf-8",
    )

    logger = BenchmarkLogger(log_path=str(log_path), enabled=True)
    logger.write("startup_scan.monitor_setup", duration_ms=1.23)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    payloads = [json.loads(line) for line in lines]

    assert len(payloads) == 3
    assert payloads[0]["event"] == "startup_scan.total"
    assert payloads[1]["event"] == "startup_scan.summary"
    assert payloads[2]["event"] == "startup_scan.monitor_setup"
