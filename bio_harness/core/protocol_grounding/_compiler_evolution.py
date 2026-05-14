"""Template compiler for bacterial evolution shared-variant analysis."""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.analysis_spec_support import preferred_helper_python_executable
from bio_harness.core.tool_env import requirement_available
from bio_harness.core.tool_launchers import tool_launcher_uses_container
from bio_harness.core.protocol_grounding._shared import (
    DEFAULT_SHARED_VARIANT_COLUMNS,
    SHARED_VARIANT_EXPORTER,
    _build_normalize_vcf_command,
    _build_variant_filter_command,
    _discover_fastq_pairs,
    _renumber_plan,
    _safe_label,
    shlex_quote,
)


def _infer_reference_and_samples(pair_map: dict[str, dict[str, str]]) -> tuple[str, list[str]]:
    labels = sorted(pair_map)
    if not labels:
        return "", []
    reference = ""
    for label in labels:
        low = label.lower()
        if any(token in low for token in ("ancestor", "anc", "parent", "wt", "wildtype", "reference")):
            reference = label
            break
    if not reference:
        reference = labels[0]
    samples = [label for label in labels if label != reference]
    return reference, samples


def _shared_export_settings(grounding: dict[str, Any]) -> dict[str, Any]:
    benchmark_profile = grounding.get("benchmark_profile", {}) if isinstance(grounding.get("benchmark_profile", {}), dict) else {}
    export_profile = (
        benchmark_profile.get("export_profile", {})
        if isinstance(benchmark_profile.get("export_profile", {}), dict)
        else {}
    )
    shared_policy = (
        benchmark_profile.get("shared_variant_policy", {})
        if isinstance(benchmark_profile.get("shared_variant_policy", {}), dict)
        else {}
    )
    output_columns = [
        str(col).strip()
        for col in (grounding.get("output_columns", []) or [])
        if str(col).strip()
    ]
    return {
        "filename": str(export_profile.get("filename", "")).strip() or "variants_shared.csv",
        "header_case": str(export_profile.get("header_case", "")).strip().lower() or "lower",
        "status": str(export_profile.get("status", "")).strip() or "shared",
        "dedupe_by_gene": bool(export_profile.get("dedupe_by_gene", shared_policy.get("dedupe_by_gene", True))),
        "min_impact": str(export_profile.get("min_impact", shared_policy.get("min_impact", "MODERATE"))).strip() or "MODERATE",
        "output_columns": output_columns
        or [str(col).strip() for col in (export_profile.get("output_columns", []) or []) if str(col).strip()]
        or list(DEFAULT_SHARED_VARIANT_COLUMNS),
        "normalize_before_compare": bool(shared_policy.get("normalize_before_compare", True)),
    }


def _compile_bacterial_evolution_shared_plan(
    *,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    grounding = (
        analysis_spec.get("protocol_grounding", {})
        if isinstance(analysis_spec, dict) and isinstance(analysis_spec.get("protocol_grounding", {}), dict)
        else {}
    )
    # Note: requires_shared_comparison is now inferred from the data layout
    # (ancestor + evolved pairs) rather than requiring the LLM to set it.

    pair_map = _discover_fastq_pairs(data_root)
    reference_label, sample_labels = _infer_reference_and_samples(pair_map)
    min_variant_branches = int(grounding.get("min_variant_branches", 0) or 0)
    if not reference_label:
        return plan, {"changed": False, "why": "reference_pair_not_found"}
    if len(sample_labels) < max(2, min_variant_branches):
        return plan, {
            "changed": False,
            "why": "insufficient_sample_pairs",
            "reference_label": reference_label,
            "sample_labels": sample_labels,
        }

    sample_labels = sample_labels[: max(2, min_variant_branches)]
    reference_pair = pair_map[reference_label]
    benchmark_profile = grounding.get("benchmark_profile", {}) if isinstance(grounding.get("benchmark_profile", {}), dict) else {}
    export_settings = _shared_export_settings(grounding)
    annotation_strategy = (
        benchmark_profile.get("annotation_strategy", {})
        if isinstance(benchmark_profile.get("annotation_strategy", {}), dict)
        else {}
    )
    preferred_annotation_tool = str(annotation_strategy.get("tool_name", "")).strip().lower() or "prokka_annotate"
    fallback_annotation_tool = str(annotation_strategy.get("fallback_tool_name", "")).strip().lower() or "prodigal_annotate"
    use_fallback_annotation = preferred_annotation_tool == "prokka_annotate" and (
        not requirement_available("prokka") or tool_launcher_uses_container("prokka")
    )
    annotation_tool_name = fallback_annotation_tool if use_fallback_annotation else preferred_annotation_tool

    # Pre-validation: warn about missing tools (non-blocking; runtime validation catches)
    _preflight_tools = {
        "trimming": ["fastp"],
        "assembly": ["spades.py"],
        "alignment": ["bwa", "bwa-mem2"],
        "variant_calling": ["freebayes"],
        "filtering": ["bcftools", "vcffilter"],
        "indexing": ["samtools", "bgzip", "tabix"],
        "annotation": ["prokka", "prodigal"] if annotation_tool_name == "prokka_annotate" else ["prodigal"],
        "effect_prediction": ["snpEff", "snpeff"],
    }
    _missing_categories: list[str] = []
    for category, binaries in _preflight_tools.items():
        if not any(requirement_available(b) for b in binaries):
            _missing_categories.append(f"{category}({','.join(binaries)})")
    if _missing_categories:
        import logging
        logging.getLogger(__name__).warning("Preflight: missing tool categories: %s", "; ".join(_missing_categories))

    variant_filter = (
        benchmark_profile.get("variant_filter", {})
        if isinstance(benchmark_profile.get("variant_filter", {}), dict)
        else {}
    )
    # Fix #18: bare AO breaks bcftools when FreeBayes defines both INFO/AO
    # and FORMAT/AO ("ambiguous filtering expression"). Qualify INFO/*
    # explicitly for safety.
    filter_expression = (
        str(variant_filter.get("expression", "")).strip()
        or "QUAL > 1 & QUAL / INFO/AO > 10 & INFO/SAF > 0 & INFO/SAR > 0 & INFO/RPR > 1 & INFO/RPL > 1"
    )
    trimmed_dir = selected_dir / "trimmed"
    assembly_dir = selected_dir / "assembly" / "ancestor_spades"
    reference_fasta = str((assembly_dir / "scaffolds.fasta").resolve(strict=False))
    annotation_dir = selected_dir / "annotation" / "prokka"
    annotation_gff = str((annotation_dir / "ancestor.gff").resolve(strict=False))
    annotation_faa = str((annotation_dir / "ancestor.faa").resolve(strict=False))
    snpeff_config_dir = selected_dir / "annotation" / "_snpeff"

    # Build fastp trimming commands for all samples (reference + evolved).
    # NOTE: Trimming is opt-in via benchmark_profile["enable_read_trimming"].
    # De novo assembly (SPAdes) is sensitive to read quality changes — trimming
    # alters k-mer distributions, producing different contigs.  Benchmark truth
    # files are generated from raw reads, so trimming must be disabled by default
    # to ensure reproducible variant comparisons across runs.
    _fastp_bin = "fastp"
    _enable_trimming = bool(benchmark_profile.get("enable_read_trimming", False))
    _fastp_has = requirement_available(_fastp_bin) and _enable_trimming

    def _trimmed_pair(label: str, pair: dict[str, str]) -> tuple[str, str, dict[str, Any] | None]:
        """Return (trimmed_r1, trimmed_r2, trim_step_or_None)."""
        slug = _safe_label(label)
        t_r1 = str((trimmed_dir / f"{slug}_R1.fastq.gz").resolve(strict=False))
        t_r2 = str((trimmed_dir / f"{slug}_R2.fastq.gz").resolve(strict=False))
        if not _fastp_has:
            return pair["reads_1"], pair["reads_2"], None
        cmd = (
            f"mkdir -p {shlex.quote(str(trimmed_dir.resolve(strict=False)))} && "
            f"fastp --detect_adapter_for_pe --correction --cut_right --thread 8 "
            f"-i {shlex.quote(pair['reads_1'])} -I {shlex.quote(pair['reads_2'])} "
            f"-o {shlex.quote(t_r1)} -O {shlex.quote(t_r2)} "
            f"--html {shlex.quote(str((trimmed_dir / f'{slug}.fastp.html').resolve(strict=False)))} "
            f"--json {shlex.quote(str((trimmed_dir / f'{slug}.fastp.json').resolve(strict=False)))}"
        )
        step = {"tool_name": "bash_run", "arguments": {"command": cmd}}
        return t_r1, t_r2, step

    # Trim reference/ancestor reads
    ref_r1, ref_r2, ref_trim_step = _trimmed_pair(reference_label, reference_pair)

    # Trim evolved sample reads (stored for later per-sample blocks)
    trimmed_samples: dict[str, tuple[str, str]] = {}
    sample_trim_steps: list[dict[str, Any]] = []
    for label in sample_labels:
        s_r1, s_r2, trim_step = _trimmed_pair(label, pair_map[label])
        trimmed_samples[label] = (s_r1, s_r2)
        if trim_step is not None:
            sample_trim_steps.append(trim_step)

    steps: list[dict[str, Any]] = []

    # Add all trimming steps first (ancestor + all evolved samples)
    if ref_trim_step is not None:
        steps.append(ref_trim_step)
    steps.extend(sample_trim_steps)

    steps.append({
        "tool_name": "spades_assemble",
        "arguments": {
            "reads_1": ref_r1,
            "reads_2": ref_r2,
            "threads": 8,
            "memory_gb": 32,
            "careful": True,
            "output_dir": str(assembly_dir.resolve(strict=False)),
        },
    })
    steps.append({
        "tool_name": annotation_tool_name,
        "arguments": (
            {
                "input_fasta": reference_fasta,
                "output_dir": str(annotation_dir.resolve(strict=False)),
                "sample_prefix": "ancestor",
                "cpus": 8,
                "kingdom": str(annotation_strategy.get("kingdom", "")).strip() or "Bacteria",
                "genus": str(annotation_strategy.get("genus", "")).strip() or "Escherichia",
                "species": str(annotation_strategy.get("species", "")).strip() or "coli",
            }
            if annotation_tool_name == "prokka_annotate"
            else {
                "input_fasta": reference_fasta,
                "output_gff": annotation_gff,
                "output_faa": annotation_faa,
            }
        ),
    })

    for label in sample_labels:
        slug = _safe_label(label)
        sample_r1, sample_r2 = trimmed_samples.get(label, (pair_map[label]["reads_1"], pair_map[label]["reads_2"]))
        bam_path = str((selected_dir / "mappings" / f"{slug}.sorted.dedup.q20.bam").resolve(strict=False))
        unmapped_bam = str((selected_dir / "mappings" / f"{slug}.sorted.unmapped.bam").resolve(strict=False))
        raw_vcf = str((selected_dir / "variants" / f"{slug}.raw.vcf").resolve(strict=False))
        filtered_vcf = str((selected_dir / "variants" / f"{slug}.filtered.vcf.gz").resolve(strict=False))
        annotated_vcf = str((selected_dir / "variants" / f"{slug}.annotated.vcf").resolve(strict=False))
        normalized_vcf = str((selected_dir / "variants" / f"{slug}.normalized.vcf.gz").resolve(strict=False))
        filter_cmd = _build_variant_filter_command(raw_vcf, filtered_vcf, filter_expression)
        steps.extend(
            [
                {
                    "tool_name": "bwa_mem_align",
                    "arguments": {
                        "reference_fasta": reference_fasta,
                        "reads_1": sample_r1,
                        "reads_2": sample_r2,
                        "output_bam": bam_path,
                        "threads": 8,
                        "postprocess_mode": "fixmate_markdup_q20",
                        "output_unmapped_bam": unmapped_bam,
                    },
                },
                {
                    "tool_name": "freebayes_call",
                    "arguments": {
                        "reference_fasta": reference_fasta,
                        "input_bam": bam_path,
                        "output_vcf": raw_vcf,
                        "ploidy": 1,
                    },
                },
                {
                    "tool_name": "bash_run",
                    "arguments": {"command": filter_cmd},
                },
                {
                    "tool_name": "snpeff_annotate",
                    "arguments": {
                        "genome_db": "ancestor",
                        "reference_fasta": reference_fasta,
                        "annotation_gff": annotation_gff,
                        "input_vcf": filtered_vcf,
                        "output_vcf": annotated_vcf,
                        "config_dir": str(snpeff_config_dir.resolve(strict=False)),
                        "genome_label": "EColiMut",
                        "codon_table": "",
                        "check_protein": False,
                        "check_cds": False,
                    },
                },
                {
                    "tool_name": "bash_run",
                    "arguments": {
                        "command": _build_normalize_vcf_command(annotated_vcf, normalized_vcf, reference_fasta),
                    },
                },
            ]
        )

    compare_vcfs = []
    for label in sample_labels[:2]:
        slug = _safe_label(label)
        normalized_vcf = str((selected_dir / "variants" / f"{slug}.normalized.vcf.gz").resolve(strict=False))
        annotated_vcf = str((selected_dir / "variants" / f"{slug}.annotated.vcf").resolve(strict=False))
        compare_vcfs.append(normalized_vcf if export_settings["normalize_before_compare"] else annotated_vcf)
    output_csv = str((selected_dir / "final" / export_settings["filename"]).resolve(strict=False))
    helper_python = shlex_quote(str(preferred_helper_python_executable()))
    export_cmd = (
        f"mkdir -p {shlex.quote(str((selected_dir / 'final').resolve(strict=False)))} && "
        f"{helper_python} {shlex_quote(str(SHARED_VARIANT_EXPORTER.resolve(strict=False)))} "
        f"--input-vcf-a {shlex_quote(compare_vcfs[0])} "
        f"--input-vcf-b {shlex_quote(compare_vcfs[1])} "
        f"--output-csv {shlex_quote(output_csv)} "
        f"--min-impact {shlex_quote(export_settings['min_impact'])} "
        f"--status {shlex_quote(export_settings['status'])} "
        f"--header-case {shlex_quote(export_settings['header_case'])}"
    )
    if export_settings["dedupe_by_gene"]:
        export_cmd += " --dedupe-by-gene"
    steps.append({"tool_name": "bash_run", "arguments": {"command": export_cmd}})

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    thought = str(patched.get("thought_process", "")).strip()
    suffix = "Applied deterministic protocol compiler for shared bacterial evolution variant analysis."
    patched["thought_process"] = f"{thought} {suffix}".strip()
    return _renumber_plan(patched), {
        "changed": True,
        "why": "compiled_bacterial_shared_variant_protocol",
        "reference_label": reference_label,
        "sample_labels": sample_labels,
        "step_count": len(steps),
        "benchmark_profile_id": str(benchmark_profile.get("profile_id", "")).strip(),
        "annotation_tool_name": annotation_tool_name,
    }
