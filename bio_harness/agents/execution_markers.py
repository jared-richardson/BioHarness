"""Helpers for detecting semantic runtime failure markers in step output."""

from __future__ import annotations

_FAILURE_MARKERS = (
    "__NO_FASTQ_FOUND__",
    "__NO_CONTROL_FASTQ__",
    "__NO_TREATMENT_FASTQ__",
    "__NO_GROUP_FASTQ_FOUND__",
    "__TEST_SUBSET_SKIPPED__:missing_inputs",
    "__TEST_SUBSET_SKIPPED__:invalid_reads_per_fastq",
    "__TEST_SUBSET_EMPTY__:",
    "__TEST_SUBSET_MISSING_PAIR__:",
    "__EMPTY_INPUT_FILE__:",
)


def detect_failure_marker(output_line: str) -> str | None:
    """Return the first known semantic failure marker found in *output_line*."""

    line = str(output_line or "").strip()
    for marker in _FAILURE_MARKERS:
        if marker in line:
            return marker
    return None
