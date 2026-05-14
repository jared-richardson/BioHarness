"""Strict benchmark binder helpers for bash-oriented artifact rebinding."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict

from bio_harness.core.strict_artifact_binding_benchmark_helpers import (
    _benchmark_task_data_dir,
    _build_metagenomics_command,
    _build_multi_model_compare_command,
    _build_multi_model_verify_command,
    _build_phylogenetics_command,
    _build_viral_metagenomics_command,
    _discover_primary_fastq_pair,
)
from bio_harness.core.strict_artifact_binding_command_builders import _copy_step_with_arguments

if TYPE_CHECKING:
    from bio_harness.core.strict_artifact_binding import StrictArtifactBindingContext


def _requested_data_root(ctx: StrictArtifactBindingContext) -> Path | None:
    """Return the runtime data root declared by the analysis spec."""

    raw = str(ctx.analysis_spec.get("requested_data_root", "") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve(strict=False)


def _bind_multi_model_dge_pathway(
    step_spec: Dict[str, Any],
    ctx: StrictArtifactBindingContext,
) -> Dict[str, Any]:
    """Bind strict Alzheimer multi-model steps onto the deterministic helper flow."""

    constrained, args = _copy_step_with_arguments(step_spec)
    if ctx.selected_dir is None or ctx.tool_name != "bash_run":
        constrained["arguments"] = args
        return constrained

    objective_l = str(ctx.objective or "").strip().lower()
    command = str(args.get("command", "") or "").strip()
    command_l = command.lower()
    compare_command = _build_multi_model_compare_command(
        selected_dir=ctx.selected_dir,
        data_root=_requested_data_root(ctx) or _benchmark_task_data_dir(ctx.selected_dir),
    )

    is_compare_role = (
        "differential expression" in objective_l
        or "compare_pathways.py" in objective_l
        or "run dge" in objective_l
        or "load count matrices" in objective_l
        or "generate per-model de result tables" in objective_l
        or "compare_pathways.py" in command_l
    )
    is_verification_role = (
        "pathway enrichment" in objective_l
        or "aggregate results" in objective_l
        or ("pathway_comparison.csv" in command_l and not is_compare_role)
    )

    if is_compare_role:
        if compare_command is not None:
            args["command"] = compare_command
    elif is_verification_role:
        args["command"] = _build_multi_model_verify_command(selected_dir=ctx.selected_dir)
    constrained["arguments"] = args
    return constrained


def _bind_phylogenetics(
    step_spec: Dict[str, Any],
    ctx: StrictArtifactBindingContext,
) -> Dict[str, Any]:
    """Bind strict phylogenetics steps onto the deterministic helper scaffold."""

    constrained, args = _copy_step_with_arguments(step_spec)
    if ctx.selected_dir is None or ctx.tool_name != "bash_run":
        constrained["arguments"] = args
        return constrained

    objective_l = str(ctx.objective or "").strip().lower()
    command_l = str(args.get("command", "") or "").strip().lower()
    if not (
        "phylogen" in objective_l
        or "newick" in objective_l
        or "tree" in objective_l
        or "infer_phylogeny_biopython.py" in command_l
    ):
        constrained["arguments"] = args
        return constrained

    command = _build_phylogenetics_command(
        selected_dir=ctx.selected_dir,
        data_root=_requested_data_root(ctx) or _benchmark_task_data_dir(ctx.selected_dir),
    )
    if command is not None:
        args["command"] = command
    constrained["arguments"] = args
    return constrained


def _bind_metagenomics_classification(
    step_spec: Dict[str, Any],
    ctx: StrictArtifactBindingContext,
) -> Dict[str, Any]:
    """Bind strict metagenomics classification onto the helper scaffold."""

    constrained, args = _copy_step_with_arguments(step_spec)
    if ctx.selected_dir is None:
        constrained["arguments"] = args
        return constrained

    data_root = _requested_data_root(ctx) or _benchmark_task_data_dir(ctx.selected_dir)
    if ctx.tool_name == "spades_assemble":
        reads = _discover_primary_fastq_pair(data_root)
        if reads is not None:
            args["reads_1"], args["reads_2"] = reads
        args["output_dir"] = str((ctx.selected_dir / "assembly" / "metaspades").resolve(strict=False))
        args["meta_mode"] = True
        args.setdefault("threads", 8)
        args.setdefault("memory_gb", 32)
        constrained["arguments"] = args
        return constrained

    if ctx.tool_name != "bash_run":
        constrained["arguments"] = args
        return constrained

    objective_l = str(ctx.objective or "").strip().lower()
    command_l = str(args.get("command", "") or "").strip().lower()
    if not (
        "metagenomic" in objective_l
        or "taxonomic" in objective_l
        or "community composition" in objective_l
        or "classify" in objective_l
        or "classify_metagenomics_kmer.py" in command_l
    ):
        constrained["arguments"] = args
        return constrained

    command = _build_metagenomics_command(
        selected_dir=ctx.selected_dir,
        data_root=data_root,
    )
    if command is not None:
        args["command"] = command
    constrained["arguments"] = args
    return constrained


def _bind_viral_metagenomics(
    step_spec: Dict[str, Any],
    ctx: StrictArtifactBindingContext,
) -> Dict[str, Any]:
    """Bind strict viral metagenomics classification onto the helper scaffold."""

    constrained, args = _copy_step_with_arguments(step_spec)
    if ctx.selected_dir is None or ctx.tool_name != "bash_run":
        constrained["arguments"] = args
        return constrained

    objective_l = str(ctx.objective or "").strip().lower()
    command_l = str(args.get("command", "") or "").strip().lower()
    if not (
        "virus" in objective_l
        or "viral" in objective_l
        or "coverage" in objective_l
        or "abundance" in objective_l
        or "classify_viral_reads_kmer.py" in command_l
    ):
        constrained["arguments"] = args
        return constrained

    data_root = _requested_data_root(ctx) or _benchmark_task_data_dir(ctx.selected_dir)
    reference_dir = data_root.parent / "references" if data_root is not None else None
    command = _build_viral_metagenomics_command(
        selected_dir=ctx.selected_dir,
        data_root=data_root,
        reference_dir=reference_dir,
    )
    if command is not None:
        args["command"] = command
    constrained["arguments"] = args
    return constrained
