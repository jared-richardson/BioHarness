"""Helpers for extracting repo-aware path hints from chat text."""

from __future__ import annotations

import re
from pathlib import Path


_RELATIVE_PATH_PREFIXES = (
    "workspace/",
    "benchmark_data/",
    "bio_harness/",
    "scripts/",
    "docs/",
    "tests/",
    "./",
    "../",
)


def extract_paths_from_text(text: str, project_root: Path | None = None) -> list[str]:
    """Extract absolute and repo-relative paths from chat text.

    Args:
        text: Free-form user text that may contain path hints.
        project_root: Optional repository root used to resolve relative tokens.

    Returns:
        A de-duplicated list of candidate path strings in discovery order.
    """
    root = (project_root or Path.cwd()).resolve()
    seen: set[str] = set()
    discovered: list[str] = []

    def _record(token: str) -> None:
        value = str(token).strip().strip(".,;:!?()[]{}<>\"'`")
        if value and value not in seen:
            seen.add(value)
            discovered.append(value)

    for match in re.findall(r"(?:(?<=^)|(?<=[\s\"'`(]))(/[^ \n\t,;\"')]+)", text or ""):
        _record(match)

    for raw_token in re.split(r"\s+", text or ""):
        token = raw_token.strip().strip(".,;:!?()[]{}<>\"'`")
        if not token or token.startswith("/"):
            continue
        if token.startswith("~"):
            _record(token)
            continue
        if any(token.startswith(prefix) for prefix in _RELATIVE_PATH_PREFIXES):
            resolved = (root / token).resolve(strict=False)
            _record(str(resolved) if resolved.exists() else token)
            continue
        if "/" in token:
            resolved = (root / token).resolve(strict=False)
            if resolved.exists():
                _record(str(resolved))

    return discovered
