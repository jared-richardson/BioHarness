from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.skills.library._gatk_support import render_reference_and_bam_prereqs, resolve_gatk_bin


def gatk_mutect2_call(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    reference_fasta = str(kwargs.get("reference_fasta", "")).strip()
    tumor_bam = str(kwargs.get("tumor_bam", "")).strip()
    tumor_sample = str(kwargs.get("tumor_sample", "")).strip()
    output_vcf = str(kwargs.get("output_vcf", "")).strip()
    if not reference_fasta or not tumor_bam or not tumor_sample or not output_vcf:
        raise ValueError("Missing required parameter(s) for template: output_vcf, reference_fasta, tumor_bam, tumor_sample")

    normal_bam = str(kwargs.get("normal_bam", "")).strip()
    normal_sample = str(kwargs.get("normal_sample", "")).strip()
    pon_vcf = str(kwargs.get("panel_of_normals", "")).strip()
    germline_resource = str(kwargs.get("germline_resource", "")).strip()
    threads = int(kwargs.get("threads", 2) or 2)
    out_dir = str(Path(output_vcf).expanduser().parent)
    gatk_bin = resolve_gatk_bin()

    normal_args = ""
    if normal_bam and normal_sample:
        normal_args = f" -I {shlex.quote(normal_bam)} -normal {shlex.quote(normal_sample)}"
    optional_args = ""
    if pon_vcf:
        optional_args += f" --panel-of-normals {shlex.quote(pon_vcf)}"
    if germline_resource:
        optional_args += f" --germline-resource {shlex.quote(germline_resource)}"

    command = (
        "set -euo pipefail; "
        f"mkdir -p {shlex.quote(out_dir)}; "
        + "; ".join(render_reference_and_bam_prereqs(reference_fasta, [tumor_bam, normal_bam]))
        + "; "
        f"{gatk_bin} Mutect2 -R {shlex.quote(reference_fasta)} "
        f"-I {shlex.quote(tumor_bam)} -tumor {shlex.quote(tumor_sample)}"
        f"{normal_args}{optional_args} --native-pair-hmm-threads {int(threads)} "
        f"-O {shlex.quote(output_vcf)}"
    )
    return command
