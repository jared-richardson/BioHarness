"""Helpers for selecting directory roots from user-supplied path hints."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


def discovery_root_for_path(path: Path) -> Path:
    """Return the directory root that should back discovery for one path.

    Args:
        path: Resolved candidate path from chat or UI state.

    Returns:
        The path itself when it is a directory, otherwise the containing
        directory for existing files.
    """
    try:
        if path.exists() and path.is_file():
            return path.parent
    except OSError:
        return path
    return path


def select_preferred_latest_root(
    candidates: list[Path],
    *,
    fastq_counter: Callable[[Path], int],
) -> tuple[Path | None, int]:
    """Select the best latest-message root while preserving explicit intent.

    Args:
        candidates: Canonical candidate paths from the latest user message.
        fastq_counter: Callback that counts FASTQ files for one candidate root.

    Returns:
        Tuple of ``(best_root, fastq_count)``. When explicit paths exist but do
        not contain FASTQ files, this still returns the latest usable root with
        count ``0`` instead of falling back to older chat context.
    """
    best_root: Path | None = None
    best_count = -1
    for candidate in candidates:
        root = discovery_root_for_path(candidate)
        count = int(fastq_counter(root))
        if count > best_count:
            best_count = count
            best_root = root
    if best_root is None:
        return None, -1
    return best_root, max(0, best_count)


def latest_path_hints_from_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    extractor: Callable[[str], list[str]],
) -> list[str]:
    """Return paths from the most recent user message that actually mentions them.

    Args:
        messages: Recent chat transcript messages.
        extractor: Path extraction callback for message content.

    Returns:
        Candidate path strings from the most recent user message containing at
        least one path hint, or an empty list when none are present.
    """
    for message in reversed(list(messages or [])):
        if str(message.get("role", "")).strip().lower() != "user":
            continue
        paths = extractor(str(message.get("content", "")))
        if paths:
            return paths
    return []
