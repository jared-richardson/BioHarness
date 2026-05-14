"""FASTQ discovery and repair helpers for workflow template canonicalization."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.pathing import discover_fastq_files_guarded

FASTQ_PAIR_RE = re.compile(
    r"^(?P<prefix>.+?)_R(?P<read>[12])(?:_001)?\.(?:f(?:ast)?q)(?:\.gz)?$",
    flags=re.IGNORECASE,
)
SAMPLE_HINT_RE = re.compile(r"(^|[_-])(s\d+)([_-]|$)", flags=re.IGNORECASE)
FASTQ_READ_HINT_RE = re.compile(r"(^|[_-])r([12])(?:[_-]|\.|$)", flags=re.IGNORECASE)


def discover_fastq_pair_map(data_root: str, max_files: int = 5000) -> dict[str, dict[str, str]]:
    """Discover paired FASTQ files under one data root."""

    root = Path(str(data_root or "")).expanduser()
    if not root.exists():
        return {}
    fastqs = discover_fastq_files_guarded(root, include_subdirs=True, name_filter="", max_files=max_files)
    pair_map: dict[str, dict[str, str]] = {}
    for fastq_path in fastqs:
        match = FASTQ_PAIR_RE.match(Path(fastq_path).name)
        if not match:
            continue
        pair_map.setdefault(match.group("prefix"), {})[match.group("read")] = fastq_path
    return pair_map


def get_fastq_pair_map(state: dict[str, Any], data_root: str) -> dict[str, dict[str, str]]:
    """Cache and return the FASTQ pair map in canonicalization state."""

    pair_map = state.get("fastq_pair_map")
    if pair_map is None:
        pair_map = discover_fastq_pair_map(data_root)
        state["fastq_pair_map"] = pair_map
    return pair_map


def extract_sample_hint(*values: str) -> str:
    """Extract a stable sample hint from path-like values."""

    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        for candidate in (text, Path(text).name):
            match = SAMPLE_HINT_RE.search(candidate.lower())
            if match:
                return match.group(2).upper()
    return ""


def resolve_fastq_pair_from_hints(
    pair_map: dict[str, dict[str, str]],
    *,
    sample_hint: str,
    requested_reads_1: str,
    requested_reads_2: str,
) -> tuple[str, str]:
    """Resolve one FASTQ pair using sample and filename hints."""

    if not pair_map:
        return "", ""
    normalized_hint = sample_hint.strip().lower()
    requested_1 = Path(str(requested_reads_1 or "")).name.lower()
    requested_2 = Path(str(requested_reads_2 or "")).name.lower()
    hint_pat = re.compile(rf"(^|[_-]){re.escape(normalized_hint)}([_-]|$)") if normalized_hint else None

    scored: list[tuple[int, str, str, str]] = []
    for key, pair in pair_map.items():
        r1 = str(pair.get("1", "")).strip()
        r2 = str(pair.get("2", "")).strip()
        if not r1 or not r2:
            continue
        score = 0
        searchable = f"{key.lower()} {Path(r1).name.lower()} {Path(r2).name.lower()}"
        if hint_pat and hint_pat.search(searchable):
            score += 20
        if requested_1 and requested_1.replace(".gz", "") in Path(r1).name.lower():
            score += 5
        if requested_2 and requested_2.replace(".gz", "") in Path(r2).name.lower():
            score += 5
        scored.append((score, key.lower(), r1, r2))

    if not scored:
        return "", ""
    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return scored[0][2], scored[0][3]


def extract_read_hint(value: str) -> str:
    """Extract an ``R1`` or ``R2`` hint from one FASTQ-like path."""

    text = str(value or "").strip()
    if not text:
        return ""
    for candidate in (text, Path(text).name):
        match = FASTQ_READ_HINT_RE.search(candidate.lower())
        if match:
            return match.group(2)
    return ""


def resolve_single_fastq_from_hints(
    pair_map: dict[str, dict[str, str]],
    *,
    sample_hint: str,
    read_hint: str,
    requested_path: str,
) -> str:
    """Resolve one FASTQ path using sample and mate hints."""

    if not pair_map:
        return ""
    requested_name = Path(str(requested_path or "")).name.lower()
    requested_core = requested_name.replace(".gz", "")
    hint_pat = re.compile(rf"(^|[_-]){re.escape(sample_hint.lower())}([_-]|$)") if sample_hint else None

    scored: list[tuple[int, str, str]] = []
    for key, pair in pair_map.items():
        if read_hint in {"1", "2"}:
            candidate = str(pair.get(read_hint, "")).strip()
        else:
            candidate = str(pair.get("1", "")).strip() or str(pair.get("2", "")).strip()
        if not candidate:
            continue

        score = 0
        key_l = key.lower()
        cand_name = Path(candidate).name.lower()
        searchable = f"{key_l} {cand_name}"
        if hint_pat and hint_pat.search(searchable):
            score += 20
        if read_hint in {"1", "2"} and FASTQ_READ_HINT_RE.search(cand_name) and f"r{read_hint}" in cand_name:
            score += 20
        if requested_core and requested_core in cand_name.replace(".gz", ""):
            score += 5
        scored.append((score, key_l, candidate))

    if not scored:
        return ""
    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return scored[0][2]


def repair_fastqc_input_files(
    input_file: str,
    *,
    data_root: str,
    pair_map: dict[str, dict[str, str]] | None = None,
) -> tuple[str, bool]:
    """Repair missing FastQC input paths using discovered FASTQ hints."""

    raw = str(input_file or "").strip()
    if not raw:
        return raw, False
    try:
        tokens = shlex.split(raw, posix=True)
    except Exception:
        tokens = raw.split()
    if not tokens:
        return raw, False

    local_pair_map = pair_map if pair_map is not None else discover_fastq_pair_map(data_root)
    changed = False
    rewritten: list[str] = []
    for token in tokens:
        candidate = str(token).strip()
        if not candidate:
            continue
        if Path(candidate).expanduser().exists():
            rewritten.append(candidate)
            continue
        replacement = resolve_single_fastq_from_hints(
            local_pair_map,
            sample_hint=extract_sample_hint(candidate),
            read_hint=extract_read_hint(candidate),
            requested_path=candidate,
        )
        if replacement and replacement != candidate:
            rewritten.append(replacement)
            changed = True
        else:
            rewritten.append(candidate)

    if not changed:
        return raw, False
    return " ".join(shlex.quote(item) for item in rewritten), True
