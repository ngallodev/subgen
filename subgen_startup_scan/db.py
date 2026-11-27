import os
import sqlite3
import time

from .schema import ensure_startup_scan_schema


def _sqlite_identity(value: int | None) -> str | None:
    return None if value is None else f"x{value:x}"


def _native_identity(value):
    if value is None:
        return None
    if isinstance(value, str) and value.startswith("x"):
        try:
            return int(value[1:], 16)
        except ValueError:
            return None
    return int(value)


def _decode_media_identity(row: dict) -> dict:
    row["device"] = _native_identity(row.get("device"))
    row["inode"] = _native_identity(row.get("inode"))
    return row


class StartupScanDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 120000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.ensure_schema()

    def close(self):
        self.conn.close()

    def begin(self):
        self.conn.execute("BEGIN IMMEDIATE")

    def commit(self):
        self.conn.execute("COMMIT")

    def rollback(self):
        self.conn.execute("ROLLBACK")

    def ensure_schema(self):
        ensure_startup_scan_schema(self.conn)

    def set_meta(self, key: str, value: str):
        self.conn.execute(
            """
            INSERT INTO scan_meta (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )

    def get_meta(self, key: str):
        row = self.conn.execute("SELECT value FROM scan_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def get_media(self, path: str):
        row = self.conn.execute("SELECT * FROM media_files WHERE path = ?", (path,)).fetchone()
        return _decode_media_identity(dict(row)) if row else None

    def get_all_media(self):
        return [_decode_media_identity(dict(row)) for row in self.conn.execute("SELECT * FROM media_files").fetchall()]

    def get_all_excluded(self):
        return [dict(row) for row in self.conn.execute("SELECT * FROM excluded_files").fetchall()]

    def get_all_subtitles(self):
        return [dict(row) for row in self.conn.execute("SELECT * FROM subtitle_files").fetchall()]

    def get_directory_state(self, path: str):
        row = self.conn.execute("SELECT * FROM directory_state WHERE path = ?", (path,)).fetchone()
        return dict(row) if row else None

    def get_all_directory_state(self):
        return [dict(row) for row in self.conn.execute("SELECT * FROM directory_state").fetchall()]

    def count_rows(self, table_name: str) -> int:
        row = self.conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
        return int(row["count"]) if row else 0

    def count_subtitles_missing_media_path(self) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM subtitle_files
            WHERE media_path IS NULL OR TRIM(media_path) = ''
            """
        ).fetchone()
        return int(row["count"]) if row else 0

    def delete_excluded(self, path: str) -> None:
        self.conn.execute("DELETE FROM excluded_files WHERE path=?", (path,))

    def prune_scanned_roots(self, root_results, inventory) -> dict[str, int]:
        roots = [
            os.path.abspath(result["path"])
            for result in root_results or []
            if result.get("status") == "success"
        ]
        removed = {"media": 0, "excluded": 0, "subtitles": 0}
        if not roots:
            return removed

        def in_scope(path: str | None) -> bool:
            if not path:
                return False
            absolute = os.path.abspath(path)
            for root in roots:
                try:
                    if os.path.commonpath([root, absolute]) == root:
                        return True
                except ValueError:
                    continue
            return False

        live_media = {os.path.normcase(os.path.abspath(row["path"])) for row in inventory.media_files}
        live_subtitles = {os.path.normcase(os.path.abspath(row["path"])) for row in inventory.subtitle_files}
        for table, result_key, live_paths in (
            ("media_files", "media", live_media),
            ("excluded_files", "excluded", live_media),
            ("subtitle_files", "subtitles", live_subtitles),
        ):
            for row in self.conn.execute(f"SELECT path,source_path FROM {table}").fetchall():
                identities = [row["path"], row["source_path"]]
                if not any(in_scope(path) for path in identities):
                    continue
                if any(os.path.normcase(os.path.abspath(path)) in live_paths for path in identities if path):
                    continue
                self.conn.execute(f"DELETE FROM {table} WHERE path=?", (row["path"],))
                removed[result_key] += 1
        return removed

    def upsert_media(
        self,
        *,
        path: str,
        source_path: str,
        size: int,
        mtime: int,
        mtime_ns: int | None = None,
        device: int | None = None,
        inode: int | None = None,
        subtitle_signature: str,
        has_audio: bool,
        audio_language: str,
        subtitle_state: str,
        decision: str,
        reason: str,
        policy_signature: str,
        task_type: str | None = None,
        transcription_type: str | None = None,
        force_language_code: str | None = None,
        audio_tracks_json: str | None = None,
        audio_langs_json: str | None = None,
    ):
        if mtime_ns is None or device is None or inode is None:
            try:
                source_stat = os.stat(source_path)
            except OSError:
                source_stat = None
            if source_stat is not None:
                mtime_ns = mtime_ns if mtime_ns is not None else getattr(source_stat, "st_mtime_ns", None)
                device = device if device is not None else getattr(source_stat, "st_dev", None)
                inode = inode if inode is not None else getattr(source_stat, "st_ino", None)
        self.conn.execute(
            """
            INSERT INTO media_files (
              path, source_path, size, mtime, mtime_ns, device, inode, subtitle_signature, has_audio,
              audio_language, subtitle_state, decision, reason, policy_signature,
              task_type, transcription_type, force_language_code, audio_tracks_json,
              audio_langs_json, last_seen
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
              source_path=excluded.source_path,
              size=excluded.size,
              mtime=excluded.mtime,
              mtime_ns=excluded.mtime_ns,
              device=excluded.device,
              inode=excluded.inode,
              subtitle_signature=excluded.subtitle_signature,
              has_audio=excluded.has_audio,
              audio_language=excluded.audio_language,
              subtitle_state=excluded.subtitle_state,
              decision=excluded.decision,
              reason=excluded.reason,
              policy_signature=excluded.policy_signature,
              task_type=excluded.task_type,
              transcription_type=excluded.transcription_type,
              force_language_code=excluded.force_language_code,
              audio_tracks_json=excluded.audio_tracks_json,
              audio_langs_json=excluded.audio_langs_json,
              last_seen=excluded.last_seen
            """,
            (
                path,
                source_path,
                size,
                mtime,
                mtime_ns,
                _sqlite_identity(device),
                _sqlite_identity(inode),
                subtitle_signature,
                1 if has_audio else 0,
                audio_language,
                subtitle_state,
                decision,
                reason,
                policy_signature,
                task_type,
                transcription_type,
                force_language_code,
                audio_tracks_json,
                audio_langs_json,
                int(time.time()),
            ),
        )

    def upsert_excluded(
        self,
        *,
        path: str,
        source_path: str,
        size: int,
        mtime: int,
        reason: str,
        details: str,
        policy_signature: str,
    ):
        self.conn.execute(
            """
            INSERT INTO excluded_files (
              path, source_path, size, mtime, reason, details, policy_signature, last_seen
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
              source_path=excluded.source_path,
              size=excluded.size,
              mtime=excluded.mtime,
              reason=excluded.reason,
              details=excluded.details,
              policy_signature=excluded.policy_signature,
              last_seen=excluded.last_seen
            """,
            (
                path,
                source_path,
                size,
                mtime,
                reason,
                details,
                policy_signature,
                int(time.time()),
            ),
        )

    def upsert_subtitle(
        self,
        *,
        path: str,
        source_path: str,
        size: int,
        mtime: int,
        media_path: str | None,
        subtitle_type: str = "external",
        language: str | None = None,
    ):
        self.conn.execute(
            """
            INSERT INTO subtitle_files (
              path, source_path, size, mtime, media_path, subtitle_type, language, last_seen
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
              source_path=excluded.source_path,
              size=excluded.size,
              mtime=excluded.mtime,
              media_path=excluded.media_path,
              subtitle_type=excluded.subtitle_type,
              language=excluded.language,
              last_seen=excluded.last_seen
            """,
            (
                path,
                source_path,
                size,
                mtime,
                media_path,
                subtitle_type,
                language,
                int(time.time()),
            ),
        )

    def upsert_directory_state(
        self,
        *,
        path: str,
        size: int,
        mtime: int,
        child_dirs_json: str,
        media_files_json: str,
        subtitle_files_json: str,
        entries_signature: str,
        subtree_inventory_json: str = "",
    ):
        self.conn.execute(
            """
            INSERT INTO directory_state (
              path,
              size,
              mtime,
              child_dirs_json,
              media_files_json,
              subtitle_files_json,
              entries_signature,
              subtree_inventory_json,
              last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
              size=excluded.size,
              mtime=excluded.mtime,
              child_dirs_json=excluded.child_dirs_json,
              media_files_json=excluded.media_files_json,
              subtitle_files_json=excluded.subtitle_files_json,
              entries_signature=excluded.entries_signature,
              subtree_inventory_json=excluded.subtree_inventory_json,
              last_seen=excluded.last_seen
            """,
            (
                path,
                size,
                mtime,
                child_dirs_json,
                media_files_json,
                subtitle_files_json,
                entries_signature,
                subtree_inventory_json,
                int(time.time()),
            ),
        )
