"""Prompt-intent helpers for request-contract inference.

These helpers keep prompt-specific heuristics isolated from the broader
contract utilities so negated tool parsing, downstream-context trimming, and
direct-wrapper capability shaping can evolve without growing the main contract
module further.
"""

from __future__ import annotations

import re

from bio_harness.harness.contract_prompt_capabilities import (
    downstream_capability_hints_from_text,
    has_explicit_single_cell_diff_request,
    is_count_matrix_de_request,
    is_direct_wrapper_prompt,
    is_precounted_scanpy_request,
    requests_alignment,
    requests_group_comparison,
    strip_downstream_context_capabilities,
    strip_tool_family_false_positive_capabilities,
    strip_upstream_capabilities_for_direct_wrapper_prompt,
)

_NEGATED_TOOL_SPAN_PATTERNS: tuple[str, ...] = (
    (
        r"(?:do not use|don't use|dont use|avoid|without|"
        r"do not run|don't run|dont run|"
        r"do not rerun|don't rerun|dont rerun|"
        r"do not execute|don't execute|dont execute|"
        r"do not switch to|don't switch to|dont switch to|"
        r"do not change to|don't change to|dont change to|"
        r"do not pseudoalign with|don't pseudoalign with|dont pseudoalign with|"
        r"do not quantify with|don't quantify with|dont quantify with|"
        r"do not align with|don't align with|dont align with|"
        r"instead of|rather than)\s+([^.;\n]+)"
    ),
)


def tool_hint_aliases() -> dict[str, str]:
    """Return normalized aliases used for prompt-level tool mentions."""

    return {
        "scanpy": "scanpy_workflow",
        "deseq2": "deseq2_run",
        "edger": "edger_run",
        "limma": "limma_voom_run",
        "rmats": "rmats",
        "rmats.py": "rmats.py",
        "featurecounts": "featurecounts",
        "subread": "subread",
        "subjunc": "subread",
        "subread-align": "subread",
        "fastp": "fastp_run",
        "fastp_run": "fastp_run",
        "cutadapt": "cutadapt_run",
        "cutadapt_run": "cutadapt_run",
        "bedtools intersect": "bedtools_intersect",
        "bedtools coverage": "bedtools_coverage",
        "bedtools genomecov": "bedtools_genomecov",
        "genomecov": "bedtools_genomecov",
        "salmon": "salmon",
        "kallisto": "kallisto",
        "stringtie": "stringtie_quant",
        "multiqc": "multiqc_report",
        "multiqc report": "multiqc_report",
        "quarto": "quarto_report",
        "quarto report": "quarto_report",
        "bwa": "bwa",
        "bwa-mem2": "bwa",
        "bowtie2": "bowtie2",
        "hisat2": "hisat2",
        "minimap2": "minimap2",
        "samtools flagstat": "samtools_flagstat",
        "flagstat": "samtools_flagstat",
        "samtools idxstats": "samtools_idxstats",
        "idxstats": "samtools_idxstats",
        "samtools stats": "samtools_stats",
        "mutect2": "mutect2",
        "gatk": "gatk",
        "bcftools": "bcftools",
        "varscan": "varscan",
        "varscan2": "varscan",
        "blastp": "blastp",
        "hmmscan": "hmmscan",
        "dexseq": "dexseq_run",
        "majiq": "majiq",
    }


def is_completed_output_report_prompt(request_text: str) -> bool:
    """Return whether a prompt asks to build a report from completed outputs."""

    text_l = str(request_text or "").lower()
    if not text_l:
        return False
    references_completed_outputs = any(
        marker in text_l
        for marker in (
            "completed run",
            "completed fastqc outputs",
            "completed outputs",
            "existing fastqc outputs",
            "existing outputs",
        )
    )
    mentions_reporting = any(
        marker in text_l
        for marker in (
            "report bundle",
            "researcher-facing report",
            "multiqc report",
            "quarto report",
        )
    ) or ("multiqc" in text_l or "quarto" in text_l)
    return references_completed_outputs and mentions_reporting

def negated_tool_spans(text_l: str) -> list[str]:
    """Return prompt fragments that negate or forbid tool selection."""

    spans: list[str] = []
    for pattern in _NEGATED_TOOL_SPAN_PATTERNS:
        spans.extend(re.findall(pattern, str(text_l or "")))
    return [str(span).strip().lower() for span in spans if str(span).strip()]


def required_tool_hints_from_text(request_text: str, explicit_tools: list[str]) -> list[str]:
    """Return normalized tool hints that the prompt explicitly requires."""

    text = str(request_text or "")
    text_l = text.lower()
    required: list[str] = []
    alias_map = tool_hint_aliases()
    negated_spans = negated_tool_spans(text_l)

    def _appears_in_negated_clause(token: str) -> bool:
        token_l = str(token or "").strip().lower()
        if not token_l:
            return False
        token_re = re.escape(token_l)
        return any(re.search(rf"\b{token_re}\b", span) for span in negated_spans)

    def _is_required_request(token: str) -> bool:
        token_re = re.escape(token)
        patterns = (
            rf"\b(?:please\s+)?use\s+{token_re}\b",
            rf"\b(?:please\s+)?use\s+only\s+(?:the\s+)?{token_re}(?:\s+tool)?\b",
            rf"\bkeep\s+(?:the\s+workflow|this)\s+on\s+{token_re}\b",
            rf"\b(?:run|execute|perform|apply)\s+(?:only\s+)?(?:the\s+)?{token_re}(?:\s+tool)?\b",
            rf"\busing\s+{token_re}\b",
            rf"\bwith\s+{token_re}\b",
            rf"\bvia\s+{token_re}\b",
        )
        return any(re.search(pattern, text_l) for pattern in patterns)

    for raw_token, normalized in alias_map.items():
        if _appears_in_negated_clause(raw_token):
            continue
        if not _is_required_request(raw_token):
            continue
        if normalized not in required:
            required.append(normalized)
    for hint in explicit_tools:
        hint_norm = str(hint).strip().lower()
        if _appears_in_negated_clause(hint_norm):
            continue
        if hint_norm in required:
            continue
        if _is_required_request(hint_norm):
            required.append(hint_norm)
    return required


def blocked_tool_hints_from_text(request_text: str, explicit_tools: list[str]) -> list[str]:
    """Return normalized tool hints that appear in explicitly negated clauses."""

    text_l = str(request_text or "").lower()
    alias_map = tool_hint_aliases()
    negated_spans = negated_tool_spans(text_l)
    candidate_tokens: list[str] = []
    candidate_tokens.extend(str(item).strip().lower() for item in explicit_tools if str(item).strip())
    candidate_tokens.extend(alias_map.keys())
    blocked: list[str] = []
    seen: set[str] = set()

    def _record(token: str) -> None:
        normalized = str(alias_map.get(token, token) or "").strip().lower()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        blocked.append(normalized)

    for raw_token in candidate_tokens:
        token = str(raw_token or "").strip().lower()
        if not token:
            continue
        token_re = re.escape(token)
        if re.search(rf"\bnot\s+(?:the\s+)?{token_re}(?:\s+tool)?\b", text_l):
            _record(token)
            continue
        if any(re.search(rf"\b{token_re}\b", span) for span in negated_spans):
            _record(token)
    return blocked

__all__ = [
    "blocked_tool_hints_from_text",
    "downstream_capability_hints_from_text",
    "has_explicit_single_cell_diff_request",
    "is_completed_output_report_prompt",
    "is_count_matrix_de_request",
    "is_direct_wrapper_prompt",
    "is_precounted_scanpy_request",
    "negated_tool_spans",
    "requests_group_comparison",
    "requests_alignment",
    "required_tool_hints_from_text",
    "strip_downstream_context_capabilities",
    "strip_tool_family_false_positive_capabilities",
    "strip_upstream_capabilities_for_direct_wrapper_prompt",
    "tool_hint_aliases",
]
