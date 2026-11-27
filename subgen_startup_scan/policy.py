import hashlib
import json
from typing import Any, cast


def startup_scan_policy_payload(
    *,
    transcribe_or_translate: str,
    lrc_for_audio_files: bool,
    skip_unknown_language: bool,
    skip_if_no_audio_language_but_subtitles_exist: bool,
    limit_to_preferred_audio_languages: bool,
    preferred_audio_languages: list[str],
    skip_audio_languages: list[str],
    skip_if_target_subtitle_exists: bool,
    subtitle_language_name: str,
    skip_if_internal_sub_language: str,
    skip_subtitle_languages: list[str],
    skip_if_external_sub_exists: bool,
    only_match_subgen_subtitles: bool,
    force_detected_language_to: str,
    should_whisper_detect_audio_language: bool,
    ignore_forced_subtitles: bool = True,
    subtitle_language_naming_type: str = "ISO_639_2_B",
    show_in_subname_subgen: bool = True,
    show_in_subname_model: bool = True,
    whisper_model: str = "",
    decision_cache_version: int = 2,
) -> dict:
    return {
        # Bump when the cache starts reusing a new class of decisions.
        "decision_cache_version": decision_cache_version,
        "transcribe_or_translate": transcribe_or_translate,
        "lrc_for_audio_files": lrc_for_audio_files,
        "skip_unknown_language": skip_unknown_language,
        "skip_if_no_audio_language_but_subtitles_exist": skip_if_no_audio_language_but_subtitles_exist,
        "limit_to_preferred_audio_languages": limit_to_preferred_audio_languages,
        "preferred_audio_languages": preferred_audio_languages,
        "skip_audio_languages": skip_audio_languages,
        "skip_if_target_subtitle_exists": skip_if_target_subtitle_exists,
        "subtitle_language_name": subtitle_language_name,
        "skip_if_internal_sub_language": skip_if_internal_sub_language,
        "skip_subtitle_languages": skip_subtitle_languages,
        "skip_if_external_sub_exists": skip_if_external_sub_exists,
        "only_match_subgen_subtitles": only_match_subgen_subtitles,
        "force_detected_language_to": force_detected_language_to,
        "should_whisper_detect_audio_language": should_whisper_detect_audio_language,
        "ignore_forced_subtitles": ignore_forced_subtitles,
        "subtitle_language_naming_type": subtitle_language_naming_type,
        "show_in_subname_subgen": show_in_subname_subgen,
        "show_in_subname_model": show_in_subname_model,
        "whisper_model": whisper_model,
    }


def startup_scan_policy_signature(**policy_kwargs) -> str:
    policy = startup_scan_policy_payload(**policy_kwargs)
    return hashlib.sha1(json.dumps(policy, sort_keys=True).encode("utf-8")).hexdigest()


def startup_scan_policy_benchmark_fields(policy: dict[str, Any]) -> dict[str, object]:
    return {
        "decision_cache_version": policy["decision_cache_version"],
        "transcribe_or_translate": policy["transcribe_or_translate"],
        "lrc_for_audio_files": policy["lrc_for_audio_files"],
        "skip_unknown_language": policy["skip_unknown_language"],
        "skip_if_no_audio_language_but_subtitles_exist": policy["skip_if_no_audio_language_but_subtitles_exist"],
        "limit_to_preferred_audio_languages": policy["limit_to_preferred_audio_languages"],
        "preferred_audio_languages": list(cast(list[str], policy["preferred_audio_languages"])),
        "skip_audio_languages": list(cast(list[str], policy["skip_audio_languages"])),
        "skip_if_target_subtitle_exists": policy["skip_if_target_subtitle_exists"],
        "subtitle_language_name": policy["subtitle_language_name"],
        "skip_if_internal_sub_language": policy["skip_if_internal_sub_language"],
        "skip_subtitle_languages": list(cast(list[str], policy["skip_subtitle_languages"])),
        "skip_if_external_sub_exists": policy["skip_if_external_sub_exists"],
        "only_match_subgen_subtitles": policy["only_match_subgen_subtitles"],
        "force_detected_language_to": policy["force_detected_language_to"],
        "should_whisper_detect_audio_language": policy["should_whisper_detect_audio_language"],
        "ignore_forced_subtitles": policy["ignore_forced_subtitles"],
        "subtitle_language_naming_type": policy["subtitle_language_naming_type"],
        "show_in_subname_subgen": policy["show_in_subname_subgen"],
        "show_in_subname_model": policy["show_in_subname_model"],
        "whisper_model": policy["whisper_model"],
    }
