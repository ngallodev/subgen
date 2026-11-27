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
- `allow_invalid` defaults to `false`. When false, Subgen requires an existing regular file with a supported audio or video extension.

## Response

The endpoint returns the queue planner's result immediately; it does not wait for transcription to finish. A `queued` status means a worker can process the request. Depending on queue state, the response can instead report an active/busy job, a skip such as missing audio, or a validation error. With the persistent backend, forced requests use durable job IDs and admission handling; the in-memory backend retains its queue's duplicate behavior.

Forced requests still require usable audio and must pass worker input checks. They do not bypass output validation or publication safeguards.
