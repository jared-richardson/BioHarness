"""Shared helpers for deterministic request-contract inference."""

from __future__ import annotations

import re

_REFERENCE_INPUT_PATTERNS = (
    r"\bgtf\b",
    r"\bgff3?\b",
    r"\bfasta\b",
    r"\bfa\b",
    r"\bfna\b",
    r"\bgenome\b",
    r"\btranscriptome\b",
    r"\bmouse_gtf\b",
    r"\bmouse_fasta\b",
    r"\bclinvar\b",
    r"\breference[_ -]?(?:fasta|genome|annotation|gtf|gff|transcriptome|vcf)\b",
)


def requires_reference_inputs(request_text: str) -> bool:
    """Return whether a request truly depends on explicit reference assets.

    This is intentionally narrower than a bare substring check for the word
    ``reference``. Chat prompts often mention "references" in the sense of
    papers or task context, which should not force the ``reference_inputs``
    capability.

    Args:
        request_text: Raw user-facing request text.

    Returns:
        True when the request clearly mentions concrete reference assets such as
        FASTA/GTF/GFF/genome/transcriptome inputs.
    """
    text = str(request_text or "").lower()
    return any(re.search(pattern, text) for pattern in _REFERENCE_INPUT_PATTERNS)
