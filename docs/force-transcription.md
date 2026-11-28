# Forced transcription endpoint

`POST /force` submits one media file for transcription or translation while bypassing the normal eligibility checks for existing subtitles, cached skip decisions, and `.subgen_skip` markers. It uses the regular worker and output publication path.

## Request

Send a JSON object:

```json
{
  "file_path": "/media/Show/Episode.mkv",
  "transcription_type": "transcribe",
  "force_language": "eng",
  "allow_invalid": false
}
```

Fields:

- `file_path` is required. Subgen applies configured path mapping before validation.
- `transcription_type` is optional and defaults to the configured operation. Supported values are `transcribe` and `translate`.
- `force_language` is optional. When supplied, it selects the input language for the requested operation.
- `audio_tracks` is optional and can provide preselected audio-track metadata.
- `allow_invalid` defaults to `false` and must be a JSON boolean. When true, it permits an existing regular media file with an unusual extension to pass the extension check. The file must still be a regular file, FFmpeg must be able to probe it, and it must contain audio. It does not allow missing paths or directories.

## Response

The endpoint returns the queue planner's result immediately; it does not wait for transcription to finish. A `queued` status means a worker can process the request. Depending on queue state, the response can instead report `already_active` for pending work, `busy` for work in progress, a skip such as missing audio, a probe error, or a validation error. In the legacy in-memory backend, a request for an active path does not change the existing job's options. With the persistent backend, forced requests use durable job IDs and admission handling, including promotion of compatible pending work.

Forced requests still require usable audio and must pass worker input checks. They do not bypass output validation or publication safeguards. A client timeout means the queue result is unknown; active-path deduplication protects a retry only while the original job remains pending or running. Repeating the request after completion intentionally starts a new forced run. No idempotency key is currently supported.
