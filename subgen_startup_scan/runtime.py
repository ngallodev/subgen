import json
import logging
import os
import time

from .association import current_sidecar_state


def startup_scan_now() -> int:
    return int(time.time())


def startup_scan_get_media_row(conn, path: str):
    row = conn.execute("SELECT * FROM media_files WHERE path = ?", (path,)).fetchone()
    return dict(row) if row else None


def startup_scan_get_subtitle_rows(conn, media_path: str):
    rows = conn.execute(
        "SELECT * FROM subtitle_files WHERE media_path = ? ORDER BY path",
        (media_path,),
    ).fetchall()
    return [dict(row) for row in rows]


def startup_scan_record_media(conn, path: str, size: int, mtime: int, has_audio: bool, audio_language, subtitle_state: str, decision: str, reason: str, last_seen: int) -> None:
    conn.execute(
        """
        INSERT INTO media_files(path, size, mtime, has_audio, audio_language, subtitle_state, decision, reason, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
          size=excluded.size,
          mtime=excluded.mtime,
          has_audio=excluded.has_audio,
          audio_language=excluded.audio_language,
          subtitle_state=excluded.subtitle_state,
          decision=excluded.decision,
          reason=excluded.reason,
          last_seen=excluded.last_seen
        """,
        (
            path,
            size,
            mtime,
            1 if has_audio else 0,
            str(audio_language) if audio_language else None,
            subtitle_state,
            decision,
            reason,
            last_seen,
        ),
    )


def startup_scan_record_excluded(conn, path: str, size: int, mtime: int, reason: str, details: str, last_seen: int) -> None:
    conn.execute(
        """
        INSERT INTO excluded_files(path, size, mtime, reason, details, last_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
          size=excluded.size,
          mtime=excluded.mtime,
          reason=excluded.reason,
          details=excluded.details,
          last_seen=excluded.last_seen
        """,
        (path, size, mtime, reason, details, last_seen),
    )


def startup_scan_record_subtitle(conn, path: str, size: int, mtime: int, media_path: str, subtitle_type: str, language, last_seen: int) -> None:
    conn.execute(
        """
        INSERT INTO subtitle_files(path, size, mtime, media_path, subtitle_type, language, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
          size=excluded.size,
          mtime=excluded.mtime,
          media_path=excluded.media_path,
          subtitle_type=excluded.subtitle_type,
          language=excluded.language,
          last_seen=excluded.last_seen
        """,
        (
            path,
            size,
            mtime,
            media_path,
            subtitle_type,
            str(language) if language else None,
            last_seen,
        ),
    )


def startup_scan_update_state(conn, path: str, size: int, mtime: int, scan_decision: str, reason: str, updated_at: int) -> None:
    conn.execute(
        """
        INSERT INTO scan_state(path, size, mtime, scan_decision, reason, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
          size=excluded.size,
          mtime=excluded.mtime,
          scan_decision=excluded.scan_decision,
          reason=excluded.reason,
          updated_at=excluded.updated_at
        """,
        (path, size, mtime, scan_decision, reason, updated_at),
    )


def json_default(value, *, language_code):
    if isinstance(value, language_code):
        return value.to_iso_639_2_t() if value else ""
    return str(value)


def normalize_audio_tracks(audio_tracks, *, language_code):
    normalized_tracks = []
    for track in audio_tracks or []:
        if not isinstance(track, dict):
            continue
        normalized_track = dict(track)
        normalized_track["language"] = (
            normalized_track["language"]
            if isinstance(normalized_track.get("language"), language_code)
            else language_code.from_string(normalized_track.get("language"))
        )
        normalized_tracks.append(normalized_track)
    return normalized_tracks


def startup_scan_deserialize_audio_tracks(audio_tracks_json: str | None, *, language_code):
    if not audio_tracks_json:
        return []
    try:
        audio_tracks = json.loads(audio_tracks_json)
    except json.JSONDecodeError:
        return []
    return normalize_audio_tracks(audio_tracks, language_code=language_code)


def initialize_startup_scan(db_path: str, *, db_factory) -> None:
    db = db_factory(db_path)
    db.close()


def refresh_processed_file(
    path: str,
    transcription_type: str,
    force_language,
    *,
    audio_tracks,
    language_code,
    get_audio_tracks,
    choose_transcribe_language,
    path_mapping,
    name_subtitle,
    collect_startup_inventory,
    compute_subtitle_signature,
    get_startup_policy_signature,
    db_factory,
    startup_scan_db_path: str,
) -> None:
    if not os.path.exists(path):
        return

    try:
        stat_result = os.stat(path)
    except OSError as exc:
        logging.debug("Could not refresh startup cache for %s: %s", path, exc)
        return

    normalized_audio_tracks = normalize_audio_tracks(
        audio_tracks if audio_tracks is not None else get_audio_tracks(path),
        language_code=language_code,
    )
    resolved_audio_language = choose_transcribe_language(
        path,
        force_language,
        audio_tracks=normalized_audio_tracks,
    )
    source_path = path_mapping(path)
    subtitle_path = name_subtitle(
        path,
        resolved_audio_language if resolved_audio_language != language_code.NONE else language_code.ENGLISH,
    )
    subtitle_state = "generated" if os.path.exists(subtitle_path) else "unknown"
    audio_language = resolved_audio_language.to_iso_639_2_t() if resolved_audio_language else ""
    audio_langs = [
        track["language"].to_iso_639_2_t() if track.get("language") else ""
        for track in normalized_audio_tracks
    ]
    policy_signature = get_startup_policy_signature()
    subtitle_inventory = collect_startup_inventory([os.path.dirname(path)], recursive=False)
    subtitle_signature, _ = compute_subtitle_signature(path, subtitle_inventory, {})

    try:
        db = db_factory(startup_scan_db_path)
        try:
            db.begin()
            db.upsert_media(
                path=source_path,
                source_path=path,
                size=stat_result.st_size,
                mtime=int(stat_result.st_mtime),
                subtitle_signature=subtitle_signature,
                has_audio=bool(normalized_audio_tracks),
                audio_language=audio_language,
                subtitle_state=subtitle_state,
                decision="eligible",
                reason="completed",
                policy_signature=policy_signature,
                task_type="transcribe" if transcription_type != "translate" else "translate",
                transcription_type=transcription_type,
                force_language_code=resolved_audio_language.to_iso_639_2_t() if resolved_audio_language else "",
                audio_tracks_json=json.dumps(
                    normalized_audio_tracks,
                    default=lambda value: json_default(value, language_code=language_code),
                ),
                audio_langs_json=json.dumps(audio_langs),
            )
            db.upsert_subtitle(
                path=subtitle_path,
                source_path=subtitle_path,
                size=os.path.getsize(subtitle_path) if os.path.exists(subtitle_path) else 0,
                mtime=int(os.path.getmtime(subtitle_path)) if os.path.exists(subtitle_path) else int(stat_result.st_mtime),
                media_path=path,
                subtitle_type="generated",
                language=resolved_audio_language.to_iso_639_2_t() if resolved_audio_language else None,
            )
            db.set_meta("flat_inventory_policy_signature", policy_signature)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as exc:
        logging.debug("Could not update startup cache for %s: %s", path, exc)


def startup_scan_process_record(
    conn,
    media_record: dict,
    subtitle_rows: list,
    force_language,
    *,
    task_queue,
    gen_subtitles_queue,
    prepare_media_queue_job,
    transcribe_or_translate: str,
) -> str:
    file_path = media_record["path"]
    size = media_record["size"]
    mtime = media_record["mtime"]
    sidecar_state = current_sidecar_state(subtitle_rows)
    cached_row = startup_scan_get_media_row(conn, file_path)

    if cached_row and cached_row["size"] == size and cached_row["mtime"] == mtime:
        cached_state = cached_row.get("subtitle_state", "unknown")
        reusable = (
            sidecar_state == "none" and cached_state in {"none", "unknown", "internal"}
        ) or (
            sidecar_state != "none" and cached_state == sidecar_state
        )
        if reusable:
            reused_decision = "queued" if cached_row.get("decision") in {"eligible", "queued"} else cached_row.get("decision", "unknown")
            if cached_row.get("decision") in {"eligible", "queued"} and not task_queue.is_active(file_path):
                gen_subtitles_queue(file_path, transcribe_or_translate, force_language, mtime=mtime)
                reused_decision = "queued"
            startup_scan_record_media(
                conn,
                file_path,
                size,
                mtime,
                bool(cached_row.get("has_audio", 0)),
                cached_row.get("audio_language"),
                cached_state,
                reused_decision,
                cached_row.get("reason"),
                startup_scan_now(),
            )
            startup_scan_update_state(
                conn,
                file_path,
                size,
                mtime,
                reused_decision,
                cached_row.get("reason"),
                startup_scan_now(),
            )
            return "reused"

    plan = prepare_media_queue_job(file_path, transcribe_or_translate, force_language=force_language)
    if plan["status"] == "skip":
        startup_scan_record_media(
            conn,
            file_path,
            size,
            mtime,
            bool(plan.get("audio_tracks")),
            str(plan.get("force_language")) if plan.get("force_language") else None,
            sidecar_state,
            "excluded",
            plan["reason"],
            startup_scan_now(),
        )
        startup_scan_record_excluded(
            conn,
            file_path,
            size,
            mtime,
            plan["reason"],
            plan.get("details"),
            startup_scan_now(),
        )
        startup_scan_update_state(conn, file_path, size, mtime, "excluded", plan["reason"], startup_scan_now())
        return "excluded"

    if plan["status"] in {"queue", "detect", "active"}:
        if plan["status"] != "active":
            gen_subtitles_queue(
                file_path,
                transcribe_or_translate,
                plan.get("force_language", force_language),
                mtime=mtime,
            )
        startup_scan_record_media(
            conn,
            file_path,
            size,
            mtime,
            bool(plan.get("audio_tracks")),
            str(plan.get("force_language")) if plan.get("force_language") else None,
            sidecar_state,
            "queued",
            plan["reason"],
            startup_scan_now(),
        )
        startup_scan_update_state(conn, file_path, size, mtime, "queued", plan["reason"], startup_scan_now())
        return "queued"

    return "ignored"
