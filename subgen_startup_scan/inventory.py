import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass

from .inventory_cache import (
    build_directory_state_payload,
    cached_entries_signature,
    deserialize_directory_state,
    directory_entries_signature,
    materialize_cached_subtree,
    serialize_root_subtree,
    store_directory_state,
    update_directory_state_cache,
)
from .traversal import build_record, list_directory_entries, scan_directory_records


SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".sub", ".ass", ".ssa", ".idx", ".sbv", ".pgs", ".ttml", ".lrc"}


@dataclass
class StartupInventory:
    media_files: list
    subtitle_files: list
    subtitle_files_by_dir: dict
    child_dirs_by_dir: dict


@dataclass
class InventoryBuildState:
    inventory: StartupInventory
    media_index: dict
    cache_reason_counts: dict
    recursive: bool
    db: object | None
    directory_state_cache: dict | None
    has_video_extension: object
    has_audio_extension: object
    enrich_subtitle_records: object
    json_default: object
    cache_hits: int = 0
    cache_misses: int = 0
    directory_stat_count: int = 0
    directory_list_count: int = 0
    file_stat_count: int = 0
    quick_signature_checks: int = 0
    full_directory_scans: int = 0


def is_subtitle_file_name(file_name: str) -> bool:
    return os.path.splitext(file_name)[1].lower() in SUBTITLE_EXTENSIONS


def is_relevant_inventory_file_name(file_name: str, *, has_video_extension, has_audio_extension) -> bool:
    return is_subtitle_file_name(file_name) or has_video_extension(file_name) or has_audio_extension(file_name)


def _preload_directory_state_cache(*, recursive: bool, db) -> dict | None:
    if not (recursive and db and hasattr(db, "get_all_directory_state")):
        return None
    try:
        return {record["path"]: record for record in db.get_all_directory_state()}
    except Exception as exc:
        logging.warning("Could not preload cached directory state: %s", exc)
        return None


def _index_media_records(state: InventoryBuildState, media_records: list[dict]) -> None:
    for record in media_records:
        state.media_index[record["dir"]][record["stem"]] = record["path"]


def _extend_directory_records(
    state: InventoryBuildState,
    directory: str,
    child_dirs: list[str],
    media_records: list[dict],
    subtitle_records: list[dict],
) -> None:
    state.inventory.child_dirs_by_dir[directory].extend(child_dirs)
    state.inventory.media_files.extend(media_records)
    state.inventory.subtitle_files.extend(subtitle_records)
    for subtitle in subtitle_records:
        state.inventory.subtitle_files_by_dir[subtitle["dir"]].append(subtitle)


def _apply_cached_records(
    state: InventoryBuildState,
    directory: str,
    child_dirs: list[str],
    media_records: list[dict],
    subtitle_records: list[dict],
) -> None:
    _index_media_records(state, media_records)
    state.enrich_subtitle_records(subtitle_records, state.media_index)
    _extend_directory_records(state, directory, child_dirs, media_records, subtitle_records)


def _persist_directory_state(
    state: InventoryBuildState,
    root_path: str,
    *,
    stat_result,
    child_dirs: list[str],
    media_records: list[dict],
    subtitle_records: list[dict],
    is_root: bool,
) -> None:
    if not state.db:
        return
    subtree_inventory_json = ""
    if is_root:
        subtree_inventory_json = serialize_root_subtree(
            state.inventory,
            root_path,
            json_default=state.json_default,
        )
        state.inventory.root_subtree_snapshot_bytes = len(subtree_inventory_json)

    payload = build_directory_state_payload(
        path=root_path,
        stat_result=stat_result,
        child_dirs=child_dirs,
        media_records=media_records,
        subtitle_records=subtitle_records,
        subtree_inventory_json=subtree_inventory_json,
        json_default=state.json_default,
    )
    store_directory_state(state.db, payload)
    update_directory_state_cache(state.directory_state_cache, payload)


def _reuse_cached_directory(
    state: InventoryBuildState,
    root_path: str,
    *,
    child_dirs: list[str],
    media_records: list[dict],
    subtitle_records: list[dict],
    subtree_inventory_json: str,
    is_root: bool,
    scan_directory,
    stat_result,
) -> bool:
    if state.recursive and subtree_inventory_json:
        materialize_cached_subtree(
            state.inventory,
            subtree_inventory_json,
            media_index=state.media_index,
            enrich_subtitle_records=state.enrich_subtitle_records,
            index_media_records=lambda records: _index_media_records(state, records),
        )
        return True

    _apply_cached_records(state, root_path, child_dirs, media_records, subtitle_records)
    if state.recursive:
        for child_dir in child_dirs:
            scan_directory(child_dir)
    if is_root:
        _persist_directory_state(
            state,
            root_path,
            stat_result=stat_result,
            child_dirs=child_dirs,
            media_records=media_records,
            subtitle_records=subtitle_records,
            is_root=True,
        )
    return True


def _try_stat_match_reuse(
    state: InventoryBuildState,
    root_path: str,
    *,
    cached_state: dict,
    child_dirs: list[str],
    media_records: list[dict],
    subtitle_records: list[dict],
    stat_result,
    is_root: bool,
    scan_directory,
) -> bool:
    if cached_state.get("size") != stat_result.st_size or cached_state.get("mtime") != int(stat_result.st_mtime):
        return False
    state.cache_hits += 1
    state.cache_reason_counts["stat_match"] += 1
    return _reuse_cached_directory(
        state,
        root_path,
        child_dirs=child_dirs,
        media_records=media_records,
        subtitle_records=subtitle_records,
        subtree_inventory_json=cached_state.get("subtree_inventory_json") or "",
        is_root=is_root,
        scan_directory=scan_directory,
        stat_result=stat_result,
    )


def _try_signature_match_reuse(
    state: InventoryBuildState,
    root_path: str,
    *,
    cached_state: dict,
    child_dirs: list[str],
    media_records: list[dict],
    subtitle_records: list[dict],
    stat_result,
    is_root: bool,
    scan_directory,
) -> bool:
    try:
        state.quick_signature_checks += 1
        state.directory_list_count += 1
        listed_child_dirs, listed_file_names = list_directory_entries(
            root_path,
            is_relevant_inventory_file_name=lambda file_name: is_relevant_inventory_file_name(
                file_name,
                has_video_extension=state.has_video_extension,
                has_audio_extension=state.has_audio_extension,
            ),
        )
    except OSError as exc:
        logging.warning("Could not inspect directory %s: %s", root_path, exc)
        state.cache_reason_counts["list_error"] += 1
        return False

    current_signature = directory_entries_signature(listed_child_dirs, listed_file_names)
    cached_signature = cached_entries_signature(cached_state, child_dirs, media_records, subtitle_records)
    if current_signature != cached_signature:
        state.cache_reason_counts["entries_signature_mismatch"] += 1
        return False

    state.cache_hits += 1
    state.cache_reason_counts["entries_signature_match"] += 1
    return _reuse_cached_directory(
        state,
        root_path,
        child_dirs=child_dirs,
        media_records=media_records,
        subtitle_records=subtitle_records,
        subtree_inventory_json=cached_state.get("subtree_inventory_json") or "",
        is_root=is_root,
        scan_directory=scan_directory,
        stat_result=stat_result,
    )


def _scan_directory_full(
    state: InventoryBuildState,
    root_path: str,
    *,
    stat_result,
    is_root: bool,
    scan_directory,
) -> None:
    state.cache_misses += 1
    state.full_directory_scans += 1
    try:
        state.directory_list_count += 1
        child_dirs, media_records, subtitle_records = scan_directory_records(
            root_path,
            is_subtitle_file_name=is_subtitle_file_name,
            has_video_extension=state.has_video_extension,
            has_audio_extension=state.has_audio_extension,
        )
        state.file_stat_count += len(media_records) + len(subtitle_records)
    except OSError as exc:
        logging.warning("Could not inspect directory %s: %s", root_path, exc)
        return

    _apply_cached_records(state, root_path, child_dirs, media_records, subtitle_records)
    if state.recursive:
        for child_dir in child_dirs:
            scan_directory(child_dir)
    _persist_directory_state(
        state,
        root_path,
        stat_result=stat_result,
        child_dirs=child_dirs,
        media_records=media_records,
        subtitle_records=subtitle_records,
        is_root=is_root,
    )


def _scan_single_file(state: InventoryBuildState, path: str) -> None:
    try:
        stat_result = os.stat(path)
    except OSError as exc:
        logging.warning("Could not inspect file %s: %s", path, exc)
        return
    record = build_record(path, file_name=os.path.basename(path), stat_result=stat_result)
    if is_subtitle_file_name(record["name"]):
        state.enrich_subtitle_records([record], state.media_index)
        state.inventory.subtitle_files.append(record)
        state.inventory.subtitle_files_by_dir[record["dir"]].append(record)
    elif state.has_video_extension(record["name"]) or state.has_audio_extension(record["name"]):
        state.inventory.media_files.append(record)
        _index_media_records(state, [record])


def _scan_non_recursive_directory(state: InventoryBuildState, path: str) -> None:
    try:
        state.directory_list_count += 1
        child_dirs, media_records, subtitle_records = scan_directory_records(
            path,
            is_subtitle_file_name=is_subtitle_file_name,
            has_video_extension=state.has_video_extension,
            has_audio_extension=state.has_audio_extension,
        )
        state.file_stat_count += len(media_records) + len(subtitle_records)
    except OSError as exc:
        logging.warning("Could not inspect directory %s: %s", path, exc)
        return
    _apply_cached_records(state, path, child_dirs, media_records, subtitle_records)


def _finalize_inventory(state: InventoryBuildState) -> StartupInventory:
    state.inventory.cache_hits = state.cache_hits
    state.inventory.cache_misses = state.cache_misses
    state.inventory.cache_reason_counts = dict(state.cache_reason_counts)
    state.inventory.directory_stat_count = state.directory_stat_count
    state.inventory.directory_list_count = state.directory_list_count
    state.inventory.file_stat_count = state.file_stat_count
    state.inventory.quick_signature_checks = state.quick_signature_checks
    state.inventory.full_directory_scans = state.full_directory_scans
    return state.inventory


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
    state = InventoryBuildState(
        inventory=StartupInventory(
            media_files=[],
            subtitle_files=[],
            subtitle_files_by_dir=defaultdict(list),
            child_dirs_by_dir=defaultdict(list),
        ),
        media_index=defaultdict(dict),
        cache_reason_counts=defaultdict(int),
        recursive=recursive,
        db=db,
        directory_state_cache=_preload_directory_state_cache(recursive=recursive, db=db),
        has_video_extension=has_video_extension,
        has_audio_extension=has_audio_extension,
        enrich_subtitle_records=enrich_subtitle_records,
        json_default=json_default,
    )

    def scan_directory(root_path: str, *, is_root: bool = False) -> None:
        try:
            stat_result = os.stat(root_path)
            state.directory_stat_count += 1
        except OSError as exc:
            logging.warning("Could not inspect directory %s: %s", root_path, exc)
            return

        cached_state = None
        if state.db:
            cached_state = (
                state.directory_state_cache.get(root_path)
                if state.directory_state_cache is not None
                else state.db.get_directory_state(root_path)
            )

        if cached_state:
            try:
                child_dirs, media_records, subtitle_records = deserialize_directory_state(cached_state)
            except json.JSONDecodeError:
                state.cache_reason_counts["decode_error"] += 1
            else:
                if _try_stat_match_reuse(
                    state,
                    root_path,
                    cached_state=cached_state,
                    child_dirs=child_dirs,
                    media_records=media_records,
                    subtitle_records=subtitle_records,
                    stat_result=stat_result,
                    is_root=is_root,
                    scan_directory=scan_directory,
                ):
                    return
                if _try_signature_match_reuse(
                    state,
                    root_path,
                    cached_state=cached_state,
                    child_dirs=child_dirs,
                    media_records=media_records,
                    subtitle_records=subtitle_records,
                    stat_result=stat_result,
                    is_root=is_root,
                    scan_directory=scan_directory,
                ):
                    return
        else:
            state.cache_reason_counts["no_cache"] += 1

        _scan_directory_full(
            state,
            root_path,
            stat_result=stat_result,
            is_root=is_root,
            scan_directory=scan_directory,
        )

    for path in root_paths:
        if not path:
            continue
        if os.path.isfile(path):
            _scan_single_file(state, path)
            continue
        if os.path.isdir(path):
            if state.recursive:
                scan_directory(path, is_root=True)
            else:
                _scan_non_recursive_directory(state, path)

    return _finalize_inventory(state)
