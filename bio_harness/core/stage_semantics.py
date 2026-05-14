"""Generic runtime stage semantics for artifact-aware plan validation.

This module intentionally avoids importing the tool registry, wrapper
contracts, or orchestrator code at module import time so it can be reused by
multiple validation layers without introducing import cycles.
"""

from __future__ import annotations

import re
from pathlib import Path

_ALLOWED_STAGES: frozenset[str] = frozenset(
    {
        "assembled",
        "aligned",
        "raw",
        "filtered",
        "subtracted",
        "annotated",
        "normalized",
        "shared",
        "counts",
        "expression",
        "indexed",
    }
)

_COMPRESSION_SUFFIXES: tuple[str, ...] = (".bgz", ".gz")
_TERMINAL_SUFFIXES: tuple[str, ...] = (
    ".vcf",
    ".bam",
    ".csv",
    ".tsv",
    ".fasta",
    ".fa",
    ".fna",
)
_STAGE_SUFFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\.annotated$", re.IGNORECASE),
    re.compile(r"\.normalized$", re.IGNORECASE),
    re.compile(r"_raw$", re.IGNORECASE),
    re.compile(r"_filtered$", re.IGNORECASE),
    re.compile(r"_subtracted$", re.IGNORECASE),
    re.compile(r"\.sorted$", re.IGNORECASE),
)


def _strip_one_suffix(text: str, suffixes: tuple[str, ...]) -> str:
    """Strip one matching suffix from *text*.

    Args:
        text: Source string to trim.
        suffixes: Candidate suffixes in matching order.

    Returns:
        Source text with at most one suffix removed.
    """

    lowered = text.lower()
    for suffix in suffixes:
        if lowered.endswith(suffix):
            return text[: -len(suffix)]
    return text


def classify_path_stage(path: str) -> str | None:
    """Infer a generic artifact stage from one filesystem path.

    Args:
        path: Candidate path string.

    Returns:
        Stage label when one generic stage can be inferred, otherwise ``None``.
    """

    raw = str(path or "").strip()
    if not raw:
        return None
    name = Path(raw).name.strip()
    if not name:
        return None
    lowered = name.lower()
    if lowered.endswith((".tbi", ".bai", ".csi", ".fai")):
        return "indexed"

    without_compression = _strip_one_suffix(lowered, _COMPRESSION_SUFFIXES)

    if ".normalized.vcf" in without_compression:
        return "normalized"
    if ".annotated.vcf" in without_compression:
        return "annotated"
    if "subtracted" in without_compression and ".vcf" in without_compression:
        return "subtracted"
    if "filtered" in without_compression and ".vcf" in without_compression:
        return "filtered"
    if without_compression.endswith("_raw.vcf"):
        return "raw"
    if without_compression.endswith(".sorted.bam") or without_compression.endswith(".bam"):
        return "aligned"
    if without_compression in {"scaffolds.fasta", "contigs.fasta"}:
        return "assembled"
    if without_compression.endswith((".fasta", ".fa", ".fna")):
        return "assembled"

    stem = without_compression.rsplit(".", 1)[0] if "." in without_compression else without_compression
    if lowered.endswith(".csv") and (
        stem.endswith("_shared")
        or stem.startswith("shared_")
        or "shared_variants" in stem
    ):
        return "shared"
    return None


def classify_artifact_identity(path: str) -> str | None:
    """Infer the stable artifact identity stem for one staged path.

    Args:
        path: Candidate path string.

    Returns:
        Artifact identity with known stage suffixes stripped, or ``None`` when
        the basename cannot be reduced to a meaningful identity.
    """

    raw = str(path or "").strip()
    if not raw:
        return None
    stem = Path(raw).name.strip()
    if not stem:
        return None

    stem = _strip_one_suffix(stem, _COMPRESSION_SUFFIXES)
    stem = _strip_one_suffix(stem, _TERMINAL_SUFFIXES)

    changed = True
    while changed and stem:
        changed = False
        for pattern in _STAGE_SUFFIX_PATTERNS:
            updated = pattern.sub("", stem)
            if updated != stem:
                stem = updated
                changed = True
                break
    stem = stem.rstrip("._-")
    return stem or None


def canonicalize_bash_command_for_stage_dedupe(command: str) -> str:
    """Return a conservative canonical form for bash-step dedupe comparisons.

    Args:
        command: Raw bash command string.

    Returns:
        Canonical comparison string that preserves semantically important shell
        distinctions such as quoting, flag order, operators, and variable
        expansion.
    """

    text = " ".join(str(command or "").split()).rstrip(" ;")
    if not text:
        return ""
    segments = [segment.strip() for segment in text.split(" && ")]
    leading_paths: list[str] = []
    seen_paths: set[str] = set()
    idx = 0
    while idx < len(segments):
        segment = segments[idx]
        if not segment.lower().startswith("mkdir -p "):
            break
        tail = segment[len("mkdir -p ") :].strip()
        if not tail:
            break
        for token in tail.split():
            if token not in seen_paths:
                seen_paths.add(token)
                leading_paths.append(token)
        idx += 1

    if idx == 0:
        return text

    rebuilt: list[str] = []
    if leading_paths:
        rebuilt.append("mkdir -p " + " ".join(leading_paths))
    rebuilt.extend(segments[idx:])
    return " && ".join(segment for segment in rebuilt if segment).rstrip(" ;")


__all__ = [
    "_ALLOWED_STAGES",
    "canonicalize_bash_command_for_stage_dedupe",
    "classify_artifact_identity",
    "classify_path_stage",
]
