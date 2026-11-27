"""Shared parsing for configured media roots."""

import os


def parse_roots(spec: str | None) -> list[str]:
    """Parse pipe-delimited roots, preserving spaces inside each path."""
    if not spec:
        return []
    roots: list[str] = []
    seen: set[str] = set()
    for raw_path in str(spec).split("|"):
        path = os.path.expanduser(raw_path.strip())
        if not path:
            continue
        identity = os.path.normcase(os.path.normpath(path))
        if identity in seen:
            continue
        seen.add(identity)
        roots.append(path)
    return roots


def find_skip_marker(path: str, *, is_directory: bool | None = None, marker_name: str = ".subgen_skip") -> str | None:
    """Return the nearest ancestor skip marker applying to a path."""
    if is_directory is None:
        is_directory = os.path.isdir(path)
    current = os.path.abspath(path if is_directory else os.path.dirname(path))
    while True:
        marker = os.path.join(current, marker_name)
        if os.path.exists(marker):
            return marker
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
