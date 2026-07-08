# SQLite Startup Scan Plan

## Goal

Replace the current startup-time full recursive `os.walk()` pass with a cached, persistent scan index backed by SQLite on a host bind mount.

The startup scan should:

1. Discover candidate media files quickly.
2. Reuse prior decisions when files have not changed.
3. Track files that already have qualifying subtitles.
4. Track excluded files with a reason.
5. Queue only the files that still need work.

## Storage Location

Use a bind-mounted host directory for persistent state, for example:

```text
/subgen/state/subgen_scan.db
```

The container should not treat this as ephemeral runtime data.

## Schema

### `scan_meta`

Stores schema/version and scan bookkeeping.

```sql
CREATE TABLE IF NOT EXISTS scan_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
```

Suggested keys:

- `schema_version`
- `last_full_scan_at`
- `last_prune_at`

### `media_files`

Tracks media candidates and whether they were already eligible or processed.

```sql
CREATE TABLE IF NOT EXISTS media_files (
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

CREATE INDEX IF NOT EXISTS idx_media_files_mtime ON media_files(mtime);
CREATE INDEX IF NOT EXISTS idx_media_files_decision ON media_files(decision);
CREATE INDEX IF NOT EXISTS idx_media_files_subtitle_state ON media_files(subtitle_state);
```

Suggested `decision` values:

- `eligible`
- `excluded`
- `queued`
- `processed`

Suggested `subtitle_state` values:

- `unknown`
- `none`
- `internal`
- `external`
- `generated`

### `excluded_files`

Tracks files that were skipped and why.

```sql
CREATE TABLE IF NOT EXISTS excluded_files (
  path TEXT PRIMARY KEY,
  size INTEGER NOT NULL,
  mtime INTEGER NOT NULL,
  reason TEXT NOT NULL,
  details TEXT,
  last_seen INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_excluded_files_reason ON excluded_files(reason);
```

### `subtitle_files`

Tracks subtitle artifacts and which media file they belong to.

```sql
CREATE TABLE IF NOT EXISTS subtitle_files (
  path TEXT PRIMARY KEY,
  size INTEGER NOT NULL,
  mtime INTEGER NOT NULL,
  media_path TEXT,
  subtitle_type TEXT NOT NULL DEFAULT 'external',
  language TEXT,
  last_seen INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_subtitle_files_media_path ON subtitle_files(media_path);
CREATE INDEX IF NOT EXISTS idx_subtitle_files_language ON subtitle_files(language);
```

### `scan_state`

Optional helper table for fast invalidation and incremental scans.

```sql
CREATE TABLE IF NOT EXISTS scan_state (
  path TEXT PRIMARY KEY,
  size INTEGER NOT NULL,
  mtime INTEGER NOT NULL,
  scan_decision TEXT NOT NULL,
  reason TEXT,
  updated_at INTEGER NOT NULL
);
```

## Startup Flow

```text
startup():
  open SQLite DB from bind-mounted path
  initialize schema if needed
  load persisted state
  discover current media and subtitle files

  for each discovered file:
    if subtitle file:
      upsert subtitle_files row
      continue

    if not media candidate:
      continue

    if cached row exists and size/mtime match:
      reuse cached decision
      if decision == eligible or queued:
        enqueue if not already active
      if decision == excluded:
        skip
      continue

    compute cheap candidate filters
    probe only the candidates that survive cheap filters

    if file already has acceptable subtitles:
      upsert excluded_files + update media_files subtitle_state
      continue

    if file passes audio and skip rules:
      upsert media_files as eligible
      enqueue
    else:
      upsert excluded_files with reason

  update scan_meta timestamps
```

## Discovery Rules

Use a Python-native recursive walker, not shelling out to `rg`.

Discovery should:

- recurse into subdirectories
- ignore only directories that are clearly irrelevant
- record subtitle files separately from media files
- keep traversal cheap by avoiding expensive probes during recursion

## Invalidation Rules

A cached decision is valid only if:

- `path` matches
- `size` matches
- `mtime` matches

If any of those change, the file must be re-evaluated.

## Implementation Plan

### Phase 1

- Add SQLite access layer in `subgen.py` or a small helper module.
- Add bind-mounted persistence path configuration.
- Create schema initialization.
- Add read/write helpers for media, subtitle, and exclusion rows.

### Phase 2

- Refactor startup scan into:
  - discovery
  - classification
  - queueing
- Use cached rows to skip unchanged files.
- Record exclusion reasons.

### Phase 3

- Update docker compose to mount the persistent state directory.
- Update docs and config examples.
- Add tests for:
  - schema creation
  - cache reuse
  - subtitle indexing
  - exclusion recording
  - invalidation on path/mtime/size change

## Notes

- The DB is a cache/index, not the only source of truth.
- The queue still owns active deduplication and priority ordering.
- Startup should become incremental and cheap enough to avoid rescanning the entire library in full detail on every boot.
