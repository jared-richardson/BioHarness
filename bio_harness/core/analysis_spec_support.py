from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, TypedDict

from pydantic import BaseModel, Field


class ProfileSeed(TypedDict, total=False):
    """Schema for analysis-type profile seed dicts.

    Every profile builder must return a dict conforming to this shape.
    ``total=False`` because some fields are optional (plan_skeleton, etc.).
    """

    biological_objective: str
    context_facts: list[str]
    candidate_methods: list[str]
    chosen_method: str
    preferred_tools: list[str]
    discouraged_tools: list[str]
    parameter_profile: list[dict[str, Any]]
    acceptance_checks: list[str]
    rerun_triggers: list[str]
    source_provenance: list[str]
    open_risks: list[str]
    plan_skeleton: list[tuple[str, str, dict[str, Any]]]

HIGH_IMPACT_CAPABILITIES = {
    "variant_calling",
    "structural_variant_calling",
    "differential_analysis",
    "splicing_analysis",
    "quantification",
}

METAGENOMICS_KMER_HELPER_SCRIPT = Path(__file__).resolve().parents[1] / "pipeline_scripts" / "classify_metagenomics_kmer.py"
PHYLOGENY_HELPER_SCRIPT = Path(__file__).resolve().parents[1] / "pipeline_scripts" / "infer_phylogeny_biopython.py"
VIRAL_KMER_HELPER_SCRIPT = Path(__file__).resolve().parents[1] / "pipeline_scripts" / "classify_viral_reads_kmer.py"
VIRAL_PAF_SUMMARY_HELPER_SCRIPT = Path(__file__).resolve().parents[1] / "pipeline_scripts" / "summarize_viral_paf.py"
SAMPLE_METADATA_HELPER_SCRIPT = Path(__file__).resolve().parents[1] / "pipeline_scripts" / "write_sample_metadata_table.py"
COMPARE_PATHWAYS_HELPER_SCRIPT = Path(__file__).resolve().parents[1] / "pipeline_scripts" / "compare_pathways.py"

# Canonical analysis-type strings (must match TEMPLATE_COMPILER_TYPES in
# protocol_grounding.py).  When the LLM returns a non-canonical string we
# try fuzzy-matching to one of these.
CANONICAL_ANALYSIS_TYPES: frozenset[str] = frozenset({
    "artifact_schema_profiling",
    "bacterial_evolution_variant_calling",
    "direct_skill_smoke",
    "long_read_assembly",
    "long_read_rna",
    "metabolomics",
    "proteomics",
    "rna_seq_differential_expression",
    "transcript_quantification",
    "metagenomics_classification",
    "single_cell_rna_seq",
    "spatial_transcriptomics",
    "germline_variant_calling",
    "variant_annotation",
    "comparative_genomics",
    "viral_metagenomics",
    "multi_model_dge_pathway",
    "phylogenetics",
    "run_reporting",
    "somatic_variant_calling",
    "structural_variant_calling",
})

_DIRECT_SKILL_SMOKE_TOKENS: tuple[str, ...] = (
    "direct one-step skill smoke test",
    "direct one step skill smoke test",
    "one-step skill smoke test",
    "one step skill smoke test",
    "skill smoke test",
)

_DATA_EXTENSIONS = frozenset({
    ".fastq", ".fastq.gz", ".fq", ".fq.gz",
    ".fa", ".fa.gz", ".fasta", ".fasta.gz", ".fna", ".fna.gz",
    ".gtf", ".gtf.gz", ".gff", ".gff.gz", ".gff3", ".gff3.gz",
    ".bam", ".bam.bai", ".cram",
    ".vcf", ".vcf.gz",
    ".bed", ".bed.gz",
    ".csv", ".tsv", ".txt",
    ".sam",
    ".sra",
    ".h5", ".h5ad", ".loom",
    ".tar.gz",
})


def preferred_helper_python_executable() -> Path:
    """Return the preferred Python executable for helper-backed benchmark steps.

    Returns:
        The repo-local Pixi Python when available, otherwise the current Python
        interpreter.
    """
    project_root = Path(__file__).resolve().parents[2]
    pixi_python = project_root / ".pixi" / "envs" / "default" / "bin" / "python"
    if pixi_python.exists():
        return pixi_python.resolve(strict=False)
    return Path(sys.executable).resolve(strict=False)


def managed_python_command_parts(
    *,
    python_executable: str,
    script_path: str,
) -> list[str]:
    """Return shell command parts for one managed Python script.

    Bundled repo-local helpers are often executed from benchmark output
    directories rather than the repository root. When the script lives inside
    this repository, prepend the project root to ``PYTHONPATH`` so package
    imports remain stable. External scripts are left untouched.

    Args:
        python_executable: Python executable used to launch the script.
        script_path: Absolute or user-provided script path.

    Returns:
        Shell command parts that launch the script with the correct environment.
    """

    python_bin = str(python_executable or "").strip()
    script_text = str(script_path or "").strip()
    resolved_script = Path(script_text).expanduser().resolve(strict=False)
    project_root = Path(__file__).resolve().parents[2]
    try:
        resolved_script.relative_to(project_root)
    except ValueError:
        return [python_bin, script_text]
    return ["env", f"PYTHONPATH={project_root}", python_bin, str(resolved_script)]


def is_direct_skill_smoke_query(user_query: str) -> bool:
    query_l = str(user_query or "").lower()
    return any(token in query_l for token in _DIRECT_SKILL_SMOKE_TOKENS)


def _canonicalize_analysis_type(raw_type: str, heuristic_type: str) -> str:
    """Map LLM-generated analysis type to canonical string.

    If *raw_type* already matches a canonical entry, return it unchanged.
    Otherwise, try to find a canonical type that is a prefix/substring of
    *raw_type* (e.g. ``"variant_annotation_and_filtering"`` →
    ``"variant_annotation"``).  If that fails, fall back to *heuristic_type*
    (from :func:`detect_analysis_type`).
    """
    if raw_type in CANONICAL_ANALYSIS_TYPES:
        if (
            heuristic_type in {"long_read_assembly", "long_read_rna", "metabolomics", "proteomics"}
            and raw_type in {
                "comparative_genomics",
                "transcript_quantification",
                "rna_seq_differential_expression",
                "protein_analysis",
                "generic_analysis",
            }
        ):
            return heuristic_type
        return raw_type
    # Explicit aliases for common LLM misclassifications.
    # The LLM often returns bare "variant_calling" when it means annotation
    # or germline VC, or bare "differential_expression" for RNA-seq DE.
    _ALIASES: dict[str, str] = {
        "variant_calling": "germline_variant_calling",
        "differential_expression": "rna_seq_differential_expression",
    }
    if raw_type in _ALIASES:
        aliased = _ALIASES[raw_type]
        # Only use the alias if the heuristic type doesn't provide a better match
        if heuristic_type and heuristic_type in CANONICAL_ANALYSIS_TYPES:
            return heuristic_type
        return aliased
    # Try longest-prefix match
    candidates = sorted(
        (c for c in CANONICAL_ANALYSIS_TYPES if raw_type.startswith(c)),
        key=len,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    # Try substring match
    candidates = sorted(
        (c for c in CANONICAL_ANALYSIS_TYPES if c in raw_type),
        key=len,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    # Fall back to heuristic detection
    return heuristic_type if heuristic_type else raw_type

CALLER_LIKE_TOOLS = {
    "freebayes_call",
    "bcftools_call",
    "gatk_haplotypecaller",
    "gatk_mutect2_call",
    "varscan_call",
}


class ParameterRecommendation(BaseModel):
    tool_name: str = Field(default="", description="Tool name associated with the recommendation.")
    settings: Dict[str, Any] = Field(default_factory=dict, description="Recommended arguments or flags.")
    rationale: str = Field(default="", description="Short rationale for the recommendation.")


class AnalysisSpecSchema(BaseModel):
    analysis_type: str = Field(default="generic_analysis")
    benchmark_policy: str = Field(default="scientific_harness")
    biological_objective: str = Field(default="")
    context_facts: List[str] = Field(default_factory=list)
    candidate_methods: List[str] = Field(default_factory=list)
    chosen_method: str = Field(default="")
    preferred_tools: List[str] = Field(default_factory=list)
    discouraged_tools: List[str] = Field(default_factory=list)
    parameter_profile: List[ParameterRecommendation] = Field(default_factory=list)
    acceptance_checks: List[str] = Field(default_factory=list)
    rerun_triggers: List[str] = Field(default_factory=list)
    source_provenance: List[str] = Field(default_factory=list)
    open_risks: List[str] = Field(default_factory=list)
    plan_skeleton: List[Any] = Field(default_factory=list)
    protocol_grounding: Dict[str, Any] = Field(default_factory=dict)
    requested_output_paths: List[str] = Field(default_factory=list)
    required_deliverables: List[str] = Field(default_factory=list)
    explicit_execution_intent: Dict[str, Any] = Field(default_factory=dict)
    execution_contract: Dict[str, Any] = Field(default_factory=dict)


def _dedupe(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


__all__ = [name for name in globals() if not name.startswith("__")]
