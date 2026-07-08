import sqlite3


SCHEMA_VERSION = "3"

MEDIA_FILE_COLUMNS = {
    "path": "TEXT PRIMARY KEY",
    "source_path": "TEXT",
    "size": "INTEGER NOT NULL",
    "mtime": "INTEGER NOT NULL",
    "subtitle_signature": "TEXT NOT NULL DEFAULT ''",
    "has_audio": "INTEGER NOT NULL DEFAULT 0",
    "audio_language": "TEXT",
    "subtitle_state": "TEXT NOT NULL DEFAULT 'unknown'",
    "decision": "TEXT NOT NULL DEFAULT 'unknown'",
    "reason": "TEXT",
    "policy_signature": "TEXT NOT NULL DEFAULT ''",
    "task_type": "TEXT",
    "transcription_type": "TEXT",
    "force_language_code": "TEXT",
    "audio_tracks_json": "TEXT",
    "audio_langs_json": "TEXT",
    "last_seen": "INTEGER NOT NULL",
}

EXCLUDED_FILE_COLUMNS = {
    "path": "TEXT PRIMARY KEY",
    "source_path": "TEXT",
    "size": "INTEGER NOT NULL",
    "mtime": "INTEGER NOT NULL",
    "reason": "TEXT NOT NULL",
    "details": "TEXT",
    "policy_signature": "TEXT NOT NULL DEFAULT ''",
    "last_seen": "INTEGER NOT NULL",
}

SUBTITLE_FILE_COLUMNS = {
    "path": "TEXT PRIMARY KEY",
    "source_path": "TEXT",
    "size": "INTEGER NOT NULL",
    "mtime": "INTEGER NOT NULL",
    "media_path": "TEXT",
    "subtitle_type": "TEXT NOT NULL DEFAULT 'external'",
    "language": "TEXT",
    "last_seen": "INTEGER NOT NULL",
}

DIRECTORY_STATE_COLUMNS = {
    "path": "TEXT PRIMARY KEY",
    "size": "INTEGER NOT NULL",
    "mtime": "INTEGER NOT NULL",
    "child_dirs_json": "TEXT NOT NULL",
    "media_files_json": "TEXT NOT NULL",
    "subtitle_files_json": "TEXT NOT NULL",
    "entries_signature": "TEXT NOT NULL DEFAULT ''",
    "subtree_inventory_json": "TEXT NOT NULL DEFAULT ''",
    "last_seen": "INTEGER NOT NULL",
}


def _ensure_columns(conn: sqlite3.Connection, table_name: str, columns: dict[str, str]) -> None:
    existing_columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    for column_name, column_def in columns.items():
        if column_name in existing_columns:
            continue
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")


def ensure_startup_scan_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scan_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS media_files (
          path TEXT PRIMARY KEY,
          source_path TEXT,
          size INTEGER NOT NULL,
          mtime INTEGER NOT NULL,
          subtitle_signature TEXT NOT NULL DEFAULT '',
          has_audio INTEGER NOT NULL DEFAULT 0,
          audio_language TEXT,
          subtitle_state TEXT NOT NULL DEFAULT 'unknown',
          decision TEXT NOT NULL DEFAULT 'unknown',
          reason TEXT,
          policy_signature TEXT NOT NULL DEFAULT '',
          task_type TEXT,
          transcription_type TEXT,
          force_language_code TEXT,
          audio_tracks_json TEXT,
          audio_langs_json TEXT,
          last_seen INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_media_files_mtime ON media_files(mtime);
        CREATE INDEX IF NOT EXISTS idx_media_files_decision ON media_files(decision);
        CREATE INDEX IF NOT EXISTS idx_media_files_subtitle_state ON media_files(subtitle_state);

        CREATE TABLE IF NOT EXISTS excluded_files (
          path TEXT PRIMARY KEY,
          source_path TEXT,
          size INTEGER NOT NULL,
          mtime INTEGER NOT NULL,
          reason TEXT NOT NULL,
          details TEXT,
          policy_signature TEXT NOT NULL DEFAULT '',
          last_seen INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_excluded_files_reason ON excluded_files(reason);

        CREATE TABLE IF NOT EXISTS subtitle_files (
          path TEXT PRIMARY KEY,
          source_path TEXT,
          size INTEGER NOT NULL,
          mtime INTEGER NOT NULL,
          media_path TEXT,
          subtitle_type TEXT NOT NULL DEFAULT 'external',
          language TEXT,
          last_seen INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_subtitle_files_media_path ON subtitle_files(media_path);
        CREATE INDEX IF NOT EXISTS idx_subtitle_files_language ON subtitle_files(language);

        CREATE TABLE IF NOT EXISTS directory_state (
          path TEXT PRIMARY KEY,
          size INTEGER NOT NULL,
          mtime INTEGER NOT NULL,
          child_dirs_json TEXT NOT NULL,
          media_files_json TEXT NOT NULL,
          subtitle_files_json TEXT NOT NULL,
          entries_signature TEXT NOT NULL DEFAULT '',
          subtree_inventory_json TEXT NOT NULL DEFAULT '',
          last_seen INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scan_state (
          path TEXT PRIMARY KEY,
          size INTEGER NOT NULL,
          mtime INTEGER NOT NULL,
          scan_decision TEXT NOT NULL,
          reason TEXT,
          updated_at INTEGER NOT NULL
        );
        """
    )
    _ensure_columns(conn, "media_files", MEDIA_FILE_COLUMNS)
    _ensure_columns(conn, "excluded_files", EXCLUDED_FILE_COLUMNS)
    _ensure_columns(conn, "subtitle_files", SUBTITLE_FILE_COLUMNS)
    _ensure_columns(conn, "directory_state", DIRECTORY_STATE_COLUMNS)
    conn.execute(
        """
        INSERT INTO scan_meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        ("schema_version", SCHEMA_VERSION),
    )
