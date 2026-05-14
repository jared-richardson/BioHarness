"""Template compiler for variant annotation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from bio_harness.core.protocol_grounding._shared import _renumber_plan


def _compile_variant_annotation_plan(
    *,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Deterministic template for variant annotation (SnpEff + SnpSift).

    Two modes:
      1. **Custom database**: reference FASTA + GFF found in *data_root* →
         build a custom SnpEff database on-the-fly then annotate.
      2. **Pre-built database**: only a VCF is present → use a standard
         database such as GRCh37.75.
    """
    sd = str(selected_dir)

    # ----- discover input files -----
    input_vcf = ""
    reference_fasta = ""
    annotation_gff = ""
    for candidate in sorted(data_root.rglob("*")):
        if not candidate.is_file():
            continue
        name_l = candidate.name.lower()
        if not input_vcf and name_l.endswith((".vcf", ".vcf.gz")):
            input_vcf = str(candidate.resolve())
        elif not reference_fasta and name_l.endswith((".fa", ".fasta", ".fna")):
            reference_fasta = str(candidate.resolve())
        elif not annotation_gff and name_l.endswith((".gff", ".gff3", ".gff.gz")):
            annotation_gff = str(candidate.resolve())

    if not input_vcf:
        return plan, {"changed": False, "why": "no_input_vcf"}

    grounding = (analysis_spec or {}).get("protocol_grounding", {}) if isinstance(analysis_spec, dict) else {}
    genome_db = str(grounding.get("genome_db", "")).strip()
    custom_db = bool(reference_fasta and annotation_gff)
    if not genome_db:
        genome_db = "custom_bench" if custom_db else "GRCh37.75"

    annotated_vcf = f"{sd}/output/annotated.vcf"
    filtered_vcf = f"{sd}/output/filtered_pathogenic.vcf"

    # ----- step 1: annotate with SnpEff -----
    annot_args: dict[str, Any] = {
        "input_vcf": input_vcf,
        "output_vcf": annotated_vcf,
        "genome_db": genome_db,
    }
    if custom_db:
        annot_args["reference_fasta"] = reference_fasta
        annot_args["annotation_gff"] = annotation_gff
        annot_args["config_dir"] = f"{sd}/output/_snpeff/{genome_db}"

    steps: list[dict[str, Any]] = [
        {
            "step_id": 1,
            "tool_name": "snpeff_annotate",
            "purpose": "Annotate variants with SnpEff" + (" (custom database)" if custom_db else ""),
            "arguments": annot_args,
        },
        {
            "step_id": 2,
            "tool_name": "bash_run",
            "purpose": "Filter for clinically significant variants with SnpSift",
            "arguments": {
                "command": (
                    f"mkdir -p {sd}/output && "
                    f"SnpSift filter \"(ANN[*].IMPACT = 'HIGH') || (ANN[*].IMPACT = 'MODERATE')\" "
                    f"{annotated_vcf} > {filtered_vcf}"
                ),
            },
        },
    ]

    compiled = {
        "thought_process": (
            f"[variant_annotation_template] SnpEff {genome_db}"
            + (" custom-db" if custom_db else "")
            + "->SnpSift filter. "
            + str(plan.get("thought_process", ""))
        ),
        "plan": steps,
    }
    return _renumber_plan(compiled), {
        "changed": True,
        "why": "compiled_variant_annotation_protocol",
        "input_vcf": input_vcf,
        "genome_db": genome_db,
        "custom_db": custom_db,
        "reference_fasta": reference_fasta,
        "annotation_gff": annotation_gff,
    }
