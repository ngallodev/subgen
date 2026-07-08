import os
from typing import Any, Callable


def subtitle_type_for_name(file_name: str) -> str:
    return "generated" if "subgen" in os.path.splitext(file_name)[0].split(".") else "external"


def guess_subtitle_language(filename: str, *, from_string: Callable[[str], Any]):
    stem = os.path.splitext(filename)[0]
    for part in reversed(stem.split(".")):
        language = from_string(part)
        if getattr(language, "to_iso_639_2_t", None):
            return language
    return None


def guess_media_path(subtitle_path: str, media_index: dict) -> str | None:
    subtitle_dir = os.path.dirname(subtitle_path)
    stem_parts = os.path.splitext(os.path.basename(subtitle_path))[0].split(".")

    while True:
        candidate_stem = ".".join(stem_parts)
        search_dir = subtitle_dir
        while True:
            directory_index = media_index.get(search_dir, {})
            if candidate_stem in directory_index:
                return directory_index[candidate_stem]

            parent_dir = os.path.dirname(search_dir)
            if parent_dir == search_dir:
                break
            search_dir = parent_dir

        if not stem_parts:
            break
        stem_parts.pop()

    return None


def enrich_subtitle_records(
    subtitle_records: list[dict],
    media_index: dict,
    *,
    from_string: Callable[[str], Any],
) -> None:
    for subtitle_record in subtitle_records:
        file_name = subtitle_record.get("name") or os.path.basename(subtitle_record["path"])
        language = guess_subtitle_language(file_name, from_string=from_string)
        subtitle_record["subtitle_type"] = subtitle_type_for_name(file_name)
        subtitle_record["language"] = language.to_iso_639_2_t() if language else None
        subtitle_record["media_path"] = guess_media_path(subtitle_record["path"], media_index)


def current_sidecar_state(subtitle_rows: list[dict[str, Any]]) -> str:
    if not subtitle_rows:
        return "none"
    if any(row.get("subtitle_type") == "generated" for row in subtitle_rows):
        return "generated"
    return "external"
