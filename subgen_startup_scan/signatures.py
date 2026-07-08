import hashlib
import logging
import os
from bisect import bisect_left
from collections import defaultdict

from .inventory import StartupInventory, is_subtitle_file_name


def startup_scan_flat_manifest(root_paths, *, has_video_extension, has_audio_extension):
    file_paths = []
    media_entries = []
    subtitle_entries = []
    media_count = 0
    subtitle_count = 0
    for path in root_paths:
        if not path:
            continue
        if os.path.isfile(path):
            file_paths.append(path)
            name = os.path.basename(path)
            try:
                stat_result = os.stat(path)
            except OSError as exc:
                logging.warning("Could not inspect file %s: %s", path, exc)
                continue
            if is_subtitle_file_name(name):
                subtitle_entries.append(f"{path}:{stat_result.st_size}:{int(stat_result.st_mtime)}")
                subtitle_count += 1
            elif has_video_extension(name) or has_audio_extension(name):
                media_entries.append(f"{path}:{stat_result.st_size}:{int(stat_result.st_mtime)}")
                media_count += 1
            continue
        if not os.path.isdir(path):
            continue
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        file_paths.append(entry.path)
                        if is_subtitle_file_name(entry.name):
                            stat_result = entry.stat(follow_symlinks=False)
                            subtitle_entries.append(f"{entry.path}:{stat_result.st_size}:{int(stat_result.st_mtime)}")
                            subtitle_count += 1
                        elif has_video_extension(entry.name) or has_audio_extension(entry.name):
                            stat_result = entry.stat(follow_symlinks=False)
                            media_entries.append(f"{entry.path}:{stat_result.st_size}:{int(stat_result.st_mtime)}")
                            media_count += 1
                    except OSError as exc:
                        logging.warning("Could not inspect %s: %s", entry.path, exc)
        except OSError as exc:
            logging.warning("Could not inspect directory %s: %s", path, exc)
    manifest = hashlib.sha1("\n".join(sorted(media_entries + subtitle_entries)).encode("utf-8")).hexdigest()
    media_manifest = hashlib.sha1("\n".join(sorted(media_entries)).encode("utf-8")).hexdigest()
    subtitle_manifest = hashlib.sha1("\n".join(sorted(subtitle_entries)).encode("utf-8")).hexdigest()
    return {
        "manifest": manifest,
        "media_manifest": media_manifest,
        "subtitle_manifest": subtitle_manifest,
        "file_count": len(file_paths),
        "media_count": media_count,
        "subtitle_count": subtitle_count,
    }


def startup_scan_collect_flat_media_records(root_paths, *, has_video_extension, has_audio_extension):
    media_records = []
    for path in root_paths:
        if not path:
            continue
        if os.path.isfile(path):
            try:
                stat_result = os.stat(path)
            except OSError as exc:
                logging.warning("Could not inspect file %s: %s", path, exc)
                continue
            name = os.path.basename(path)
            if has_video_extension(name) or has_audio_extension(name):
                media_records.append({
                    "path": path,
                    "name": name,
                    "dir": os.path.dirname(path),
                    "stem": os.path.splitext(name)[0],
                    "size": stat_result.st_size,
                    "mtime": int(stat_result.st_mtime),
                })
            continue
        if not os.path.isdir(path):
            continue
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        if not (has_video_extension(entry.name) or has_audio_extension(entry.name)):
                            continue
                        stat_result = entry.stat(follow_symlinks=False)
                        media_records.append({
                            "path": entry.path,
                            "name": entry.name,
                            "dir": os.path.dirname(entry.path),
                            "stem": os.path.splitext(entry.name)[0],
                            "size": stat_result.st_size,
                            "mtime": int(stat_result.st_mtime),
                        })
                    except OSError as exc:
                        logging.warning("Could not inspect %s: %s", entry.path, exc)
        except OSError as exc:
            logging.warning("Could not inspect directory %s: %s", path, exc)
    return media_records


def startup_scan_build_flat_inventory(media_records: list, subtitle_records: list):
    inventory = StartupInventory(
        media_files=media_records,
        subtitle_files=subtitle_records,
        subtitle_files_by_dir=defaultdict(list),
        child_dirs_by_dir=defaultdict(list),
    )
    for subtitle in subtitle_records:
        inventory.subtitle_files_by_dir[subtitle["dir"]].append(subtitle)
    return inventory


def startup_scan_inventory_signature(inventory: StartupInventory, policy_signature: str):
    media_entries = [f"{record['path']}:{record['size']}:{record['mtime']}" for record in inventory.media_files]
    subtitle_entries = [f"{record['path']}:{record['size']}:{record['mtime']}" for record in inventory.subtitle_files]
    return {
        "file_count": len(media_entries) + len(subtitle_entries),
        "manifest": hashlib.sha1("\n".join(sorted(media_entries + subtitle_entries)).encode("utf-8")).hexdigest(),
        "media_manifest": hashlib.sha1("\n".join(sorted(media_entries)).encode("utf-8")).hexdigest(),
        "subtitle_manifest": hashlib.sha1("\n".join(sorted(subtitle_entries)).encode("utf-8")).hexdigest(),
        "media_count": len(media_entries),
        "subtitle_count": len(subtitle_entries),
        "policy_signature": policy_signature,
    }


def startup_scan_store_inventory_signature(db, signature: dict) -> None:
    db.set_meta("flat_inventory_manifest", signature["manifest"])
    db.set_meta("flat_inventory_media_manifest", signature["media_manifest"])
    db.set_meta("flat_inventory_subtitle_manifest", signature["subtitle_manifest"])
    db.set_meta("flat_inventory_media_count", str(signature["media_count"]))
    db.set_meta("flat_inventory_subtitle_count", str(signature["subtitle_count"]))
    db.set_meta("flat_inventory_policy_signature", signature["policy_signature"])


def startup_scan_inventory_signature_matches(db, signature: dict) -> bool:
    return (
        db.get_meta("flat_inventory_manifest") == signature["manifest"]
        and db.get_meta("flat_inventory_media_manifest") == signature["media_manifest"]
        and db.get_meta("flat_inventory_subtitle_manifest") == signature["subtitle_manifest"]
        and db.get_meta("flat_inventory_policy_signature") == signature["policy_signature"]
    )


def _gather_subtitles_for_dir(directory: str, inventory: StartupInventory, cache: dict):
    if directory in cache:
        return cache[directory]
    subtitle_files_by_dir = getattr(inventory, "subtitle_files_by_dir", None)
    if subtitle_files_by_dir is None:
        subtitle_files_by_dir = defaultdict(list)
        for subtitle in getattr(inventory, "subtitle_files", []):
            subtitle_dir = subtitle.get("dir") or os.path.dirname(subtitle.get("path", ""))
            subtitle_stem = subtitle.get("stem")
            if subtitle_stem is None:
                subtitle_stem = os.path.splitext(os.path.basename(subtitle.get("path", "")))[0]
            subtitle_files_by_dir[subtitle_dir].append({**subtitle, "dir": subtitle_dir, "stem": subtitle_stem})
    records = list(subtitle_files_by_dir.get(directory, []))
    for child_dir in getattr(inventory, "child_dirs_by_dir", {}).get(directory, []):
        _, child_records = _gather_subtitles_for_dir(child_dir, inventory, cache)
        records.extend(child_records)
    records.sort(key=lambda record: record["stem"])
    stems = [record["stem"] for record in records]
    cache[directory] = (stems, records)
    return cache[directory]


def compute_subtitle_signature(media_path: str, inventory: StartupInventory, cache: dict):
    media_dir = os.path.dirname(media_path)
    media_stem = os.path.splitext(os.path.basename(media_path))[0]
    subtitle_stems, subtitle_records = _gather_subtitles_for_dir(media_dir, inventory, cache)
    start_index = bisect_left(subtitle_stems, media_stem)
    matching = []
    for index in range(start_index, len(subtitle_stems)):
        subtitle_stem = subtitle_stems[index]
        if not subtitle_stem.startswith(media_stem):
            break
        subtitle = subtitle_records[index]
        matching.append(f"{subtitle['path']}:{subtitle['size']}:{subtitle['mtime']}")
    payload = "\n".join(sorted(matching)).encode("utf-8")
    return hashlib.sha1(payload).hexdigest(), matching
