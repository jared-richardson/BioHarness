"""Capability-focused prompt intent helpers for contract inference."""

from __future__ import annotations

import re

from bio_harness.core.tool_registry import default_tool_registry

_ALIGNMENT_REQUEST_PATTERNS = (
    r"\balign\b",
    r"\balignment\b",
    r"map reads",
    r"splice-aware",
)
_ALIGNMENT_NEGATION_TOKENS = (
    "do not align",
    "don't align",
    "dont align",
    "do not rerun alignment",
    "don't rerun alignment",
    "dont rerun alignment",
    "without rerunning alignment",
    "without alignment",
    "skip alignment",
    "no alignment",
)
_SCANPY_PRECOUNT_TOKENS = (
    ".h5ad",
    "processed h5ad",
    "pre-counted",
    "precounted",
    "pre-counted matrix",
    "precounted matrix",
)
_EXPLICIT_SC_DIFF_TOKENS = (
    "differential expression",
    "differentially expressed",
    "rank_genes_groups",
    "compare clusters",
    "compare conditions",
    "compare groups",
    "case ",
    "control",
    "treatment",
    " versus ",
    " vs ",
)
_COUNT_MATRIX_PATH_PATTERN = re.compile(
    r"\b[^\s]*(?:count|counts|gene_counts|featurecounts)[^\s]*\.(?:tsv|txt|csv)\b"
)
_METADATA_PATH_PATTERN = re.compile(
    r"\b[^\s]*(?:meta|metadata|coldata|sample)[^\s]*\.(?:tsv|txt|csv)\b"
)
_DOWNSTREAM_DIFF_PATTERNS: tuple[str, ...] = (
    r"\bdownstream differential expression\b",
    r"\bdownstream differential analysis\b",
    r"\bfor downstream differential expression\b",
    r"\bfor later differential expression\b",
    r"\bbefore downstream differential expression\b",
    r"\blater differential expression\b",
    r"\bsubsequent differential expression\b",
)
_DOWNSTREAM_GROUP_PATTERNS: tuple[str, ...] = (
    r"\bdownstream group comparison\b",
    r"\bfor downstream group comparison\b",
    r"\blater compare conditions\b",
)
_GROUP_COMPARISON_LITERAL_TOKENS: tuple[str, ...] = (
    "compare groups",
    "group comparison",
    "compared conditions",
    "compare conditions",
)
_GROUP_COMPARISON_REGEX_PATTERNS: tuple[str, ...] = (
    r"(?<!quality\s)\bcontrol\b",
    r"\btreatment\b",
    r"\bvs\.?\b",
    r"\bversus\b",
)
_ASSEMBLY_WRAPPER_TOOLS: frozenset[str] = frozenset(
    {
        "spades_assemble",
        "flye_assemble",
        "trinity_assemble",
        "canu_assemble",
        "megahit_assemble",
        "megahit",
        "shovill_assemble",
        "unicycler_assemble",
    }
)
_TRIMMING_QC_WRAPPER_TOOLS: frozenset[str] = frozenset(
    {
        "fastp_run",
        "cutadapt_run",
    }
)
_SINGLE_CELL_CUE_TOKENS: tuple[str, ...] = (
    "single-cell",
    "single cell",
    "scrna",
    "scanpy",
    "scanpy_workflow",
    "seurat",
    "seurat_rscript_workflow",
    "cellranger",
    "cellranger_count",
    "star_solo",
    "star_solo_count",
    "starsolo",
    "sc_count_and_cluster",
    "alevin",
    "10x",
)
_SINGLE_CELL_TOOL_HINTS: frozenset[str] = frozenset(
    {
        "scanpy",
        "scanpy_workflow",
        "seurat",
        "seurat_rscript_workflow",
        "cellranger",
        "cellranger_count",
        "star_solo_count",
        "sc_count_and_cluster",
        "alevin",
        "alevin-fry",
        "alevin_fry",
    }
)


def is_precounted_scanpy_request(text: str) -> bool:
    """Return whether a request targets Scanpy with processed single-cell input."""

    text_l = str(text or "").lower()
    has_scanpy = "scanpy" in text_l or "scanpy_workflow" in text_l
    has_precounted_input = any(token in text_l for token in _SCANPY_PRECOUNT_TOKENS)
    return has_scanpy and has_precounted_input


def has_explicit_single_cell_diff_request(text: str) -> bool:
    """Return whether a single-cell prompt explicitly requests DE/group comparison."""

    text_l = str(text or "").lower()
    return any(token in text_l for token in _EXPLICIT_SC_DIFF_TOKENS)


def requests_group_comparison(text: str) -> bool:
    """Return whether a prompt explicitly requests comparing groups."""

    text_l = str(text or "").lower()
    if any(token in text_l for token in _GROUP_COMPARISON_LITERAL_TOKENS):
        return True
    return any(re.search(pattern, text_l) for pattern in _GROUP_COMPARISON_REGEX_PATTERNS)


def is_count_matrix_de_request(text: str) -> bool:
    """Return whether a request is a count-matrix differential-expression prompt."""

    text_l = str(text or "").lower()
    has_de = "differential expression" in text_l or any(
        token in text_l for token in ("deseq2", "edger", "limma", "limma_voom")
    )
    has_group_request = requests_group_comparison(text_l)
    has_counts = any(
        token in text_l
        for token in ("count matrix", "counts matrix", "counts table", "counts.tsv", "counts.txt")
    ) or bool(_COUNT_MATRIX_PATH_PATTERN.search(text_l))
    has_metadata = any(
        token in text_l for token in ("metadata", "sample metadata", "metadata.tsv", "coldata")
    ) or bool(_METADATA_PATH_PATTERN.search(text_l))
    has_splicing = any(token in text_l for token in ("splicing", "exon", "dexseq", "rmats", "majiq"))
    has_raw_reads = any(token in text_l for token in (".fastq", ".fq", "reads_1", "reads_2", ".bam"))
    return (has_de or has_group_request) and has_counts and has_metadata and not has_splicing and not has_raw_reads


def requests_alignment(text: str) -> bool:
    """Return whether the prompt positively requests an alignment stage."""

    text_l = str(text or "").lower()
    if any(token in text_l for token in _ALIGNMENT_NEGATION_TOKENS):
        return False
    return any(re.search(pattern, text_l) for pattern in _ALIGNMENT_REQUEST_PATTERNS)


def is_direct_wrapper_prompt(
    request_text: str,
    *,
    explicit_tools: list[str],
    required_tools: list[str],
) -> bool:
    """Return whether a prompt explicitly requests one direct-wrapper mode."""

    text_l = str(request_text or "").lower()
    tool_hints = [str(item).strip() for item in (required_tools or explicit_tools) if str(item).strip()]
    if not tool_hints or len(set(tool_hints)) > 2:
        return False
    has_prohibition = any(
        token in text_l
        for token in (
            "use only",
            "do not align",
            "do not use bash_run",
            "do not add",
            "do not look for fastq",
            "keep this on the direct",
        )
    )
    has_preexisting_inputs = any(
        token in text_l
        for token in (
            "count matrix",
            "counts matrix",
            "counts table",
            "counts.tsv",
            "counts.txt",
            "metadata.tsv",
            "sample metadata",
            "processed h5ad",
            "processed anndata",
            ".h5ad",
            "aligned bam",
            ".bam",
        )
    ) or bool(_COUNT_MATRIX_PATH_PATTERN.search(text_l)) or bool(_METADATA_PATH_PATTERN.search(text_l))
    return has_prohibition or has_preexisting_inputs


def downstream_capability_hints_from_text(request_text: str) -> list[str]:
    """Return capabilities that appear only as downstream context in the prompt."""

    text_l = str(request_text or "").lower()
    hinted: list[str] = []

    def _add(capability_name: str) -> None:
        if capability_name not in hinted:
            hinted.append(capability_name)

    if any(re.search(pattern, text_l) for pattern in _DOWNSTREAM_DIFF_PATTERNS):
        _add("differential_analysis")
        _add("group_comparison")
    if any(re.search(pattern, text_l) for pattern in _DOWNSTREAM_GROUP_PATTERNS):
        _add("group_comparison")
    return hinted


def strip_downstream_context_capabilities(
    caps: list[str],
    *,
    downstream_capability_hints: list[str],
    explicit_tools: list[str],
    required_tools: list[str],
) -> list[str]:
    """Remove downstream-only capabilities not supported by the chosen wrapper family."""

    if not downstream_capability_hints:
        return list(caps)

    registry = default_tool_registry()
    tool_hints = [str(item).strip() for item in (required_tools or explicit_tools) if str(item).strip()]
    resolved_tools = [tool for tool in tool_hints if registry.get(tool) is not None]
    if not resolved_tools:
        return list(caps)

    trimmed: list[str] = []
    for capability_name in caps:
        cap = str(capability_name).strip()
        if cap not in downstream_capability_hints:
            trimmed.append(cap)
            continue
        if any(cap in registry.capabilities_for(tool) for tool in resolved_tools):
            trimmed.append(cap)
    return trimmed


def strip_upstream_capabilities_for_direct_wrapper_prompt(
    request_text: str,
    caps: list[str],
    *,
    explicit_tools: list[str],
    required_tools: list[str],
) -> list[str]:
    """Return a capability list with incompatible upstream stages removed."""

    if not is_direct_wrapper_prompt(
        request_text,
        explicit_tools=explicit_tools,
        required_tools=required_tools,
    ):
        return list(caps)

    ordered_tools = [str(item).strip() for item in (required_tools or explicit_tools) if str(item).strip()]
    direct_wrapper_tools = {
        "deseq2_run",
        "edger_run",
        "limma_voom_run",
        "dexseq_run",
        "scanpy_workflow",
        "seurat_rscript_workflow",
        "stringtie_quant",
    }
    primary_tool = next((tool for tool in ordered_tools if tool in direct_wrapper_tools), "")
    if not primary_tool:
        primary_tool = ordered_tools[0] if ordered_tools else ""
    trimmed = list(caps)

    def _discard(*capability_names: str) -> None:
        for capability_name in capability_names:
            while capability_name in trimmed:
                trimmed.remove(capability_name)

    text_l = str(request_text or "").lower()
    has_count_matrix_inputs = any(
        token in text_l
        for token in (
            "count matrix",
            "counts matrix",
            "counts table",
            "counts.tsv",
            "counts.txt",
            "metadata.tsv",
            "sample metadata",
            "metadata table",
        )
    ) or bool(_COUNT_MATRIX_PATH_PATTERN.search(text_l)) or bool(_METADATA_PATH_PATTERN.search(text_l))
    if primary_tool in {"deseq2_run", "edger_run", "limma_voom_run", "dexseq_run"} and (
        is_count_matrix_de_request(request_text) or has_count_matrix_inputs
    ):
        _discard("alignment", "quantification", "reference_inputs")
    elif primary_tool in {"scanpy_workflow", "seurat_rscript_workflow"} and is_precounted_scanpy_request(request_text):
        _discard("alignment", "quantification", "reference_inputs")
    elif primary_tool == "stringtie_quant" and (".bam" in text_l or "aligned bam" in text_l):
        _discard("alignment")
    return trimmed


def strip_tool_family_false_positive_capabilities(
    request_text: str,
    caps: list[str],
    *,
    explicit_tools: list[str],
    required_tools: list[str],
) -> list[str]:
    """Remove lexical capability false-positives that conflict with chosen tools."""

    trimmed = list(caps)
    tool_hints = [str(item).strip() for item in (required_tools or explicit_tools) if str(item).strip()]
    tool_set = {tool for tool in tool_hints if tool}
    registry = default_tool_registry()
    resolved_tools = [tool for tool in tool_hints if registry.get(tool) is not None]
    text_l = str(request_text or "").lower()

    def _discard(capability_name: str) -> None:
        while capability_name in trimmed:
            trimmed.remove(capability_name)

    if (
        "minimap2_align" in tool_set
        and "genome_assembly" in trimmed
        and not (tool_set & _ASSEMBLY_WRAPPER_TOOLS)
        and any(token in text_l for token in ("assembly-to-assembly", "assembly to assembly", "preset asm", " asm5", " asm10", " asm20"))
    ):
        _discard("genome_assembly")

    if (
        "group_comparison" in trimmed
        and resolved_tools
        and not requests_group_comparison(request_text)
        and all("group_comparison" not in registry.capabilities_for(tool) for tool in resolved_tools)
    ):
        _discard("group_comparison")

    if (
        "fastqc" in trimmed
        and resolved_tools
        and not any(tool in {"fastqc_run", "fastqc"} for tool in tool_set)
        and any(tool in _TRIMMING_QC_WRAPPER_TOOLS for tool in resolved_tools)
        and any(token in text_l for token in ("trim", "trimming", "adapter", "low-quality", "low quality"))
    ):
        _discard("fastqc")

    if (
        "annotation" in trimmed
        and resolved_tools
        and all("annotation" not in registry.capabilities_for(tool) for tool in resolved_tools)
        and any(token in text_l for token in (".gtf", ".gff", ".gff3"))
    ):
        _discard("annotation")

    if (
        "single_cell_analysis" in trimmed
        and not any(token in text_l for token in _SINGLE_CELL_CUE_TOKENS)
        and not (tool_set & _SINGLE_CELL_TOOL_HINTS)
    ):
        _discard("single_cell_analysis")

    return trimmed


__all__ = [
    "downstream_capability_hints_from_text",
    "has_explicit_single_cell_diff_request",
    "is_count_matrix_de_request",
    "is_direct_wrapper_prompt",
    "is_precounted_scanpy_request",
    "requests_alignment",
    "requests_group_comparison",
    "strip_downstream_context_capabilities",
    "strip_tool_family_false_positive_capabilities",
    "strip_upstream_capabilities_for_direct_wrapper_prompt",
]
