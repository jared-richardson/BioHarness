"""Template compiler for germline variant calling."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bio_harness.core.protocol_grounding._shared import (
    _discover_fastq_pairs,
    _renumber_plan,
)
from bio_harness.core.request_output_intent import is_file_like_output_path


def _germline_output_vcf(
    *,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    selected_dir: Path,
) -> str:
    """Return the germline VCF path for one compiled plan."""

    candidate_values: list[str] = []
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "gatk_haplotypecaller":
            continue
        args = step.get("arguments", {})
        if not isinstance(args, dict):
            continue
        output_vcf = str(args.get("output_vcf", "") or "").strip()
        if output_vcf:
            candidate_values.append(output_vcf)

    spec = analysis_spec if isinstance(analysis_spec, dict) else {}
    explicit_execution_intent = (
        spec.get("explicit_execution_intent", {})
        if isinstance(spec.get("explicit_execution_intent", {}), dict)
        else {}
    )
    locked_argument_values = (
        explicit_execution_intent.get("locked_argument_values", {})
        if isinstance(explicit_execution_intent.get("locked_argument_values", {}), dict)
        else {}
    )
    haplotype_locked_args = (
        locked_argument_values.get("gatk_haplotypecaller", {})
        if isinstance(locked_argument_values.get("gatk_haplotypecaller", {}), dict)
        else {}
    )
    locked_output_vcf = str(haplotype_locked_args.get("output_vcf", "") or "").strip()
    if locked_output_vcf:
        candidate_values.append(locked_output_vcf)

    requested_output_paths = spec.get("requested_output_paths", [])
    if isinstance(requested_output_paths, list):
        for raw_path in requested_output_paths:
            path_text = str(raw_path or "").strip()
            if not path_text or not is_file_like_output_path(path_text):
                continue
            path_lower = path_text.lower()
            if path_lower.endswith(".vcf") or path_lower.endswith(".vcf.gz"):
                candidate_values.append(path_text)

    seen: set[str] = set()
    for raw_path in candidate_values:
        normalized = Path(raw_path).expanduser()
        if not normalized.is_absolute():
            normalized = selected_dir / normalized
        resolved = normalized.resolve(strict=False)
        rendered = str(resolved)
        if rendered in seen:
            continue
        seen.add(rendered)
        return rendered

    return str((selected_dir / "final" / "variants.vcf").resolve(strict=False))


def _compile_germline_variant_calling_plan(
    *,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Deterministic template for germline variant calling (BWA + GATK/DeepVariant + hap.py)."""
    pairs = _discover_fastq_pairs(data_root)
    if not pairs:
        return plan, {"changed": False, "why": "no_fastq_pairs"}

    sd = str(selected_dir)
    # Find reference genome
    ref_fasta = ""
    for search_dir in [data_root, data_root.parent / "references", Path(sd).parent / "references"]:
        if not search_dir.is_dir():
            continue
        for candidate in sorted(search_dir.rglob("*")):
            if candidate.is_file() and re.search(r"\.(fa|fasta|fna)(\.gz)?$", candidate.name, re.I) and re.search(r"(genome|ref|grch|hg19|hg38|hs37)", candidate.name, re.I):
                ref_fasta = str(candidate.resolve())
                break
        if ref_fasta:
            break
    if not ref_fasta:
        return plan, {"changed": False, "why": "no_reference_genome"}

    # Find truth VCF and confident regions BED for hap.py benchmark
    truth_vcf = ""
    conf_bed = ""
    for search_dir in [data_root, data_root.parent / "references"]:
        if not search_dir.is_dir():
            continue
        for candidate in sorted(search_dir.rglob("*")):
            if candidate.is_file():
                name_l = candidate.name.lower()
                if not truth_vcf and re.search(r"(truth|giab|nist|hg00[12])", name_l) and name_l.endswith((".vcf", ".vcf.gz")):
                    truth_vcf = str(candidate.resolve())
                if not conf_bed and re.search(r"(confident|highconf|callable)", name_l) and name_l.endswith((".bed", ".bed.gz")):
                    conf_bed = str(candidate.resolve())

    label = next(iter(pairs))
    pair = pairs[label]
    bam_path = f"{sd}/intermediate/aligned_sorted_markdup.bam"
    output_vcf = _germline_output_vcf(
        plan=plan,
        analysis_spec=analysis_spec,
        selected_dir=selected_dir,
    )

    steps: list[dict[str, Any]] = [
        {
            "step_id": 1,
            "tool_name": "bwa_mem_align",
            "purpose": f"Align {label} reads to reference genome",
            "arguments": {
                "reference_fasta": ref_fasta,
                "reads_1": pair["reads_1"],
                "reads_2": pair["reads_2"],
                "threads": 4,
                "output_bam": bam_path,
                "postprocess_mode": "fixmate_markdup_q20",
            },
        },
        {
            "step_id": 2,
            "tool_name": "gatk_haplotypecaller",
            "purpose": f"Call germline variants for {label}",
            "arguments": {
                "reference_fasta": ref_fasta,
                "input_bam": bam_path,
                "output_vcf": output_vcf,
            },
        },
    ]

    if truth_vcf:
        happy_cmd = f"mkdir -p {sd}/benchmark && hap.py {truth_vcf} {output_vcf} -r {ref_fasta} -o {sd}/benchmark/{label}"
        if conf_bed:
            happy_cmd += f" -f {conf_bed}"
        happy_cmd += " --pass-only"
        steps.append({
            "step_id": 3,
            "tool_name": "bash_run",
            "purpose": "Benchmark variant calls with hap.py",
            "arguments": {"command": happy_cmd},
        })

    compiled = {
        "thought_process": "[germline_vc_template] BWA->GATK HaplotypeCaller" + (" ->hap.py" if truth_vcf else "") + f" for {label}. " + str(plan.get("thought_process", "")),
        "plan": steps,
    }
    return _renumber_plan(compiled), {
        "changed": True,
        "why": "compiled_germline_variant_calling_protocol",
        "sample_label": label,
        "reference_fasta": ref_fasta,
        "truth_vcf": truth_vcf,
        "confident_bed": conf_bed,
    }
