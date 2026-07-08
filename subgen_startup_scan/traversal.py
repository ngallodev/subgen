import logging
import os


def is_media_candidate(file_name: str, *, has_video_extension, has_audio_extension) -> bool:
    return has_video_extension(file_name) or has_audio_extension(file_name)


def build_record(path: str, *, file_name: str, stat_result) -> dict:
    return {
        "path": path,
        "name": file_name,
        "dir": os.path.dirname(path),
        "stem": os.path.splitext(file_name)[0],
        "size": stat_result.st_size,
        "mtime": int(stat_result.st_mtime),
    }


def walk_media_tree(root_path: str, *, ignored_dir_names: set[str]):
    try:
        entries = sorted(os.scandir(root_path), key=lambda entry: entry.name)
    except OSError as exc:
        logging.warning("Could not scan %s: %s", root_path, exc)
        return

    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                if entry.name.startswith(".") or entry.name in ignored_dir_names:
                    continue
                yield from walk_media_tree(entry.path, ignored_dir_names=ignored_dir_names)
            elif entry.is_file(follow_symlinks=False):
                yield entry.path
        except OSError as exc:
            logging.debug("Could not inspect %s: %s", entry.path, exc)


def collect_root_files(root_path: str, *, ignored_dir_names: set[str]):
    if os.path.isfile(root_path):
        yield root_path
        return
    if os.path.isdir(root_path):
        yield from walk_media_tree(root_path, ignored_dir_names=ignored_dir_names)


def collect_startup_records(
    root_path: str,
    *,
    ignored_dir_names: set[str],
    path_mapping,
    is_subtitle_file_name,
    has_video_extension,
    has_audio_extension,
    enrich_subtitle_records,
):
    media_records = []
    subtitle_records = []
    media_index = {}

    for file_path in collect_root_files(root_path, ignored_dir_names=ignored_dir_names):
        mapped_path = path_mapping(file_path)
        file_name = os.path.basename(mapped_path)

        try:
            stat_result = os.stat(file_path, follow_symlinks=False)
        except OSError as exc:
            logging.debug("Could not stat %s: %s", file_path, exc)
            continue

        record = {
            "path": mapped_path,
            "size": stat_result.st_size,
            "mtime": int(stat_result.st_mtime),
        }

        if is_subtitle_file_name(file_name):
            subtitle_records.append(
                {
                    **record,
                    "name": file_name,
                    "dir": os.path.dirname(mapped_path),
                    "stem": os.path.splitext(file_name)[0],
                    "subtitle_type": None,
                    "language": None,
                    "media_path": None,
                }
            )
            continue

        if is_media_candidate(
            file_name,
            has_video_extension=has_video_extension,
            has_audio_extension=has_audio_extension,
        ):
            media_records.append(record)
            media_index.setdefault(os.path.dirname(mapped_path), {})[os.path.splitext(file_name)[0]] = mapped_path

    enrich_subtitle_records(subtitle_records, media_index)
    return media_records, subtitle_records


def list_directory_entries(directory: str, *, is_relevant_inventory_file_name):
    child_dirs = []
    file_names = []
    with os.scandir(directory) as entries:
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    child_dirs.append(entry.path)
                elif entry.is_file(follow_symlinks=False) and is_relevant_inventory_file_name(entry.name):
                    file_names.append(entry.name)
            except OSError as exc:
                logging.warning("Could not inspect %s: %s", entry.path, exc)
    return child_dirs, file_names


def scan_directory_records(
    directory: str,
    *,
    is_subtitle_file_name,
    has_video_extension,
    has_audio_extension,
):
    child_dirs = []
    media_records = []
    subtitle_records = []

    with os.scandir(directory) as entries:
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    child_dirs.append(entry.path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                stat_result = entry.stat(follow_symlinks=False)
                record = build_record(entry.path, file_name=entry.name, stat_result=stat_result)
                if is_subtitle_file_name(entry.name):
                    subtitle_records.append(record)
                elif is_media_candidate(
                    entry.name,
                    has_video_extension=has_video_extension,
                    has_audio_extension=has_audio_extension,
                ):
                    media_records.append(record)
            except OSError as exc:
                logging.warning("Could not inspect %s: %s", entry.path, exc)

    return child_dirs, media_records, subtitle_records
