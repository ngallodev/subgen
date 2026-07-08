import hashlib
import json
import os


def directory_entries_signature(child_dirs: list[str], file_names: list[str]) -> str:
    payload = "\n".join(
        sorted([f"d:{os.path.basename(path)}" for path in child_dirs] + [f"f:{name}" for name in file_names])
    ).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def cached_entries_signature(cached_state: dict, child_dirs: list[str], media_records: list[dict], subtitle_records: list[dict]) -> str:
    signature = cached_state.get("entries_signature")
    if signature:
        return signature
    return directory_entries_signature(
        child_dirs,
        [record.get("name") or os.path.basename(record["path"]) for record in media_records + subtitle_records],
    )


def path_is_under_root(path: str, root_path: str) -> bool:
    normalized_path = os.path.normcase(path)
    normalized_root = os.path.normcase(root_path)
    return normalized_path == normalized_root or normalized_path.startswith(normalized_root + os.sep)


def serialize_root_subtree(inventory, root_path: str, *, json_default) -> str:
    subtree_media_records = [record for record in inventory.media_files if path_is_under_root(record["path"], root_path)]
    subtree_subtitle_records = [record for record in inventory.subtitle_files if path_is_under_root(record["path"], root_path)]
    subtree_child_dirs_by_dir = {
        directory: list(child_dirs)
        for directory, child_dirs in inventory.child_dirs_by_dir.items()
        if path_is_under_root(directory, root_path)
    }
    return json.dumps(
        {
            "media_records": subtree_media_records,
            "subtitle_records": subtree_subtitle_records,
            "child_dirs_by_dir": subtree_child_dirs_by_dir,
        },
        default=json_default,
    )


def materialize_cached_subtree(
    inventory,
    subtree_inventory_json: str,
    *,
    media_index: dict,
    enrich_subtitle_records,
    index_media_records,
) -> None:
    subtree_state = json.loads(subtree_inventory_json)
    media_records = subtree_state.get("media_records") or []
    subtitle_records = subtree_state.get("subtitle_records") or []
    child_dirs_by_dir = subtree_state.get("child_dirs_by_dir") or {}
    inventory.root_subtree_cache_used = getattr(inventory, "root_subtree_cache_used", 0) + 1
    inventory.root_subtree_snapshot_bytes = max(
        getattr(inventory, "root_subtree_snapshot_bytes", 0),
        len(subtree_inventory_json),
    )
    index_media_records(media_records)
    enrich_subtitle_records(subtitle_records, media_index)
    inventory.media_files.extend(media_records)
    inventory.subtitle_files.extend(subtitle_records)
    for directory, child_dirs in child_dirs_by_dir.items():
        inventory.child_dirs_by_dir[directory].extend(child_dirs)
    for subtitle in subtitle_records:
        inventory.subtitle_files_by_dir[subtitle["dir"]].append(subtitle)
