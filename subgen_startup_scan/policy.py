import hashlib
import json


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
) -> dict:
    return {
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
    }


def startup_scan_policy_signature(**policy_kwargs) -> str:
    policy = startup_scan_policy_payload(**policy_kwargs)
    return hashlib.sha1(json.dumps(policy, sort_keys=True).encode("utf-8")).hexdigest()
