"""Helpers for request-scoped skill and data-root inference.

These helpers keep prompt-driven execution focused on the user's explicitly
named tools and files instead of letting broad workspace discovery dominate
planning.
"""

from __future__ import annotations

import os
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

_EXECUTION_VERBS = (
    "run",
    "execute",
    "use",
    "perform",
    "process",
    "quantify",
    "analyze",
    "analyse",
    "cluster",
)

_NON_EXECUTION_HINTS = (
    "how do i use",
    "how to use",
    "what is",
    "tell me about",
    "explain",
    "manual",
    "documentation",
    "docs",
    "capabilities",
)

_SKILL_ANALYSIS_TYPE_OVERRIDES: dict[str, str] = {
    "scanpy_workflow": "single_cell_rna_seq",
    "sc_count_and_cluster": "single_cell_rna_seq",
    "seurat_rscript_workflow": "single_cell_rna_seq",
    "spatial_transcriptomics_workflow": "spatial_transcriptomics",
    "metabolomics_diff_abundance": "metabolomics",
    "proteomics_diff_abundance": "proteomics",
    "deseq2_run": "rna_seq_differential_expression",
    "edger_run": "rna_seq_differential_expression",
    "gatk_haplotypecaller": "germline_variant_calling",
    "limma_voom_run": "rna_seq_differential_expression",
    "stringtie_quant": "transcript_quantification",
    "salmon_quant": "transcript_quantification",
    "kallisto_quant": "transcript_quantification",
}

_SKILL_REQUEST_ALIASES: dict[str, str] = {
    "scanpy": "scanpy_workflow",
    "deseq2": "deseq2_run",
    "edger": "edger_run",
    "limma": "limma_voom_run",
    "stringtie": "stringtie_quant",
    "salmon": "salmon_quant",
    "kallisto": "kallisto_quant",
}

_STRINGTIE_TRANSCRIPT_INTENT_TOKENS: tuple[str, ...] = (
    "transcript quant",
    "quantify transcripts",
    "quantify transcript",
    "transcript abundance",
    "abundance table",
    "gene abundance",
    "assembled transcript",
    "assembled gtf",
)

_STRINGTIE_BAM_TOKENS: tuple[str, ...] = (
    ".bam",
    "aligned bam",
    "alignment bam",
    "coordinate-sorted bam",
    "coordinate sorted bam",
    "sorted bam",
)

_STRINGTIE_GTF_TOKENS: tuple[str, ...] = (
    ".gtf",
    "annotation gtf",
    "annotation file",
    "gene annotation",
    "transcript annotation",
)

_RAW_READ_TOKENS: tuple[str, ...] = (
    ".fastq",
    ".fastq.gz",
    ".fq",
    ".fq.gz",
    "paired-end reads",
    "paired end reads",
    "raw reads",
)


def extract_request_paths(text: str, *, project_root: Path | None = None) -> list[Path]:
    """Extract existing absolute or repo-relative filesystem paths from text.

    Args:
        text: Free-form user request text.
        project_root: Optional repository root used to resolve repo-relative
            path tokens.

    Returns:
        Existing file or directory paths in first-seen order.
    """
    root = (project_root or Path.cwd()).resolve(strict=False)
    seen: set[str] = set()
    discovered: list[Path] = []

    def _record(raw_value: str) -> None:
        value = str(raw_value or "").strip().strip(".,;:!?()[]{}<>\"'`")
        if not value:
            return
        candidate = Path(os.path.expanduser(value))
        if not candidate.is_absolute():
            candidate = Path(os.path.abspath(str(root / candidate)))
        else:
            candidate = Path(os.path.abspath(str(candidate)))
        if not candidate.exists():
            return
        key = str(candidate)
        if key in seen:
            return
        seen.add(key)
        discovered.append(candidate)

    for match in re.findall(r"(?:(?<=^)|(?<=[\s\"'`(]))(/[^ \n\t,;\"')]+)", text or ""):
        _record(match)

    for raw_token in re.split(r"\s+", text or ""):
        token = raw_token.strip().strip(".,;:!?()[]{}<>\"'`")
        if not token or token.startswith("/"):
            continue
        if token == "~":
            continue
        if token.startswith("~"):
            _record(token)
            continue
        if any(token.startswith(prefix) for prefix in _RELATIVE_PATH_PREFIXES):
            _record(token)
            continue
        if "/" in token:
            _record(token)

    return discovered


def infer_request_data_root(
    text: str,
    *,
    project_root: Path | None = None,
) -> str:
    """Infer a focused data root from explicit prompt paths.

    Args:
        text: Free-form user request text.
        project_root: Optional repository root for resolving repo-relative
            paths.

    Returns:
        The narrowest common existing parent directory for the explicit input
        paths, or ``""`` when no explicit paths can be inferred.
    """
    explicit_paths = extract_request_paths(text, project_root=project_root)
    if not explicit_paths:
        return ""

    roots = [path if path.is_dir() else path.parent for path in explicit_paths]
    common = roots[0]
    for candidate in roots[1:]:
        common_parts = []
        for left, right in zip(common.parts, candidate.parts):
            if left != right:
                break
            common_parts.append(left)
        if not common_parts:
            return ""
        common = Path(*common_parts)

    return str(common.resolve(strict=False)) if common.exists() else ""


def infer_explicit_requested_skill(user_query: str, available_skill_names: list[str] | None = None) -> str:
    """Infer one explicitly requested executable skill from a user query.

    Args:
        user_query: Raw user request text.
        available_skill_names: Known Bio-Harness skill names.

    Returns:
        The one explicitly requested skill name when the request clearly asks to
        execute that tool, otherwise ``""``.
    """
    query_l = str(user_query or "").strip().lower()
    if not query_l or any(hint in query_l for hint in _NON_EXECUTION_HINTS):
        return ""

    available = [str(name).strip() for name in (available_skill_names or []) if str(name).strip()]
    matches = [name for name in available if name != "bash_run" and name.lower() in query_l]
    alias_matches = [
        (alias, skill_name)
        for alias, skill_name in _SKILL_REQUEST_ALIASES.items()
        if skill_name in available and re.search(rf"\b{re.escape(alias)}\b", query_l)
    ]
    alias_matches = list(dict.fromkeys(alias_matches))
    if len(matches) == 1:
        skill_name = matches[0]
        match_tokens = [skill_name.lower()]
    elif not matches and len(alias_matches) == 1:
        alias, skill_name = alias_matches[0]
        match_tokens = [alias, skill_name.lower()]
    else:
        return ""

    explicit_patterns = tuple(
        pattern
        for token in dict.fromkeys(match_tokens)
        for pattern in (
            f"use only the {token}",
            f"use {token}",
            f"run {token}",
            f"execute {token}",
            f"perform {token}",
            f"using {token}",
            f"with {token}",
        )
    )
    if any(pattern in query_l for pattern in explicit_patterns):
        return skill_name
    if extract_request_paths(user_query):
        return skill_name
    if any(verb in query_l for verb in _EXECUTION_VERBS):
        return skill_name
    return ""


def requested_skill_analysis_type(skill_name: str) -> str:
    """Return the canonical analysis type implied by one explicit skill name."""
    return _SKILL_ANALYSIS_TYPE_OVERRIDES.get(str(skill_name or "").strip().lower(), "")


def semantically_requests_stringtie_quant(user_query: str) -> bool:
    """Return whether a request semantically implies the StringTie wrapper.

    This helper is intentionally narrow. It only fires for transcript-
    quantification requests that already point at an aligned BAM plus an
    annotation GTF and ask for StringTie-style deliverables such as an
    assembled GTF or abundance table.

    Args:
        user_query: Raw user request text.

    Returns:
        ``True`` when the request semantics imply ``stringtie_quant`` even if
        the tool name is omitted.
    """
    text_l = str(user_query or "").strip().lower()
    if not text_l:
        return False
    if "stringtie_quant" in text_l or re.search(r"\bstringtie\b", text_l):
        return True
    has_bam = any(token in text_l for token in _STRINGTIE_BAM_TOKENS)
    has_gtf = any(token in text_l for token in _STRINGTIE_GTF_TOKENS)
    has_transcript_intent = any(token in text_l for token in _STRINGTIE_TRANSCRIPT_INTENT_TOKENS)
    if not (has_bam and has_gtf and has_transcript_intent):
        return False
    if any(token in text_l for token in _RAW_READ_TOKENS) and not has_bam:
        return False
    return True


def semantically_requests_long_read_rna_stringtie_pipeline(user_query: str) -> bool:
    """Return whether a request implies long-read RNA alignment plus StringTie.

    This helper targets annotation-backed long-read RNA requests that begin
    from raw long-read FASTQ input but still ask for transcript or isoform
    abundance outputs. Those requests should prefer a minimap2-plus-StringTie
    pipeline rather than an alignment-only handoff.

    Args:
        user_query: Raw user request text.

    Returns:
        ``True`` when the request semantics imply a long-read RNA alignment
        followed by StringTie quantification.
    """
    text_l = str(user_query or "").strip().lower()
    if not text_l:
        return False
    has_long_read_context = any(
        token in text_l
        for token in (
            "direct-rna",
            "direct rna",
            "oxford nanopore",
            "nanopore",
            "ont",
            "long-read rna",
            "long read rna",
        )
    )
    has_raw_reads = any(token in text_l for token in _RAW_READ_TOKENS) or (
        "reads" in text_l and has_long_read_context and not any(token in text_l for token in _STRINGTIE_BAM_TOKENS)
    )
    has_gtf = any(token in text_l for token in _STRINGTIE_GTF_TOKENS) or any(
        token in text_l
        for token in (
            "provided annotation",
            "use the provided annotation",
            "using the provided annotation",
        )
    )
    has_transcript_intent = any(token in text_l for token in _STRINGTIE_TRANSCRIPT_INTENT_TOKENS)
    annotation_missing = any(
        token in text_l
        for token in (
            "no annotation file is provided",
            "no annotation is provided",
            "without annotation",
            "without a gtf",
            "without gtf",
            "no gtf",
            "no gff",
        )
    )
    return has_long_read_context and has_raw_reads and has_gtf and has_transcript_intent and not annotation_missing
