"""Helpers for extracting requested output locations from user prompts.

These helpers keep "execution outputs" (tool-owned roots such as
``output_dir``) separate from "published deliverables" (final CSV/JSON/VCF
artifacts requested by the user). The planner can then preserve both without
inventing undocumented wrapper parameters such as ``output_file``.
"""

from __future__ import annotations

import re
from pathlib import Path

_RAW_PATH_TOKEN = r"(?:~?/[^,\s;\"')]+|(?:workspace|benchmark_data|bio_harness|scripts|docs|tests)/[^,\s;\"')]+)"

_MULTI_OUTPUT_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"write\s+outputs?\s+(?:to|at)\s+(?P<paths>{_RAW_PATH_TOKEN}(?:\s+and\s+{_RAW_PATH_TOKEN})+)",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"(?:write|save|store|place)\s+(?:the\s+)?[^,;]+?\s+(?:to|at)\s+(?P<path1>{_RAW_PATH_TOKEN})\s+and\s+(?:the\s+)?[^,;]+?\s+(?:to|at)\s+(?P<path2>{_RAW_PATH_TOKEN})",
        flags=re.IGNORECASE,
    ),
)

_OUTPUT_ROOT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"write\s+(?:all\s+|intermediate\s+)?(?:outputs?|results?)\s+under\s+(?P<path>{_RAW_PATH_TOKEN})",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"(?:outputs?|results?)\s+under\s+(?P<path>{_RAW_PATH_TOKEN})",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"(?:write\s+)?(?:outputs?|results?)\s+(?:to|at)\s+(?P<path>{_RAW_PATH_TOKEN})",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"output\s+(?:to|at)\s+(?P<path>{_RAW_PATH_TOKEN})",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"\boutput\s+(?P<path>{_RAW_PATH_TOKEN})(?:\s+only\b)?",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"(?:put|place|keep)\s+(?:all\s+)?(?:outputs?|results?)\s+(?:in|under|to|at)\s+(?P<path>{_RAW_PATH_TOKEN})",
        flags=re.IGNORECASE,
    ),
)

_DELIVERABLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"(?:write|save|store)\s+(?:the\s+)?(?:final\s+)?(?:csv|tsv|json|vcf|bam|gtf|results?|file|artifact|deliverable)\s+(?:to|at)\s+(?P<path>{_RAW_PATH_TOKEN})",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"(?:write|save|store)\s+(?:final\s+)?outputs?\s+(?:to|at)\s+(?P<path>{_RAW_PATH_TOKEN})",
        flags=re.IGNORECASE,
    ),
)


def _clean_path_token(value: str) -> str:
    """Return a trimmed path token extracted from prompt text."""

    return str(value or "").strip().strip(",.;")


def _dedupe_paths(paths: list[str]) -> list[str]:
    """Return stable unique path strings."""

    seen: set[str] = set()
    deduped: list[str] = []
    for raw in paths:
        path = _clean_path_token(raw)
        if not path or path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def is_file_like_output_path(path_text: str) -> bool:
    """Return whether one requested output path looks like a file.

    Args:
        path_text: Raw path string extracted from a user request.

    Returns:
        ``True`` when the path appears to target a file-like artifact rather
        than an output directory/root.
    """

    path = Path(str(path_text or "").strip())
    suffix = str(path.suffix or "").strip()
    if suffix:
        return True
    lowered_parts = [part.lower() for part in path.parts]
    return "final" in lowered_parts


def extract_requested_output_paths(user_query: str) -> list[str]:
    """Extract explicit output paths requested by the user.

    Args:
        user_query: Original user request.

    Returns:
        Ordered explicit output paths mentioned in the request. This includes
        both output roots (for example ``write outputs under ...``) and final
        published artifacts (for example ``write the final CSV to ...``).
    """

    text = str(user_query or "")
    found: list[str] = []
    for pattern in _MULTI_OUTPUT_PATH_PATTERNS:
        for match in pattern.finditer(text):
            for group_name in ("paths", "path1", "path2"):
                if group_name not in match.re.groupindex:
                    continue
                paths_text = str(match.group(group_name) or "")
                for path_match in re.finditer(_RAW_PATH_TOKEN, paths_text):
                    path = _clean_path_token(path_match.group(0))
                    if path:
                        found.append(path)
    for pattern in _OUTPUT_ROOT_PATTERNS + _DELIVERABLE_PATTERNS:
        for match in pattern.finditer(text):
            path = _clean_path_token(match.group("path"))
            if path:
                found.append(path)
    return _dedupe_paths(found)


def extract_requested_output_root(user_query: str) -> str:
    """Return the first explicit output root requested by the user.

    Args:
        user_query: Original user request.

    Returns:
        The first directory-like output root requested in phrases such as
        ``write outputs under ...``. File-like final deliverables are ignored.
    """

    text = str(user_query or "")
    for pattern in _OUTPUT_ROOT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        path = _clean_path_token(match.group("path"))
        if path and not is_file_like_output_path(path):
            return path
    return ""


def extract_requested_deliverable_paths(user_query: str) -> list[str]:
    """Return explicit final deliverable paths from one request.

    Args:
        user_query: Original user request.

    Returns:
        Ordered file-like output paths that represent requested published
        deliverables.
    """

    text = str(user_query or "")
    found: list[str] = []
    for pattern in _DELIVERABLE_PATTERNS:
        for match in pattern.finditer(text):
            path = _clean_path_token(match.group("path"))
            if path:
                found.append(path)
    return _dedupe_paths([path for path in found if is_file_like_output_path(path)])
