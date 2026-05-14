"""Variant-oriented strict artifact binders."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bio_harness.core.cystic_fibrosis_scaffold import is_cystic_fibrosis_variant_analysis
from bio_harness.core.protocol_grounding._shared import (
    SHARED_VARIANT_EXPORTER,
    _build_normalize_vcf_command,
    _build_variant_filter_command,
)
from bio_harness.core.strict_artifact_binding_benchmark_helpers import _benchmark_task_data_dir
from bio_harness.core.strict_artifact_binding_command_builders import _copy_step_with_arguments
from bio_harness.core.strict_artifact_binding_direct_steps import _normalize_cystic_fibrosis_bash_command

if TYPE_CHECKING:
    from bio_harness.core.strict_artifact_binding import StrictArtifactBindingContext


def _is_cystic_fibrosis_variant_task(ctx: "StrictArtifactBindingContext") -> bool:
    """Detect whether a strict variant task is the cystic-fibrosis benchmark."""

    return is_cystic_fibrosis_variant_analysis(
        analysis_spec=ctx.analysis_spec,
        selected_dir=ctx.selected_dir,
        objective=ctx.objective,
    )


def _discover_evolution_annotation_gff(annotation_dir: Path) -> Path:
    """Return the preferred existing annotation GFF for evolution SnpEff.

    Prodigal writes the deterministic ``genes.gff`` scaffold, while Prokka
    writes ``{sample_prefix}.gff`` and the stepwise harness currently uses the
    stable prefix ``ancestor`` for the assembled reference. Prefer whichever
    concrete producer output exists, then fall back to the Prodigal path so
    runs that have not produced an annotation yet still advertise the
    conventional missing prerequisite.

    Args:
        annotation_dir: Strict-mode annotation directory under ``selected_dir``.

    Returns:
        Existing producer GFF path when available, otherwise the canonical
        Prodigal ``genes.gff`` path.
    """

    preferred_names = (
        "ancestor.gff",
        "genes.gff",
        "ancestor.gff3",
        "genes.gff3",
        "reference.gff",
        "reference.gff3",
    )
    for name in preferred_names:
        candidate = annotation_dir / name
        if candidate.exists():
            return candidate.resolve(strict=False)

    try:
        existing_gffs = sorted(
            path
            for pattern in ("*.gff", "*.gff3")
            for path in annotation_dir.glob(pattern)
            if path.is_file() and ".tmp." not in path.name
        )
    except OSError:
        existing_gffs = []
    if existing_gffs:
        return existing_gffs[0].resolve(strict=False)
    return (annotation_dir / "genes.gff").resolve(strict=False)


def _discover_evolution_reference_fasta(selected_dir: Path) -> Path:
    """Return the preferred SPAdes reference FASTA for evolution workflows.

    SPAdes normally advertises both ``scaffolds.fasta`` and ``contigs.fasta``,
    but tiny or low-complexity inputs can legitimately produce only contigs.
    Prefer a non-empty scaffold file when present, otherwise use the non-empty
    contig file before falling back to the canonical scaffold path for missing
    prerequisite messaging.

    Args:
        selected_dir: Strict-mode selected run directory.

    Returns:
        Existing assembly FASTA path when available, otherwise the canonical
        scaffold path.
    """

    assembly_dir = selected_dir / "assembly"
    scaffolds = assembly_dir / "scaffolds.fasta"
    contigs = assembly_dir / "contigs.fasta"
    if _is_non_empty_file(scaffolds):
        return scaffolds.resolve(strict=False)
    if _is_non_empty_file(contigs):
        return contigs.resolve(strict=False)
    return scaffolds.resolve(strict=False)


def _discover_evolution_annotated_vcf(
    *,
    selected_dir: Path,
    variants_dir: Path,
    branch_id: str,
    canonical_path: Path,
) -> Path:
    """Return the concrete annotated VCF produced for one evolution branch.

    Stepwise plans can reach a valid annotated branch artifact while preserving
    a planner-chosen path such as ``selected/evol1.annotated.vcf.gz``. Prefer
    the canonical strict path when it exists, but consume already-produced
    branch-local outputs before advertising a missing canonical file.

    Args:
        selected_dir: Strict-mode selected run directory.
        variants_dir: Strict-mode variants directory under ``selected_dir``.
        branch_id: Evolution branch identifier such as ``evol1`` or ``evol2``.
        canonical_path: Canonical strict annotated VCF path for fallback
            missing-prerequisite messages.

    Returns:
        Existing annotated VCF path when available, otherwise
        ``canonical_path``.
    """

    branch = str(branch_id or "").strip()
    if not branch:
        return canonical_path.resolve(strict=False)
    candidates = (
        variants_dir / f"{branch}.annotated.vcf",
        variants_dir / f"{branch}.annotated.vcf.gz",
        selected_dir / f"{branch}.annotated.vcf",
        selected_dir / f"{branch}.annotated.vcf.gz",
    )
    for candidate in candidates:
        if _is_non_empty_file(candidate):
            return candidate.resolve(strict=False)
    return canonical_path.resolve(strict=False)


def _is_non_empty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _infer_evolution_branch_from_context(
    *,
    branch_id: str,
    objective: str,
    arguments: dict[str, Any],
) -> str:
    """Infer an evolution branch from explicit metadata or concrete paths.

    Args:
        branch_id: Planner-supplied branch identifier.
        objective: Planner-supplied step objective.
        arguments: Current step arguments after shallow copy.

    Returns:
        ``"evol1"``, ``"evol2"``, or ``"ancestor"`` when the branch is
        unambiguous; otherwise ``""``.
    """

    branch_id_lower = str(branch_id or "").strip().lower()
    objective_lower = str(objective or "").lower()
    if "evol1" in branch_id_lower:
        return "evol1"
    if "evol2" in branch_id_lower:
        return "evol2"
    if branch_id_lower in {"anc", "ancestor"}:
        return "ancestor"
    if "evol1" in objective_lower:
        return "evol1"
    if "evol2" in objective_lower:
        return "evol2"
    if "ancestor" in objective_lower and "evol" not in objective_lower:
        return "ancestor"

    path_text = _flatten_argument_text(arguments).lower()
    has_evol1 = "evol1" in path_text
    has_evol2 = "evol2" in path_text
    if has_evol1 and not has_evol2:
        return "evol1"
    if has_evol2 and not has_evol1:
        return "evol2"
    if not has_evol1 and not has_evol2:
        tokens = {
            item.strip("._-/ ")
            for item in path_text.replace("\\", "/").replace(".", "/").split("/")
        }
        if {"anc", "ancestor"} & tokens:
            return "ancestor"
    return ""


def _flatten_argument_text(value: Any) -> str:
    """Return a space-joined text view of nested step argument values."""

    if isinstance(value, dict):
        return " ".join(_flatten_argument_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_argument_text(item) for item in value)
    if isinstance(value, str):
        return value
    return ""


def _bind_bacterial_evolution_variant_calling(
    step_spec: dict[str, Any],
    ctx: "StrictArtifactBindingContext",
) -> dict[str, Any]:
    """Bind evolution-task artifacts onto the strict deterministic scaffold."""

    constrained, args = _copy_step_with_arguments(step_spec)

    if ctx.selected_dir is None:
        constrained["arguments"] = args
        return constrained

    selected_dir = ctx.selected_dir
    reference_fasta = str(_discover_evolution_reference_fasta(selected_dir))
    annotation_dir = selected_dir / "annotation"
    prodigal_annotation_gff = str((annotation_dir / "genes.gff").resolve(strict=False))
    annotation_gff = str(_discover_evolution_annotation_gff(annotation_dir))
    annotation_faa = str((annotation_dir / "proteins.faa").resolve(strict=False))
    snpeff_config_dir = str((annotation_dir / "_snpeff").resolve(strict=False))
    alignments_dir = selected_dir / "alignments"
    variants_dir = selected_dir / "variants"
    final_dir = selected_dir / "final"

    anc_bam = str((alignments_dir / "anc_aligned.bam").resolve(strict=False))
    evol1_bam = str((alignments_dir / "evol1_aligned.bam").resolve(strict=False))
    evol2_bam = str((alignments_dir / "evol2_aligned.bam").resolve(strict=False))
    anc_raw = str((variants_dir / "anc_raw.vcf").resolve(strict=False))
    evol1_raw = str((variants_dir / "evol1_raw.vcf").resolve(strict=False))
    evol2_raw = str((variants_dir / "evol2_raw.vcf").resolve(strict=False))
    anc_filtered = str((variants_dir / "anc.filtered.vcf.gz").resolve(strict=False))
    evol1_filtered = str((variants_dir / "evol1.filtered.vcf.gz").resolve(strict=False))
    evol2_filtered = str((variants_dir / "evol2.filtered.vcf.gz").resolve(strict=False))
    evol1_sub = str((variants_dir / "evol1.ancestor_subtracted.vcf.gz").resolve(strict=False))
    evol2_sub = str((variants_dir / "evol2.ancestor_subtracted.vcf.gz").resolve(strict=False))
    evol1_isec_dir = str(
        (variants_dir / ".isec_evol1.ancestor_subtracted").resolve(strict=False)
    )
    evol2_isec_dir = str(
        (variants_dir / ".isec_evol2.ancestor_subtracted").resolve(strict=False)
    )
    evol1_annotated_path = (variants_dir / "evol1.annotated.vcf").resolve(
        strict=False
    )
    evol2_annotated_path = (variants_dir / "evol2.annotated.vcf").resolve(
        strict=False
    )
    evol1_annotated = str(evol1_annotated_path)
    evol2_annotated = str(evol2_annotated_path)
    evol1_annotated_for_norm = str(
        _discover_evolution_annotated_vcf(
            selected_dir=selected_dir,
            variants_dir=variants_dir,
            branch_id="evol1",
            canonical_path=evol1_annotated_path,
        )
    )
    evol2_annotated_for_norm = str(
        _discover_evolution_annotated_vcf(
            selected_dir=selected_dir,
            variants_dir=variants_dir,
            branch_id="evol2",
            canonical_path=evol2_annotated_path,
        )
    )
    evol1_annotated_normalized = str(
        (variants_dir / "evol1.annotated.normalized.vcf.gz").resolve(strict=False)
    )
    evol2_annotated_normalized = str(
        (variants_dir / "evol2.annotated.normalized.vcf.gz").resolve(strict=False)
    )
    final_csv = str((final_dir / "variants_shared.csv").resolve(strict=False))
    # Fix #23: Path("") evaluates to Path(".") with str representation ".",
    # which is truthy. If the upstream ``analysis_spec`` lacks
    # ``requested_data_root`` the raw string here is "", expansion yields
    # Path("."), the old truthy check accepted it as "a real path", and
    # subsequent joins (e.g. ``data_root / "anc_R1.fastq.gz"``) resolved
    # against cwd — corrupting the planner's correct absolute paths into
    # repo-root fakes like ``/Users/.../bio_harness/anc_R1.fastq.gz``.
    # Observed in exp38/exp39 Qwen 3.6 runs where the planner emitted the
    # canonical data-root paths correctly but the binder overwrote them.
    # Fix: treat empty / cwd-only ("." or "") as "no requested_data_root"
    # and defer to the benchmark-task-dir fallback.
    requested_raw = str(ctx.analysis_spec.get("requested_data_root", "") or "").strip()
    if requested_raw and requested_raw not in {".", "./"}:
        analysis_spec_data_root = Path(requested_raw).expanduser()
    else:
        analysis_spec_data_root = None
    data_root = analysis_spec_data_root if analysis_spec_data_root else _benchmark_task_data_dir(selected_dir)
    if data_root is not None:
        data_root = data_root.resolve(strict=False)
    anc_reads_1 = str((data_root / "anc_R1.fastq.gz").resolve(strict=False)) if data_root else ""
    anc_reads_2 = str((data_root / "anc_R2.fastq.gz").resolve(strict=False)) if data_root else ""
    evol1_reads_1 = str((data_root / "evol1_R1.fastq.gz").resolve(strict=False)) if data_root else ""
    evol1_reads_2 = str((data_root / "evol1_R2.fastq.gz").resolve(strict=False)) if data_root else ""
    evol2_reads_1 = str((data_root / "evol2_R1.fastq.gz").resolve(strict=False)) if data_root else ""
    evol2_reads_2 = str((data_root / "evol2_R2.fastq.gz").resolve(strict=False)) if data_root else ""

    if ctx.tool_name == "spades_assemble":
        if anc_reads_1:
            args["reads_1"] = anc_reads_1
        if anc_reads_2:
            args["reads_2"] = anc_reads_2
        args["output_dir"] = str((selected_dir / "assembly").resolve(strict=False))
        args.setdefault("careful", True)
        args.setdefault("threads", 8)
        args.setdefault("memory_gb", 32)
    elif ctx.tool_name == "prodigal_annotate":
        args["input_fasta"] = reference_fasta
        args["output_gff"] = prodigal_annotation_gff
        args["output_faa"] = annotation_faa
        args["mode"] = "auto"
    elif ctx.tool_name == "prokka_annotate":
        args["input_fasta"] = reference_fasta
        args["output_dir"] = str(annotation_dir.resolve(strict=False))
        args["sample_prefix"] = "ancestor"
        args.setdefault("cpus", 8)
    elif ctx.tool_name == "bwa_mem_align":
        args["reference_fasta"] = reference_fasta
        args.setdefault("threads", 8)
        args.setdefault("postprocess_mode", "fixmate_markdup_q20")
        # Fix #16: prefer branch_id over objective. Objectives frequently
        # mention "ancestor" when referring to the SCAFFOLD REFERENCE used
        # for the alignment (e.g. "Align evol1 reads to the assembled
        # ancestor scaffold reference"), which must not cause the evolved
        # branch to be re-bound to ancestor reads/output.
        # Fix #24: evol1/evol2 objective-based fallback when branch_id is
        # empty. Qwen 3.6 in hierarchical workflow mode frequently emits
        # bwa_mem_align with an empty branch_id but a branch-mentioning
        # objective ("Align evol1 reads to the assembly reference", etc.).
        # Previously only "ancestor" had an objective fallback, so evolved
        # steps silently passed through the planner's non-canonical
        # ``output_bam`` (e.g. ``evol1.bam``) — which then caused the next
        # step's ``evol1_aligned.bam`` input reference to be missing (exp40
        # stalled at step 7 for exactly this reason). Pattern mirrors
        # Fix #21's bcftools_isec_run objective fallback. branch_id still
        # wins over objective when both are present.
        branch_id_lower = str(ctx.branch_id or "").strip().lower()
        objective_lower = str(ctx.objective or "").lower()
        if "evol1" in branch_id_lower or (
            not branch_id_lower and "evol1" in objective_lower
        ):
            if evol1_reads_1:
                args["reads_1"] = evol1_reads_1
            if evol1_reads_2:
                args["reads_2"] = evol1_reads_2
            args["output_bam"] = evol1_bam
        elif "evol2" in branch_id_lower or (
            not branch_id_lower and "evol2" in objective_lower
        ):
            if evol2_reads_1:
                args["reads_1"] = evol2_reads_1
            if evol2_reads_2:
                args["reads_2"] = evol2_reads_2
            args["output_bam"] = evol2_bam
        elif branch_id_lower in {"anc", "ancestor"} or (
            not branch_id_lower and "ancestor" in objective_lower
        ):
            if anc_reads_1:
                args["reads_1"] = anc_reads_1
            if anc_reads_2:
                args["reads_2"] = anc_reads_2
            args["output_bam"] = anc_bam
    elif ctx.tool_name == "freebayes_call":
        args["reference_fasta"] = reference_fasta
        args["ploidy"] = 1
        # Fix #16: same branch_id precedence for freebayes_call.
        # Fix #24: add evol1/evol2 objective-based fallback (see
        # bwa_mem_align above for rationale). If the planner emits
        # freebayes_call with empty branch_id but the objective mentions
        # a specific evolved branch, rebind onto that branch's canonical
        # input_bam + output_vcf so the variant-call chain stays on the
        # deterministic scaffold.
        branch_id_lower = str(ctx.branch_id or "").strip().lower()
        objective_lower = str(ctx.objective or "").lower()
        if "evol1" in branch_id_lower or (
            not branch_id_lower and "evol1" in objective_lower
        ):
            args["input_bam"] = evol1_bam
            args["output_vcf"] = evol1_raw
        elif "evol2" in branch_id_lower or (
            not branch_id_lower and "evol2" in objective_lower
        ):
            args["input_bam"] = evol2_bam
            args["output_vcf"] = evol2_raw
        elif branch_id_lower in {"anc", "ancestor"} or (
            not branch_id_lower and "ancestor" in objective_lower
        ):
            args["input_bam"] = anc_bam
            args["output_vcf"] = anc_raw
    elif ctx.tool_name == "bash_run":
        if "final csv" in ctx.objective or "intersect" in ctx.objective:
            args["command"] = " && ".join(
                [
                    _build_normalize_vcf_command(
                        evol1_annotated_for_norm,
                        evol1_annotated_normalized,
                        reference_fasta,
                    ),
                    _build_normalize_vcf_command(
                        evol2_annotated_for_norm,
                        evol2_annotated_normalized,
                        reference_fasta,
                    ),
                    (
                        f"mkdir -p {shlex.quote(str(final_dir.resolve(strict=False)))} && "
                        f"python3 {shlex.quote(str(SHARED_VARIANT_EXPORTER))} "
                        f"--input-vcf-a {shlex.quote(evol1_annotated_normalized)} "
                        f"--input-vcf-b {shlex.quote(evol2_annotated_normalized)} "
                        f"--output-csv {shlex.quote(final_csv)} "
                        "--min-impact MODERATE --status shared --header-case upper --dedupe-by-gene"
                    ),
                ]
            )
        elif "comparison-ready vcf" in ctx.objective or "filter" in ctx.objective:
            # Fix #18: disambiguate AO (bcftools otherwise errors when
            # FreeBayes defines both INFO/AO and FORMAT/AO). SAF/SAR/RPR/RPL
            # are INFO-only in FreeBayes but we qualify them explicitly too
            # so the expression works against VCFs from any caller that
            # defines a FORMAT duplicate in the future.
            expr = (
                "QUAL > 1 & QUAL / INFO/AO > 10 & INFO/SAF > 0 "
                "& INFO/SAR > 0 & INFO/RPR > 1 & INFO/RPL > 1"
            )
            args["command"] = " && ".join(
                [
                    _build_variant_filter_command(anc_raw, anc_filtered, expr),
                    _build_variant_filter_command(evol1_raw, evol1_filtered, expr),
                    _build_variant_filter_command(evol2_raw, evol2_filtered, expr),
                ]
            )
        elif "subtract" in ctx.objective and "evol1" in ctx.branch_id:
            args["command"] = (
                "set -euo pipefail && "
                f"mkdir -p {shlex.quote(str(variants_dir.resolve(strict=False)))} && "
                f"bcftools isec -C -w1 {shlex.quote(evol1_filtered)} {shlex.quote(anc_filtered)} "
                f"-Oz -o {shlex.quote(evol1_sub)} && "
                f"tabix -f -p vcf {shlex.quote(evol1_sub)}"
            )
        elif "subtract" in ctx.objective and "evol2" in ctx.branch_id:
            args["command"] = (
                "set -euo pipefail && "
                f"mkdir -p {shlex.quote(str(variants_dir.resolve(strict=False)))} && "
                f"bcftools isec -C -w1 {shlex.quote(evol2_filtered)} {shlex.quote(anc_filtered)} "
                f"-Oz -o {shlex.quote(evol2_sub)} && "
                f"tabix -f -p vcf {shlex.quote(evol2_sub)}"
            )
        elif (
            "subtract" in ctx.objective
            and "intersect" not in ctx.objective
            and "final csv" not in ctx.objective
            and "annotated" not in ctx.objective
        ):
            args["command"] = " && ".join(
                [
                    (
                        "set -euo pipefail && "
                        f"mkdir -p {shlex.quote(str(variants_dir.resolve(strict=False)))} && "
                        f"bcftools isec -C -w1 {shlex.quote(evol1_filtered)} {shlex.quote(anc_filtered)} "
                        f"-Oz -o {shlex.quote(evol1_sub)} && "
                        f"tabix -f -p vcf {shlex.quote(evol1_sub)}"
                    ),
                    (
                        "set -euo pipefail && "
                        f"mkdir -p {shlex.quote(str(variants_dir.resolve(strict=False)))} && "
                        f"bcftools isec -C -w1 {shlex.quote(evol2_filtered)} {shlex.quote(anc_filtered)} "
                        f"-Oz -o {shlex.quote(evol2_sub)} && "
                        f"tabix -f -p vcf {shlex.quote(evol2_sub)}"
                    ),
                ]
            )
    elif ctx.tool_name == "snpeff_annotate":
        args.pop("annotation_field", None)
        args["reference_fasta"] = reference_fasta
        args["annotation_gff"] = annotation_gff
        args["config_dir"] = snpeff_config_dir
        args["genome_db"] = "ancestor"
        # Fix #24: add evol1/evol2 objective-based fallback so snpeff_annotate
        # still binds correctly when the planner emits the step with an empty
        # branch_id but a branch-mentioning objective.
        branch_id_lower = str(ctx.branch_id or "").strip().lower()
        objective_lower = str(ctx.objective or "").lower()
        if "evol1" in branch_id_lower or (
            not branch_id_lower and "evol1" in objective_lower
        ):
            args["input_vcf"] = evol1_sub
            args["output_vcf"] = evol1_annotated
        elif "evol2" in branch_id_lower or (
            not branch_id_lower and "evol2" in objective_lower
        ):
            args["input_vcf"] = evol2_sub
            args["output_vcf"] = evol2_annotated
    elif ctx.tool_name == "bcftools_norm_run":
        args["reference_fasta"] = reference_fasta
        args.setdefault("multiallelic_mode", "-any")
        branch_hint = _infer_evolution_branch_from_context(
            branch_id=str(ctx.branch_id or ""),
            objective=str(ctx.objective or ""),
            arguments=args,
        )
        if branch_hint == "evol1":
            args["input_vcf"] = evol1_annotated_for_norm
            args["output_vcf"] = evol1_annotated_normalized
        elif branch_hint == "evol2":
            args["input_vcf"] = evol2_annotated_for_norm
            args["output_vcf"] = evol2_annotated_normalized
    elif ctx.tool_name == "bcftools_filter_run":
        # Fix #17: when the LLM pivots from bash_run to the typed
        # bcftools_filter_run wrapper, it often synthesizes paths rooted at
        # the data dir (raw fastq location) rather than selected/variants.
        # Rebind by branch_id so the filter still lands on the deterministic
        # scaffold paths that downstream isec/snpeff steps expect.
        # Fix #24: add evol1/evol2 objective-based fallback (same pattern
        # as bwa_mem_align and freebayes_call above). Without this the
        # planner could emit a typed filter step with empty branch_id,
        # pass through its own non-canonical paths, and break the
        # downstream ancestor-subtract / annotate chain.
        branch_id_lower = str(ctx.branch_id or "").strip().lower()
        objective_lower = str(ctx.objective or "").lower()
        if "evol1" in branch_id_lower or (
            not branch_id_lower and "evol1" in objective_lower
        ):
            args["input_vcf"] = evol1_raw
            args["output_vcf"] = evol1_filtered
        elif "evol2" in branch_id_lower or (
            not branch_id_lower and "evol2" in objective_lower
        ):
            args["input_vcf"] = evol2_raw
            args["output_vcf"] = evol2_filtered
        elif branch_id_lower in {"anc", "ancestor"} or (
            not branch_id_lower and "ancestor" in objective_lower
        ):
            args["input_vcf"] = anc_raw
            args["output_vcf"] = anc_filtered
        # Fix #18: INFO/-qualify ambiguous fields (see fallback branch above).
        args.setdefault(
            "filter_expression",
            (
                "QUAL > 1 & QUAL / INFO/AO > 10 & INFO/SAF > 0 "
                "& INFO/SAR > 0 & INFO/RPR > 1 & INFO/RPL > 1"
            ),
        )
    elif ctx.tool_name == "shared_variants_export_run":
        # Fix #22a: rebind the typed shared-variants export wrapper onto the
        # canonical evolution scaffold. The stepwise planner emits this
        # wrapper with hallucinated paths (e.g. ``evol1.normalized.vcf`` /
        # ``evol2.normalized.vcf`` in a non-standard directory), then fails
        # either with "Missing required parameter(s)" or with
        # FileNotFoundError when the invented paths don't exist on disk.
        # The binder is the authoritative source for evolution artifact
        # locations — it already knows the canonical annotated-and-
        # normalized VCF names (evol1.annotated.normalized.vcf.gz /
        # evol2.annotated.normalized.vcf.gz) and the final CSV path
        # (final/variants_shared.csv). Unconditionally overwrite the
        # planner's paths with those canonical ones so the wrapper always
        # receives a well-formed request and so Fix #22b's branch-
        # completeness check sees paths it can reason about.
        args["input_vcf_a"] = evol1_annotated_normalized
        args["input_vcf_b"] = evol2_annotated_normalized
        args["output_csv"] = final_csv
        args.setdefault("min_impact", "MODERATE")
        args.setdefault("status", "shared")
        args.setdefault("header_case", "upper")
        args.setdefault("dedupe_by_gene", True)
    elif ctx.tool_name == "bcftools_isec_run":
        # Fix #17: rebind typed isec wrapper inputs when the LLM pivots from
        # bash_run. The wrapper only accepts `input_vcfs` + `output_dir`
        # plus an optional branch-named `output_vcf`. The helper still writes
        # bcftools' numbered private-variant VCFs into output_dir, then
        # materializes the stable branch artifact for downstream binders.
        branch_hint = _infer_evolution_branch_from_context(
            branch_id=str(ctx.branch_id or ""),
            objective=str(ctx.objective or ""),
            arguments=args,
        )
        if branch_hint == "evol1":
            args["input_vcfs"] = [evol1_filtered, anc_filtered]
            args["output_dir"] = evol1_isec_dir
            args["output_vcf"] = evol1_sub
        elif branch_hint == "evol2":
            args["input_vcfs"] = [evol2_filtered, anc_filtered]
            args["output_dir"] = evol2_isec_dir
            args["output_vcf"] = evol2_sub
        else:
            # Fix #21: objective-based fallback when branch_id is empty.
            # The stepwise planner emits bcftools_isec_run inside a
            # hierarchical workflow using fuzzy parameter_hints (e.g.
            # ``input_files``, ``output_file``) that the plan normalizer
            # cannot map to the wrapper schema. Result: the accepted step
            # arrives with ``arguments: {}`` and fails on
            # "Missing required parameter(s) for template: input_vcfs,
            # output_dir". Once the failed step is frozen into the executed
            # prefix the planner's repair attempt is then rejected as a
            # prefix mutation, livelocking the run. Fill the wrapper inputs
            # from the deterministic selected/variants scaffold so the
            # first attempt always has the correct typed params. Objective
            # keywords or concrete path names pick evol1 vs evol2; ambiguous
            # candidates default to evol1 (the first ancestor-subtraction in
            # the chain), which matches the order downstream bash-run fallbacks
            # already use.
            args["input_vcfs"] = [evol1_filtered, anc_filtered]
            args["output_dir"] = evol1_isec_dir
            args["output_vcf"] = evol1_sub
        args.setdefault("mode", "complement")

    constrained["arguments"] = args
    return constrained


def _bind_variant_annotation(
    step_spec: dict[str, Any],
    ctx: "StrictArtifactBindingContext",
) -> dict[str, Any]:
    """Bind strict variant-annotation artifacts without replacing workflow intent."""

    constrained, args = _copy_step_with_arguments(step_spec)

    if ctx.selected_dir is None:
        constrained["arguments"] = args
        return constrained

    selected_dir = ctx.selected_dir
    if _is_cystic_fibrosis_variant_task(ctx):
        if ctx.tool_name == "bash_run":
            command = str(args.get("command", "") or "").strip()
            if command:
                args["command"] = _normalize_cystic_fibrosis_bash_command(
                    command,
                    objective=ctx.objective,
                    step_id=int(step_spec.get("step_id", 0) or 0),
                    selected_dir=ctx.selected_dir,
                    data_root=_benchmark_task_data_dir(ctx.selected_dir),
                )
        constrained["arguments"] = args
        return constrained

    data_root = _benchmark_task_data_dir(selected_dir)
    output_dir = selected_dir / "output"
    annotated_vcf = str((output_dir / "annotated.vcf").resolve(strict=False))
    filtered_vcf = str((output_dir / "filtered_pathogenic.vcf").resolve(strict=False))
    config_dir = str((output_dir / "snpeff_custom_db").resolve(strict=False))

    input_vcf = str(args.get("input_vcf", "") or "").strip()
    reference_fasta = str(args.get("reference_fasta", "") or "").strip()
    annotation_gff = str(args.get("annotation_gff", "") or "").strip()
    if data_root is not None:
        if not input_vcf:
            input_vcf = str((data_root / "input_variants.vcf").resolve(strict=False))
        if not reference_fasta:
            reference_fasta = str((data_root / "reference.fa").resolve(strict=False))
        if not annotation_gff:
            annotation_gff = str((data_root / "genes.gff").resolve(strict=False))

    if ctx.tool_name == "snpeff_annotate":
        args.pop("annotation_field", None)
        if input_vcf:
            args["input_vcf"] = input_vcf
        if reference_fasta:
            args["reference_fasta"] = reference_fasta
        if annotation_gff:
            args["annotation_gff"] = annotation_gff
        args["genome_db"] = "custom_ref"
        args["config_dir"] = config_dir
        args["output_vcf"] = annotated_vcf
    elif ctx.tool_name == "bash_run":
        args["command"] = (
            "mkdir -p "
            f"{shlex.quote(str(output_dir.resolve(strict=False)))} && "
            "SnpSift filter "
            "\"(ANN[*].IMPACT = 'HIGH') || (ANN[*].IMPACT = 'MODERATE')\" "
            f"{shlex.quote(annotated_vcf)} > {shlex.quote(filtered_vcf)}"
        )

    constrained["arguments"] = args
    return constrained
