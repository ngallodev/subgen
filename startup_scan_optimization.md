# Startup Scan Optimization Notes

## Current startup behavior

At startup, `subgen.py` reads `TRANSCRIBE_FOLDERS` and, if it is non-empty, launches a background thread from the FastAPI `lifespan()` hook.

Current flow:

```text
lifespan()
  if TRANSCRIBE_FOLDERS is set:
    start thread -> transcribe_existing(TRANSCRIBE_FOLDERS)

transcribe_existing(transcribe_folders):
  split TRANSCRIBE_FOLDERS on "|"
  for each configured path:
    walk the entire tree with os.walk()
    for every file found:
      queue it for subtitle generation
  if a configured path is a single file:
    queue it directly if it has audio
  if MONITOR is enabled:
    attach filesystem watchers for new files
```

## Why startup is slow

The current initial scan is expensive because it:

1. Recurses every configured tree immediately.
2. Treats every file as a candidate before checking whether it is actually worth processing.
3. Repeats the same expensive metadata work for files that will later be skipped.
4. Has no early pruning strategy for folders that are obviously irrelevant.

## Smarter startup scan pseudocode

```text
startup_scan(configured_paths):
  normalize and de-duplicate paths
  for each path in configured_paths:
    if path is a file:
      process_single_file(path)
      continue

    if path is not a directory:
      skip path
      continue

    walk directory tree with pruning:
      ignore hidden/system directories
      ignore known non-media directories
      optionally cap depth if configured

      for each file candidate:
        if file extension is not media/audio:
          skip immediately

        if file is already known to be processed:
          skip immediately

        if sibling subtitle evidence makes this a no-op:
          skip immediately

        if file is unstable or still being written:
          defer it

        enqueue only the remaining candidates
```

## Recommended optimization layers

### 1. Early file filtering before expensive probes

Prefer cheap checks first:

- extension check
- hidden/system path check
- known skip-pattern check
- dedupe check

Only then do audio probing and subtitle inspection.

### 2. Separate discovery from validation

Instead of doing full metadata validation during the walk, make startup scan two-stage:

- Stage 1: discover likely candidates quickly
- Stage 2: validate candidates in a worker/queue

That keeps the startup thread from blocking on deep media analysis.

### 3. Cache or persist prior scan state

If the same library is scanned repeatedly, remember what was already processed:

- path
- mtime
- size
- last-seen subtitle state

Then skip unchanged files on the next startup pass.

### 4. Prune directory traversal

Do not recurse into directories that cannot contain useful media, such as:

- hidden folders
- app cache folders
- subtitle-only folders
- known exclude patterns

### 5. Batch queueing by confidence

Queue files in priority bands:

- high confidence: media extension + no existing subtitle signal
- medium confidence: media extension but unknown metadata
- low confidence: ambiguous or partial files, processed later

## Practical target behavior

The startup scan should behave more like:

```text
scan only what is likely to matter
delay expensive checks until after startup
avoid rescanning obvious no-op files
preserve correctness for files that actually need subtitles
```

## Source touchpoints

- `subgen.py`
  - `lifespan()`
  - `transcribe_existing()`
  - `gen_subtitles_queue()`
  - `should_skip_file()`
- `docker-compose.yml`
  - `/movies` mount
- `subgen.xml`
  - `TRANSCRIBE_FOLDERS` and `MONITOR` config
