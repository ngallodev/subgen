from .dependencies import StartupScanDependencies


def backfill_subtitle_links(db, subtitle_records: list[dict]) -> None:
    for subtitle in subtitle_records:
        db.upsert_subtitle(
            path=subtitle["path"],
            source_path=subtitle["path"],
            size=subtitle["size"],
            mtime=subtitle["mtime"],
            media_path=subtitle.get("media_path"),
            subtitle_type=subtitle.get("subtitle_type") or "external",
            language=subtitle.get("language"),
        )


def build_dependencies(**kwargs) -> StartupScanDependencies:
    return StartupScanDependencies(**kwargs)


def get_startup_scan_backend(deps: StartupScanDependencies):
    from .backend import build_startup_scan_backend

    return build_startup_scan_backend(deps)
