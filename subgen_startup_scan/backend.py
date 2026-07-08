from dataclasses import dataclass
from typing import Protocol, Any

from .config import get_startup_scan_backend_name
from .dependencies import StartupScanDependencies
from .service import startup_scan_existing as run_startup_scan_existing


class StartupScanBackend(Protocol):
    def name(self) -> str: ...
    def initialize(self) -> None: ...
    def startup_scan_existing(self, transcribe_folder_spec: str) -> None: ...
    def refresh_processed_file(self, path: str, transcription_type: str, force_language: Any, audio_tracks=None) -> None: ...


@dataclass
class PersistentStartupScanBackend:
    deps: StartupScanDependencies

    def name(self) -> str:
        return "persistent"

    def initialize(self) -> None:
        if self.deps.persistent_initialize:
            self.deps.persistent_initialize()

    def startup_scan_existing(self, transcribe_folder_spec: str) -> None:
        if self.deps.persistent_startup_scan_existing:
            self.deps.persistent_startup_scan_existing(transcribe_folder_spec)
            return
        run_startup_scan_existing(self.deps, transcribe_folder_spec)

    def refresh_processed_file(self, path: str, transcription_type: str, force_language: Any, audio_tracks=None) -> None:
        if self.deps.persistent_refresh_processed_file:
            self.deps.persistent_refresh_processed_file(path, transcription_type, force_language, audio_tracks)


@dataclass
class LegacyStartupScanBackend:
    deps: StartupScanDependencies

    def name(self) -> str:
        return "legacy"

    def initialize(self) -> None:
        return None

    def startup_scan_existing(self, transcribe_folder_spec: str) -> None:
        if self.deps.legacy_startup_scan_existing:
            self.deps.legacy_startup_scan_existing(transcribe_folder_spec)

    def refresh_processed_file(self, path: str, transcription_type: str, force_language: Any, audio_tracks=None) -> None:
        return None


def build_startup_scan_backend(deps: StartupScanDependencies, default: str = "persistent") -> StartupScanBackend:
    backend_name = get_startup_scan_backend_name(default=default)
    if backend_name == "legacy":
        return LegacyStartupScanBackend(deps)
    return PersistentStartupScanBackend(deps)
