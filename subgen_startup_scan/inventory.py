import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass

from .inventory_cache import (
    cached_entries_signature,
    directory_entries_signature,
    materialize_cached_subtree,
    serialize_root_subtree,
)
from .traversal import build_record, list_directory_entries, scan_directory_records


SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".sub", ".ass", ".ssa", ".idx", ".sbv", ".pgs", ".ttml", ".lrc"}


@dataclass
class StartupInventory:
    media_files: list
    subtitle_files: list
    subtitle_files_by_dir: dict
    child_dirs_by_dir: dict


def is_subtitle_file_name(file_name: str) -> bool:
    return os.path.splitext(file_name)[1].lower() in SUBTITLE_EXTENSIONS


def is_relevant_inventory_file_name(file_name: str, *, has_video_extension, has_audio_extension) -> bool:
    return is_subtitle_file_name(file_name) or has_video_extension(file_name) or has_audio_extension(file_name)


def collect_startup_inventory(
    root_paths,
    recursive: bool = False,
    db=None,
    *,
    has_video_extension,
    has_audio_extension,
    enrich_subtitle_records,
    json_default,
):
    inventory = StartupInventory(
        media_files=[],
        subtitle_files=[],
        subtitle_files_by_dir=defaultdict(list),
        child_dirs_by_dir=defaultdict(list),
    )
    cache_hits = 0
    cache_misses = 0
    media_index = defaultdict(dict)
    cache_reason_counts = defaultdict(int)
    directory_stat_count = 0
    directory_list_count = 0
    file_stat_count = 0
    quick_signature_checks = 0
    full_directory_scans = 0
    directory_state_cache = None

    if recursive and db and hasattr(db, "get_all_directory_state"):
        try:
            directory_state_cache = {record["path"]: record for record in db.get_all_directory_state()}
        except Exception as exc:
            logging.warning("Could not preload cached directory state: %s", exc)
            directory_state_cache = None

    def _index_media_records(media_records: list[dict]) -> None:
        for record in media_records:
            media_index[record["dir"]][record["stem"]] = record["path"]

    def _extend_directory_records(directory: str, child_dirs: list[str], media_records: list[dict], subtitle_records: list[dict]) -> None:
        inventory.child_dirs_by_dir[directory].extend(child_dirs)
        inventory.media_files.extend(media_records)
        inventory.subtitle_files.extend(subtitle_records)
        for subtitle in subtitle_records:
            inventory.subtitle_files_by_dir[subtitle["dir"]].append(subtitle)

    def scan_directory(root_path: str, *, is_root: bool = False) -> None:
        nonlocal cache_hits
        nonlocal cache_misses
        nonlocal directory_stat_count
        nonlocal directory_list_count
        nonlocal file_stat_count
        nonlocal quick_signature_checks
        nonlocal full_directory_scans

        try:
            stat_result = os.stat(root_path)
            directory_stat_count += 1
        except OSError as exc:
            logging.warning("Could not inspect directory %s: %s", root_path, exc)
            return

        cached_state = None
        if db:
            cached_state = (
                directory_state_cache.get(root_path)
                if directory_state_cache is not None
                else db.get_directory_state(root_path)
            )

        if cached_state:
            try:
                child_dirs = json.loads(cached_state.get("child_dirs_json") or "[]")
                media_records = json.loads(cached_state.get("media_files_json") or "[]")
                subtitle_records = json.loads(cached_state.get("subtitle_files_json") or "[]")
            except json.JSONDecodeError:
                cache_reason_counts["decode_error"] += 1
            else:
                if cached_state.get("size") == stat_result.st_size and cached_state.get("mtime") == int(stat_result.st_mtime):
                    cache_hits += 1
                    cache_reason_counts["stat_match"] += 1
                    subtree_inventory_json = cached_state.get("subtree_inventory_json") or ""
                    if recursive and subtree_inventory_json:
                        materialize_cached_subtree(
                            inventory,
                            subtree_inventory_json,
                            media_index=media_index,
                            enrich_subtitle_records=enrich_subtitle_records,
                            index_media_records=_index_media_records,
                        )
                        return

                    _index_media_records(media_records)
                    enrich_subtitle_records(subtitle_records, media_index)
                    _extend_directory_records(root_path, child_dirs, media_records, subtitle_records)
                    if recursive:
                        for child_dir in child_dirs:
                            scan_directory(child_dir)
                    if db and is_root:
                        serialized_records = media_records + subtitle_records
                        entries_signature = directory_entries_signature(
                            child_dirs,
                            [record["name"] for record in serialized_records],
                        )
                        child_dirs_json = json.dumps(child_dirs)
                        media_files_json = json.dumps(media_records, default=json_default)
                        subtitle_files_json = json.dumps(subtitle_records, default=json_default)
                        subtree_inventory_json = serialize_root_subtree(
                            inventory,
                            root_path,
                            json_default=json_default,
                        )
                        inventory.root_subtree_snapshot_bytes = len(subtree_inventory_json)
                        db.upsert_directory_state(
                            path=root_path,
                            size=stat_result.st_size,
                            mtime=int(stat_result.st_mtime),
                            child_dirs_json=child_dirs_json,
                            media_files_json=media_files_json,
                            subtitle_files_json=subtitle_files_json,
                            entries_signature=entries_signature,
                            subtree_inventory_json=subtree_inventory_json,
                        )
                        if directory_state_cache is not None:
                            directory_state_cache[root_path] = {
                                "path": root_path,
                                "size": stat_result.st_size,
                                "mtime": int(stat_result.st_mtime),
                                "child_dirs_json": child_dirs_json,
                                "media_files_json": media_files_json,
                                "subtitle_files_json": subtitle_files_json,
                                "entries_signature": entries_signature,
                                "subtree_inventory_json": subtree_inventory_json,
                            }
                    return

                try:
                    quick_signature_checks += 1
                    directory_list_count += 1
                    listed_child_dirs, listed_file_names = list_directory_entries(
                        root_path,
                        is_relevant_inventory_file_name=lambda file_name: is_relevant_inventory_file_name(
                            file_name,
                            has_video_extension=has_video_extension,
                            has_audio_extension=has_audio_extension,
                        ),
                    )
                except OSError as exc:
                    logging.warning("Could not inspect directory %s: %s", root_path, exc)
                    cache_reason_counts["list_error"] += 1
                else:
                    current_signature = directory_entries_signature(listed_child_dirs, listed_file_names)
                    cached_signature = cached_entries_signature(cached_state, child_dirs, media_records, subtitle_records)
                    if current_signature == cached_signature:
                        cache_hits += 1
                        cache_reason_counts["entries_signature_match"] += 1
                        subtree_inventory_json = cached_state.get("subtree_inventory_json") or ""
                        if recursive and subtree_inventory_json:
                            materialize_cached_subtree(
                                inventory,
                                subtree_inventory_json,
                                media_index=media_index,
                                enrich_subtitle_records=enrich_subtitle_records,
                                index_media_records=_index_media_records,
                            )
                            return

                        _index_media_records(media_records)
                        enrich_subtitle_records(subtitle_records, media_index)
                        _extend_directory_records(root_path, child_dirs, media_records, subtitle_records)
                        if recursive:
                            for child_dir in child_dirs:
                                scan_directory(child_dir)
                        if db and is_root:
                            serialized_records = media_records + subtitle_records
                            entries_signature = directory_entries_signature(
                                child_dirs,
                                [record["name"] for record in serialized_records],
                            )
                            child_dirs_json = json.dumps(child_dirs)
                            media_files_json = json.dumps(media_records, default=json_default)
                            subtitle_files_json = json.dumps(subtitle_records, default=json_default)
                            subtree_inventory_json = serialize_root_subtree(
                                inventory,
                                root_path,
                                json_default=json_default,
                            )
                            inventory.root_subtree_snapshot_bytes = len(subtree_inventory_json)
                            db.upsert_directory_state(
                                path=root_path,
                                size=stat_result.st_size,
                                mtime=int(stat_result.st_mtime),
                                child_dirs_json=child_dirs_json,
                                media_files_json=media_files_json,
                                subtitle_files_json=subtitle_files_json,
                                entries_signature=entries_signature,
                                subtree_inventory_json=subtree_inventory_json,
                            )
                            if directory_state_cache is not None:
                                directory_state_cache[root_path] = {
                                    "path": root_path,
                                    "size": stat_result.st_size,
                                    "mtime": int(stat_result.st_mtime),
                                    "child_dirs_json": child_dirs_json,
                                    "media_files_json": media_files_json,
                                    "subtitle_files_json": subtitle_files_json,
                                    "entries_signature": entries_signature,
                                    "subtree_inventory_json": subtree_inventory_json,
                                }
                        return
                    cache_reason_counts["entries_signature_mismatch"] += 1
        else:
            cache_reason_counts["no_cache"] += 1

        cache_misses += 1
        full_directory_scans += 1
        try:
            directory_list_count += 1
            child_dirs, media_records, subtitle_records = scan_directory_records(
                root_path,
                is_subtitle_file_name=is_subtitle_file_name,
                has_video_extension=has_video_extension,
                has_audio_extension=has_audio_extension,
            )
            file_stat_count += len(media_records) + len(subtitle_records)
        except OSError as exc:
            logging.warning("Could not inspect directory %s: %s", root_path, exc)
            return

        _index_media_records(media_records)
        enrich_subtitle_records(subtitle_records, media_index)
        _extend_directory_records(root_path, child_dirs, media_records, subtitle_records)

        if recursive:
            for child_dir in child_dirs:
                scan_directory(child_dir)

        if db:
            serialized_records = media_records + subtitle_records
            entries_signature = directory_entries_signature(
                child_dirs,
                [record["name"] for record in serialized_records],
            )
            child_dirs_json = json.dumps(child_dirs)
            media_files_json = json.dumps(media_records, default=json_default)
            subtitle_files_json = json.dumps(subtitle_records, default=json_default)
            subtree_inventory_json = (
                serialize_root_subtree(inventory, root_path, json_default=json_default)
                if is_root
                else ""
            )
            if subtree_inventory_json:
                inventory.root_subtree_snapshot_bytes = len(subtree_inventory_json)
            db.upsert_directory_state(
                path=root_path,
                size=stat_result.st_size,
                mtime=int(stat_result.st_mtime),
                child_dirs_json=child_dirs_json,
                media_files_json=media_files_json,
                subtitle_files_json=subtitle_files_json,
                entries_signature=entries_signature,
                subtree_inventory_json=subtree_inventory_json,
            )
            if directory_state_cache is not None:
                directory_state_cache[root_path] = {
                    "path": root_path,
                    "size": stat_result.st_size,
                    "mtime": int(stat_result.st_mtime),
                    "child_dirs_json": child_dirs_json,
                    "media_files_json": media_files_json,
                    "subtitle_files_json": subtitle_files_json,
                    "entries_signature": entries_signature,
                    "subtree_inventory_json": subtree_inventory_json,
                }

    for path in root_paths:
        if not path:
            continue
        if os.path.isfile(path):
            try:
                stat_result = os.stat(path)
            except OSError as exc:
                logging.warning("Could not inspect file %s: %s", path, exc)
                continue
            record = build_record(path, file_name=os.path.basename(path), stat_result=stat_result)
            if is_subtitle_file_name(record["name"]):
                enrich_subtitle_records([record], media_index)
                inventory.subtitle_files.append(record)
                inventory.subtitle_files_by_dir[record["dir"]].append(record)
            elif has_video_extension(record["name"]) or has_audio_extension(record["name"]):
                inventory.media_files.append(record)
                _index_media_records([record])
            continue
        if os.path.isdir(path):
            if recursive:
                scan_directory(path, is_root=True)
            else:
                try:
                    directory_list_count += 1
                    child_dirs, media_records, subtitle_records = scan_directory_records(
                        path,
                        is_subtitle_file_name=is_subtitle_file_name,
                        has_video_extension=has_video_extension,
                        has_audio_extension=has_audio_extension,
                    )
                    file_stat_count += len(media_records) + len(subtitle_records)
                except OSError as exc:
                    logging.warning("Could not inspect directory %s: %s", path, exc)
                    continue
                _index_media_records(media_records)
                enrich_subtitle_records(subtitle_records, media_index)
                _extend_directory_records(path, child_dirs, media_records, subtitle_records)

    inventory.cache_hits = cache_hits
    inventory.cache_misses = cache_misses
    inventory.cache_reason_counts = dict(cache_reason_counts)
    inventory.directory_stat_count = directory_stat_count
    inventory.directory_list_count = directory_list_count
    inventory.file_stat_count = file_stat_count
    inventory.quick_signature_checks = quick_signature_checks
    inventory.full_directory_scans = full_directory_scans
    return inventory
