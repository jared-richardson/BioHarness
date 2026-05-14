"""Execution-mode inference helpers for analysis-spec normalization.

This module separates assay-family compatibility from concrete wrapper or
pipeline choice.  The goal is to give later grounding and repair stages a
stable description of what kinds of plans are compatible with the user's
inputs without forcing a generic sibling-template interpretation.
"""

from __future__ import annotations

from typing import Any

from bio_harness.core.wrapper_contracts import (
    wrapper_allowed_input_modes,
    wrapper_has_contract,
    wrapper_lock_requires_evidence,
    wrapper_supports_input_mode,
)


_RAW_FASTQ_TOKENS: tuple[str, ...] = (
    ".fastq",
    ".fastq.gz",
    ".fq",
    ".fq.gz",
    "reads_1",
    "reads_2",
    "paired-end reads",
    "paired end reads",
    "raw reads",
    "fastq",
)
_COUNT_MATRIX_TOKENS: tuple[str, ...] = (
    "count matrix",
    "counts matrix",
    "counts table",
    "gene counts",
    "featurecounts",
    "abundance matrix",
    "feature table",
    "feature intensity",
    "metabolite intensity",
    "peak table",
    "protein abundance",
    "protein expression data",
    "intensity matrix",
    "metadata table",
    "sample metadata",
    "coldata",
    "counts.tsv",
)
_PROCESSED_SINGLE_CELL_TOKENS: tuple[str, ...] = (
    ".h5ad",
    ".h5mu",
    "anndata",
    "processed h5ad",
    "processed anndata",
    "processed counts",
)
_ALIGNED_BAM_TOKENS: tuple[str, ...] = (
    ".bam",
    ".cram",
    "aligned bam",
    "aligned cram",
    "input_bam",
    "sorted bam",
)
_VCF_TOKENS: tuple[str, ...] = (
    ".vcf",
    ".vcf.gz",
    "input vcf",
    "annotate variants",
    "variant annotation",
)
_PROTEIN_FASTA_TOKENS: tuple[str, ...] = (
    ".faa",
    "protein fasta",
    "amino acid fasta",
    "protein sequence",
)
_RAW_FASTQ_DIRECT_WRAPPERS: tuple[str, ...] = (
    "flye_assemble",
    "minimap2_align",
)
_COUNT_MATRIX_FILENAMES: frozenset[str] = frozenset(
    {
        "count_matrix.tsv",
        "counts_matrix.tsv",
        "gene_counts.txt",
        "gene_counts.tsv",
        "counts.tsv",
        "counts.txt",
        "abundance_matrix.csv",
        "abundance_matrix.tsv",
        "feature_table.csv",
        "feature_table.tsv",
        "peak_table.csv",
        "peak_table.tsv",
        "protein_abundance.csv",
        "protein_abundance.tsv",
        "intensity_matrix.csv",
        "intensity_matrix.tsv",
    }
)
_METADATA_FILENAMES: frozenset[str] = frozenset(
    {
        "metadata.csv",
        "metadata.tsv",
        "sample_metadata.csv",
        "sample_metadata.tsv",
        "coldata.csv",
        "coldata.tsv",
    }
)

_COMPATIBILITY_MAP: dict[str, dict[str, tuple[str, ...]]] = {
    "long_read_assembly": {
        "raw_fastq": ("flye_assemble",),
    },
    "long_read_rna": {
        "raw_fastq": ("minimap2_align",),
    },
    "single_cell_rna_seq": {
        "processed_single_cell": ("scanpy_workflow", "seurat_rscript_workflow"),
        "count_matrix": ("scanpy_workflow", "seurat_rscript_workflow"),
        "raw_fastq": ("sc_count_and_cluster", "star_solo_count", "cellranger_count"),
    },
    "spatial_transcriptomics": {
        "processed_single_cell": ("spatial_transcriptomics_workflow",),
    },
    "metabolomics": {
        "count_matrix": ("metabolomics_diff_abundance",),
    },
    "proteomics": {
        "count_matrix": ("proteomics_diff_abundance",),
    },
    "rna_seq_differential_expression": {
        "count_matrix": ("deseq2_run", "edger_run", "limma_voom_run"),
        "raw_fastq": (
            "subread_align",
            "featurecounts_run",
            "star_align",
            "star_2pass_align",
            "hisat2_align",
        ),
    },
    "transcript_quantification": {
        "aligned_bam": ("stringtie_quant",),
        "raw_fastq": ("salmon_quant", "kallisto_quant"),
    },
    "variant_annotation": {
        "vcf": ("snpeff_annotate",),
    },
    "germline_variant_calling": {
        "aligned_bam": ("gatk_haplotypecaller",),
    },
    "phylogenetics": {
        "protein_fasta": ("phylogenetics_iqtree_style",),
    },
}

_DIRECT_WRAPPER_INPUT_MODES: frozenset[str] = frozenset(
    {"count_matrix", "aligned_bam", "processed_single_cell", "vcf", "protein_fasta"}
)

_RNA_SEQ_DE_RAW_FASTQ_HINT_TOOLS: frozenset[str] = frozenset(
    _COMPATIBILITY_MAP["rna_seq_differential_expression"]["raw_fastq"]
) | frozenset(
    {
        "salmon_quant",
        "kallisto_quant",
        "cutadapt_run",
        "fastqc_run",
    }
)


def _assert_compatibility_map_consistency() -> None:
    """Validate direct-wrapper compatibility entries against wrapper contracts."""

    mismatches: list[str] = []
    for analysis_type, mode_map in _COMPATIBILITY_MAP.items():
        for input_mode, wrappers in mode_map.items():
            for wrapper_name in wrappers:
                if not wrapper_has_contract(wrapper_name):
                    mismatches.append(
                        f"{analysis_type}:{input_mode}:{wrapper_name}:missing_contract"
                    )
                    continue
                if input_mode not in _DIRECT_WRAPPER_INPUT_MODES:
                    continue
                allowed_modes = wrapper_allowed_input_modes(wrapper_name)
                if input_mode not in allowed_modes:
                    mismatches.append(
                        f"{analysis_type}:{input_mode}:{wrapper_name}:allowed={sorted(allowed_modes)}"
                    )
    if mismatches:
        joined = ", ".join(sorted(mismatches))
        raise RuntimeError(
            "Execution-mode compatibility map drifted from wrapper contracts: "
            f"{joined}"
        )


_assert_compatibility_map_consistency()


def _normalize_tool_hints(explicit_tools: list[str] | None) -> set[str]:
    """Return normalized wrapper hints derived from explicit tool strings.

    Args:
        explicit_tools: Optional tool-hint list, including composite strings.

    Returns:
        Set of normalized wrapper names.
    """

    normalized: set[str] = set()
    for item in explicit_tools or []:
        text = str(item).strip()
        if not text:
            continue
        parts = [part.strip() for part in text.split("+")]
        normalized.update(part for part in parts if part)
    return normalized


def _infer_input_mode_from_tools(*, analysis_type: str, tools: set[str]) -> str:
    """Infer input mode from wrapper compatibility when the mapping is unique.

    Args:
        analysis_type: Canonical analysis family string.
        tools: Normalized wrapper-hint set.

    Returns:
        One deterministic input-mode token when the tools imply a unique mode,
        otherwise ``""``.
    """

    if not tools:
        return ""
    if analysis_type == "rna_seq_differential_expression":
        count_matrix_wrappers = set(_COMPATIBILITY_MAP["rna_seq_differential_expression"]["count_matrix"])
        if tools.issubset(count_matrix_wrappers):
            return ""
    compatible_modes = {
        input_mode
        for input_mode, candidates in _COMPATIBILITY_MAP.get(str(analysis_type or ""), {}).items()
        if any(tool in candidates for tool in tools)
    }
    if len(compatible_modes) == 1:
        return next(iter(compatible_modes))
    return ""


def _discovered_file_names(discovered_data_files: list[dict[str, Any]] | None) -> set[str]:
    """Return normalized discovered input filenames."""

    names: set[str] = set()
    for entry in discovered_data_files or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "") or "").strip().lower()
        path = str(entry.get("path", "") or "").strip().lower()
        if name:
            names.add(name)
        if path:
            names.add(path.rsplit("/", 1)[-1])
    return names


def _infer_input_mode_from_discovered_data(
    *,
    analysis_type: str,
    discovered_data_files: list[dict[str, Any]] | None,
) -> str:
    """Infer one input mode from discovered benchmark or user data files."""

    names = _discovered_file_names(discovered_data_files)
    if not names:
        return ""

    has_raw_fastq = any(
        name.endswith((".fastq", ".fastq.gz", ".fq", ".fq.gz"))
        for name in names
    )
    has_count_matrix = any(
        name in _COUNT_MATRIX_FILENAMES
        or any(token in name for token in ("count_matrix", "counts_matrix", "gene_counts"))
        for name in names
    )
    has_metadata = any(name in _METADATA_FILENAMES for name in names)
    has_processed_single_cell = any(
        name.endswith((".h5ad", ".h5mu", ".loom", ".h5"))
        for name in names
    )
    has_aligned_bam = any(name.endswith((".bam", ".cram")) for name in names)
    has_vcf = any(name.endswith((".vcf", ".vcf.gz", ".bcf")) for name in names)
    has_protein_fasta = any(name.endswith((".faa",)) for name in names)

    if analysis_type == "single_cell_rna_seq":
        if has_processed_single_cell:
            return "processed_single_cell"
        if has_raw_fastq:
            return "raw_fastq"
        if has_count_matrix:
            return "count_matrix"
    if analysis_type == "spatial_transcriptomics" and has_processed_single_cell:
        return "processed_single_cell"
    if analysis_type in {"proteomics", "metabolomics"} and has_count_matrix and has_metadata:
        return "count_matrix"
    if analysis_type == "rna_seq_differential_expression":
        if has_raw_fastq:
            return "raw_fastq"
        if has_count_matrix and has_metadata:
            return "count_matrix"
    if analysis_type == "transcript_quantification":
        if has_aligned_bam:
            return "aligned_bam"
        if has_raw_fastq:
            return "raw_fastq"
    if analysis_type == "variant_annotation" and has_vcf:
        return "vcf"
    if analysis_type == "phylogenetics" and has_protein_fasta:
        return "protein_fasta"
    if analysis_type in {"long_read_assembly", "long_read_rna"} and has_raw_fastq:
        return "raw_fastq"
    return ""


def infer_input_mode(
    *,
    user_query: str,
    analysis_type: str,
    explicit_tools: list[str] | None = None,
    discovered_data_files: list[dict[str, Any]] | None = None,
) -> str:
    """Infer the dominant input mode from the request text.

    Args:
        user_query: Raw user request text.
        analysis_type: Canonical analysis family string.
        explicit_tools: Optional normalized explicit tool hints.
        discovered_data_files: Optional discovered input-file records.

    Returns:
        A stable input-mode token such as ``raw_fastq`` or ``count_matrix``.
        Returns ``""`` when no strong mode can be inferred.
    """

    text_l = str(user_query or "").lower()
    tools = _normalize_tool_hints(explicit_tools)
    mode_from_discovered = _infer_input_mode_from_discovered_data(
        analysis_type=analysis_type,
        discovered_data_files=discovered_data_files,
    )
    if mode_from_discovered:
        return mode_from_discovered

    if analysis_type == "single_cell_rna_seq":
        if any(token in text_l for token in _PROCESSED_SINGLE_CELL_TOKENS):
            return "processed_single_cell"
        if "scanpy_workflow" in tools or "seurat_rscript_workflow" in tools:
            return "processed_single_cell"
        if any(token in text_l for token in _RAW_FASTQ_TOKENS):
            return "raw_fastq"

    if analysis_type == "spatial_transcriptomics":
        if any(token in text_l for token in _PROCESSED_SINGLE_CELL_TOKENS):
            return "processed_single_cell"
        if "spatial_transcriptomics_workflow" in tools:
            return "processed_single_cell"

    if analysis_type == "proteomics":
        if any(token in text_l for token in _COUNT_MATRIX_TOKENS):
            return "count_matrix"
        if "proteomics_diff_abundance" in tools:
            return "count_matrix"

    if analysis_type == "metabolomics":
        if any(token in text_l for token in _COUNT_MATRIX_TOKENS):
            return "count_matrix"
        if "metabolomics_diff_abundance" in tools:
            return "count_matrix"

    if analysis_type == "rna_seq_differential_expression":
        has_counts = any(token in text_l for token in _COUNT_MATRIX_TOKENS)
        has_raw_reads = any(token in text_l for token in _RAW_FASTQ_TOKENS)
        if has_raw_reads:
            return "raw_fastq"
        if has_counts:
            return "count_matrix"
        if any(tool in tools for tool in _RNA_SEQ_DE_RAW_FASTQ_HINT_TOOLS):
            return "raw_fastq"

    if analysis_type == "transcript_quantification":
        if any(token in text_l for token in _ALIGNED_BAM_TOKENS) or "stringtie_quant" in tools:
            return "aligned_bam"
        if any(token in text_l for token in _RAW_FASTQ_TOKENS):
            return "raw_fastq"

    if analysis_type == "variant_annotation":
        if any(token in text_l for token in _VCF_TOKENS):
            return "vcf"

    if analysis_type == "phylogenetics":
        if any(token in text_l for token in _PROTEIN_FASTA_TOKENS):
            return "protein_fasta"

    if analysis_type in {"long_read_assembly", "long_read_rna"}:
        if any(token in text_l for token in _RAW_FASTQ_TOKENS):
            return "raw_fastq"

    mode_from_tools = _infer_input_mode_from_tools(analysis_type=analysis_type, tools=tools)
    if mode_from_tools:
        return mode_from_tools

    if any(token in text_l for token in _ALIGNED_BAM_TOKENS):
        return "aligned_bam"
    if any(token in text_l for token in _COUNT_MATRIX_TOKENS):
        return "count_matrix"
    if any(token in text_l for token in _RAW_FASTQ_TOKENS):
        return "raw_fastq"
    return ""


def compatible_tools_for_execution_mode(
    *,
    analysis_type: str,
    input_mode: str,
    available_skill_names: list[str] | None = None,
) -> list[str]:
    """Return wrapper tools compatible with the assay family and input mode.

    Args:
        analysis_type: Canonical analysis family.
        input_mode: Deterministic input-mode token.
        available_skill_names: Optional skill-name filter from the active repo.

    Returns:
        Ordered list of compatible wrapper names.
    """

    candidates = list(_COMPATIBILITY_MAP.get(str(analysis_type or ""), {}).get(str(input_mode or ""), ()))
    available = {str(name).strip() for name in (available_skill_names or []) if str(name).strip()}
    if not available:
        return candidates
    return [tool for tool in candidates if tool in available]


def infer_execution_mode(
    *,
    chosen_method: str,
    input_mode: str,
    explicit_execution_intent: dict[str, Any] | None = None,
) -> str:
    """Infer whether the request should behave like a direct wrapper or pipeline.

    Args:
        chosen_method: Final normalized chosen-method string.
        input_mode: Deterministic input-mode token.
        explicit_execution_intent: Explicit execution-intent metadata.

    Returns:
        ``direct_wrapper`` or ``compiled_pipeline`` when a stable inference can
        be made, otherwise ``""``.
    """

    intent = explicit_execution_intent if isinstance(explicit_execution_intent, dict) else {}
    locked_tools = [str(tool).strip() for tool in (intent.get("locked_tools", []) or []) if str(tool).strip()]
    method = str(chosen_method or "").strip()
    if locked_tools and len(locked_tools) == 1:
        locked_tool = locked_tools[0]
        if wrapper_lock_requires_evidence(locked_tool) and not wrapper_supports_input_mode(
            locked_tool,
            input_mode,
        ):
            return ""
        return "direct_wrapper"
    if " + " in method or "+" in method:
        return "compiled_pipeline"
    if method in _RAW_FASTQ_DIRECT_WRAPPERS:
        return "direct_wrapper"
    if input_mode in {"count_matrix", "aligned_bam", "processed_single_cell", "vcf", "protein_fasta"}:
        return "direct_wrapper"
    if input_mode == "raw_fastq":
        return "compiled_pipeline"
    if method:
        if wrapper_lock_requires_evidence(method):
            return "direct_wrapper" if wrapper_supports_input_mode(method, input_mode) else ""
        return "direct_wrapper"
    return ""


def build_execution_contract(
    *,
    analysis_type: str,
    user_query: str,
    chosen_method: str,
    contract: dict[str, Any] | None,
    explicit_execution_intent: dict[str, Any] | None,
    available_skill_names: list[str] | None = None,
    discovered_data_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the normalized execution contract for one analysis spec.

    Args:
        analysis_type: Canonical analysis family.
        user_query: Raw request text.
        chosen_method: Final chosen-method string.
        contract: Plan contract inferred from the request.
        explicit_execution_intent: Explicit tool/output lock metadata.
        available_skill_names: Optional active repo skill-name filter.
        discovered_data_files: Optional discovered input-file records.

    Returns:
        A normalized execution-contract dict suitable for persistence.
    """

    explicit_intent = (
        explicit_execution_intent if isinstance(explicit_execution_intent, dict) else {}
    )
    locked_tools = [
        str(tool).strip()
        for tool in (explicit_intent.get("locked_tools", []) or [])
        if str(tool).strip()
    ]
    required_tools = [
        str(tool).strip()
        for tool in ((contract or {}).get("required_tool_hints", []) if isinstance(contract, dict) else [])
        if str(tool).strip()
    ]
    blocked_tools = [
        str(tool).strip()
        for tool in ((contract or {}).get("blocked_tool_hints", []) if isinstance(contract, dict) else [])
        if str(tool).strip()
    ]
    input_mode = infer_input_mode(
        user_query=user_query,
        analysis_type=analysis_type,
        explicit_tools=locked_tools or required_tools or ([chosen_method] if str(chosen_method).strip() else []),
        discovered_data_files=discovered_data_files,
    )
    locked_tools = [
        tool
        for tool in locked_tools
        if not wrapper_lock_requires_evidence(tool) or wrapper_supports_input_mode(tool, input_mode)
    ]
    explicit_intent = {**explicit_intent, "locked_tools": locked_tools}
    execution_mode = infer_execution_mode(
        chosen_method=chosen_method,
        input_mode=input_mode,
        explicit_execution_intent=explicit_intent,
    )
    compatible_tools = compatible_tools_for_execution_mode(
        analysis_type=analysis_type,
        input_mode=input_mode,
        available_skill_names=available_skill_names,
    )
    if locked_tools:
        compatible_tools = [
            tool
            for tool in compatible_tools
            if tool in locked_tools
        ] or locked_tools
    elif len(required_tools) == 1:
        compatible_tools = [
            tool
            for tool in compatible_tools
            if tool == required_tools[0]
        ] or list(required_tools)
    return {
        "analysis_family": str(analysis_type or "").strip(),
        "input_mode": input_mode,
        "execution_mode": execution_mode,
        "compatible_tools": compatible_tools,
        "locked_tools": locked_tools,
        "required_tools": required_tools,
        "blocked_tools": blocked_tools,
    }


__all__ = [
    "build_execution_contract",
    "compatible_tools_for_execution_mode",
    "infer_execution_mode",
    "infer_input_mode",
]
