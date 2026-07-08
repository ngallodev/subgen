from .inventory import collect_startup_inventory as package_collect_startup_inventory
from .inventory import is_relevant_inventory_file_name as package_is_relevant_inventory_file_name
from .inventory import is_subtitle_file_name as package_is_subtitle_file_name
from .signatures import (
    startup_scan_build_flat_inventory as package_startup_scan_build_flat_inventory,
    startup_scan_collect_flat_media_records as package_startup_scan_collect_flat_media_records,
    startup_scan_flat_manifest as package_startup_scan_flat_manifest,
    startup_scan_inventory_signature as package_startup_scan_inventory_signature,
    startup_scan_inventory_signature_matches as package_startup_scan_inventory_signature_matches,
    startup_scan_store_inventory_signature as package_startup_scan_store_inventory_signature,
)


def is_subtitle_file_name(file_name: str) -> bool:
    return package_is_subtitle_file_name(file_name)


def is_relevant_inventory_file_name(file_name: str, *, has_video_extension, has_audio_extension) -> bool:
    return package_is_relevant_inventory_file_name(
        file_name,
        has_video_extension=has_video_extension,
        has_audio_extension=has_audio_extension,
    )


def collect_startup_inventory(
    root_paths,
    *,
    recursive: bool = False,
    db=None,
    has_video_extension,
    has_audio_extension,
    enrich_subtitle_records,
    json_default,
):
    return package_collect_startup_inventory(
        root_paths,
        recursive=recursive,
        db=db,
        has_video_extension=has_video_extension,
        has_audio_extension=has_audio_extension,
        enrich_subtitle_records=enrich_subtitle_records,
        json_default=json_default,
    )


def startup_scan_flat_manifest(root_paths, *, has_video_extension, has_audio_extension):
    return package_startup_scan_flat_manifest(
        root_paths,
        has_video_extension=has_video_extension,
        has_audio_extension=has_audio_extension,
    )


def startup_scan_collect_flat_media_records(root_paths, *, has_video_extension, has_audio_extension):
    return package_startup_scan_collect_flat_media_records(
        root_paths,
        has_video_extension=has_video_extension,
        has_audio_extension=has_audio_extension,
    )


def startup_scan_build_flat_inventory(media_records: list, subtitle_records: list):
    return package_startup_scan_build_flat_inventory(media_records, subtitle_records)


def startup_scan_inventory_signature(inventory, policy_signature: str):
    return package_startup_scan_inventory_signature(inventory, policy_signature)


def startup_scan_store_inventory_signature(db, signature: dict) -> None:
    package_startup_scan_store_inventory_signature(db, signature)


def startup_scan_inventory_signature_matches(db, signature: dict) -> bool:
    return package_startup_scan_inventory_signature_matches(db, signature)
