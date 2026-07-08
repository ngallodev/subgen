import logging
import os


def legacy_startup_scan_existing(
    transcribe_folder_spec: str,
    *,
    skip_startup_scan: bool,
    skip_marker: str,
    monitor,
    observer_factory,
    new_file_handler_factory,
    has_audio,
    path_mapping,
    gen_subtitles_queue,
    transcribe_or_translate: str,
    retain_observer,
) -> None:
    folders = [path for path in transcribe_folder_spec.split("|") if path]
    if skip_startup_scan:
        logging.info("SKIP_STARTUP_SCAN enabled - skipping existing file scan.")
    else:
        logging.info("Starting to search folders to see if we need to create subtitles.")
        logging.debug("The folders are:")
        for path in folders:
            logging.debug(path)
            for root, dirs, files in os.walk(path):
                if skip_marker in files:
                    logging.info("Skipping (skip marker present): %s", root)
                    dirs.clear()
                    continue
                for file_name in files:
                    file_path = os.path.join(root, file_name)
                    gen_subtitles_queue(path_mapping(file_path), transcribe_or_translate)
            if os.path.isfile(path) and has_audio(path):
                gen_subtitles_queue(path_mapping(path), transcribe_or_translate)

    if not monitor:
        return

    observer = observer_factory()
    for path in folders:
        if os.path.isdir(path):
            handler = new_file_handler_factory()
            observer.schedule(handler, path, recursive=True)
    observer.start()
    retain_observer(observer)
    logging.info("Finished searching and queueing files for transcription. Now watching for new files.")
