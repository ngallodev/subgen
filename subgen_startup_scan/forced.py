import logging
import os


def startup_scan_existing_forced_language(
    transcribe_folders_spec: str,
    force_language,
    *,
    startup_scan_db_path: str,
    db_factory,
    startup_scan_now,
    collect_records,
    record_subtitle,
    process_record,
    monitor,
    observer_factory,
    new_file_handler_factory,
    retain_observer,
) -> None:
    folders = [path for path in transcribe_folders_spec.split("|") if path]
    if not folders:
        return

    logging.info("Starting to search folders to see if we need to create subtitles.")
    logging.debug("The folders are:")
    for path in folders:
        logging.debug(path)

    db = db_factory(startup_scan_db_path)
    try:
        scan_started_at = startup_scan_now()
        conn = db.conn

        for path in folders:
            if not os.path.exists(path):
                logging.warning("%s does not exist, skipping startup scan.", path)
                continue

            media_records, subtitle_records = collect_records(path)
            subtitle_rows_by_media = {}

            for subtitle_record in subtitle_records:
                record_subtitle(
                    conn,
                    subtitle_record["path"],
                    subtitle_record["size"],
                    subtitle_record["mtime"],
                    subtitle_record["media_path"],
                    subtitle_record["subtitle_type"],
                    subtitle_record["language"],
                    scan_started_at,
                )
                if subtitle_record["media_path"]:
                    subtitle_rows_by_media.setdefault(subtitle_record["media_path"], []).append(subtitle_record)

            for media_record in media_records:
                subtitle_rows = subtitle_rows_by_media.get(media_record["path"], [])
                process_record(conn, media_record, subtitle_rows, force_language)

        db.set_meta("last_full_scan_at", str(scan_started_at))
    finally:
        db.close()

    if not monitor:
        return

    observer = observer_factory()
    for path in folders:
        if os.path.isdir(path):
            handler = new_file_handler_factory()
            observer.schedule(handler, path, recursive=True)
    retain_observer(observer)
    observer.start()
    logging.info("Finished searching and queueing files for transcription. Now watching for new files.")
