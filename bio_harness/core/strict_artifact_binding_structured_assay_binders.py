"""Strict benchmark binder helpers for structured assay workflows."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict

from bio_harness.core.strict_artifact_binding_benchmark_helpers import _benchmark_task_data_dir
from bio_harness.core.strict_artifact_binding_command_builders import (
    _build_germline_verify_command,
    _copy_step_with_arguments,
)
from bio_harness.core.strict_artifact_binding_paths import (
    RnaSeqDeArtifactPaths,
    _build_germline_variant_paths,
    _build_rna_seq_de_paths,
    _build_single_cell_paths,
)

if TYPE_CHECKING:
    from bio_harness.core.strict_artifact_binding import StrictArtifactBindingContext


def _structured_assay_data_root(ctx: StrictArtifactBindingContext) -> Path | None:
    """Return the runtime data root for structured assay strict binding."""

    for key in ("requested_data_root", "data_root"):
        raw_value = str(ctx.analysis_spec.get(key, "") or "").strip()
        if raw_value:
            return Path(raw_value).expanduser()
    return _benchmark_task_data_dir(ctx.selected_dir)


def _sample_id_from_bam_path(bam_path: str) -> str:
    """Return the RNA-seq sample identifier encoded in a BAM path."""

    return Path(str(bam_path)).stem


def _rna_seq_de_sample_pairs(paths: RnaSeqDeArtifactPaths) -> list[tuple[str, str]]:
    """Return ordered ``(sample_id, bam_path)`` pairs from strict DE paths."""

    pairs: list[tuple[str, str]] = []
    for bam_path in paths.bam_paths:
        sample_id = _sample_id_from_bam_path(bam_path)
        if sample_id:
            pairs.append((sample_id, bam_path))
    return pairs


def _find_rna_seq_read_path(data_dir: Path, sample_id: str, mate: int) -> str:
    """Return the best existing FASTQ path for a sample mate."""

    mate_tokens = (str(mate), f"R{mate}", f"read{mate}")
    suffixes = (".fastq", ".fq", ".fastq.gz", ".fq.gz")
    for token in mate_tokens:
        for suffix in suffixes:
            candidate = data_dir / f"{sample_id}_{token}{suffix}"
            if candidate.exists():
                return str(candidate.resolve(strict=False))
    return str((data_dir / f"{sample_id}_{mate}.fastq").resolve(strict=False))


def _requested_rna_seq_de_sample_id(
    *,
    step_spec: Dict[str, Any],
    args: Dict[str, Any],
    ctx: StrictArtifactBindingContext,
    paths: RnaSeqDeArtifactPaths,
) -> str:
    """Infer the sample requested by a planner candidate, if it names one."""

    hint_parts: list[str] = [
        str(ctx.branch_id or ""),
        str(ctx.objective or ""),
        str(step_spec.get("branch_id", "") or ""),
        str(step_spec.get("sample_name", "") or ""),
        str(step_spec.get("sample_id", "") or ""),
        str(args.get("sample_name", "") or ""),
        str(args.get("sample_id", "") or ""),
        str(args.get("reads_1", "") or ""),
        str(args.get("reads_2", "") or ""),
        str(args.get("output_bam", "") or ""),
    ]
    parameter_hints = step_spec.get("parameter_hints", {})
    if isinstance(parameter_hints, dict):
        hint_parts.extend(str(value or "") for value in parameter_hints.values())
    haystack = " ".join(hint_parts).lower()
    if not haystack.strip():
        return ""

    for sample_id, _bam_path in _rna_seq_de_sample_pairs(paths):
        if sample_id.lower() in haystack:
            return sample_id
    return ""


def _next_rna_seq_de_sample_id(paths: RnaSeqDeArtifactPaths) -> str:
    """Return the first sample whose strict BAM is not present yet."""

    pairs = _rna_seq_de_sample_pairs(paths)
    for sample_id, bam_path in pairs:
        if not Path(bam_path).exists():
            return sample_id
    return pairs[0][0] if pairs else ""


def _bind_rna_seq_de_subread_alignment(
    *,
    step_spec: Dict[str, Any],
    args: Dict[str, Any],
    ctx: StrictArtifactBindingContext,
    paths: RnaSeqDeArtifactPaths,
) -> Dict[str, Any]:
    """Bind one Subread alignment step to the requested or next missing sample."""

    sample_id = _requested_rna_seq_de_sample_id(
        step_spec=step_spec,
        args=args,
        ctx=ctx,
        paths=paths,
    ) or _next_rna_seq_de_sample_id(paths)
    data_dir = Path(paths.metadata_tsv).parent
    return {
        "index_base": paths.index_base,
        "reference_fasta": paths.reference_fasta,
        "reads_1": _find_rna_seq_read_path(data_dir, sample_id, 1),
        "reads_2": _find_rna_seq_read_path(data_dir, sample_id, 2),
        "output_bam": str((Path(paths.alignments_dir) / f"{sample_id}.bam").resolve(strict=False)),
        "threads": int(args.get("threads", 8) or 8),
    }


def _rna_seq_de_strand_specificity(
    *,
    args: Dict[str, Any],
    ctx: StrictArtifactBindingContext,
) -> int:
    """Return featureCounts strandedness without guessing protocol metadata."""

    candidate_values = [
        args.get("strand_specificity"),
        args.get("strandedness"),
        ctx.analysis_spec.get("strand_specificity"),
        ctx.analysis_spec.get("strandedness"),
        ctx.analysis_spec.get("library_strandedness"),
    ]
    for raw_value in candidate_values:
        normalized = str(raw_value or "").strip().lower()
        if not normalized:
            continue
        if normalized in {"0", "unstranded", "none", "false", "no"}:
            return 0
        if normalized in {"1", "forward", "yes", "true", "fr", "stranded"}:
            return 1
        if normalized in {"2", "reverse", "rf", "reverse_stranded", "reversely_stranded"}:
            return 2
    return 0


def _bind_rna_seq_differential_expression(
    step_spec: Dict[str, Any],
    ctx: StrictArtifactBindingContext,
) -> Dict[str, Any]:
    """Bind strict RNA-seq DE artifacts onto deterministic benchmark paths."""

    constrained, args = _copy_step_with_arguments(step_spec)
    paths = _build_rna_seq_de_paths(
        selected_dir=ctx.selected_dir,
        data_root=_structured_assay_data_root(ctx),
    )
    if paths is None:
        constrained["arguments"] = args
        return constrained

    if ctx.tool_name == "subread_align":
        args = _bind_rna_seq_de_subread_alignment(
            step_spec=step_spec,
            args=args,
            ctx=ctx,
            paths=paths,
        )
    elif ctx.tool_name == "featurecounts_run":
        args["input_bams"] = list(paths.bam_paths)
        args["annotation_gtf"] = paths.annotation_gff
        args["annotation_format"] = "GFF"
        args["feature_type"] = "gene"
        args["attribute_type"] = "ID"
        args["output_counts"] = paths.counts_path
        args["count_read_pairs"] = True
        args["is_paired_end"] = True
        args["strand_specificity"] = _rna_seq_de_strand_specificity(args=args, ctx=ctx)
        args["threads"] = 8
    elif ctx.tool_name == "deseq2_run":
        args["counts_matrix"] = paths.counts_path
        args["metadata_table"] = paths.metadata_tsv
        args["design_formula"] = "~ condition"
        args["contrast"] = paths.contrast
        args["output_dir"] = paths.deseq_output_dir
        args["engine"] = "pydeseq2"

    constrained["arguments"] = args
    return constrained


def _bind_germline_variant_calling(
    step_spec: Dict[str, Any],
    ctx: StrictArtifactBindingContext,
) -> Dict[str, Any]:
    """Bind strict germline-variant steps onto deterministic benchmark paths."""

    constrained, args = _copy_step_with_arguments(step_spec)
    paths = _build_germline_variant_paths(
        selected_dir=ctx.selected_dir,
        data_root=_structured_assay_data_root(ctx),
    )
    if paths is None:
        constrained["arguments"] = args
        return constrained

    if ctx.tool_name == "bwa_mem_align":
        args["reference_fasta"] = paths.reference_fasta
        args["reads_1"] = paths.reads_1
        args["reads_2"] = paths.reads_2
        args["output_bam"] = paths.aligned_bam
        args.setdefault("postprocess_mode", "fixmate_markdup_q20")
    elif ctx.tool_name == "gatk_haplotypecaller":
        args["reference_fasta"] = paths.reference_fasta
        args["input_bam"] = paths.aligned_bam
        args["output_vcf"] = paths.final_vcf
        args.pop("emit_ref_confidence", None)
        args.pop("mode", None)
    elif ctx.tool_name == "bash_run":
        objective_l = str(ctx.objective or "").strip().lower()
        command_l = str(args.get("command", "") or "").strip().lower()
        if "hap.py" in command_l or "benchmark" in objective_l or "truth set" in objective_l:
            args["command"] = _build_germline_verify_command(paths)

    constrained["arguments"] = args
    return constrained


def _bind_single_cell_rna_seq(
    step_spec: Dict[str, Any],
    ctx: StrictArtifactBindingContext,
) -> Dict[str, Any]:
    """Bind strict single-cell steps onto deterministic benchmark paths."""

    constrained, args = _copy_step_with_arguments(step_spec)
    paths = _build_single_cell_paths(
        selected_dir=ctx.selected_dir,
        data_root=_structured_assay_data_root(ctx),
    )
    if paths is None:
        constrained["arguments"] = args
        return constrained

    if ctx.tool_name == "sc_count_and_cluster":
        args["r1"] = paths.r1_fastq
        args["r2"] = paths.r2_fastq
        if paths.whitelist:
            args["whitelist"] = paths.whitelist
        else:
            args.pop("whitelist", None)
        args["reference"] = paths.reference_fasta
        args["gtf"] = paths.annotation_gtf
        args["output_dir"] = paths.output_dir

    constrained["arguments"] = args
    return constrained
