def transcribe_existing_dispatch(
    transcribe_folders,
    force_language,
    *,
    language_code_none,
    get_backend,
    initialize,
    run_forced_language_scan,
):
    if force_language == language_code_none:
        backend = get_backend()
        backend.initialize()
        backend.startup_scan_existing(transcribe_folders)
        return

    initialize()
    run_forced_language_scan(transcribe_folders, force_language)
