"""
conftest.py — patches heavy ML/AV dependencies before any test module imports subgen.

The mock setup must happen here, at collection time, before subgen.py is imported.
subgen.py starts worker threads at import time; they're daemon threads and are harmless.
"""
import os
import tempfile
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Mock heavy dependencies that are not installed in CI
# ---------------------------------------------------------------------------
_MOCKED_MODULES = [
    "stable_whisper",
    "faster_whisper",
    "torch",
    "av",
    "ffmpeg",
    "watchdog",
    "watchdog.observers",
    "watchdog.observers.polling",
    "watchdog.events",
    "numpy",
]

for _mod in _MOCKED_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Keep test DB writes out of /subgen, which is not writable in this environment.
_TEST_DB_DIR = os.path.join(tempfile.gettempdir(), "subgen-tests")
os.environ.setdefault("STARTUP_SCAN_DB_PATH", os.path.join(_TEST_DB_DIR, "subgen_scan.db"))

# Give the version mocks a usable string so /status doesn't blow up
sys.modules["stable_whisper"].__version__ = "1.0.0"
sys.modules["faster_whisper"].__version__ = "1.0.0"

# Ensure sub-attribute imports work
# e.g. `from stable_whisper import Segment`
sys.modules["stable_whisper"].Segment = MagicMock()

# Ensure watchdog attribute imports work
# e.g. `from watchdog.observers.polling import PollingObserver as Observer`
sys.modules["watchdog.observers.polling"].PollingObserver = MagicMock()
# e.g. `from watchdog.events import FileSystemEventHandler`
sys.modules["watchdog.events"].FileSystemEventHandler = object


class _MockResponse:
    def __init__(self, status_code=200, json_data=None, content=None):
        self.status_code = status_code
        self._json_data = json_data
        if content is not None:
            self.content = content
        elif json_data is not None:
            import json

            self.content = json.dumps(json_data).encode("utf-8")
        else:
            self.content = b""

    def json(self):
        if self._json_data is not None:
            return self._json_data
        import json

        return json.loads(self.content.decode("utf-8"))


class _RequestsMock:
    def __init__(self):
        self.routes = {}
        self.request_history = []

    def get(self, url, **kwargs):
        self.routes[("GET", url)] = kwargs
        return None

    def post(self, url, **kwargs):
        self.routes[("POST", url)] = kwargs
        return None

    def _dispatch(self, method, url, **kwargs):
        self.request_history.append(SimpleNamespace(url=url, method=method, **kwargs))
        route = self.routes.get((method, url))
        if route is None:
            return _MockResponse(status_code=404)

        return _MockResponse(
            status_code=route.get("status_code", 200),
            json_data=route.get("json"),
            content=route.get("content"),
        )


import pytest


@pytest.fixture
def requests_mock(monkeypatch):
    mocker = _RequestsMock()
    import requests

    monkeypatch.setattr(requests, "get", lambda url, **kwargs: mocker._dispatch("GET", url, **kwargs))
    monkeypatch.setattr(requests, "post", lambda url, **kwargs: mocker._dispatch("POST", url, **kwargs))
    return mocker
