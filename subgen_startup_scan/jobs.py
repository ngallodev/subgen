"""Durable queue state for file-backed subtitle jobs.

The inventory database may be inside a long startup transaction while its
planner admits jobs. Keeping this small SQLite journal in a sibling file lets
admission commit independently, so a worker can never outlive an uncommitted
queue request.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import time
import uuid
from dataclasses import dataclass
from functools import wraps
from threading import RLock
from typing import Any

MAX_ATTEMPTS = 4
LEASE_SECONDS = 900
LEASE_RENEW_INTERVAL_SECONDS = LEASE_SECONDS // 3


def _serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


def _canonical_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def _sqlite_identity(value: int | None) -> str | None:
    """Encode filesystem IDs as text; some Windows IDs exceed SQLite's signed int range."""
    return None if value is None else f"x{value:x}"


def _native_identity(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and value.startswith("x"):
        try:
            return int(value[1:], 16)
        except ValueError:
            return None
    return int(value)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _as_plain_task(task: dict[str, Any]) -> dict[str, Any]:
    """Persist only safe file-job fields; never serialize request credentials."""
    tracks = []
    for track in task.get("audio_tracks") or []:
        row = dict(track)
        language = row.get("language")
        if language is not None and hasattr(language, "to_iso_639_2_t"):
            row["language"] = language.to_iso_639_2_t()
        tracks.append(row)
    force_language = task.get("force_language")
    if force_language is not None and hasattr(force_language, "to_iso_639_2_t"):
        force_language = force_language.to_iso_639_2_t()
    return {
        "path": str(task["path"]),
        "type": task.get("type", "transcribe"),
        "transcribe_or_translate": task.get("transcribe_or_translate", "transcribe"),
        "force_language": force_language or "",
        "audio_tracks": tracks,
        "mtime": int(task.get("mtime", 0) or 0),
        "force": bool(task.get("force", False)),
        "allow_unsupported_extension": task.get("allow_unsupported_extension") is True,
        "policy_signature": str(task.get("policy_signature") or ""),
    }


@dataclass(frozen=True)
class Admission:
    accepted: bool
    job_id: str | None
    status: str


class DurableJobStore:
    def __init__(self, database_path: str):
        self._lock = RLock()
        self.database_path = database_path
        parent = os.path.dirname(database_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.connection = sqlite3.connect(database_path, timeout=30, isolation_level=None, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS subtitle_jobs (
              job_id TEXT PRIMARY KEY,
              canonical_path TEXT NOT NULL,
              operation TEXT NOT NULL,
              language TEXT NOT NULL DEFAULT '',
              active_key TEXT,
              source_size INTEGER,
              source_mtime_ns INTEGER,
              source_device INTEGER,
              source_inode INTEGER,
              force INTEGER NOT NULL DEFAULT 0,
              priority INTEGER NOT NULL DEFAULT 2,
              task_json TEXT NOT NULL,
              status TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              next_retry_at REAL NOT NULL DEFAULT 0,
              lease_until REAL,
              claim_token TEXT,
              last_error TEXT,
              output_path TEXT,
              publish_temp TEXT,
              publish_sha256 TEXT,
              output_size INTEGER,
              output_mtime_ns INTEGER,
              output_language TEXT,
              output_format TEXT,
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_subtitle_jobs_active
              ON subtitle_jobs(active_key) WHERE active_key IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_subtitle_jobs_ready
              ON subtitle_jobs(status, next_retry_at, priority, created_at);
            CREATE INDEX IF NOT EXISTS idx_subtitle_jobs_success
              ON subtitle_jobs(canonical_path, operation, status, updated_at);
            CREATE TABLE IF NOT EXISTS subtitle_output_reservations (
              canonical_output TEXT PRIMARY KEY,
              job_id TEXT NOT NULL,
              claim_token TEXT NOT NULL,
              reserved_at REAL NOT NULL
            );
            """
        )
        existing = {row["name"] for row in self.connection.execute("PRAGMA table_info(subtitle_jobs)")}
        for column, definition in {
            "publish_temp": "TEXT",
            "publish_sha256": "TEXT",
            "output_language": "TEXT",
            "output_format": "TEXT",
            "source_device": "INTEGER",
            "source_inode": "INTEGER",
        }.items():
            if column not in existing:
                self.connection.execute(f"ALTER TABLE subtitle_jobs ADD COLUMN {column} {definition}")

    @_serialized
    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _active_key(path: str, operation: str, language: str) -> str:
        raw = "\0".join((_canonical_path(path), operation.lower(), language.lower()))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @_serialized
    def admit(self, task: dict[str, Any], force: bool = False) -> Admission:
        plain = _as_plain_task({**task, "force": force or task.get("force", False)})
        path = plain["path"]
        operation = plain["transcribe_or_translate"]
        language = str(plain.get("force_language") or "")
        active_key = self._active_key(path, operation, language)
        stat_result = None
        try:
            stat_result = os.stat(path)
        except OSError:
            pass
        now = time.time()
        job_id = uuid.uuid4().hex
        priority = 0 if plain["type"] == "detect_language" else (1 if plain["type"] == "asr" else 2)
        encoded = json.dumps(plain, sort_keys=True, separators=(",", ":"))
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            path_active = conn.execute(
                "SELECT job_id,status,operation,active_key FROM subtitle_jobs WHERE canonical_path=? AND status IN ('pending','running','publishing','retry_due') ORDER BY created_at LIMIT 1",
                (_canonical_path(path),),
            ).fetchone()
            if path_active and path_active["active_key"] != active_key:
                promotable = (
                    force
                    and path_active["status"] in {"pending", "retry_due"}
                    and path_active["operation"] == operation
                )
                if promotable:
                    conflict = conn.execute(
                        "SELECT job_id FROM subtitle_jobs WHERE active_key=? AND job_id<>?",
                        (active_key, path_active["job_id"]),
                    ).fetchone()
                    if not conflict:
                        conn.execute(
                            "UPDATE subtitle_jobs SET active_key=?,language=?,force=1,task_json=?,updated_at=? WHERE job_id=?",
                            (active_key, language, encoded, now, path_active["job_id"]),
                        )
                        conn.execute("COMMIT")
                        return Admission(False, path_active["job_id"], "promoted")
                conn.execute("COMMIT")
                status = "busy" if path_active["status"] in {"running", "publishing"} else "already_active"
                return Admission(False, path_active["job_id"], status)
            existing = conn.execute(
                "SELECT job_id, status, force FROM subtitle_jobs WHERE active_key=?",
                (active_key,),
            ).fetchone()
            if existing:
                if force and existing["force"]:
                    conn.execute("COMMIT")
                    return Admission(False, existing["job_id"], "already_active")
                if force and not existing["force"] and existing["status"] == "pending":
                    conn.execute(
                        "UPDATE subtitle_jobs SET force=1, task_json=?, updated_at=? WHERE job_id=?",
                        (encoded, now, existing["job_id"]),
                    )
                    conn.execute("COMMIT")
                    return Admission(False, existing["job_id"], "promoted")
                conn.execute("COMMIT")
                return Admission(False, existing["job_id"], "busy" if existing["status"] in {"running", "publishing"} else "already_active")
            if force:
                pending = conn.execute(
                    "SELECT job_id, task_json FROM subtitle_jobs WHERE canonical_path=? AND operation=? AND status='pending' ORDER BY created_at LIMIT 1",
                    (_canonical_path(path), operation),
                ).fetchone()
                if pending:
                    conn.execute(
                        "UPDATE subtitle_jobs SET active_key=?,language=?,force=1,task_json=?,updated_at=? WHERE job_id=?",
                        (active_key, language, encoded, now, pending["job_id"]),
                    )
                    conn.execute("COMMIT")
                    return Admission(False, pending["job_id"], "promoted")
            conn.execute(
                """INSERT INTO subtitle_jobs
                   (job_id, canonical_path, operation, language, active_key,
                    source_size, source_mtime_ns, source_device, source_inode, force, priority, task_json,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (
                    job_id,
                    _canonical_path(path),
                    operation,
                    language,
                    active_key,
                    stat_result.st_size if stat_result else None,
                    getattr(stat_result, "st_mtime_ns", None) if stat_result else None,
                    _sqlite_identity(getattr(stat_result, "st_dev", None)) if stat_result else None,
                    _sqlite_identity(getattr(stat_result, "st_ino", None)) if stat_result else None,
                    int(plain["force"]),
                    priority,
                    encoded,
                    now,
                    now,
                ),
            )
            conn.execute("COMMIT")
            return Admission(True, job_id, "queued")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    @_serialized
    def claim(self, job_id: str | None = None, *, exclude_paths: list[str] | None = None) -> dict[str, Any] | None:
        now = time.time()
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._recover_expired_in_transaction(now)
            if job_id:
                row = conn.execute(
                    "SELECT * FROM subtitle_jobs WHERE job_id=? AND status IN ('pending','retry_due') AND next_retry_at<=?",
                    (job_id, now),
                ).fetchone()
            else:
                excluded = {_canonical_path(path) for path in exclude_paths or []}
                blocked_clause = ""
                parameters: tuple[Any, ...] = (now,)
                if excluded:
                    blocked_clause = f"AND canonical_path NOT IN ({','.join('?' for _ in excluded)})"
                    parameters += tuple(excluded)
                row = conn.execute(
                    "SELECT * FROM subtitle_jobs WHERE status IN ('pending','retry_due') AND next_retry_at<=? "
                    f"{blocked_clause} ORDER BY priority, created_at LIMIT 1",
                    parameters,
                ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return None
            token = uuid.uuid4().hex
            lease_until = now + LEASE_SECONDS
            conn.execute(
                "UPDATE subtitle_jobs SET status='running', claim_token=?, lease_until=?, updated_at=? WHERE job_id=?",
                (token, lease_until, now, row["job_id"]),
            )
            conn.execute("COMMIT")
            task = json.loads(row["task_json"])
            task.update(
                _job_id=row["job_id"],
                _claim_token=token,
                _job_attempts=row["attempts"] + 1,
                _input_size=row["source_size"],
                _input_mtime_ns=row["source_mtime_ns"],
                _input_device=_native_identity(row["source_device"]),
                _input_inode=_native_identity(row["source_inode"]),
            )
            return task
        except Exception:
            conn.execute("ROLLBACK")
            raise

    @_serialized
    def renew(self, job_id: str, claim_token: str) -> bool:
        cursor = self.connection.execute(
                "UPDATE subtitle_jobs SET lease_until=?, updated_at=? WHERE job_id=? AND status IN ('running','publishing') AND claim_token=?",
            (time.time() + LEASE_SECONDS, time.time(), job_id, claim_token),
        )
        return cursor.rowcount == 1

    @_serialized
    def complete_detection(self, task: dict[str, Any], detected_task: dict[str, Any]) -> bool:
        """Record the next stage while retaining this job's admission identity and claim."""
        now = time.time()
        cursor = self.connection.execute(
            """UPDATE subtitle_jobs SET task_json=?, priority=2, updated_at=?
               WHERE job_id=? AND claim_token=? AND status='running' AND lease_until>=?""",
            (
                json.dumps(_as_plain_task({**task, **detected_task}), separators=(",", ":")),
                now,
                task["_job_id"],
                task["_claim_token"],
                now,
            ),
        )
        return cursor.rowcount == 1

    @_serialized
    def finish(
        self,
        task: dict[str, Any],
        success: bool,
        error: str | None = None,
        outcome: str | None = None,
    ) -> None:
        job_id = task.get("_job_id")
        token = task.get("_claim_token")
        if not job_id or not token:
            return
        now = time.time()
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT attempts, active_key FROM subtitle_jobs WHERE job_id=? AND claim_token=? AND status IN ('running','publishing')",
                (job_id, token),
            ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return
            if outcome == "superseded":
                conn.execute(
                    "UPDATE subtitle_jobs SET status='superseded',active_key=NULL,lease_until=NULL,claim_token=NULL,publish_temp=NULL,publish_sha256=NULL,last_error=?,updated_at=? WHERE job_id=?",
                    ((error or "input or policy changed")[:2000], now, job_id),
                )
                conn.execute("DELETE FROM subtitle_output_reservations WHERE job_id=?", (job_id,))
            elif success:
                conn.execute(
                    """UPDATE subtitle_jobs SET status='succeeded', active_key=NULL, attempts=attempts+1,
                         lease_until=NULL, claim_token=NULL,publish_temp=NULL,publish_sha256=NULL,last_error=NULL,updated_at=? WHERE job_id=?""",
                    (now, job_id),
                )
                conn.execute("DELETE FROM subtitle_output_reservations WHERE job_id=?", (job_id,))
            else:
                attempts = row["attempts"] + 1
                retry = attempts < MAX_ATTEMPTS
                delay = min(3600, 30 * (2 ** max(0, attempts - 1)))
                conn.execute(
                    """UPDATE subtitle_jobs SET status=?, active_key=?, attempts=?, next_retry_at=?,
                         lease_until=NULL, claim_token=NULL,publish_temp=NULL,publish_sha256=NULL,last_error=?,updated_at=? WHERE job_id=?""",
                    (
                        "retry_due" if retry else "failed",
                        row["active_key"] if retry else None,
                        attempts,
                        now + delay if retry else 0,
                        (error or "worker failed")[:2000],
                        now,
                        job_id,
                    ),
                )
                conn.execute("DELETE FROM subtitle_output_reservations WHERE job_id=?", (job_id,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    @_serialized
    def publish_file(
        self,
        task: dict[str, Any],
        temp_path: str,
        output_path: str,
        sha256: str,
        output_language: str = "",
        output_format: str = "",
    ) -> bool:
        job_id = task.get("_job_id")
        token = task.get("_claim_token")
        if not job_id or not token:
            os.replace(temp_path, output_path)
            return True
        canonical_output = _canonical_path(output_path)
        backup_path = f"{output_path}.subgen-backup-{job_id}"
        had_previous_output = os.path.isfile(output_path)
        conn = self.connection
        now = time.time()
        backup_ready = False
        output_replaced = False
        conn.execute("BEGIN IMMEDIATE")
        try:
            claim = conn.execute(
                "SELECT active_key FROM subtitle_jobs WHERE job_id=? AND claim_token=? AND status='publishing' AND lease_until>=?",
                (job_id, token, now),
            ).fetchone()
            reservation = conn.execute(
                "SELECT job_id,claim_token FROM subtitle_output_reservations WHERE canonical_output=?",
                (canonical_output,),
            ).fetchone()
            if not claim or not reservation or reservation["job_id"] != job_id or reservation["claim_token"] != token:
                conn.execute("ROLLBACK")
                return False
            if _sha256_file(temp_path) != sha256:
                conn.execute("ROLLBACK")
                return False
            if had_previous_output:
                shutil.copy2(output_path, backup_path)
                backup_ready = _sha256_file(backup_path) == _sha256_file(output_path)
                if not backup_ready:
                    raise RuntimeError(f"Could not verify subtitle backup before publication: {output_path}")
            os.replace(temp_path, output_path)
            output_replaced = True
            # Verify the file at its final path before recording the job as
            # successful. The caller also verifies after publication, but the
            # durable success state must never precede this check.
            if _sha256_file(output_path) != sha256:
                raise RuntimeError(f"Published subtitle digest changed during publication: {output_path}")
            stat = os.stat(output_path)
            conn.execute(
                """UPDATE subtitle_jobs SET status='succeeded', active_key=NULL, output_path=?,
                     output_size=?, output_mtime_ns=?, output_language=?, output_format=?,
                     publish_temp=NULL, lease_until=NULL,
                     claim_token=NULL, updated_at=? WHERE job_id=?""",
                (output_path, stat.st_size, getattr(stat, "st_mtime_ns", None), output_language, output_format, now, job_id),
            )
            conn.execute("DELETE FROM subtitle_output_reservations WHERE job_id=?", (job_id,))
            conn.execute("COMMIT")
            if had_previous_output:
                try:
                    os.unlink(backup_path)
                except OSError:
                    pass
            return True
        except Exception:
            conn.execute("ROLLBACK")
            if output_replaced and had_previous_output and backup_ready:
                os.replace(backup_path, output_path)
            elif output_replaced and not had_previous_output and os.path.exists(output_path):
                os.unlink(output_path)
            elif os.path.exists(backup_path):
                os.unlink(backup_path)
            raise

    @_serialized
    def set_publish_intent(
        self,
        task: dict[str, Any],
        output_path: str,
        temp_path: str,
        sha256: str,
        output_language: str = "",
        output_format: str = "",
    ) -> bool:
        job_id = task.get("_job_id")
        token = task.get("_claim_token")
        if not job_id or not token:
            return True
        canonical_output = _canonical_path(output_path)
        conn = self.connection
        now = time.time()
        conn.execute("BEGIN IMMEDIATE")
        try:
            claim = conn.execute(
                "SELECT 1 FROM subtitle_jobs WHERE job_id=? AND claim_token=? AND status='running' AND lease_until>=?",
                (job_id, token, now),
            ).fetchone()
            reservation = conn.execute(
                "SELECT job_id,claim_token FROM subtitle_output_reservations WHERE canonical_output=?",
                (canonical_output,),
            ).fetchone()
            if not claim or (reservation and (reservation["job_id"] != job_id or reservation["claim_token"] != token)):
                conn.execute("ROLLBACK")
                return False
            if not reservation:
                conn.execute(
                    "INSERT INTO subtitle_output_reservations(canonical_output,job_id,claim_token,reserved_at) VALUES(?,?,?,?)",
                    (canonical_output, job_id, token, now),
                )
            conn.execute(
                "UPDATE subtitle_jobs SET status='publishing',output_path=?,publish_temp=?,publish_sha256=?,output_language=?,output_format=?,updated_at=? WHERE job_id=?",
                (output_path, temp_path, sha256, output_language, output_format, now, job_id),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise

    @_serialized
    def release_output(self, task: dict[str, Any], output_path: str) -> None:
        if task.get("_job_id") and task.get("_claim_token"):
            self.connection.execute(
                "DELETE FROM subtitle_output_reservations WHERE canonical_output=? AND job_id=? AND claim_token=?",
                (_canonical_path(output_path), task["_job_id"], task["_claim_token"]),
            )

    @_serialized
    def reconcile_expired(self) -> int:
        now = time.time()
        return self._recover_expired_in_transaction(now)

    def _recover_expired_in_transaction(self, now: float) -> int:
        rows = self.connection.execute(
            "SELECT * FROM subtitle_jobs WHERE status IN ('running','publishing') AND lease_until < ?",
            (now,),
        ).fetchall()
        recovered = 0
        for row in rows:
            source_matches = False
            try:
                source_stat = os.stat(row["canonical_path"])
                source_matches = bool(
                    source_stat.st_size == row["source_size"]
                    and getattr(source_stat, "st_mtime_ns", None) == row["source_mtime_ns"]
                    and _sqlite_identity(getattr(source_stat, "st_dev", None)) == row["source_device"]
                    and _sqlite_identity(getattr(source_stat, "st_ino", None)) == row["source_inode"]
                )
            except OSError:
                source_matches = False
            output_matches = False
            if source_matches and row["status"] == "publishing" and row["output_path"] and row["publish_sha256"]:
                try:
                    output_matches = _sha256_file(row["output_path"]) == row["publish_sha256"]
                except OSError:
                    output_matches = False
            if output_matches:
                stat = os.stat(row["output_path"])
                self.connection.execute(
                    """UPDATE subtitle_jobs SET status='succeeded',active_key=NULL,output_size=?,
                         output_mtime_ns=?,publish_temp=NULL,publish_sha256=NULL,claim_token=NULL,lease_until=NULL,updated_at=?
                       WHERE job_id=?""",
                    (stat.st_size, getattr(stat, "st_mtime_ns", None), now, row["job_id"]),
                )
            else:
                if row["publish_temp"]:
                    try:
                        os.unlink(row["publish_temp"])
                    except OSError:
                        pass
                if not source_matches:
                    self.connection.execute(
                        """UPDATE subtitle_jobs SET status='superseded',active_key=NULL,claim_token=NULL,
                             lease_until=NULL,publish_temp=NULL,publish_sha256=NULL,
                             last_error='source changed or vanished during worker lease',updated_at=? WHERE job_id=?""",
                        (now, row["job_id"]),
                    )
                else:
                    attempts = row["attempts"] + 1
                    retry = attempts < MAX_ATTEMPTS
                    self.connection.execute(
                        """UPDATE subtitle_jobs SET status=?,active_key=?,attempts=?,next_retry_at=?,claim_token=NULL,
                             lease_until=NULL,publish_temp=NULL,publish_sha256=NULL,
                             last_error='worker lease expired',updated_at=? WHERE job_id=?""",
                        ("retry_due" if retry else "failed", row["active_key"] if retry else None,
                         attempts, now, now, row["job_id"]),
                    )
            self.connection.execute("DELETE FROM subtitle_output_reservations WHERE job_id=?", (row["job_id"],))
            recovered += 1
        return recovered

    @_serialized
    def is_active(self, path: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM subtitle_jobs WHERE canonical_path=? AND status IN ('pending','running','publishing','retry_due') LIMIT 1",
            (_canonical_path(path),),
        ).fetchone()
        return row is not None

    @_serialized
    def verified_completions(
        self,
        candidates: dict[str, dict[str, Any]],
        *,
        operation: str,
        policy_signature: str,
    ) -> set[str]:
        """Return candidate paths with still-valid, successfully published outputs.

        Publication recorded the output's size and nanosecond mtime only after
        verifying its digest.  Startup can therefore validate the completed
        output with cheap metadata checks and avoid probing the media again.
        The query is batched so a warm scan does not execute one SQLite query
        per title. Missing identity fields or metadata are cache misses.
        """
        canonical_candidates = {_canonical_path(path): path for path in candidates}
        verified: set[str] = set()
        paths = list(canonical_candidates)
        for offset in range(0, len(paths), 400):
            batch = paths[offset:offset + 400]
            placeholders = ",".join("?" for _ in batch)
            rows = self.connection.execute(
                f"""SELECT canonical_path, task_json, source_size, source_mtime_ns,
                           source_device, source_inode, output_path, output_size, output_mtime_ns
                      FROM subtitle_jobs
                     WHERE status='succeeded' AND operation=?
                       AND canonical_path IN ({placeholders})
                     ORDER BY updated_at DESC""",
                (operation, *batch),
            ).fetchall()
            for row in rows:
                original_path = canonical_candidates[row["canonical_path"]]
                media = candidates[original_path]
                if (
                    media.get("size") != row["source_size"]
                    or media.get("mtime_ns") != row["source_mtime_ns"]
                    or media.get("device") is None
                    or media.get("inode") is None
                    or _sqlite_identity(media["device"]) != row["source_device"]
                    or _sqlite_identity(media["inode"]) != row["source_inode"]
                    or row["output_size"] is None
                    or row["output_mtime_ns"] is None
                    or not row["output_path"]
                    or original_path in verified
                ):
                    continue
                try:
                    task = json.loads(row["task_json"])
                    if task.get("policy_signature") != policy_signature:
                        continue
                    output_stat = os.stat(row["output_path"])
                    if (
                    stat.S_ISREG(output_stat.st_mode)
                        and output_stat.st_size == row["output_size"]
                        and getattr(output_stat, "st_mtime_ns", None) == row["output_mtime_ns"]
                    ):
                        verified.add(original_path)
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    continue
        return verified

    @_serialized
    def queued_tasks(self) -> list[str]:
        return [
            row["canonical_path"]
            for row in self.connection.execute(
                "SELECT canonical_path FROM subtitle_jobs WHERE status IN ('pending','retry_due')"
            ).fetchall()
        ]

    @_serialized
    def processing_tasks(self) -> list[str]:
        return [
            row["canonical_path"]
            for row in self.connection.execute(
                "SELECT canonical_path FROM subtitle_jobs WHERE status IN ('running','publishing')"
            ).fetchall()
        ]
